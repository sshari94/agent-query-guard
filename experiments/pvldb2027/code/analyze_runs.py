"""Summarize E2 (interference) and E3 (repair loops) runs for one engine.

E3 slowdowns are relative to the dashboard-only baseline of the same
engine's E2 runs.
"""
import argparse
import json
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from analyze import e2 as e2_rows

ORDER = ["dashboard_only", "no_guard", "G3_runtime_only", "G2_cost_only", "G1_static", "G1+G2_cost",
         "G1+G2+G3_runtime", "G1-G4_full"]


def e2(paths, outdir, tag):
    cwd = os.getcwd()
    tmp = os.path.join(outdir, f"_tmp_{tag}")
    os.makedirs(tmp, exist_ok=True)
    rows = e2_rows(paths, tmp)  # writes its figure into tmp; we draw our own below
    for f in os.listdir(tmp):
        os.remove(os.path.join(tmp, f))
    os.rmdir(tmp)
    os.chdir(cwd)
    rows.sort(key=lambda r: ORDER.index(r["config"]))
    # Benign questions answered per run, summed over sessions: distinct (session, question) pairs that finished.
    runs = []
    for p in paths:
        runs += json.load(open(p))["runs"]
    ans = {}
    for r in runs:
        if r["agent_log"]:
            n = len({(e["agent"], e["qid"]) for e in r["agent_log"] if e["family"] == "benign" and e["status"] in ("ok", "truncated")})
            ans.setdefault(r["config"], []).append(n)
    for row in rows:
        if row["config"] in ans:
            row["benign_answered"] = float(np.mean(ans[row["config"]]))
    base = next(r for r in rows if r["config"] == "dashboard_only")
    fig, ax = plt.subplots(figsize=(3.4, 2.6))
    ax.bar(range(len(rows)), [r["dash_p95_ms"] for r in rows], yerr=[r["dash_p95_ms_sd"] for r in rows],
           color="#4C78A8", capsize=2)
    ax.axhline(base["dash_p95_ms"], ls="--", c="gray", lw=1)
    ax.set_ylabel("Dashboard p95 latency (ms)")
    ax.set_xticks(range(len(rows)))
    ax.set_xticklabels([r["config"].replace("_", " ") for r in rows], rotation=35, ha="right", fontsize=6.5)
    fig.tight_layout()
    for ext in ("pdf", "png"):
        fig.savefig(os.path.join(outdir, f"fig_dashboard_p95_{tag}.{ext}"), dpi=200)
    return rows, base


def e3(paths, base):
    agg = {}
    for p in paths:
        for r in json.load(open(p))["runs"]:
            name = "+".join(r["layers"])
            lat = np.array(r["dash_latencies"]) * 1000
            log = r["loop_log"]
            dur = json.load(open(p))["args"]["duration"]
            m = {"p95": np.percentile(lat, 95), "p99": np.percentile(lat, 99), "qps": len(lat) / dur,
                 "loop_exec": sum(e["exec_s"] for e in log), "loop_q": len(log),
                 "blocked": sum(e["status"] == "blocked" for e in log),
                 "ok": sum(e["status"] in ("ok", "truncated") for e in log)}
            for k, v in m.items():
                agg.setdefault(name, {}).setdefault(k, []).append(float(v))
    out = {}
    for name, d in agg.items():
        out[name] = {k: [float(np.mean(v)), float(np.std(v, ddof=1))] for k, v in d.items()}
        out[name]["p95_slowdown"] = out[name]["p95"][0] / base["dash_p95_ms"]
        out[name]["qps_ratio"] = out[name]["qps"][0] / base["dash_qps"]
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--e2", nargs="+", required=True)
    ap.add_argument("--e3", nargs="*")
    ap.add_argument("--tag", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    rows, base = e2(a.e2, a.out, a.tag)
    res = {"e2": rows}
    if a.e3:
        res["e3"] = e3(a.e3, base)
    json.dump(res, open(os.path.join(a.out, f"summary_runs_{a.tag}.json"), "w"), indent=1)
    for r in rows:
        print(f"{r['config']:<18} p95 {r['dash_p95_ms']:6.1f}±{r['dash_p95_ms_sd']:4.1f}  thr {r['qps_ratio']:.2f}  "
              f"answered {r.get('benign_answered', float('nan')):5.1f}  harmful_s {r.get('agent_exec_s_mutant', float('nan')):6.1f}")
    if a.e3:
        for k, v in res["e3"].items():
            print(k, {x: (round(y[0], 2) if isinstance(y, list) else round(y, 3)) for x, y in v.items()})
