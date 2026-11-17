"""Cross-engine figure: dashboard p95 slowdown per configuration, DuckDB vs PostgreSQL."""
import json
import os
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

res = sys.argv[1]
LABEL = {"no_guard": "none", "G3_runtime_only": "G3", "G2_cost_only": "G2", "G1_static": "G1",
         "G1+G2_cost": "G1+G2", "G1+G2+G3_runtime": "G1-G3", "G1-G4_full": "G1-G4"}
fig, ax = plt.subplots(figsize=(3.4, 2.3))
w = 0.38
for k, (tag, name, col) in enumerate([("duckdb", "DuckDB", "#4C78A8"), ("pg", "PostgreSQL", "#F58518")]):
    rows = {r["config"]: r for r in json.load(open(os.path.join(res, f"summary_runs_{tag}.json")))["e2"]}
    base = rows["dashboard_only"]["dash_p95_ms"]
    x = np.arange(len(LABEL))
    ax.bar(x + (k - 0.5) * w, [rows[c]["dash_p95_ms"] / base for c in LABEL], w,
           yerr=[rows[c]["dash_p95_ms_sd"] / base for c in LABEL], capsize=1.5, color=col, label=name)
ax.axhline(1, ls="--", c="gray", lw=1)
ax.set_xticks(np.arange(len(LABEL))); ax.set_xticklabels(LABEL.values(), fontsize=7)
ax.set_ylabel("Dashboard p95 / baseline", fontsize=7); ax.tick_params(axis="y", labelsize=7)
ax.legend(fontsize=7, frameon=False)
fig.tight_layout()
for ext in ("pdf", "png"):
    fig.savefig(os.path.join(res, f"fig_engines.{ext}"), dpi=200)
