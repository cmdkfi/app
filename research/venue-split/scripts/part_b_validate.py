"""Validation of the simulated push feeds against real Chainlink rounds on 10 Oct 2025.

Real rounds: research/liquidation-dataset/data/oracle_rounds_ext.csv (release asset)
(read on-chain for the 22 Oct 2026 backtest): Ethereum mainnet ETH/USD (0.5 % / 3600 s)
and Base BTC/USD (0.1 % / 1200 s). The simulation posts minute closes.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np, pandas as pd
from common import load_minutes, seg_slice, OUT
from part_b_oracles import push_feed

ROUNDS = os.environ.get("CMDK_ORACLE_ROUNDS", os.path.join(os.path.dirname(__file__), "../../liquidation-dataset/data/oracle_rounds_ext.csv"))  # release asset, DATA.md
o = pd.read_csv(ROUNDS, low_memory=False); o["t"] = pd.to_datetime(o.updated_at, unit="s", utc=True)
lo, hi = pd.Timestamp("2025-10-10 20:00", tz="UTC"), pd.Timestamp("2025-10-10 23:00", tz="UTC")
rows = []
for sym, chain, feedpat, dev, hb in [("ETHUSDT", "eth", "ETH/USD", 0.005, 3600), ("BTCUSDT", "base", "BTC/USD", 0.001, 1200)]:
    # the public rounds file also carries Aave's own sources and Morpho market feeds for the same pair; keep the
    # standard Chainlink feed (exact pair name, usable_for_replay True)
    real = o[(o.chain == chain) & (o.feed == feedpat) & (o.usable_for_replay == True)].sort_values("t")
    real = real[(real.t >= lo) & (real.t <= hi)]
    df = seg_slice(load_minutes(sym), "2025-10-10 08:00", "2025-10-11 06:00")
    T = ((df.t - pd.Timestamp(0, tz="UTC")).dt.total_seconds().values).astype("int64") + 60
    sim = push_feed(df.close.values, T, dev, hb)
    posts = np.r_[True, sim[1:] != sim[:-1]]
    tt = pd.to_datetime(T, unit="s", utc=True)
    m = (tt >= lo) & (tt <= hi)
    mkt = df[(df.t >= lo) & (df.t <= hi)]
    rows.append(dict(symbol=sym, feed=f"{chain} {feedpat} {dev*100}%/{hb}s",
                     real_rounds_20_23=len(real), sim_posts_20_23=int((posts & m).sum()),
                     real_min=real.answer.min(), real_min_t=real.loc[real.answer.idxmin(), "t"],
                     sim_min=sim[m].min(), sim_min_t=tt[m][np.argmin(sim[m])],
                     binance_min_low=mkt.low.min(), binance_min_close=mkt.close.min(),
                     real_median_gap_s=real.t.diff().dt.total_seconds().median(),
                     real_max_gap_s=real.t.diff().dt.total_seconds().max(),
                     real_max_step_pct=100 * (real.answer.pct_change().abs().max()),
                     sim_max_step_pct=100 * np.abs(np.diff(sim[m]) / sim[m][:-1]).max()))
v = pd.DataFrame(rows); v.to_csv(f"{OUT}/B_validation_real_rounds.csv", index=False)
pd.set_option("display.width", 300); pd.set_option("display.max_columns", 30)
print(v.T.to_string())
