"""E2 on PostgreSQL: the same interference experiment as interfere.py.

Each dashboard thread and agent session has its own connection (a separate
PostgreSQL backend). All agent queries still running when the window closes
are cancelled.
"""
import argparse
import json
import random
import threading
import time

from interfere import CONFIGS, MAX_RETRIES, NO_GUARD_SAFETY_CAP_S, RETRY_PAUSE_S, THINK_MEAN_S
from pg_guard import _STOPPED, DSN, QueryGuardPG, connect, execute_pg, stop_conn, to_pg
from workload import agent_corpus, dashboard_queries


def run(dsn, config, duration, n_dash, n_agents, card_threshold, seed):
    # Connection ids are reused after close, so forget connections stopped in earlier runs.
    _STOPPED.clear()
    stop = time.time() + duration
    dash_lat, agent_log = [], []
    lock = threading.Lock()
    layers = CONFIGS[config]
    corpus_pg = [{**q, "sql": to_pg(q["sql"])} for q in agent_corpus()]

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

    agent_conns = []

    def agent(i):
        conn = connect(dsn)
        with lock:
            agent_conns.append(conn)
        corpus = list(corpus_pg)
        rng = random.Random(seed * 100 + i)
        rng.shuffle(corpus)
        guard = QueryGuardPG(layers=layers, card_threshold=card_threshold)
        k = 0
        while time.time() < stop:
            q = corpus[k % len(corpus)]
            k += 1
            for attempt in range(1 + MAX_RETRIES):
                if time.time() >= stop:
                    break
                if attempt:
                    time.sleep(RETRY_PAUSE_S)
                if layers:
                    r = guard.run(conn, q["sql"], session=f"agent{i}")
                else:
                    s, n, sec = execute_pg(conn, q["sql"], NO_GUARD_SAFETY_CAP_S)
                    r = {"status": s, "reason": None, "exec_s": sec, "rows": n, "guard_s": 0.0}
                with lock:
                    agent_log.append({"agent": i, "qid": q["qid"], "family": q["family"], "attempt": attempt,
                                      "t_end": time.time() - (stop - duration), **r})
                if r["status"] in ("ok", "truncated"):
                    break
            time.sleep(min(rng.expovariate(1 / THINK_MEAN_S), max(0.0, stop - time.time())))

    def closer():
        time.sleep(max(0.0, stop - time.time()))
        with lock:
            for c in agent_conns:
                try:
                    stop_conn(c)
                except Exception:
                    pass

    ts = [threading.Thread(target=dashboard, args=(i,)) for i in range(n_dash)]
    if layers is not None:
        ts += [threading.Thread(target=agent, args=(i,)) for i in range(n_agents)]
        ts.append(threading.Thread(target=closer))
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    for c in agent_conns:
        c.close()
    return {"config": config, "duration": duration, "dash_latencies": dash_lat, "agent_log": agent_log}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dsn", default=DSN)
    ap.add_argument("--out", required=True)
    ap.add_argument("--duration", type=float, default=120)
    ap.add_argument("--dash", type=int, default=4)
    ap.add_argument("--agents", type=int, default=6)
    ap.add_argument("--card-threshold", type=float, required=True)
    ap.add_argument("--seed", type=int, default=11)
    ap.add_argument("--configs", default=",".join(CONFIGS))
    a = ap.parse_args()
    results = []
    for cfg in a.configs.split(","):
        print("running", cfg, flush=True)
        results.append(run(a.dsn, cfg, a.duration, a.dash, a.agents, a.card_threshold, a.seed))
        time.sleep(3)
    with open(a.out, "w") as f:
        json.dump({"args": vars(a), "engine": "postgresql-16", "runs": results}, f)
