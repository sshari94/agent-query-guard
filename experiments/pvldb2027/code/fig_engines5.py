import json, sys, os
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt, numpy as np
d = json.load(open(sys.argv[1])); out = sys.argv[2]
LABEL = {"no_guard": "none", "G3_runtime_only": "G3", "G2_cost_only": "G2", "G1_static": "G1",
         "G1+G2_cost": "G1+G2", "G1+G2+G3_runtime": "G1-G3", "G1-G4_full": "G1-G4"}
fig, ax = plt.subplots(figsize=(3.4, 2.3)); w = 0.38
for k, (tag, name, col) in enumerate([("duck", "DuckDB", "#4C78A8"), ("pg", "PostgreSQL", "#F58518")]):
    x = np.arange(len(LABEL)); m = [d[tag][c]["p95x"] for c in LABEL]; e = [d[tag][c]["p95x_ci"] for c in LABEL]
    lo = [min(e_, m_ - 0.8) for m_, e_ in zip(m, e)]  # keep error bar above axis floor on log scale
    ax.bar(x + (k - 0.5) * w, m, w, yerr=[lo, e], capsize=1.5, color=col, label=name)
ax.set_yscale("log"); ax.axhline(1, ls="--", c="gray", lw=1); ax.set_ylim(0.8, 100)
ax.set_xticks(np.arange(len(LABEL))); ax.set_xticklabels(LABEL.values(), fontsize=7)
ax.set_ylabel("Dashboard p95 / baseline (log)", fontsize=7); ax.tick_params(axis="y", labelsize=7)
ax.legend(fontsize=7, frameon=False); fig.tight_layout()
for ext in ("pdf", "png"): fig.savefig(os.path.join(out, f"fig_engines.{ext}"), dpi=200)
