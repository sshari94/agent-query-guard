"""Generate a synthetic factory-analytics database in DuckDB.

The schema is generic discrete manufacturing (sites, lines, stations, units,
process events, tests, scrap, material lots). No real company data is used.

Usage: python gen_db.py --db path/to/analytics.duckdb --units 2000000 --seed 7
"""
import argparse
import time

import duckdb
import numpy as np
import pandas as pd

T0 = np.datetime64("2026-01-01T00:00:00")


def build(db_path, n_units, seed):
    rng = np.random.default_rng(seed)
    con = duckdb.connect(db_path)
    t = time.time()

    n_sites, lines_per_site, stations_per_line = 3, 8, 20
    n_stations = n_sites * lines_per_site * stations_per_line
    st = pd.DataFrame({
        "station_id": np.arange(n_stations, dtype=np.int32),
        "line_id": (np.arange(n_stations) // stations_per_line).astype(np.int32),
        "site_id": (np.arange(n_stations) // (stations_per_line * lines_per_site)).astype(np.int16),
        "station_type": np.array(["load", "process", "inspect", "test", "pack"])[np.arange(n_stations) % 5],
    })
    n_products = 200
    pr = pd.DataFrame({
        "product_id": np.arange(n_products, dtype=np.int32),
        "family": np.array(["A", "B", "C", "D"])[rng.integers(0, 4, n_products)],
    })
    n_lots = max(1000, n_units // 100)
    lots = pd.DataFrame({
        "lot_id": np.arange(n_lots, dtype=np.int32),
        "supplier_id": rng.integers(0, 60, n_lots).astype(np.int16),
        "material": np.array(["M1", "M2", "M3", "M4", "M5"])[rng.integers(0, 5, n_lots)],
        "received_ts": T0 + rng.integers(0, 180 * 86400, n_lots).astype("timedelta64[s]"),
    })

    # Units: created over 180 days, each on one line.
    created = T0 + np.sort(rng.integers(0, 180 * 86400, n_units)).astype("timedelta64[s]")
    unit_line = rng.integers(0, n_sites * lines_per_site, n_units).astype(np.int32)
    units = pd.DataFrame({
        "unit_id": np.arange(n_units, dtype=np.int64),
        "product_id": rng.integers(0, n_products, n_units).astype(np.int32),
        "line_id": unit_line,
        "site_id": (unit_line // lines_per_site).astype(np.int16),
        "lot_id": np.minimum(np.arange(n_units) // 100 + rng.integers(-5, 5, n_units), n_lots - 1).clip(0).astype(np.int32),
        "created_ts": created,
    })

    # Process events: each unit visits 6 random stations on its line.
    k = 6
    uid = np.repeat(units.unit_id.values, k)
    line = np.repeat(unit_line, k)
    step = np.tile(np.arange(k), n_units)
    station = (line * stations_per_line + rng.integers(0, stations_per_line, n_units * k)).astype(np.int32)
    ts = np.repeat(created, k) + (step * 600 + rng.integers(0, 600, n_units * k)).astype("timedelta64[s]")
    ev = pd.DataFrame({
        "event_id": np.arange(n_units * k, dtype=np.int64),
        "unit_id": uid,
        "station_id": station,
        "ts": ts,
        "duration_s": rng.gamma(2.0, 20.0, n_units * k).astype(np.float32),
        "result": np.where(rng.random(n_units * k) < 0.985, "OK", "NOK"),
    })

    # Tests: one per unit plus retests.
    retest = rng.random(n_units) < 0.06
    tu = np.concatenate([units.unit_id.values, units.unit_id.values[retest]])
    attempt = np.concatenate([np.ones(n_units, np.int8), np.full(retest.sum(), 2, np.int8)])
    tline = np.concatenate([unit_line, unit_line[retest]])
    ma = rng.normal(100, 5, len(tu)).astype(np.float32)
    tests = pd.DataFrame({
        "unit_id": tu,
        "station_id": (tline * stations_per_line + 15).astype(np.int32),
        "ts": np.concatenate([created, created[retest]]) + np.where(attempt == 1, 4000, 7000).astype("timedelta64[s]"),
        "attempt": attempt,
        "measure_a": ma,
        "measure_b": rng.normal(3.7, 0.05, len(tu)).astype(np.float32),
        "pass": (ma < 112).astype(np.bool_),
    })

    sc = rng.random(n_units) < 0.03
    scrap = pd.DataFrame({
        "unit_id": units.unit_id.values[sc],
        "ts": created[sc] + np.timedelta64(8000, "s"),
        "reason_code": np.array(["R%02d" % i for i in range(25)])[rng.zipf(1.6, sc.sum()).clip(1, 25) - 1],
        "station_id": (unit_line[sc] * stations_per_line + rng.integers(0, stations_per_line, sc.sum())).astype(np.int32),
    })

    for name, df in [("stations", st), ("products", pr), ("lots", lots), ("units", units),
                     ("unit_events", ev), ("test_results", tests), ("scrap", scrap)]:
        con.execute(f"CREATE OR REPLACE TABLE {name} AS SELECT * FROM df")
    con.execute("CHECKPOINT")
    sizes = {n: con.execute(f"SELECT count(*) FROM {n}").fetchone()[0]
             for n in ["stations", "products", "lots", "units", "unit_events", "test_results", "scrap"]}
    con.close()
    print(f"built {db_path} in {time.time() - t:.1f}s", sizes)
    return sizes


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--units", type=int, default=2_000_000)
    ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args()
    build(a.db, a.units, a.seed)
