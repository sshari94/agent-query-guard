"""Turn raw results into the tables and figures used in the paper."""
import argparse
import json
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from scipy.stats import spearmanr


def prf(pred, truth):
    pred, truth = np.asarray(pred), np.asarray(truth)
    tp = int((pred & truth).sum()); fp = int((pred & ~truth).sum())
    fn = int((~pred & truth).sum()); tn = int((~pred & ~truth).sum())
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    fpr = fp / (fp + tn) if fp + tn else 0.0
    return {"tp": tp, "fp": fp, "fn": fn, "tn": tn, "precision": p, "recall": r, "fpr": fpr,
            "f1": 2 * p * r / (p + r) if p + r else 0.0}


def calibrate(est, truth):
    """Threshold on estimated cardinality that maximises F1 on the calibration split."""
    best = (None, -1)
    for th in sorted(set(est)):
        f1 = prf(est > th, truth)["f1"]
        if f1 > best[1]:
            best = (th, f1)
    return best[0]


def e1(path, outdir):
    d = json.load(open(path))
    qs = d["queries"]
    truth = np.array([q["harmful"] for q in qs])
    est = np.array([q["est_card"] for q in qs], dtype=float)
    static = np.array([q["static_reason"] is not None for q in qs])
    idx = np.arange(len(qs))
    cal, test = idx % 2 == 0, idx % 2 == 1
    th = calibrate(est[cal], truth[cal])
    res = {"n": len(qs), "n_harmful": int(truth.sum()), "threshold": float(th),
           "static_all": prf(static, truth),
           "cost_test": prf(est[test] > th, truth[test]),
           "static_or_cost_test": prf(static[test] | (est[test] > th), truth[test]),
           "cost_all": prf(est > th, truth),
           "static_or_cost_all": prf(static | (est > th), truth)}
    fam = {}
    for q in qs:
        f = fam.setdefault(q["family"], {"n": 0, "harmful": 0, "static": 0, "cost": 0, "exec_s": []})
        f["n"] += 1; f["harmful"] += int(q["harmful"]); f["static"] += int(q["static_reason"] is not None)
        f["cost"] += int(q["est_card"] > th); f["exec_s"].append(q["exec_s"])
    for f in fam.values():
        f["median_exec_s"] = float(np.median(f.pop("exec_s")))
    res["by_family"] = fam
    ex = np.array([q["explain_s"] for q in qs]) * 1000
    st = np.array([q["static_s"] for q in qs]) * 1000
    res["overhead_ms"] = {"explain_p50": float(np.percentile(ex, 50)), "explain_p95": float(np.percentile(ex, 95)),
                          "static_p50": float(np.percentile(st, 50)), "static_p95": float(np.percentile(st, 95))}
    done = [q for q in qs if q["status"] == "ok"]
    rho = spearmanr([q["est_card"] for q in done], [q["exec_s"] for q in done])
    res["spearman_est_vs_exec_completed"] = {"rho": float(rho.correlation), "p": float(rho.pvalue), "n": len(done)}

    fig, ax = plt.subplots(figsize=(3.4, 2.6))
    for h, c, lab in [(False, "#4C78A8", "benign"), (True, "#E45756", "harmful")]:
        m = truth == h
        ax.scatter(est[m] + 1, np.array([q["exec_s"] for q in qs])[m], s=10, c=c, label=lab, alpha=.75)
    ax.axvline(th, ls="--", c="gray", lw=1)
    ax.set_xscale("log"); ax.set_yscale("log")
    ax.set_xlabel("Optimizer max estimated cardinality"); ax.set_ylabel("Isolated runtime (s)")
    ax.legend(fontsize=7, frameon=False); fig.tight_layout()
    fig.savefig(os.path.join(outdir, "fig_est_vs_runtime.pdf")); fig.savefig(os.path.join(outdir, "fig_est_vs_runtime.png"), dpi=200)
    # Sensitivity: precision and recall as the threshold moves, expressed
    # relative to the largest table so it can transfer across database sizes.
    ths = np.logspace(5, 10, 41)
    res["sweep"] = [{"threshold": float(t), **{k: v for k, v in prf(est > t, truth).items() if k in ("precision", "recall", "fpr")}}
                    for t in ths]
    fig, ax = plt.subplots(figsize=(3.4, 2.4))
    ax.plot(ths, [r["recall"] for r in res["sweep"]], label="recall", c="#E45756")
    ax.plot(ths, [r["precision"] for r in res["sweep"]], label="precision", c="#4C78A8")
    ax.axvline(th, ls="--", c="gray", lw=1)
    ax.set_xscale("log"); ax.set_xlabel("Cardinality threshold"); ax.set_ylim(0, 1.05)
    ax.legend(fontsize=7, frameon=False); fig.tight_layout()
    fig.savefig(os.path.join(outdir, "fig_threshold_sweep.pdf")); fig.savefig(os.path.join(outdir, "fig_threshold_sweep.png"), dpi=200)
    return res


def transfer(path_small, th_rel, largest_small):
    """Apply a threshold defined as a fraction of the largest table to another database size."""
    qs = json.load(open(path_small))["queries"]
    truth = np.array([q["harmful"] for q in qs])
    est = np.array([q["est_card"] for q in qs], dtype=float)
    static = np.array([q["static_reason"] is not None for q in qs])
    th = th_rel * largest_small
    return {"n": len(qs), "n_harmful": int(truth.sum()), "threshold": float(th),
            "cost": prf(est > th, truth), "static_or_cost": prf(static | (est > th), truth),
            "benign_blocked": int(((est > th) & np.array([q["family"] == "benign" for q in qs])).sum())}


def e2(paths, outdir):
    runs = []
    for p in paths:
        runs += json.load(open(p))["runs"]
    rows = []
    base = None
    for r in runs:
        lat = np.array(r["dash_latencies"]) * 1000
        row = {"config": r["config"], "dash_n": len(lat), "dash_p50_ms": float(np.percentile(lat, 50)),
               "dash_p95_ms": float(np.percentile(lat, 95)), "dash_p99_ms": float(np.percentile(lat, 99)),
               "dash_qps": len(lat) / r["duration"]}
        log = r["agent_log"]
        if log:
            ben = [e for e in log if e["family"] == "benign"]
            row.update({
                "agent_queries": len(log),
                "agent_exec_s": float(sum(e["exec_s"] for e in log)),
                "agent_exec_s_mutant": float(sum(e["exec_s"] for e in log if e["family"] != "benign")),
                "benign_ok_rate": float(np.mean([e["status"] in ("ok", "truncated") for e in ben])) if ben else None,
                "benign_blocked": int(sum(e["status"] == "blocked" for e in ben)),
                "blocked": int(sum(e["status"] == "blocked" for e in log)),
                "timeouts": int(sum(e["status"] == "timeout" for e in log)),
                "truncated": int(sum(e["status"] == "truncated" for e in log)),
                "session_blocks": int(sum(e.get("reason") == "session_budget" for e in log)),
            })
        rows.append(row)
    # Aggregate repeated runs (one per seed): mean and standard deviation.
    agg = {}
    for row in rows:
        agg.setdefault(row["config"], []).append(row)
    rows = []
    for cfg, rs in agg.items():
        out = {"config": cfg, "repeats": len(rs)}
        for k in rs[0]:
            if k == "config" or rs[0][k] is None:
                continue
            v = np.array([r[k] for r in rs], dtype=float)
            out[k] = float(v.mean()); out[k + "_sd"] = float(v.std(ddof=1)) if len(v) > 1 else 0.0
        rows.append(out)
    base = next(r for r in rows if r["config"] == "dashboard_only")
    for row in rows:
        row["p95_slowdown"] = row["dash_p95_ms"] / base["dash_p95_ms"]
        row["p99_slowdown"] = row["dash_p99_ms"] / base["dash_p99_ms"]
        row["qps_ratio"] = row["dash_qps"] / base["dash_qps"]

    fig, ax = plt.subplots(figsize=(3.4, 2.6))
    names = [r["config"].replace("_", " ") for r in rows]
    ax.bar(range(len(rows)), [r["dash_p95_ms"] for r in rows], yerr=[r["dash_p95_ms_sd"] for r in rows],
           color="#4C78A8", capsize=2)
    ax.axhline(base["dash_p95_ms"], ls="--", c="gray", lw=1)
    ax.set_ylabel("Dashboard p95 latency (ms)")
    ax.set_xticks(range(len(rows))); ax.set_xticklabels(names, rotation=35, ha="right", fontsize=6.5)
    fig.tight_layout()
    fig.savefig(os.path.join(outdir, "fig_dashboard_p95.pdf")); fig.savefig(os.path.join(outdir, "fig_dashboard_p95.png"), dpi=200)
    return rows


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--e1", required=True)
    ap.add_argument("--e2", nargs="*")
    ap.add_argument("--e1-small")
    ap.add_argument("--largest", type=float, default=12_000_000)
    ap.add_argument("--largest-small", type=float, default=3_000_000)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    out = {"e1": e1(a.e1, a.out)}
    if a.e1_small:
        out["transfer_small"] = transfer(a.e1_small, out["e1"]["threshold"] / a.largest, a.largest_small)
    if a.e2:
        out["e2"] = e2(a.e2, a.out)
    json.dump(out, open(os.path.join(a.out, "summary.json"), "w"), indent=1)
    print(json.dumps(out, indent=1)[:6000])
