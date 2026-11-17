"""E4: template-held-out detection. Compares G2 (max cardinality threshold), PG total cost, and learned
plan-feature classifiers (logistic regression, gradient-boosted trees). Each fold holds out one template
family entirely, so no instance of a held-out template is seen in calibration or training."""
import json, sys, math
import numpy as np
import sqlglot
from sqlglot import exp
from sklearn.linear_model import LogisticRegression
from sklearn.ensemble import GradientBoostingClassifier

def feats(q, dialect):
    sql = q.get("sql_pg") if dialect == "postgres" and q.get("sql_pg") else q["sql"]
    t = sqlglot.parse_one(sql, read=dialect)
    joins = len(list(t.find_all(exp.Join)))
    f = [math.log10(1 + q["est_card"]), joins, int(t.find(exp.Where) is not None), int(t.find(exp.Limit) is not None),
         int(t.find(exp.Group) is not None), int(t.find(exp.Order) is not None), len(list(t.find_all(exp.Star)))]
    if "est_cost" in q:
        f.append(math.log10(1 + q["est_cost"]))
    return f

def best_threshold(x, y):
    # smallest threshold that blocks no benign training query above it while maximizing recall: pick the
    # threshold just above the largest benign value (the paper's calibration rule: max F1, ties -> fewer FP)
    cands = sorted(set(x))
    best, bt = -1, None
    for c in cands:
        p = x >= c
        tp = (p & y).sum(); fp = (p & ~y).sum(); fn = (~p & y).sum()
        f1 = 2 * tp / (2 * tp + fp + fn) if tp else 0
        if f1 > best or (f1 == best and fp == 0):
            best, bt = f1, c
    return bt

def run(path, dialect):
    Q = [q for q in json.load(open(path))["queries"] if q.get("est_card") is not None]
    X = np.array([feats(q, dialect) for q in Q]); y = np.array([bool(q["harmful"]) for q in Q])
    tmpl = np.array([q["template"] for q in Q]); benign = np.array([q["family"] == "benign" for q in Q])
    preds = {k: np.zeros(len(Q), bool) for k in ["G2 max card", "LogReg", "GBT"] + (["Total cost"] if dialect == "postgres" else [])}
    for t in sorted(set(tmpl)):
        te = tmpl == t; tr = ~te
        th = best_threshold(X[tr, 0], y[tr]); preds["G2 max card"][te] = X[te, 0] >= th
        if "Total cost" in preds:
            th2 = best_threshold(X[tr, -1], y[tr]); preds["Total cost"][te] = X[te, -1] >= th2
        if y[tr].all() or not y[tr].any():
            continue
        lr = LogisticRegression(max_iter=2000, class_weight="balanced").fit(X[tr], y[tr]); preds["LogReg"][te] = lr.predict(X[te])
        gb = GradientBoostingClassifier(random_state=0).fit(X[tr], y[tr]); preds["GBT"][te] = gb.predict(X[te])
    out = {}
    for k, p in preds.items():
        tp = (p & y).sum(); fp = (p & ~y).sum(); fn = (~p & y).sum()
        out[k] = {"precision": round(tp / (tp + fp), 3) if tp + fp else None, "recall": round(tp / (tp + fn), 3),
                  "fpr": round(fp / (~y).sum(), 3), "benign_blocked": int((p & benign).sum()), "n_benign": int(benign.sum()),
                  "n": len(Q), "n_harmful": int(y.sum())}
    return out

if __name__ == "__main__":
    res = {"duckdb": run(sys.argv[1], "duckdb"), "postgres": run(sys.argv[2], "postgres")}
    print(json.dumps(res, indent=1))
    json.dump(res, open(sys.argv[3], "w"), indent=1)
