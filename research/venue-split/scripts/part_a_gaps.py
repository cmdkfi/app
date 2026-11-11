"""Part A: largest drops per horizon on Binance minute data, and the LTV a
position starting at 50 / 60 / 70 % would reach.

Definitions (all within one contiguous data segment):
  drop_cl(i,h) = 1 - min(low[i+1..i+h]) / close[i]   (close-to-low)
  drop_cc(i,h) = 1 - close[i+h] / close[i]            (close-to-close)
  LTV reached  = L0 / (1 - drop)
Writes CSVs to out/.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # python -I drops it
import numpy as np
import pandas as pd
from common import load_minutes, OUT, START_LTVS, ACT_LTV, LIQ_LTV

HORIZONS = [1, 2, 5, 10, 30, 60]
EXCLUSION_MIN = 360   # one window per 6-hour cluster in the top-10 lists


def drops(df, h):
    close = df.close.values; low = df.low.values; seg = df.seg.values
    n = len(df)
    # future rolling min of low over [i+1, i+h]
    fmin = pd.Series(low[::-1]).rolling(h, min_periods=h).min().values[::-1]   # min(low[i..i+h-1])
    fmin = np.concatenate([fmin[1:], [np.nan]])                                 # min(low[i+1..i+h])
    cc = np.full(n, np.nan); cl = np.full(n, np.nan)
    idx = np.arange(n - h)
    ok = seg[idx] == seg[idx + h]
    cl[idx[ok]] = 1 - fmin[idx[ok]] / close[idx[ok]]
    cc[idx[ok]] = 1 - close[idx[ok] + h] / close[idx[ok]]
    return cl, cc


def top_windows(df, d, h, k=10):
    order = np.argsort(-np.nan_to_num(d, nan=-1))
    chosen = []
    for i in order:
        if np.isnan(d[i]) or d[i] <= 0:
            break
        t = df.t.iloc[i]
        if all(abs((t - c).total_seconds()) >= EXCLUSION_MIN * 60 for c, _ in chosen):
            chosen.append((t, i))
        if len(chosen) == k:
            break
    rows = []
    for t, i in chosen:
        rows.append(dict(start=t, end=df.t.iloc[i + h], close_start=df.close.iloc[i],
                         drop_pct=100 * d[i],
                         **{f"ltv_from_{int(L*100)}": 100 * L / (1 - d[i]) for L in START_LTVS}))
    return pd.DataFrame(rows)


def main():
    summary = []; counts = []
    for sym in ["ETHUSDT", "BTCUSDT"]:
        df = load_minutes(sym)
        print(sym, "minutes", len(df), "segments", df.seg.nunique(), "span", df.t.min(), df.t.max())
        for h in HORIZONS:
            cl, cc = drops(df, h)
            for name, d in [("close_to_low", cl), ("close_to_close", cc)]:
                i = int(np.nanargmax(d))
                summary.append(dict(symbol=sym, horizon_min=h, measure=name, n_windows=int(np.isfinite(d).sum()),
                                    max_drop_pct=100 * d[i], at=df.t.iloc[i],
                                    **{f"ltv_from_{int(L*100)}": 100 * L / (1 - d[i]) for L in START_LTVS}))
                top_windows(df, d, h).to_csv(f"{OUT}/A_top10_{sym}_{h}m_{name}.csv", index=False)
                for L in START_LTVS:
                    act = 1 - L / ACT_LTV; liq = 1 - L / LIQ_LTV
                    counts.append(dict(symbol=sym, horizon_min=h, measure=name, start_ltv=L,
                                       act_drop_pct=100 * act, liq_drop_pct=100 * liq,
                                       n_cross_act=int(np.nansum(d >= act)) if act > 0 else "starts at line",
                                       n_cross_liq=int(np.nansum(d >= liq))))
    s = pd.DataFrame(summary); s.to_csv(f"{OUT}/A_summary.csv", index=False)
    c = pd.DataFrame(counts); c.to_csv(f"{OUT}/A_threshold_counts.csv", index=False)
    pd.set_option("display.width", 250); pd.set_option("display.max_columns", 20)
    print(s.to_string()); print(c.to_string())


if __name__ == "__main__":
    main()
