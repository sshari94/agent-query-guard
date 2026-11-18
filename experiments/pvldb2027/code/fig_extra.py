"""Extra figures for the EA&B version: threshold sweep on both engines, and repair loops."""
import json, sys, os
import numpy as np
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
R, OUT = sys.argv[1], sys.argv[2]
plt.rcParams.update({"font.size": 7})
# Threshold sweep
fig, axes = plt.subplots(1, 2, figsize=(3.4, 1.9), sharey=True)
for ax, (f, th, name) in zip(axes, [("e1_ground_truth.json", 5793600, "DuckDB"), ("e1_pg_ground_truth.json", 2e6, "PostgreSQL")]):
    qs = json.load(open(os.path.join(R, f)))["queries"]
    est = np.array([q["est_card"] for q in qs], float); h = np.array([q["harmful"] for q in qs])
    ben = np.array([q["family"] == "benign" for q in qs])
    ths = np.logspace(5, 9, 200)
    rec = [((est > t) & h).sum() / h.sum() for t in ths]
    bb = [((est > t) & ben).sum() / ben.sum() for t in ths]
    fpr = [((est > t) & ~h).sum() / (~h).sum() for t in ths]
    ax.plot(ths, rec, c="#4C78A8", label="recall")
    ax.plot(ths, fpr, c="#F58518", label="false-positive rate")
    ax.plot(ths, bb, c="#54A24B", ls="--", label="benign blocked")
    ax.axvline(th, c="gray", lw=0.8, ls=":")
    ax.set_xscale("log"); ax.set_title(name, fontsize=7); ax.set_xlabel("threshold (est. rows)")
axes[0].set_ylabel("fraction of queries"); axes[1].legend(frameon=False, fontsize=5.5, loc="upper right")
fig.tight_layout()
for ext in ("pdf", "png"): fig.savefig(os.path.join(OUT, f"fig_threshold_sweep.{ext}"), dpi=200)
# Repair loops
st = json.load(open(os.path.join(R, "stats_both.json")))
fig, axes = plt.subplots(1, 2, figsize=(3.4, 1.8))
x = np.arange(2); w = 0.36
for k, (cfg, lab, col) in enumerate([("G1+G2+G3", "G1-G3", "#E45756"), ("G1+G2+G3+G4", "G1-G4", "#4C78A8")]):
    for ax, key in zip(axes, ["loop_s", "p95"]):
        m = [st[e]["loops"][cfg][key][0] for e in ("duck", "pg")]; s = [st[e]["loops"][cfg][key][1] for e in ("duck", "pg")]
        ax.bar(x + (k - 0.5) * w, m, w, yerr=s, capsize=1.5, color=col, label=lab)
for ax, yl in zip(axes, ["loop execution (s)", "dashboard p95 / baseline"]):
    ax.set_xticks(x); ax.set_xticklabels(["DuckDB", "PostgreSQL"]); ax.set_ylabel(yl)
axes[1].axhline(1, c="gray", lw=0.8, ls="--"); axes[0].legend(frameon=False, fontsize=6)
fig.tight_layout()
for ext in ("pdf", "png"): fig.savefig(os.path.join(OUT, f"fig_loops.{ext}"), dpi=200)
