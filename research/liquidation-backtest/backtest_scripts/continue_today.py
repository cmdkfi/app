"""Continue each position from the end of its window to 22 Oct 2026 on Binance HOURLY closes.
Proxy oracle = Binance hourly close (Chainlink rounds were only pulled for the crash windows) -> labelled approximation.
'Actual' = the real end-of-window leftover held passively (the borrower's later actions are unknown), same liquidation model."""
import os, sys, numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(__file__))
from prep import *; from sim import *
from run_all import corridor
cases = {c.case_id: c for c in morpho_cases() + aave_cases()}
res = pd.read_csv(BASE + "/out/results.csv"); PEND = pd.read_csv(BASE + "/out/cases.csv").set_index("case_id").p_end
k = klines(); rw, rb = ratio_fn()
r0 = pd.read_csv(DATA + "/eth_ratios.csv")
def wsteth_ratio(t):   # beyond the fetched window: extrapolate at ~2.8%/yr staking yield (assumption)
    t = np.asarray(t, dtype=float); last_t, last_r = r0.ts.iloc[-1], r0.wsteth_steth.iloc[-1]
    return np.where(t <= last_t, np.interp(t, r0.ts, r0.wsteth_steth), last_r * np.exp(0.028 * (t - last_t) / YEAR))
rows = []
for cid, c in cases.items():
    sym = c.meta["price_symbol"]; mult = wsteth_ratio if c.meta["asset"] == "wstETH" else None
    h = k[(k.symbol == sym) & (k.interval == "1h")].sort_values("open_time")
    h = h[h.open_time >= (c.t_end - 3600) * 1000]
    th = (h.open_time.values // 1000).astype(np.int64); cl = h.close.values * (mult(th + 3600) if mult else 1.0)
    # minute arrays by repeating the last completed hourly close (no intra-hour information)
    t_end_today = int(th[-1] + 3600)
    tm = np.arange(th[0], t_end_today, 60); idx = np.searchsorted(th + 3600, tm, side="right") - 1
    px = np.where(idx >= 0, cl[np.maximum(idx, 0)], cl[0])
    mkt = Market(tm, px, px, px, px); orc = Oracle(th + 3600, cl)
    for pol_name in ["ACTUAL", "P2-scaled-norelever-d120", "P2-scaled-relever-d120"]:
        x = res[(res.case_id == cid) & (res.policy == pol_name)].iloc[0]
        cc = Case(cid, c.venue, float(x.coll_end), float(x.debt_end), int(c.t_end), t_end_today - 60, c.rate, [], [])
        if pol_name == "ACTUAL":
            pol = Policy("passive", enabled=False)
        else:
            cr = corridor(c.venue.lltv, True); pol = Policy(pol_name, **cr, delay=120, relever="relever-" in pol_name and "norelever" not in pol_name)
            cc.restorable0 = float(x.coll_sold - x.coll_bought) if pol.relever else 0.0
        short = float(x.withdraw_short_coll) if not pd.isna(x.withdraw_short_coll) else 0.0
        cash = float(x.cash_adj_total) if not pd.isna(x.cash_adj_total) else 0.0
        if cc.C0 <= 1e-12:
            rows.append(dict(case_id=cid, policy=pol_name, liq_after=False, penalty_after=0.0, coll_today=0.0, debt_today=cc.D0, equity_today_managed=-cc.D0 + cash - short * cl[-1], p_today=cl[-1])); continue
        st, se = run(cc, pol, mkt, orc, record_every=3600)
        adj = cash - short * cl[-1]    # carry the window's owner-cash/gas adjustment; withdrawal shortfall marked at today's price
        rows.append(dict(case_id=cid, policy=pol_name, liq_after=len(st["liqs"]) > 0, penalty_after=st["penalty"], n_sales_after=len(st["sales"]), n_buys_after=len(st["buys"]),
                         coll_today=st["C_end"], debt_today=st["D_end"], equity_today_managed=st["C_end"] * cl[-1] - st["D_end"] + adj - st["gas"],
                         p_today=cl[-1], max_ltv_after=st["max_ltv"]))
    print(cid, "ok", flush=True)
pd.DataFrame(rows).to_csv(BASE + "/out/today_managed.csv", index=False)
