"""The guard: decide before a query runs, then run it under limits."""
from __future__ import annotations

import json
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from sqlglot import exp

from . import static
from .backends import Backend, ClickHouseBackend, DuckDBBackend, Estimate, PostgresBackend


@dataclass
class Policy:
    """What an agent is allowed to run. Every limit can be switched off with None."""

    read_only: bool = True
    allowed_tables: frozenset[str] | None = None
    denied_tables: frozenset[str] = frozenset()
    denied_functions: frozenset[str] = static.DEFAULT_DENIED_FUNCTIONS
    large_tables: frozenset[str] = frozenset()
    block_cartesian: bool = True
    max_estimated_rows: int | None = 50_000_000
    max_estimated_cost: float | None = None
    timeout_s: float | None = 10.0
    max_result_rows: int = 10_000
    session_budget_s: float | None = 60.0  # execution seconds a session may spend per window
    session_window_s: float = 300.0
    session_max_queries: int | None = None  # queries a session may run per window


@dataclass
class Decision:
    allowed: bool
    reason: str | None = None
    detail: str = ""
    estimated_rows: int | None = None
    estimated_cost: float | None = None


@dataclass
class Result:
    status: str  # ok | blocked | timeout | error
    decision: Decision
    columns: list[str] = field(default_factory=list)
    rows: list[tuple] = field(default_factory=list)
    truncated: bool = False
    seconds: float = 0.0
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["rows"] = [list(r) for r in self.rows]
        return d


class QueryGuard:
    """Admission control for SQL written by AI agents.

    >>> guard = QueryGuard.for_duckdb(con, Policy(max_result_rows=500))
    >>> result = guard.run("SELECT line, count(*) FROM events GROUP BY 1", session="agent-42")
    """

    def __init__(self, backend: Backend, policy: Policy | None = None,
                 audit: str | Path | Callable[[dict], None] | None = None):
        self.backend = backend
        self.policy = policy or Policy()
        self._audit = _audit_sink(audit)
        self._spend: dict[str, deque] = {}
        self._lock = threading.Lock()

    @classmethod
    def for_duckdb(cls, con: Any, policy: Policy | None = None, **kw) -> QueryGuard:
        return cls(DuckDBBackend(con), policy, **kw)

    @classmethod
    def for_clickhouse(cls, client: Any, policy: Policy | None = None, **kw) -> QueryGuard:
        return cls(ClickHouseBackend(client), policy, **kw)

    @classmethod
    def for_postgres(cls, conn: Any, policy: Policy | None = None, **kw) -> QueryGuard:
        return cls(PostgresBackend(conn), policy, **kw)

    def check(self, sql: str, session: str = "default") -> Decision:
        """Decide whether sql may run, without running it."""
        p = self.policy
        violations = static.check(
            sql, dialect=self.backend.dialect, read_only=p.read_only,
            allowed_tables=p.allowed_tables, denied_tables=p.denied_tables,
            denied_functions=p.denied_functions, large_tables=p.large_tables,
            block_cartesian=p.block_cartesian,
        )
        if violations:
            return Decision(False, violations[0].rule, "; ".join(map(str, violations)))

        est = Estimate()
        if p.max_estimated_rows is not None or p.max_estimated_cost is not None:
            try:
                est = self.backend.estimate(sql)
            except Exception as e:
                return Decision(False, "explain_failed", str(e))
            if p.max_estimated_rows is not None and (est.rows or 0) > p.max_estimated_rows:
                detail = f"plan estimates {est.rows:,} rows, limit {p.max_estimated_rows:,}"
                return _with(est, Decision(False, "estimated_rows", detail))
            if p.max_estimated_cost is not None and (est.cost or 0) > p.max_estimated_cost:
                return _with(est, Decision(False, "estimated_cost",
                                           f"plan cost {est.cost:,.0f}, limit {p.max_estimated_cost:,.0f}"))

        # A session may finish the query that takes it over budget; the next one is refused.
        spent, count = self._session_usage(session)
        if p.session_budget_s is not None and spent > p.session_budget_s:
            detail = f"{spent:.1f}s used of {p.session_budget_s}s per {p.session_window_s:.0f}s"
            return _with(est, Decision(False, "session_budget", detail))
        if p.session_max_queries is not None and count >= p.session_max_queries:
            return _with(est, Decision(False, "session_query_limit",
                                       f"{count} queries per {p.session_window_s:.0f}s"))
        return _with(est, Decision(True))

    def run(self, sql: str, session: str = "default") -> Result:
        """Check sql and, if allowed, execute it under the time and row limits."""
        decision = self.check(sql, session)
        if not decision.allowed:
            result = Result("blocked", decision)
        else:
            ex = self.backend.execute(self._limited(sql), self.policy.timeout_s,
                                      self.policy.max_result_rows)
            self._charge(session, ex.seconds)
            result = Result(ex.status, decision, ex.columns, ex.rows, ex.truncated, ex.seconds, ex.error)
        if self._audit:
            self._audit({"ts": time.time(), "session": session, "sql": sql, "status": result.status,
                         "reason": decision.reason, "detail": decision.detail,
                         "estimated_rows": decision.estimated_rows, "seconds": round(result.seconds, 4),
                         "rows": len(result.rows), "truncated": result.truncated})
        return result

    def _limited(self, sql: str) -> str:
        """Add a LIMIT so the database stops early instead of building a huge result."""
        tree = static.parse(sql, self.backend.dialect)
        cap = self.policy.max_result_rows + 1
        if isinstance(tree, exp.Query):
            limit = tree.args.get("limit")
            current = limit.expression if limit is not None else None
            if current is None or (isinstance(current, exp.Literal) and int(current.this) > cap):
                tree = tree.limit(cap)
        return tree.sql(dialect=self.backend.dialect)

    def _session_usage(self, session: str) -> tuple[float, int]:
        now = time.monotonic()
        with self._lock:
            q = self._spend.setdefault(session, deque())
            while q and now - q[0][0] > self.policy.session_window_s:
                q.popleft()
            return sum(s for _, s in q), len(q)

    def _charge(self, session: str, seconds: float) -> None:
        with self._lock:
            self._spend.setdefault(session, deque()).append((time.monotonic(), seconds))


def _with(est: Estimate, d: Decision) -> Decision:
    d.estimated_rows, d.estimated_cost = est.rows, est.cost
    return d


def _audit_sink(audit):
    if audit is None or callable(audit):
        return audit
    path = Path(audit)
    lock = threading.Lock()

    def write(record: dict) -> None:
        with lock, path.open("a") as f:
            f.write(json.dumps(record, default=str) + "\n")

    return write
