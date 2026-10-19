import pytest

from query_guard import check


def rules(sql, **kw):
    return [v.rule for v in check(sql, dialect="duckdb", **kw)]


def test_plain_select_passes():
    assert rules("SELECT line, count(*) FROM units GROUP BY line") == []


@pytest.mark.parametrize("sql", [
    "DELETE FROM units",
    "DROP TABLE units",
    "UPDATE units SET line = 1",
    "INSERT INTO units VALUES (1, 1)",
    "CREATE TABLE x AS SELECT 1",
])
def test_writes_are_blocked(sql):
    assert "not_read_only" in rules(sql)


def test_multiple_statements_are_blocked():
    assert rules("SELECT 1; DROP TABLE units") == ["unparsable"]


def test_file_reading_functions_are_blocked():
    assert "denied_function" in rules("SELECT * FROM read_csv('/etc/passwd')")


def test_cartesian_join_is_blocked_but_linked_comma_join_passes():
    assert "cartesian_join" in rules("SELECT * FROM units u, events e")
    assert rules("SELECT * FROM units u, events e WHERE u.id = e.unit_id") == []
    assert rules("SELECT * FROM units u JOIN events e ON u.id = e.unit_id") == []


def test_table_allow_and_deny_lists_ignore_ctes():
    assert "table_not_allowed" in rules("SELECT * FROM salaries", allowed_tables=frozenset({"units"}))
    assert rules("WITH s AS (SELECT * FROM units) SELECT * FROM s", allowed_tables=frozenset({"units"})) == []
    assert "denied_table" in rules("SELECT * FROM salaries", denied_tables=frozenset({"salaries"}))


def test_unbounded_star_on_large_table():
    large = frozenset({"events"})
    assert "unbounded_scan" in rules("SELECT * FROM events", large_tables=large)
    assert rules("SELECT * FROM events LIMIT 10", large_tables=large) == []
    assert rules("SELECT * FROM events WHERE code = 3", large_tables=large) == []


def test_write_hidden_in_cte_is_blocked():
    sql = "WITH d AS (DELETE FROM units RETURNING *) SELECT * FROM d"
    assert "not_read_only" in [v.rule for v in check(sql, dialect="postgres")]
