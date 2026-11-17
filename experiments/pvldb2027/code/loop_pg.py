"""E3 on PostgreSQL: repair-loop sessions with and without the session budget (see loop.py)."""
import argparse
import json
import random
import threading
import time

from loop import LOOP_TEMPLATES
from pg_guard import DSN, QueryGuardPG, connect, to_pg
from workload import BENIGN, _fill, dashboard_queries


def run(dsn, layers, duration, n_dash, n_loops, card_threshold, seed):
    stop = time.time() + duration
    dash_lat, loop_log, lock = [], [], threading.Lock()
    tmpls = [t for tid, t in BENIGN if tid in LOOP_TEMPLATES]

    def dashboard(i):
        conn = connect(dsn)
        conn.autocommit = True
        c = conn.cursor()
        qs = [to_pg(q) for q in dashboard_queries(seed + i)]
        k = 0
        while time.time() < stop:
            t = time.time()
            c.execute(qs[k % len(qs)])
            c.fetchall()
            with lock:
                dash_lat.append(time.time() - t)
            k += 1
        conn.close()

    def looper(i):
        conn = connect(dsn)
        r = random.Random(seed * 10 + i)
        g = QueryGuardPG(layers=layers, card_threshold=card_threshold)
        while time.time() < stop:
            res = g.run(conn, to_pg(_fill(r.choice(tmpls), r)), session=f"loop{i}")
            with lock:
                loop_log.append(res)
            if res["status"] == "blocked":
                time.sleep(0.05)
        conn.close()

    ts = [threading.Thread(target=dashboard, args=(i,)) for i in range(n_dash)]
    ts += [threading.Thread(target=looper, args=(i,)) for i in range(n_loops)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    return {"layers": list(layers), "dash_latencies": dash_lat, "loop_log": loop_log}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dsn", default=DSN)
    ap.add_argument("--out", required=True)
    ap.add_argument("--duration", type=float, default=120)
    ap.add_argument("--card-threshold", type=float, required=True)
    ap.add_argument("--seed", type=int, default=21)
    a = ap.parse_args()
    out = []
    for layers in [("G1", "G2", "G3"), ("G1", "G2", "G3", "G4")]:
        out.append(run(a.dsn, layers, a.duration, 4, 3, a.card_threshold, a.seed))
        time.sleep(3)
    json.dump({"args": vars(a), "engine": "postgresql-16", "runs": out}, open(a.out, "w"))
