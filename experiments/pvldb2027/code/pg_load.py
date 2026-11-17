"""Copy a generated DuckDB database into PostgreSQL, add the indexes a
production deployment would typically have, and collect statistics.
"""
import argparse
import io
import time

import duckdb
import psycopg2

TYPES = {"INTEGER": "integer", "SMALLINT": "smallint", "BIGINT": "bigint", "TINYINT": "smallint",
         "TIMESTAMP_S": "timestamp", "TIMESTAMP": "timestamp", "FLOAT": "real", "DOUBLE": "double precision",
         "BOOLEAN": "boolean", "VARCHAR": "text"}
INDEXES = [
    "ALTER TABLE stations ADD PRIMARY KEY (station_id)",
    "ALTER TABLE products ADD PRIMARY KEY (product_id)",
    "ALTER TABLE lots ADD PRIMARY KEY (lot_id)",
    "ALTER TABLE units ADD PRIMARY KEY (unit_id)",
    "CREATE INDEX ON units (lot_id)",
    "CREATE INDEX ON units (created_ts)",
    "CREATE INDEX ON unit_events (unit_id)",
    "CREATE INDEX ON unit_events (ts)",
    "CREATE INDEX ON test_results (unit_id)",
    "CREATE INDEX ON test_results (ts)",
    "CREATE INDEX ON scrap (ts)",
]


def main(src, dsn):
    d = duckdb.connect(src, read_only=True)
    pg = psycopg2.connect(dsn)
    pg.autocommit = True
    cur = pg.cursor()
    for (t,) in d.execute("SHOW TABLES").fetchall():
        cols = d.execute(f"SELECT column_name, data_type FROM information_schema.columns WHERE table_name = '{t}' "
                         "ORDER BY ordinal_position").fetchall()
        cur.execute(f"DROP TABLE IF EXISTS {t} CASCADE")
        cur.execute(f"CREATE TABLE {t} (" + ", ".join(f"{c} {TYPES[ty]}" for c, ty in cols) + ")")
        t0 = time.time()
        rel = d.execute(f"SELECT * FROM {t}")
        while True:
            chunk = rel.fetchmany(500_000)
            if not chunk:
                break
            buf = io.StringIO()
            for row in chunk:
                buf.write("\t".join("\\N" if v is None else str(v) for v in row) + "\n")
            buf.seek(0)
            cur.copy_expert(f"COPY {t} FROM STDIN", buf)
        print(t, f"{time.time() - t0:.1f}s", flush=True)
    for sql in INDEXES:
        cur.execute(sql)
    cur.execute("VACUUM ANALYZE")
    print("done")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True)
    ap.add_argument("--dsn", default="host=localhost port=5433 user=postgres dbname=qg")
    a = ap.parse_args()
    main(a.src, a.dsn)
