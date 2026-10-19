import duckdb
import pytest


@pytest.fixture
def con():
    c = duckdb.connect()
    c.execute("CREATE TABLE units AS SELECT range AS id, range % 7 AS line FROM range(200000)")
    c.execute("CREATE TABLE events AS SELECT range AS id, range % 200000 AS unit_id, range % 13 AS code "
              "FROM range(400000)")
    c.execute("CREATE TABLE lines AS SELECT range AS line, 'line-' || range AS name FROM range(7)")
    return c
