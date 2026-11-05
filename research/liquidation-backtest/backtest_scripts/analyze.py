import pandas as pd, numpy as np, json, sys
pd.set_option("display.width", 250); pd.set_option("display.max_columns", 60); pd.set_option("display.max_rows", 500)
r = pd.read_csv("out/results.csv"); m = pd.read_csv("out/cases.csv")
excl = set(m[m.get("exclude_from_aggregates", False) == True].case_id) if "exclude_from_aggregates" in m else set()
r["short"] = r.case_id.str.replace("-morpho-", "-M-").str.replace("-aave-", "-A-")
def piv(v, pols, sets=("A", "B")):
    x = r[r.set.isin(sets) & r.policy.isin(pols)].pivot_table(index="policy", columns="short", values=v, aggfunc="first")
    return x.reindex(pols)
SETB = ["NOACTION-model", "P2-scaled-norelever-d120", "DECOMP-bufferonly-d120", "THIN-d30", "THIN-d120", "THIN-d300", "THIN-signal-d120",
        "DECOMP-speedonly-d120", "WAKE-2100-d30", "WAKE-2100-d120", "WAKE-2100-d300", "WAKE-2105-d30", "WAKE-2105-d120", "WAKE-2110-d30", "WAKE-2110-d120", "WAKE-2113-d30"]
which = sys.argv[1] if len(sys.argv) > 1 else "B"
print("excluded:", excl)
for v in ["liquidated", "max_oracle_ltv"]:
    print("==", v); print(piv(v, SETB, (which,)).round(3).to_string())
