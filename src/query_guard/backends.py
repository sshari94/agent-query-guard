"""Database adapters: plan-based cost estimates and execution under a time limit."""
from __future__ import annotations

import contextlib
import json
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class Estimate:
    rows: int | None = None  # largest intermediate row count in the plan (rows read, on ClickHouse)
    cost: float | None = None  # optimizer cost units, where the engine reports them


@dataclass
class Execution:
    status: str  # ok | timeout | error
    columns: list[str] = field(default_factory=list)
    rows: list[tuple] = field(default_factory=list)
    truncated: bool = False
    seconds: float = 0.0
    error: str | None = None


class Backend(Protocol):
    dialect: str

    def estimate(self, sql: str) -> Estimate: ...

    def execute(self, sql: str, timeout_s: float | None, max_rows: int) -> Execution: ...

    def tables(self) -> list[str]: ...


class DuckDBBackend:
    """Wraps a duckdb connection. Timeouts use connection.interrupt()."""

    dialect = "duckdb"

    def __init__(self, con: Any):
        self.con = con

    def estimate(self, sql: str) -> Estimate:
        raw = self.con.execute("EXPLAIN (FORMAT JSON) " + sql).fetchall()[0][1]
        return Estimate(rows=max(duckdb_plan_rows(n) for n in json.loads(raw)))

    def execute(self, sql: str, timeout_s: float | None, max_rows: int) -> Execution:
        timer = threading.Timer(timeout_s, self.con.interrupt) if timeout_s else None
        if timer:
            timer.start()
        t0 = time.perf_counter()
        try:
            res = self.con.execute(sql)
            columns = [d[0] for d in res.description or []]
            rows = res.fetchmany(max_rows + 1)
            truncated = len(rows) > max_rows
            return Execution("ok", columns, rows[:max_rows], truncated, time.perf_counter() - t0)
        except Exception as e:  # duckdb raises InterruptException on timeout
            status = "timeout" if "interrupt" in str(e).lower() else "error"
            return Execution(status, seconds=time.perf_counter() - t0, error=str(e))
        finally:
            if timer:
                timer.cancel()

    def tables(self) -> list[str]:
        return [r[0] for r in self.con.execute(
            "SELECT table_name FROM information_schema.tables ORDER BY 1").fetchall()]


def duckdb_plan_rows(node: dict) -> int:
    est = 0
    info = node.get("extra_info", {})
    if isinstance(info, dict) and "Estimated Cardinality" in info:
        with contextlib.suppress(ValueError):
            est = int(str(info["Estimated Cardinality"]).lstrip("~"))
    return max([est] + [duckdb_plan_rows(c) for c in node.get("children", [])])


class PostgresBackend:
    """Wraps any DB-API connection to Postgres (psycopg 3 or psycopg2).

    Every query runs in its own READ ONLY transaction with a statement_timeout and is
    rolled back afterwards, so the guard is not the only line of defence.
    """

    dialect = "postgres"

    def __init__(self, conn: Any):
        self.conn = conn

    def estimate(self, sql: str) -> Estimate:
        with self.conn.cursor() as cur:
            cur.execute("EXPLAIN (FORMAT JSON) " + sql)
            raw = cur.fetchone()[0]
        self.conn.rollback()
        return postgres_estimate(raw if not isinstance(raw, str) else json.loads(raw))

    def execute(self, sql: str, timeout_s: float | None, max_rows: int) -> Execution:
        t0 = time.perf_counter()
        try:
            with self.conn.cursor() as cur:
                cur.execute("SET TRANSACTION READ ONLY")
                if timeout_s:
                    cur.execute(f"SET LOCAL statement_timeout = {int(timeout_s * 1000)}")
                cur.execute(sql)
                columns = [d[0] for d in cur.description or []]
                rows = [tuple(r) for r in cur.fetchmany(max_rows + 1)]
            truncated = len(rows) > max_rows
            return Execution("ok", columns, rows[:max_rows], truncated, time.perf_counter() - t0)
        except Exception as e:
            status = "timeout" if "statement timeout" in str(e).lower() else "error"
            return Execution(status, seconds=time.perf_counter() - t0, error=str(e))
        finally:
            self.conn.rollback()

    def tables(self) -> list[str]:
        with self.conn.cursor() as cur:
            cur.execute("SELECT table_schema || '.' || table_name FROM information_schema.tables "
                        "WHERE table_schema NOT IN ('pg_catalog', 'information_schema') ORDER BY 1")
            out = [r[0] for r in cur.fetchall()]
        self.conn.rollback()
        return out


def postgres_estimate(explain: list) -> Estimate:
    plan = explain[0]["Plan"]

    def walk(node: dict) -> int:
        return max([int(node.get("Plan Rows", 0))] + [walk(c) for c in node.get("Plans", [])])

    return Estimate(rows=walk(plan), cost=float(plan.get("Total Cost", 0.0)))


class ClickHouseBackend:
    """Wraps a clickhouse-connect client (``clickhouse_connect.get_client(...)``).

    The estimate is the number of rows ClickHouse expects to read (``EXPLAIN ESTIMATE``),
    summed over tables. Limits are also enforced by the server itself on every query:
    ``readonly``, ``max_execution_time`` and ``max_result_rows``.
    """

    dialect = "clickhouse"

    def __init__(self, client: Any, settings: dict[str, Any] | None = None):
        self.client = client
        self.settings = settings or {}

    def estimate(self, sql: str) -> Estimate:
        res = self.client.query("EXPLAIN ESTIMATE " + sql)
        idx = res.column_names.index("rows")
        return Estimate(rows=sum(int(r[idx]) for r in res.result_rows))

    def execute(self, sql: str, timeout_s: float | None, max_rows: int) -> Execution:
        settings = {
            "readonly": 2,  # no writes; 2 still lets this request set the limits below
            "max_result_rows": max_rows + 1,
            "result_overflow_mode": "break",
            **self.settings,
        }
        if timeout_s:
            settings["max_execution_time"] = timeout_s
            settings["timeout_overflow_mode"] = "throw"
        t0 = time.perf_counter()
        try:
            res = self.client.query(sql, settings=settings)
            rows = [tuple(r) for r in res.result_rows]
            truncated = len(rows) > max_rows
            return Execution("ok", list(res.column_names), rows[:max_rows], truncated,
                             time.perf_counter() - t0)
        except Exception as e:
            msg = str(e)
            status = "timeout" if "TIMEOUT_EXCEEDED" in msg or "Timeout exceeded" in msg else "error"
            return Execution(status, seconds=time.perf_counter() - t0, error=msg)

    def tables(self) -> list[str]:
        res = self.client.query(
            "SELECT database || '.' || name FROM system.tables "
            "WHERE database NOT IN ('system', 'INFORMATION_SCHEMA', 'information_schema') ORDER BY 1")
        return [r[0] for r in res.result_rows]
