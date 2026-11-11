"""Builds out/tables.md (markdown tables for the note) from the part A/B/C outputs."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pandas as pd, numpy as np
from common import load_minutes, OUT

L = []
def md(df, floatfmt="{:.2f}"):
    cols = list(df.columns)
    L.append("| " + " | ".join(cols) + " |"); L.append("|" + "---|" * len(cols))
    for _, r in df.iterrows():
        L.append("| " + " | ".join(floatfmt.format(v) if isinstance(v, (float, np.floating)) and not pd.isna(v) else ("" if (isinstance(v, float) and pd.isna(v)) else str(v)) for v in r.values) + " |")
    L.append("")

# Data coverage
rows = []
for s in ["ETHUSDT", "BTCUSDT"]:
    d = load_minutes(s)
    rows.append(dict(symbol=s, first=str(d.t.min())[:16], last=str(d.t.max())[:16], minutes=len(d), segments=d.seg.nunique(),
                     days_covered=round(len(d) / 1440, 1), span_days=(d.t.max() - d.t.min()).days))
L.append("### Data coverage\n"); md(pd.DataFrame(rows))

A = pd.read_csv(f"{OUT}/A_summary.csv")
for s in ["ETHUSDT", "BTCUSDT"]:
    L.append(f"### A1 {s}: largest drop per horizon (all windows in contiguous data)\n")
    cl = A[(A.symbol == s) & (A.measure == "close_to_low")].set_index("horizon_min")
    cc = A[(A.symbol == s) & (A.measure == "close_to_close")].set_index("horizon_min")
    t = pd.DataFrame({"horizon (min)": cl.index, "windows": cl.n_windows.values, "max close-to-low drop %": cl.max_drop_pct.values,
                      "at (UTC, window start)": [str(x)[:16] for x in cl["at"].values], "max close-to-close drop %": cc.max_drop_pct.values,
                      "at (cc)": [str(x)[:16] for x in cc["at"].values],
                      "LTV from 50 % (cl)": cl.ltv_from_50.values, "from 60 %": cl.ltv_from_60.values, "from 70 %": cl.ltv_from_70.values})
    md(t)
C_ = pd.read_csv(f"{OUT}/A_threshold_counts.csv")
L.append("### A2 Windows (minute starts) whose close-to-low drop crossed a line, by starting LTV\n")
c = C_[C_.measure == "close_to_low"].copy()
t = c.pivot_table(index=["symbol", "horizon_min"], columns="start_ltv", values=["n_cross_act", "n_cross_liq"], aggfunc="first")
t.columns = [f"{a.replace('n_cross_','')} line, start {int(b*100)} %" for a, b in t.columns]
t = t.reset_index(); md(t, "{:.0f}")
for s, h in [("ETHUSDT", 60), ("ETHUSDT", 10), ("ETHUSDT", 5), ("ETHUSDT", 1), ("BTCUSDT", 60), ("BTCUSDT", 5)]:
    L.append(f"### A3 {s}: ten worst {h}-minute close-to-low windows (one per 6-hour cluster)\n")
    d = pd.read_csv(f"{OUT}/A_top10_{s}_{h}m_close_to_low.csv")
    d["start"] = d.start.str[:16]; d = d.drop(columns=["end"]).rename(columns={"close_start": "close at start", "drop_pct": "drop %", "ltv_from_50": "LTV from 50 %", "ltv_from_60": "from 60 %", "ltv_from_70": "from 70 %"})
    md(d)

for tag, title in [("B", "B1 default corridor (act 70 %, liquidation 86 %)"), ("Bthin", "B4 thin corridor (start 80 %, act 83 %, liquidation 86 %)")]:
    B = pd.read_csv(f"{OUT}/{tag}_crossings.csv")
    L.append(f"### {title}: oracle-LTV crossings without an agent\n")
    B = B[B.start_ltv >= 0.6] if tag == "B" else B
    t = B[["window", "start_ltv", "design", "ltv_oracle_t0", "act_cross", "liq_cross", "gap_act_to_liq_s", "peak_ltv", "peak_t"]].copy()
    t["ltv_oracle_t0"] = 100 * t.ltv_oracle_t0; t["peak_ltv"] = 100 * t.peak_ltv
    t = t.rename(columns={"ltv_oracle_t0": "oracle LTV at t0 %", "act_cross": "act line crossed", "liq_cross": "liquidation line crossed", "gap_act_to_liq_s": "gap s", "peak_ltv": "peak oracle LTV %", "peak_t": "peak at"})
    t["act line crossed"] = t["act line crossed"].fillna("never"); t["liquidation line crossed"] = t["liquidation line crossed"].fillna("never")
    md(t, "{:.1f}")
    BL = pd.read_csv(f"{OUT}/{tag}_agent_latency.csv")
    BL = BL[BL.act_cross.notna()]
    L.append(f"### {title}: agent reacting at the act line, by latency (rows where the act line was crossed)\n")
    L.append(f"Rows: {len(BL)}; sale landed before the liquidation crossing: {int(BL.sale_before_liq.sum())}; liquidated: {int(BL.liquidated.sum())}; first fill more than 1 % under the venue oracle: {int((~BL.first_fill_within_1pct.astype(bool)).sum())}.\n")
    t = BL.pivot_table(index=["window", "start_ltv", "design"], columns="latency_s", values="first_fill_vs_oracle_pct", aggfunc="first").reset_index()
    t.columns = ["window", "start_ltv", "design", "fill vs oracle %, 30 s", "120 s", "300 s"]
    t2 = BL.pivot_table(index=["window", "start_ltv", "design"], columns="latency_s", values="eth_sold", aggfunc="first").reset_index()
    t["ETH sold, 30 s"] = t2.iloc[:, 3].values; t["120 s "] = t2.iloc[:, 4].values; t["300 s "] = t2.iloc[:, 5].values
    md(t, "{:.2f}")
W = pd.read_csv(f"{OUT}/B_whipsaw.csv")
L.append("### B3 whipsaw: minutes the oracle LTV stayed above the act line (no agent), default corridor\n")
W["peak_ltv"] = 100 * W.peak_ltv; md(W.rename(columns={"act_cross": "crossed up", "back_below_act": "back below", "minutes_above_act": "minutes above", "peak_ltv": "peak LTV %"}), "{:.1f}")
V = pd.read_csv(f"{OUT}/B_validation_real_rounds.csv"); L.append("### B0 validation of the simulated push feeds against real Chainlink rounds, 10 Oct 2025 20:00-23:00 UTC\n"); md(V.T.reset_index().rename(columns={"index": "field", 0: "ETH/USD Ethereum", 1: "BTC/USD Base"}), "{:.2f}")

Cc = pd.read_csv(f"{OUT}/C_single_vs_split.csv")
L.append("### C1 single versus split, per window and latency (ETH liquidated as a fraction of 1 ETH; penalty in USD on a 1 ETH position)\n")
t = Cc[(Cc.corridor == "thin") | (Cc.start_ltv == 0.7)][["window", "corridor", "start_ltv", "fill_rule", "latency_s", "debt_usd", "single_liq_frac", "single_penalty_usd", "single_eth_sold", "single_avg_fill", "single_reverts", "split_liq_frac", "split_penalty_usd", "split_eth_sold", "split_avg_fill", "split_reverts", "split_twap_30_liq_t"]]
md(t, "{:.3f}")
L.append("### C2 aggregate over the five windows\n")
md(pd.read_csv(f"{OUT}/C_aggregate.csv"), "{:.2f}")
open(f"{OUT}/tables.md", "w").write("\n".join(L)); print("\n".join(L))
