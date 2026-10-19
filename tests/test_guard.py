import json

from query_guard import Policy, QueryGuard
from query_guard.backends import postgres_estimate


def test_allowed_query_runs(con):
    r = QueryGuard.for_duckdb(con).run("SELECT line, count(*) AS n FROM units GROUP BY line ORDER BY line")
    assert r.status == "ok"
    assert r.columns == ["line", "n"]
    assert len(r.rows) == 7


def test_huge_join_is_blocked_by_estimate(con):
    guard = QueryGuard.for_duckdb(con, Policy(max_estimated_rows=1_000_000, block_cartesian=False))
    r = guard.run("SELECT * FROM units u CROSS JOIN events e")
    assert r.status == "blocked"
    assert r.decision.reason == "estimated_rows"
    assert r.decision.estimated_rows > 1_000_000


def test_result_rows_are_capped(con):
    r = QueryGuard.for_duckdb(con, Policy(max_result_rows=100)).run("SELECT id FROM units")
    assert r.status == "ok" and len(r.rows) == 100 and r.truncated


def test_existing_small_limit_is_kept(con):
    r = QueryGuard.for_duckdb(con, Policy(max_result_rows=100)).run("SELECT id FROM units LIMIT 5")
    assert len(r.rows) == 5 and not r.truncated


def test_timeout_interrupts_long_query(con):
    guard = QueryGuard.for_duckdb(con, Policy(timeout_s=0.2, max_estimated_rows=None, block_cartesian=False))
    r = guard.run("SELECT count(*) FROM range(100000000000) a")
    assert r.status == "timeout"


def test_session_budget_stops_query_chains(con):
    guard = QueryGuard.for_duckdb(con, Policy(session_max_queries=2))
    assert guard.run("SELECT 1", session="a").status == "ok"
    assert guard.run("SELECT 1", session="a").status == "ok"
    third = guard.run("SELECT 1", session="a")
    assert third.status == "blocked" and third.decision.reason == "session_query_limit"
    assert guard.run("SELECT 1", session="b").status == "ok"


def test_audit_log(con, tmp_path):
    log = tmp_path / "audit.jsonl"
    guard = QueryGuard.for_duckdb(con, audit=log)
    guard.run("SELECT 1")
    guard.run("DELETE FROM units")
    records = [json.loads(line) for line in log.read_text().splitlines()]
    assert [r["status"] for r in records] == ["ok", "blocked"]
    assert records[1]["reason"] == "not_read_only"


def test_postgres_plan_parsing():
    plan = [{"Plan": {"Node Type": "Hash Join", "Plan Rows": 500, "Total Cost": 1234.5,
                      "Plans": [{"Plan Rows": 90000}, {"Plan Rows": 10}]}}]
    est = postgres_estimate(plan)
    assert est.rows == 90000 and est.cost == 1234.5


def test_session_budget_allows_the_query_that_crosses_it_then_blocks(con):
    guard = QueryGuard.for_duckdb(con, Policy(session_budget_s=0.0, max_estimated_rows=None))
    assert guard.run("SELECT 1", session="s").status == "ok"
    blocked = guard.run("SELECT 1", session="s")
    assert blocked.status == "blocked" and blocked.decision.reason == "session_budget"


def test_static_rules_are_checked_before_the_cost_estimate(con):
    guard = QueryGuard.for_duckdb(con, Policy(max_estimated_rows=1))
    assert guard.check("SELECT * FROM units u, events e").reason == "cartesian_join"
    assert guard.check("SELECT * FROM units u JOIN events e ON u.id = e.unit_id").reason == "estimated_rows"
