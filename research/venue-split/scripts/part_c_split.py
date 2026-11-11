"""Part C: one position of 1 ETH on design (1) (Chainlink Base) versus the same ETH split
equally over three venues with designs (1) Chainlink Base, (3) pull 1-minute, (4) 30-min TWAP.
Same corridor, same latency, same debt per ETH. The asset is the same on all venues, so every
price move reaches all of them; only the oracle timing differs.

Corridors: default (start 50/60/70 %, act 70 %, liquidation 86 %) and thin (start 80 %, act 83 %).
Fill rules: "always" = the brief's rule (fills at 0.99 x market close at landing);
            "revert_1pct" = the build executor's rule (fill >1 % under the venue oracle reverts, retried).
Penalty: (LIF - 1) x debt on the liquidated venue, LIF = 1/(1 - 0.3 (1 - 0.86)); [ASSUMPTION] whole venue
position liquidated at once.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np, pandas as pd
from common import load_minutes, seg_slice, OUT, START_LTVS, LATENCIES_S
from part_b_oracles import WINDOWS, WARMUP_H, RUN_H, oracle_series, run_agent, PENALTY, ts

SPLIT = ["cl_base", "pull_1m", "twap_30"]
CORRIDORS = {"default": (START_LTVS, 0.70), "thin": ([0.80], 0.83)}


def summarize(r, D0, C0):
    liq = r["liq"] is not None
    return dict(liq_frac=C0 if liq else 0.0, penalty=r["liq"]["penalty"] if liq else 0.0,
                eth_sold=sum(s["eth"] for s in r["sales"]), proceeds=sum(s["eth"] * s["fill"] for s in r["sales"]),
                reverts=r["reverts"], n_sales=len(r["sales"]),
                first_sale=ts(r["sales"][0]["t"]) if r["sales"] else "", liq_t=ts(r["liq"]["t"]) if liq else "")


def main():
    df_all = load_minutes("ETHUSDT"); rows = []
    for name, t0s in WINDOWS.items():
        t0 = pd.Timestamp(t0s, tz="UTC")
        df = seg_slice(df_all, t0 - pd.Timedelta(hours=WARMUP_H), t0 + pd.Timedelta(hours=RUN_H))
        O, T = oracle_series(df); close = df.close.values
        i0 = int(np.where(df.t == t0)[0][0]); P0 = close[i0]
        for cname, (ltvs, act) in CORRIDORS.items():
            for L0 in ltvs:
                for rule in ["always", "revert_1pct"]:
                    rr = rule == "revert_1pct"
                    for lat in LATENCIES_S:
                        single = summarize(run_agent(O["cl_base"], close, T, i0, L0 * P0, lat, C=1.0, act=act, revert_rule=rr), L0 * P0, 1.0)
                        parts = {d: summarize(run_agent(O[d], close, T, i0, L0 * P0 / 3, lat, C=1 / 3, act=act, revert_rule=rr), L0 * P0 / 3, 1 / 3) for d in SPLIT}
                        split = dict(liq_frac=sum(p["liq_frac"] for p in parts.values()), penalty=sum(p["penalty"] for p in parts.values()),
                                     eth_sold=sum(p["eth_sold"] for p in parts.values()), proceeds=sum(p["proceeds"] for p in parts.values()),
                                     reverts=sum(p["reverts"] for p in parts.values()))
                        rows.append(dict(window=name, corridor=cname, start_ltv=L0, act_line=act, fill_rule=rule, latency_s=lat, P0=P0, debt_usd=L0 * P0,
                                         single_liq_frac=single["liq_frac"], single_penalty_usd=single["penalty"], single_eth_sold=single["eth_sold"],
                                         single_avg_fill=(single["proceeds"] / single["eth_sold"]) if single["eth_sold"] else None, single_reverts=single["reverts"],
                                         split_liq_frac=split["liq_frac"], split_penalty_usd=split["penalty"], split_eth_sold=split["eth_sold"],
                                         split_avg_fill=(split["proceeds"] / split["eth_sold"]) if split["eth_sold"] else None, split_reverts=split["reverts"],
                                         **{f"split_{d}_liq": parts[d]["liq_frac"] > 0 for d in SPLIT},
                                         **{f"split_{d}_liq_t": parts[d]["liq_t"] for d in SPLIT},
                                         **{f"split_{d}_reverts": parts[d]["reverts"] for d in SPLIT}))
    C = pd.DataFrame(rows); C.to_csv(f"{OUT}/C_single_vs_split.csv", index=False)
    pd.set_option("display.width", 320); pd.set_option("display.max_columns", 40); pd.set_option("display.max_rows", 500)
    cols = ["window", "corridor", "start_ltv", "fill_rule", "latency_s", "single_liq_frac", "single_penalty_usd", "single_eth_sold", "single_reverts",
            "split_liq_frac", "split_penalty_usd", "split_eth_sold", "split_reverts", "split_cl_base_liq", "split_pull_1m_liq", "split_twap_30_liq"]
    print(C[cols].to_string())
    agg = C.groupby(["corridor", "fill_rule", "latency_s"]).agg(windows=("window", "size"), single_liq_frac_sum=("single_liq_frac", "sum"),
                                                                 split_liq_frac_sum=("split_liq_frac", "sum"), single_penalty=("single_penalty_usd", "sum"),
                                                                 split_penalty=("split_penalty_usd", "sum"))
    agg.to_csv(f"{OUT}/C_aggregate.csv"); print(agg.to_string())


if __name__ == "__main__":
    main()
