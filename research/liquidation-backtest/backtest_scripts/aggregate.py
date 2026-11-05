import pandas as pd, numpy as np, json
pd.set_option("display.width", 250); pd.set_option("display.max_columns", 60); pd.set_option("display.max_rows", 500)
r = pd.read_csv("out/results.csv"); m = pd.read_csv("out/cases.csv")
excl = set(m[m.exclude_from_aggregates == True].case_id)
act = r[r.policy == "ACTUAL"].set_index("case_id")
def agg(pol, s):
    x = r[(r.policy == pol) & (r.set == s) & (~r.case_id.isin(excl))].set_index("case_id")
    a = act.loc[x.index]
    saved = (~x.liquidated) & a.liquidated
    return dict(policy=pol, set=s, n=len(x), saved=int(saved.sum()), pct_saved=saved.mean() * 100,
                pen_actual=a.penalty_usd.sum(), pen_policy=x.penalty_usd.sum(), pen_avoided=a.penalty_usd.sum() - x.penalty_usd.sum(),
                med_extra_coll_pct=np.median((x.coll_end - a.coll_end) / x.index.map(m.set_index("case_id").C0) * 100),
                med_extra_coll=np.median(x.coll_end - a.coll_end),
                d_eq_bottom=(x.equity_bottom - a.equity_bottom).sum(), d_eq_end=(x.equity_end - a.equity_end).sum(), d_eq_today=(x.equity_today - a.equity_today).sum(),
                med_d_eq_end_pct=np.median((x.equity_end - a.equity_end) / a.equity_end.abs().clip(lower=1) * 100),
                eq_end_vs_baseline=(x.equity_end - x.baseline_equity_end).sum(), slippage=x.slippage_usd.sum(), gas=x.gas_usd.sum(),
                n_sales=int(x.n_sales.sum()), reverts=int(x.reverts.sum()))
pols = ["NOACTION-model", "P2-scaled-norelever-d30", "P2-scaled-norelever-d120", "P2-scaled-norelever-d300", "P2-scaled-relever-d30", "P2-scaled-relever-d120", "P2-scaled-relever-d300",
        "P2-literal-norelever-d120", "P2-literal-relever-d120", "P3-scaled-norelever-d30", "P3-scaled-norelever-d120", "P3-scaled-norelever-d300", "P3-scaled-relever-d120",
        "DECOMP-bufferonly-d120", "DECOMP-speedonly-d30", "DECOMP-speedonly-d120", "DECOMP-speedonly-d300", "THIN-d30", "THIN-d120", "THIN-d300", "THIN-signal-d120",
        "FILL-low-d120", "HOURLY-d120", "HOURLY-signal-d120"] + [f"SENS-X{x}-Y{y}" for x in [1, 2, 3, 5] for y in [5, 15, 60]]
rows = [agg(p, s) for s in ["A", "B"] for p in pols if len(r[(r.policy == p) & (r.set == s)])]
A = pd.DataFrame(rows); A.to_csv("out/aggregates.csv", index=False)
print(A.round(1).to_string())
# actual summary per set
for s in ["A", "B"]:
    a = act[(act.set == s) & (~act.index.isin(excl))]
    print(s, "actual: n", len(a), "penalty", round(a.penalty_usd.sum()), "equity_end", round(a.equity_end.sum()), "baseline_end", round(a.baseline_equity_end.sum()))
