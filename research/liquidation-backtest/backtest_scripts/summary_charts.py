import os, sys, numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(__file__))
from prep import *
import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt, matplotlib.dates as mdates
CH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backtest_charts")  # [local absolute path removed; was the note folder]
r = pd.read_csv(BASE + "/out/results.csv"); m = pd.read_csv(BASE + "/out/cases.csv"); excl = set(m[m.exclude_from_aggregates == True].case_id)
B = r[(r.set == "B") & ~r.case_id.isin(excl)]
rows = [("Corridor 70/50 scaled, agent on all week, 30 s delay", "P2-scaled-norelever-d30"),
        ("Corridor 70/50 scaled, 2 min delay", "P2-scaled-norelever-d120"),
        ("Corridor 70/50 scaled, 5 min delay", "P2-scaled-norelever-d300"),
        ("Corridor, hourly prices + hourly checks", "HOURLY-d120"),
        ("Buffer only: agent switched off from 20:50", "DECOMP-bufferonly-d120"),
        ("No buffer, agent wakes 20:50, 2 min", "DECOMP-speedonly-d120"),
        ("No buffer, agent wakes 21:00, 2 min", "WAKE-2100-d120"),
        ("No buffer, agent wakes 21:05, 2 min", "WAKE-2105-d120"),
        ("No buffer, agent wakes 21:10, 30 s", "WAKE-2110-d30"),
        ("No buffer, agent wakes 21:10, 2 min", "WAKE-2110-d120"),
        ("Thin buffer fast keeper (act line - 3 pp), 30 s", "THIN-d30"),
        ("Thin buffer fast keeper, 2 min", "THIN-d120"),
        ("Thin buffer fast keeper, 5 min", "THIN-d300"),
        ("No action (model)", "NOACTION-model")]
saved = [int((~B[B.policy == p].liquidated).sum()) for _, p in rows]
fig, ax = plt.subplots(figsize=(10, 6.2))
y = np.arange(len(rows))[::-1]
cols = ["#2a78d6" if s == 10 else "#9b9a95" for s in saved]
ax.barh(y, saved, color=cols, height=0.62)
for yi, s in zip(y, saved): ax.text(s + 0.15, yi, f"{s}/10", va="center", fontsize=9, color="#0b0b0b")
ax.set_yticks(y); ax.set_yticklabels([a for a, _ in rows], fontsize=9)
ax.set_xlim(0, 11); ax.set_xlabel("Positions not liquidated (of 10 that were really liquidated)")
ax.set_title("10 Oct 2025: the buffer saved every position; speed without a buffer did not", loc="left", fontsize=11)
ax.spines[["top", "right"]].set_visible(False); ax.grid(axis="x", color="#e6e5e0", lw=0.6)
fig.tight_layout(); fig.savefig(CH + "/summary_oct10_buffer_vs_speed.png", dpi=120); plt.close(fig)

# oracle vs Binance, 10 Oct 20:40-21:40, with the real liquidations of the cases
k = klines(); o = pd.read_csv(DATA + "/oracle_updates.csv")
t0, t1 = 1760128800, 1760132400
liq = []
mt = pd.read_csv(RAW + "/morpho_case_txs.csv"); liq += list(mt[(mt.type == "Liquidation") & (mt.ts >= t0) & (mt.ts <= t1)].ts)
al = pd.read_csv(RAW + "/aave_case_liqs_full.csv"); aliq = list(al[(al.ts >= t0) & (al.ts <= t1)].ts)
fig, ax = plt.subplots(2, 1, figsize=(11, 7.5), sharex=True)
for a, sym, feed, chain, nm, L in [(ax[0], "BTCUSDT", "BTC/USD", "base", "BTC: Binance vs Chainlink BTC/USD on Base (Morpho oracle)", liq),
                                    (ax[1], "ETHUSDT", "ETH/USD (Aave WETH source)", "eth", "ETH: Binance vs Aave's ETH/USD source on Ethereum", aliq)]:
    x = k[(k.symbol == sym) & (k.interval == "1m") & (k.open_time >= t0 * 1000) & (k.open_time <= t1 * 1000)]
    tt = pd.to_datetime(x.open_time, unit="ms")
    a.fill_between(tt, x.low, x.high, color="#cfcec8", step="post", label="Binance 1m high-low")
    a.plot(tt, x.close, color="#52514e", lw=1, drawstyle="steps-post", label="Binance 1m close")
    y = o[(o.feed == feed) & (o.chain == chain) & (o.updated_at >= t0 - 3600) & (o.updated_at <= t1)].sort_values(["updated_at", "round_id"])
    a.step(pd.to_datetime(y.updated_at, unit="s"), y.answer, where="post", color="#2a78d6", lw=2, label="Oracle (each step = one on-chain round)")
    for tl in L: a.axvline(pd.to_datetime(tl, unit="s"), color="#eb6834", lw=0.8, alpha=0.7)
    a.plot([], [], color="#eb6834", lw=0.8, label="Real liquidation of a sampled case")
    a.set_title(nm, loc="left", fontsize=10); a.set_ylabel("USD"); a.legend(frameon=False, fontsize=8, loc="lower left")
    a.spines[["top", "right"]].set_visible(False); a.grid(axis="y", color="#e6e5e0", lw=0.6)
ax[1].set_xlim(pd.to_datetime(t0, unit="s"), pd.to_datetime(t1, unit="s"))
ax[1].xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
fig.tight_layout(); fig.savefig(CH + "/summary_oct10_oracle_vs_binance.png", dpi=120); plt.close(fig)
print("ok")
