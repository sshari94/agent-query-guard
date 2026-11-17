"""E2: interference between AI-agent queries and a live operations dashboard.

Dashboard threads refresh short queries continuously. Agent sessions follow
an interactive closed-loop model: each session asks a question from the
corpus, waits for the answer, then "thinks" for an exponentially distributed
time before the next question. A failed or blocked query is retried up to
MAX_RETRIES times after a short pause, mimicking the repair loops of LLM
agents. Think time keeps the offered load comparable across guard
configurations, so differences in dashboard latency come from what the guard
lets through, not from how fast agents cycle. All agent queries still running
when the measurement window closes are interrupted.
"""
import argparse
import json
import random
import threading
import time

import duckdb

from guard import QueryGuard, execute_with_timeout
from workload import agent_corpus, dashboard_queries

MAX_RETRIES = 3
RETRY_PAUSE_S = 1.0
THINK_MEAN_S = 3.0
NO_GUARD_SAFETY_CAP_S = 600.0  # longer than any run; queries are interrupted at window end

CONFIGS = {
    "dashboard_only": None,
    "no_guard": (),
    "G1_static": ("G1",),
    "G1+G2_cost": ("G1", "G2"),
    "G1+G2+G3_runtime": ("G1", "G2", "G3"),
    "G1-G4_full": ("G1", "G2", "G3", "G4"),
    # Single-layer baselines: is a plain timeout and row cap, or a cost check alone, enough?
    "G3_runtime_only": ("G3",),
    "G2_cost_only": ("G2",),
}


def run(db, config, duration, n_dash, n_agents, threads, card_threshold, seed):
    con = duckdb.connect(db, read_only=True)
    con.execute(f"SET threads = {threads}")
    con.execute("SET enable_progress_bar = false")
    stop = time.time() + duration
    dash_lat, agent_log = [], []
    lock = threading.Lock()
    layers = CONFIGS[config]

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

    agent_cursors = []

    def agent(i):
        c = con.cursor()
        with lock:
            agent_cursors.append(c)
        corpus = agent_corpus()
        rng = random.Random(seed * 100 + i)
        rng.shuffle(corpus)
        guard = QueryGuard(layers=layers, card_threshold=card_threshold)
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
                    r = guard.run(c, q["sql"], session=f"agent{i}")
                else:
                    s, n, sec = execute_with_timeout(c, q["sql"], NO_GUARD_SAFETY_CAP_S)
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
            for c in agent_cursors:
                try:
                    c.interrupt()
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
    con.close()
    return {"config": config, "duration": duration, "dash_latencies": dash_lat, "agent_log": agent_log}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--duration", type=float, default=120)
    ap.add_argument("--dash", type=int, default=4)
    ap.add_argument("--agents", type=int, default=6)
    ap.add_argument("--threads", type=int, default=4)
    ap.add_argument("--card-threshold", type=float, required=True)
    ap.add_argument("--seed", type=int, default=11)
    ap.add_argument("--configs", default=",".join(CONFIGS))
    a = ap.parse_args()
    results = []
    for cfg in a.configs.split(","):
        print("running", cfg, flush=True)
        results.append(run(a.db, cfg, a.duration, a.dash, a.agents, a.threads, a.card_threshold, a.seed))
        time.sleep(3)
    with open(a.out, "w") as f:
        json.dump({"args": vars(a), "runs": results}, f)
