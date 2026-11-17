"""E3: repair-loop sessions. Each query is individually acceptable, but a stuck
agent issues them back to back with no think time. G1-G3 admit every query;
G4 limits each session's execution seconds per window.
"""
import argparse
import json
import random
import threading
import time

import duckdb

from guard import QueryGuard
from workload import BENIGN, _fill, dashboard_queries

LOOP_TEMPLATES = {"B09", "B11", "B12", "B08"}  # heavier benign questions (full or wide scans)


def run(db, layers, duration, n_dash, n_loops, threads, card_threshold, seed):
    con = duckdb.connect(db, read_only=True)
    con.execute(f"SET threads = {threads}")
    con.execute("SET enable_progress_bar = false")
    stop = time.time() + duration
    dash_lat, loop_log, lock = [], [], threading.Lock()
    tmpls = [t for tid, t in BENIGN if tid in LOOP_TEMPLATES]

    def dashboard(i):
        c = con.cursor()
        qs = dashboard_queries(seed + i)
        k = 0
        while time.time() < stop:
            t = time.time()
            c.execute(qs[k % len(qs)]).fetchall()
            with lock:
                dash_lat.append(time.time() - t)
            k += 1

    def looper(i):
        c = con.cursor()
        r = random.Random(seed * 10 + i)
        g = QueryGuard(layers=layers, card_threshold=card_threshold)
        while time.time() < stop:
            res = g.run(c, _fill(r.choice(tmpls), r), session=f"loop{i}")
            with lock:
                loop_log.append(res)
            if res["status"] == "blocked":
                time.sleep(0.05)  # a stuck agent retries almost immediately

    ts = [threading.Thread(target=dashboard, args=(i,)) for i in range(n_dash)]
    ts += [threading.Thread(target=looper, args=(i,)) for i in range(n_loops)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    con.close()
    return {"layers": list(layers), "dash_latencies": dash_lat, "loop_log": loop_log}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--duration", type=float, default=120)
    ap.add_argument("--card-threshold", type=float, required=True)
    ap.add_argument("--seed", type=int, default=21)
    a = ap.parse_args()
    out = []
    for layers in [("G1", "G2", "G3"), ("G1", "G2", "G3", "G4")]:
        out.append(run(a.db, layers, a.duration, 4, 3, 4, a.card_threshold, a.seed))
        time.sleep(3)
    json.dump({"args": vars(a), "runs": out}, open(a.out, "w"))
