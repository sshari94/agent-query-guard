"""ClickHouse backend, run against embedded ClickHouse (chdb) through a small client shim."""
import json
from types import SimpleNamespace

import pytest

from query_guard import Policy, QueryGuard

chdb_session = pytest.importorskip("chdb.session")


class ChdbClient:
    """The subset of clickhouse-connect's Client that ClickHouseBackend uses."""

    def __init__(self):
        self.session = chdb_session.Session()

    def query(self, sql, settings=None):
        if settings:
            sql += " SETTINGS " + ", ".join(
                f"{k} = {v!r}" if isinstance(v, str) else f"{k} = {v}" for k, v in settings.items())
        out = json.loads(str(self.session.query(sql, "JSONCompact")))
        return SimpleNamespace(column_names=[m["name"] for m in out["meta"]], result_rows=out["data"])


@pytest.fixture(scope="module")
def client():
    c = ChdbClient()
    c.session.query("CREATE DATABASE IF NOT EXISTS f")
    c.session.query("CREATE TABLE IF NOT EXISTS f.events (id UInt64, line UInt8, code UInt8) "
                    "ENGINE = MergeTree ORDER BY (line, id)")
    c.session.query("TRUNCATE TABLE f.events")
    c.session.query("INSERT INTO f.events SELECT number, number % 7, number % 13 FROM numbers(500000)")
    return c


def test_aggregate_runs(client):
    sql = "SELECT line, count() AS n FROM f.events GROUP BY line ORDER BY line"
    r = QueryGuard.for_clickhouse(client).run(sql)
    assert r.status == "ok", r.error
    assert r.columns == ["line", "n"] and len(r.rows) == 7


def test_estimate_is_rows_read(client):
    guard = QueryGuard.for_clickhouse(client, Policy(max_estimated_rows=100_000))
    d = guard.check("SELECT sum(code) FROM f.events")
    assert not d.allowed and d.reason == "estimated_rows" and d.estimated_rows == 500_000
    # The primary key lets ClickHouse skip most of the table, so a filtered query passes.
    assert guard.check("SELECT sum(code) FROM f.events WHERE line = 3 AND id < 1000").allowed


def test_result_rows_are_capped(client):
    r = QueryGuard.for_clickhouse(client, Policy(max_result_rows=50)).run("SELECT id FROM f.events")
    assert r.status == "ok" and len(r.rows) == 50 and r.truncated


def test_server_side_timeout(client):
    guard = QueryGuard.for_clickhouse(client, Policy(timeout_s=1, max_estimated_rows=None))
    r = guard.run("SELECT count() FROM numbers(100000000000) WHERE sipHash64(number) = 1")
    assert r.status == "timeout", r.error


def test_clickhouse_specific_rules():
    guard = QueryGuard.for_clickhouse(None, Policy(max_estimated_rows=None))
    assert guard.check("SELECT * FROM url('http://example.com/x.csv', CSV)").reason == "denied_function"
    assert guard.check("SELECT * FROM remote('other-host', db.t)").reason == "denied_function"
    sql = "SELECT count() FROM f.events SETTINGS max_execution_time = 0"
    assert guard.check(sql).reason == "query_settings"
    assert guard.check("INSERT INTO f.events VALUES (1, 1, 1)").reason == "not_read_only"
