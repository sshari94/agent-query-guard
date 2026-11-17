"""QueryGuard: admission control for AI-agent SQL on a shared analytical database.

Layers (each config enables a prefix of these):
  G1 static   read-only check and structural rules (cartesian joins, unbounded SELECT * on fact tables)
  G2 cost     optimizer cardinality estimate from EXPLAIN, compared with a threshold
  G3 runtime  per-query time budget (interrupt) and result-row cap (LIMIT rewrite)
  G4 session  per-session rolling budget of execution seconds, to stop unbounded query chains
"""
import json
import threading
import time
from collections import deque

import sqlglot
from sqlglot import exp

FACT_TABLES = {"unit_events", "test_results", "units"}


def _walk_card(node):
    est = 0
    info = node.get("extra_info", {})
    if isinstance(info, dict) and "Estimated Cardinality" in info:
        try:
            est = int(str(info["Estimated Cardinality"]).lstrip("~"))
        except ValueError:
            est = 0
    return max([est] + [_walk_card(c) for c in node.get("children", [])])


def estimate(con, sql):
    """Largest estimated intermediate cardinality in the optimizer plan."""
    plan = json.loads(con.execute("EXPLAIN (FORMAT JSON) " + sql).fetchall()[0][1])
    return max(_walk_card(n) for n in plan)


def static_check(sql, dialect="duckdb"):
    """Return a reason string if the query breaks a structural rule, else None."""
    try:
        tree = sqlglot.parse_one(sql, read=dialect)
    except Exception as e:  # unparsable SQL is rejected
        return f"parse_error:{e.__class__.__name__}"
    if not isinstance(tree, (exp.Select, exp.Union)):
        return "not_read_only"
    for sel in tree.find_all(exp.Select):
        tables = [t.alias_or_name for t in sel.find_all(exp.Table)]
        joins = sel.args.get("joins") or []
        for j in joins:
            if j.args.get("kind", "").upper() == "CROSS" or (j.args.get("on") is None and not j.args.get("using")):
                if not _has_equi_link(sel, j):
                    return "cartesian_join"
        star = any(isinstance(e, exp.Star) or (isinstance(e, exp.Column) and isinstance(e.this, exp.Star))
                   for e in sel.expressions)
        if star and sel.args.get("limit") is None and set(t.name for t in sel.find_all(exp.Table)) & FACT_TABLES \
                and sel.args.get("where") is None:
            return "unbounded_star"
    return None


def _has_equi_link(sel, join):
    """True if a comma/cross-joined table is tied to the others by an equality in WHERE."""
    where = sel.args.get("where")
    if where is None:
        return False
    alias = join.this.alias_or_name
    for eq in where.find_all(exp.EQ):
        cols = [c for c in (eq.left, eq.right) if isinstance(c, exp.Column)]
        if len(cols) == 2 and alias in {c.table for c in cols} and cols[0].table != cols[1].table:
            return True
    return False


class QueryGuard:
    def __init__(self, layers=("G1", "G2", "G3", "G4"), card_threshold=5e7, time_budget_s=5.0,
                 row_cap=100_000, session_budget_s=15.0, session_window_s=60.0):
        self.layers = set(layers)
        self.card_threshold = card_threshold
        self.time_budget_s = time_budget_s
        self.row_cap = row_cap
        self.session_budget_s = session_budget_s
        self.session_window_s = session_window_s
        self._spend = {}
        self._lock = threading.Lock()

    def admit(self, con, sql, session="default"):
        """Decide before execution. Returns (allowed, reason, est_cardinality)."""
        est = None
        if "G1" in self.layers:
            reason = static_check(sql)
            if reason:
                return False, reason, est
        if "G2" in self.layers:
            est = estimate(con, sql)
            if est > self.card_threshold:
                return False, "cost_estimate", est
        if "G4" in self.layers and self._session_spent(session) > self.session_budget_s:
            return False, "session_budget", est
        return True, None, est

    def _session_spent(self, session):
        now = time.time()
        with self._lock:
            q = self._spend.setdefault(session, deque())
            while q and now - q[0][0] > self.session_window_s:
                q.popleft()
            return sum(s for _, s in q)

    def _charge(self, session, seconds):
        with self._lock:
            self._spend.setdefault(session, deque()).append((time.time(), seconds))

    def run(self, con, sql, session="default"):
        """Admit, then execute under runtime limits. Returns a dict describing the outcome."""
        t0 = time.time()
        ok, reason, est = self.admit(con, sql, session)
        overhead = time.time() - t0
        if not ok:
            return {"status": "blocked", "reason": reason, "est": est, "guard_s": overhead, "exec_s": 0.0, "rows": 0}
        q = sql
        if "G3" in self.layers:
            q = f"SELECT * FROM ({sql}) AS _g LIMIT {self.row_cap + 1}"
        status, rows, exec_s = execute_with_timeout(con, q, self.time_budget_s if "G3" in self.layers else None)
        if "G3" in self.layers and status == "ok" and rows > self.row_cap:
            status = "truncated"
        if "G4" in self.layers:
            self._charge(session, exec_s)
        return {"status": status, "reason": None, "est": est, "guard_s": overhead, "exec_s": exec_s, "rows": rows}


def execute_with_timeout(con, sql, timeout_s):
    """Run sql, interrupting after timeout_s. Returns (status, rows, seconds)."""
    timer = None
    if timeout_s:
        timer = threading.Timer(timeout_s, con.interrupt)
        timer.start()
    t0 = time.time()
    try:
        res = con.execute(sql)
        rows = 0
        while True:
            chunk = res.fetchmany(50_000)
            if not chunk:
                break
            rows += len(chunk)
        status = "ok"
    except Exception as e:
        if "INTERRUPT" in str(e).upper():
            status, rows = "timeout", 0
        else:
            status, rows = f"error:{e.__class__.__name__}", 0
    finally:
        if timer:
            timer.cancel()
    return status, rows, time.time() - t0
