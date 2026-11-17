"""E1 on PostgreSQL and the cross-engine comparison."""
import argparse
import json

import numpy as np
from scipy.stats import spearmanr

from analyze import calibrate, prf


def e1(path):
    qs = json.load(open(path))["queries"]
    truth = np.array([q["harmful"] for q in qs])
    static = np.array([q["static_reason"] is not None for q in qs])
    benign = np.array([q["family"] == "benign" for q in qs])
    idx = np.arange(len(qs))
    cal, test = idx % 2 == 0, idx % 2 == 1
    out = {"n": len(qs), "n_harmful": int(truth.sum()), "harmful_benign_family": int((truth & benign).sum()),
           "static_all": prf(static, truth)}
    for sig in ("est_card", "est_cost"):
        est = np.array([q[sig] for q in qs], dtype=float)
        th = calibrate(est[cal], truth[cal])
        done = [q for q in qs if q["status"] == "ok"]
        rho = spearmanr([q[sig] for q in done], [q["exec_s"] for q in done])
        out[sig] = {"threshold": float(th),
                    "test": prf(est[test] > th, truth[test]),
                    "static_or_test": prf(static[test] | (est[test] > th), truth[test]),
                    "benign_family_blocked_all": int(((est > th) & benign).sum()),
                    "spearman_completed": {"rho": float(rho.correlation), "p": float(rho.pvalue), "n": len(done)}}
    ex = np.array([q["explain_s"] for q in qs]) * 1000
    out["overhead_ms"] = {"explain_p50": float(np.percentile(ex, 50)), "explain_p95": float(np.percentile(ex, 95))}
    out["slowest_benign_s"] = float(max(q["exec_s"] for q in qs if q["family"] == "benign"))
    fam = {}
    for q in qs:
        f = fam.setdefault(q["family"], {"n": 0, "harmful": 0})
        f["n"] += 1; f["harmful"] += int(q["harmful"])
    out["by_family"] = fam
    return out


def compare(duck_path, pg_path):
    d = {q["qid"]: q for q in json.load(open(duck_path))["queries"]}
    p = {q["qid"]: q for q in json.load(open(pg_path))["queries"]}
    both = sorted(set(d) & set(p))
    agree = sum(d[k]["harmful"] == p[k]["harmful"] for k in both)
    only_pg = [k for k in both if p[k]["harmful"] and not d[k]["harmful"]]
    only_duck = [k for k in both if d[k]["harmful"] and not p[k]["harmful"]]
    rho = spearmanr([d[k]["exec_s"] for k in both], [p[k]["exec_s"] for k in both])
    return {"n": len(both), "label_agreement": agree / len(both), "harmful_only_on_pg": only_pg,
            "harmful_only_on_duckdb": only_duck, "runtime_spearman": float(rho.correlation)}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--pg", required=True)
    ap.add_argument("--duck", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    res = {"e1_pg": e1(a.pg), "cross_engine": compare(a.duck, a.pg)}
    json.dump(res, open(a.out, "w"), indent=1)
    print(json.dumps(res, indent=1))
