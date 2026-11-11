"""Part B: oracle designs and time-to-line on the five worst ETH windows.

Time convention: bar i has open_time t_i; its close is known at T_i = t_i + 60 s.
Every oracle value, LTV, decision, landing and liquidation is evaluated at bar
closes T_i. A decision made at T_i with latency L lands at bar i + ceil(L/60)
(30 s -> next bar, so a 30 s latency is modelled as 60 s: pessimistic by 30 s).
If the liquidation line is crossed at the same bar the sale lands, the
liquidator wins (tie goes to the liquidator).

Oracle designs (values at bar closes, warmed up from 6 h before the window):
  cl_base  : push feed, post close[i] if |close/last - 1| >= 0.15 % or 1200 s since last post
  cl_main  : push feed, 0.5 % / 3600 s
  pull_1m  : close[i] (a pull oracle refreshed every minute) [ASSUMPTION: cadence]
  twap_30  : mean of the last 30 closes   [ASSUMPTION: window]
  twap_60  : mean of the last 60 closes   [ASSUMPTION: window]
Sale: fills at 0.99 x close of the landing bar; sells enough ETH to bring the
venue's oracle LTV back to the 50 % target; repeated if the act line is crossed again.
Liquidation: at the first bar close with oracle LTV >= 86 %, the whole position is
liquidated at once and the penalty is (LIF - 1) x debt, LIF = 1/(1 - 0.3 (1 - 0.86)).
"""
import os, sys, math
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # python -I drops it
import numpy as np
import pandas as pd
from common import load_minutes, seg_slice, OUT, START_LTVS, TARGET_LTV, ACT_LTV, LIQ_LTV, LATENCIES_S

WINDOWS = {   # t0 = start of the worst 60-minute close-to-low window (part A)
    "2024-03-05": "2024-03-05 19:03",
    "2024-08-05": "2024-08-05 00:17",
    "2025-02-03": "2025-02-03 01:12",
    "2025-10-10": "2025-10-10 20:29",
    "2026-02-05": "2026-02-05 14:39",
}
WARMUP_H, RUN_H = 6, 12
LIF = 1 / (1 - 0.3 * (1 - LIQ_LTV))
PENALTY = LIF - 1
FILL_HAIRCUT = 0.01
DESIGNS = ["cl_base", "cl_main", "pull_1m", "twap_30", "twap_60"]


def push_feed(close, T, dev, hb):
    out = np.empty(len(close)); last = close[0]; tl = T[0]
    for i in range(len(close)):
        if i == 0 or abs(close[i] / last - 1) >= dev or (T[i] - tl) >= hb:
            last = close[i]; tl = T[i]
        out[i] = last
    return out


def oracle_series(df):
    close = df.close.values
    T = ((df.t - pd.Timestamp(0, tz="UTC")).dt.total_seconds().values).astype("int64") + 60
    s = pd.Series(close)
    return dict(
        cl_base=push_feed(close, T, 0.0015, 1200),
        cl_main=push_feed(close, T, 0.005, 3600),
        pull_1m=close.copy(),
        twap_30=s.rolling(30, min_periods=1).mean().values,
        twap_60=s.rolling(60, min_periods=1).mean().values,
    ), T


def no_agent_crossings(O, T, i0, D, C=1.0, act=ACT_LTV):
    ltv = D / (C * O[i0:])
    a = np.argmax(ltv >= act) if (ltv >= act).any() else None
    l = np.argmax(ltv >= LIQ_LTV) if (ltv >= LIQ_LTV).any() else None
    ta = T[i0 + a] if a is not None else None
    tl = T[i0 + l] if l is not None else None
    return ta, tl, ltv.max(), T[i0 + int(np.argmax(ltv))]


def run_agent(O, close, T, i0, D, lat_s, C=1.0, act=ACT_LTV, revert_rule=False):
    """revert_rule=True applies the build executor's rule (backtest.md section 2): a fill more
    than 1 % under the venue oracle reverts; the agent then re-sends with the same latency."""
    bars = math.ceil(lat_s / 60)
    pending = None; sales = []; liq = None; first_act = None; reverts = 0
    for i in range(i0, len(O)):
        ltv = D / (C * O[i]) if C > 0 else 0.0
        if ltv >= LIQ_LTV:
            liq = dict(t=T[i], ltv=ltv, debt=D, coll=C, penalty=PENALTY * D)
            break
        if pending is not None and i >= pending:
            F = close[i] * (1 - FILL_HAIRCUT); P = O[i]
            x = (D - TARGET_LTV * P * C) / (F - TARGET_LTV * P)
            within = (F / P - 1) >= -0.01 - 1e-9
            if revert_rule and not within:
                reverts += 1; pending = i + bars      # reverted; re-sent at once, lands after the latency
            else:
                if x > 0:
                    x = min(x, C)
                    sales.append(dict(t=T[i], eth=x, fill=F, oracle=P, fill_vs_oracle_pct=100 * (F / P - 1),
                                      within_1pct=within, ltv_before=ltv))
                    D -= x * F; C -= x
                pending = None
        ltv = D / (C * O[i]) if C > 0 else 0.0
        if ltv >= act and pending is None:
            if first_act is None:
                first_act = T[i]
            pending = i + bars
    return dict(first_act=first_act, sales=sales, liq=liq, debt_end=D, coll_end=C, reverts=reverts)


def ts(x):
    return pd.Timestamp(x, unit="s", tz="UTC").strftime("%Y-%m-%d %H:%M:%S") if x is not None else ""


def main():
    # optional thin-corridor variant: python -I part_b_oracles.py thin  -> start 80 %, act line 83 %
    thin = len(sys.argv) > 1 and sys.argv[1] == "thin"
    start_ltvs = [0.80] if thin else START_LTVS
    act = 0.83 if thin else ACT_LTV
    tag = "Bthin" if thin else "B"
    df_all = load_minutes("ETHUSDT")
    rows_b = []; rows_b_lat = []; rows_whip = []; raw = []
    for name, t0s in WINDOWS.items():
        t0 = pd.Timestamp(t0s, tz="UTC")
        df = seg_slice(df_all, t0 - pd.Timedelta(hours=WARMUP_H), t0 + pd.Timedelta(hours=RUN_H))
        O, T = oracle_series(df)
        close = df.close.values
        i0 = int(np.where(df.t == t0)[0][0])
        P0 = close[i0]
        # market-path reference for the window
        mkt = dict(P0=P0, low=close[i0:].min(), low_t=T[i0 + int(np.argmin(close[i0:]))],
                   drop_pct=100 * (1 - close[i0:].min() / P0))
        for L0 in start_ltvs:
            D = L0 * P0
            for d in DESIGNS:
                ta, tl, lmax, tmax = no_agent_crossings(O[d], T, i0, D, act=act)
                rows_b.append(dict(window=name, start_ltv=L0, design=d, P0=P0,
                                   oracle_at_t0=O[d][i0], ltv_oracle_t0=D / O[d][i0],
                                   act_cross=ts(ta), liq_cross=ts(tl),
                                   gap_act_to_liq_s=(tl - ta) if (ta is not None and tl is not None) else None,
                                   peak_ltv=lmax, peak_t=ts(tmax)))
                for lat in LATENCIES_S:
                    r = run_agent(O[d], close, T, i0, D, lat, act=act)
                    s0 = r["sales"][0] if r["sales"] else None
                    rows_b_lat.append(dict(window=name, start_ltv=L0, design=d, latency_s=lat,
                                           act_cross=ts(r["first_act"]),
                                           first_sale_landed=ts(s0["t"]) if s0 else "",
                                           sale_before_liq=(s0 is not None and (r["liq"] is None or s0["t"] < r["liq"]["t"])) if (r["first_act"] is not None) else None,
                                           liquidated=r["liq"] is not None, liq_t=ts(r["liq"]["t"]) if r["liq"] else "",
                                           penalty_usd=r["liq"]["penalty"] if r["liq"] else 0.0,
                                           n_sales=len(r["sales"]), eth_sold=sum(s["eth"] for s in r["sales"]),
                                           first_fill=s0["fill"] if s0 else None, first_fill_oracle=s0["oracle"] if s0 else None,
                                           first_fill_vs_oracle_pct=s0["fill_vs_oracle_pct"] if s0 else None,
                                           first_fill_within_1pct=s0["within_1pct"] if s0 else None,
                                           debt_end=r["debt_end"], coll_end=r["coll_end"]))
                    raw.append(dict(window=name, start_ltv=L0, design=d, latency_s=lat, result=r))
            # whipsaw (no agent): first act crossing and first return below act line, per design
            for d in DESIGNS:
                ltv = D / O[d][i0:]
                above = ltv >= act
                if above.any():
                    a = int(np.argmax(above))
                    back = np.where(~above[a:])[0]
                    b = a + int(back[0]) if len(back) else None
                    rows_whip.append(dict(window=name, start_ltv=L0, design=d, act_cross=ts(T[i0 + a]),
                                          back_below_act=ts(T[i0 + b]) if b is not None else "not within frame",
                                          minutes_above_act=(b - a) if b is not None else None,
                                          peak_ltv=ltv.max()))
        pd.DataFrame(dict(t=df.t, close=close, **{k: v for k, v in O.items()})).to_csv(f"{OUT}/{tag}_series_{name}.csv", index=False)
        print(name, mkt)
    B = pd.DataFrame(rows_b); B.to_csv(f"{OUT}/{tag}_crossings.csv", index=False)
    BL = pd.DataFrame(rows_b_lat); BL.to_csv(f"{OUT}/{tag}_agent_latency.csv", index=False)
    W = pd.DataFrame(rows_whip); W.to_csv(f"{OUT}/{tag}_whipsaw.csv", index=False)
    pd.to_pickle(raw, f"{OUT}/{tag}_raw.pkl")
    pd.set_option("display.width", 300); pd.set_option("display.max_columns", 30); pd.set_option("display.max_rows", 500)
    print(B.to_string()); print(BL.to_string()); print(W.to_string())


if __name__ == "__main__":
    main()
