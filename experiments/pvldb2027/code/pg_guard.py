"""QueryGuard on PostgreSQL: the same four layers, using PostgreSQL's planner
estimate ("Plan Rows") for G2 and query cancellation for G3.

The agent corpus and dashboard queries are written in DuckDB's dialect and
translated to PostgreSQL with sqlglot, so both engines run the same questions.
"""
import threading
import time

import psycopg2
import psycopg2.errors
import sqlglot

from guard import QueryGuard, static_check

DSN = "host=localhost port=5433 user=postgres dbname=qg"
_STOPPED = set()  # ids of connections whose experiment window has closed


def stop_conn(conn):
    """End whatever the connection is doing: cancel a running statement and stop any result streaming."""
    _STOPPED.add(id(conn))
    conn.cancel()


def to_pg(sql):
    return sqlglot.transpile(sql, read="duckdb", write="postgres")[0]


def connect(dsn=DSN):
    conn = psycopg2.connect(dsn)
    conn.autocommit = False
    return conn


def _walk(plan):
    return max([plan.get("Plan Rows", 0)] + [_walk(c) for c in plan.get("Plans", [])])


def estimate_pg(conn, sql):
    """(largest estimated row count of any plan node, total plan cost)."""
    with conn.cursor() as c:
        c.execute("EXPLAIN (FORMAT JSON) " + sql)
        plan = c.fetchone()[0][0]["Plan"]
    conn.rollback()
    return _walk(plan), plan["Total Cost"]


def execute_pg(conn, sql, timeout_s):
    """Run sql through a server-side cursor, cancelling after timeout_s. Returns (status, rows, seconds)."""
    timer = None
    if timeout_s:
        timer = threading.Timer(timeout_s, conn.cancel)
        timer.start()
    t0 = time.time()
    rows = 0
    try:
        with conn.cursor(name="q") as c:
            c.itersize = 50_000
            c.execute(sql)
            status = "ok"
            while True:
                chunk = c.fetchmany(50_000)
                if not chunk:
                    break
                rows += len(chunk)
                # A cancel sent between FETCH round trips is lost, so also enforce the budget here.
                if (timeout_s and time.time() - t0 > timeout_s) or id(conn) in _STOPPED:
                    status, rows = "timeout", 0
                    break
    except psycopg2.errors.QueryCanceled:
        status, rows = "timeout", 0
    except Exception as e:
        status, rows = f"error:{e.__class__.__name__}", 0
    finally:
        if timer:
            timer.cancel()
        try:
            conn.rollback()
        except Exception:
            pass
    return status, rows, time.time() - t0


class QueryGuardPG(QueryGuard):
    """Same decisions as QueryGuard; only the estimate and the executor differ."""

    def admit(self, conn, sql, session="default"):
        est = None
        if "G1" in self.layers:
            reason = static_check(sql, dialect="postgres")
            if reason:
                return False, reason, est
        if "G2" in self.layers:
            est = estimate_pg(conn, sql)[0]
            if est > self.card_threshold:
                return False, "cost_estimate", est
        if "G4" in self.layers and self._session_spent(session) > self.session_budget_s:
            return False, "session_budget", est
        return True, None, est

    def run(self, conn, sql, session="default"):
        t0 = time.time()
        ok, reason, est = self.admit(conn, sql, session)
        overhead = time.time() - t0
        if not ok:
            return {"status": "blocked", "reason": reason, "est": est, "guard_s": overhead, "exec_s": 0.0, "rows": 0}
        q = f"SELECT * FROM ({sql}) AS _g LIMIT {self.row_cap + 1}" if "G3" in self.layers else sql
        status, rows, exec_s = execute_pg(conn, q, self.time_budget_s if "G3" in self.layers else None)
        if "G3" in self.layers and status == "ok" and rows > self.row_cap:
            status = "truncated"
        if "G4" in self.layers:
            self._charge(session, exec_s)
        return {"status": status, "reason": None, "est": est, "guard_s": overhead, "exec_s": exec_s, "rows": rows}
