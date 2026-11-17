import json, glob, sys
import numpy as np
from scipy import stats
R = sys.argv[1]
sets = {"duck": sorted(glob.glob(R+"/e2_v2_seed1*.json")+glob.glob(R+"/e2_seed1*_baselines.json")),
        "pg": sorted(glob.glob(R+"/e2_pg_seed1*.json"))}
loops = {"duck": sorted(glob.glob(R+"/e3_v2_loop_seed2*.json")), "pg": sorted(glob.glob(R+"/e3_pg_loop_seed2*.json"))}
out = {}
for eng, fs in sets.items():
    per = {}
    for f in fs:
        d = json.load(open(f)); dur = d["args"]["duration"]
        for r in d["runs"]:
            lat = np.array(r["dash_latencies"])*1000
            log = r["agent_log"] or []
            m = {"p95": np.percentile(lat,95), "qps": len(lat)/dur,
                 "answered": len({(e["agent"],e["qid"]) for e in log if e["family"]=="benign" and e["status"] in ("ok","truncated")}),
                 "harm_s": sum(e["exec_s"] for e in log if e["family"]!="benign"),
                 "benign_blocked": len({(e["agent"],e["qid"]) for e in log if e["family"]=="benign" and e["status"] in ("blocked","session_blocked")})}
            for k,v in m.items(): per.setdefault(r["config"],{}).setdefault(k,[]).append(float(v))
    base_p95 = np.mean(per["dashboard_only"]["p95"]); base_q = np.mean(per["dashboard_only"]["qps"])
    res = {"base_p95_ms": base_p95, "base_qps": base_q}
    for c, m in per.items():
        n = len(m["p95"]); ratio = np.array(m["p95"])/base_p95
        tq = stats.t.ppf(0.975, n-1)
        res[c] = {"n": n, "p95x": ratio.mean(), "p95x_ci": tq*ratio.std(ddof=1)/np.sqrt(n),
                  "p95x_median": float(np.median(ratio)), "p95x_min": ratio.min(), "p95x_max": ratio.max(),
                  "thr": np.mean(m["qps"])/base_q, "answered": np.mean(m["answered"]), "answered_ci": tq*np.std(m["answered"],ddof=1)/np.sqrt(n),
                  "harm_s": np.mean(m["harm_s"]), "harm_s_ci": tq*np.std(m["harm_s"],ddof=1)/np.sqrt(n),
                  "benign_blocked": np.mean(m["benign_blocked"])}
    # tests: G3 only vs full guard, on per-run p95 and harmful seconds
    tests = {}
    for a,b in [("G3_runtime_only","G1-G4_full"),("no_guard","G3_runtime_only"),("G2_cost_only","G1-G4_full")]:
        for k in ("p95","harm_s"):
            x,y = per[a][k], per[b][k]
            tests[f"{a} vs {b} {k}"] = {"welch_p": float(stats.ttest_ind(x,y,equal_var=False).pvalue),
                                       "mwu_p": float(stats.mannwhitneyu(x,y,alternative="two-sided").pvalue), "nx": len(x), "ny": len(y)}
    res["tests"] = tests
    # loops
    lp = {}
    for f in loops[eng]:
        d = json.load(open(f)); dur = d["args"]["duration"]
        for r in d["runs"]:
            lat = np.array(r["dash_latencies"])*1000; name="+".join(r["layers"])
            for k,v in {"loop_s": sum(e["exec_s"] for e in r["loop_log"]), "p95": np.percentile(lat,95)/base_p95, "thr": len(lat)/dur/base_q}.items():
                lp.setdefault(name,{}).setdefault(k,[]).append(float(v))
    res["loops"] = {n:{k:[float(np.mean(v)),float(np.std(v,ddof=1))] for k,v in d.items()} for n,d in lp.items()}
    a,b = lp.get("G1+G2+G3",{}).get("loop_s"), lp.get("G1+G2+G3+G4",{}).get("loop_s")
    if a and b: res["loops_test_welch_p"] = float(stats.ttest_ind(a,b,equal_var=False).pvalue)
    out[eng] = res
json.dump(out, open(sys.argv[2],"w"), indent=1, default=float)
for eng,res in out.items():
    print(eng, "base p95 %.1f ms qps %.0f"%(res["base_p95_ms"],res["base_qps"]))
    for c in ["no_guard","G3_runtime_only","G2_cost_only","G1_static","G1+G2_cost","G1+G2+G3_runtime","G1-G4_full"]:
        r=res[c]; print(f"  {c:18s} n={r['n']:2d} p95x {r['p95x']:.2f}±{r['p95x_ci']:.2f} (med {r['p95x_median']:.2f}, {r['p95x_min']:.2f}-{r['p95x_max']:.2f}) thr {r['thr']:.2f} ans {r['answered']:.1f}±{r['answered_ci']:.1f} harm {r['harm_s']:.0f}±{r['harm_s_ci']:.0f} bblk {r['benign_blocked']:.1f}")
    for k,v in res["tests"].items(): print("   ",k,"welch %.2g mwu %.2g"%(v["welch_p"],v["mwu_p"]))
    print("  loops", {n:{k:round(v[0],2) for k,v in d.items()} for n,d in res["loops"].items()}, "p", res.get("loops_test_welch_p"))
