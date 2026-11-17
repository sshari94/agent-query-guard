"""E1 on PostgreSQL: run every agent query alone and record its real cost,
with the same harm limits as measure.py. Records both PostgreSQL planner
signals: the largest estimated row count ("Plan Rows") and the total cost.
"""
import argparse
import json
import time

from guard import static_check
from measure import CAP_SECONDS, HARM_ROWS, HARM_SECONDS
from pg_guard import DSN, connect, estimate_pg, execute_pg, to_pg
from workload import agent_corpus


def main(dsn, out):
    conn = connect(dsn)
    rows = []
    corpus = agent_corpus()
    for i, q in enumerate(corpus):
        sql = to_pg(q["sql"])
        t = time.time()
        est, cost = estimate_pg(conn, sql)
        est_s = time.time() - t
        t = time.time()
        reason = static_check(sql, dialect="postgres")
        static_s = time.time() - t
        status, n, sec = execute_pg(conn, sql, CAP_SECONDS)
        harmful = status == "timeout" or sec > HARM_SECONDS or n > HARM_ROWS
        rows.append({**q, "sql_pg": sql, "est_card": est, "est_cost": cost, "explain_s": est_s, "static_reason": reason,
                     "static_s": static_s, "status": status, "rows": n, "exec_s": sec, "harmful": harmful})
        print(f"{i + 1}/{len(corpus)} {q['qid']:<10} {status:<8} {sec:6.2f}s rows={n:<9} est={est:<12.0f} harm={harmful}",
              flush=True)
    with open(out, "w") as f:
        json.dump({"engine": "postgresql-16", "harm_seconds": HARM_SECONDS, "harm_rows": HARM_ROWS,
                   "cap_seconds": CAP_SECONDS, "queries": rows}, f, indent=1)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dsn", default=DSN)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    main(a.dsn, a.out)
