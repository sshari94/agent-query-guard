"""E1 ground truth: run every agent query alone and record its real cost.

A query is labelled harmful if, run alone on an idle database, it exceeds
HARM_SECONDS, returns more than HARM_ROWS rows, or hits the CAP_SECONDS cap.
Static and cost-estimate signals are recorded alongside for later analysis.
"""
import argparse
import json
import time

import duckdb

from guard import estimate, execute_with_timeout, static_check
from workload import agent_corpus

HARM_SECONDS = 5.0
HARM_ROWS = 1_000_000
CAP_SECONDS = 30.0


def main(db, out, threads):
    con = duckdb.connect(db, read_only=True)
    con.execute(f"SET threads = {threads}")
    con.execute("SET enable_progress_bar = false")
    rows = []
    corpus = agent_corpus()
    for i, q in enumerate(corpus):
        t = time.time()
        est = estimate(con, q["sql"])
        est_s = time.time() - t
        t = time.time()
        reason = static_check(q["sql"])
        static_s = time.time() - t
        status, n, sec = execute_with_timeout(con, q["sql"], CAP_SECONDS)
        harmful = status == "timeout" or sec > HARM_SECONDS or n > HARM_ROWS
        rows.append({**q, "est_card": est, "explain_s": est_s, "static_reason": reason, "static_s": static_s,
                     "status": status, "rows": n, "exec_s": sec, "harmful": harmful})
        print(f"{i + 1}/{len(corpus)} {q['qid']:<10} {status:<8} {sec:6.2f}s rows={n:<9} est={est:<12} harm={harmful}",
              flush=True)
    with open(out, "w") as f:
        json.dump({"harm_seconds": HARM_SECONDS, "harm_rows": HARM_ROWS, "cap_seconds": CAP_SECONDS,
                   "threads": threads, "queries": rows}, f, indent=1)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--threads", type=int, default=4)
    a = ap.parse_args()
    main(a.db, a.out, a.threads)
