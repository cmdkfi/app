"""Run every case under every policy variant; write metrics, actions, charts and the reusable dataset."""
import os, sys, json, math, numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(__file__))
from prep import *
from sim import *
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt, matplotlib.dates as mdates

NOTES = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # [local absolute path removed; was the note folder]
CH = NOTES + "/backtest_charts"; DS = NOTES + "/backtest_data"
os.makedirs(CH, exist_ok=True); os.makedirs(DS, exist_ok=True)
OUT = BASE + "/out"; os.makedirs(OUT, exist_ok=True)

CRASH_START = {"A": 1770292800, "B": 1760129400}   # A: 5 Feb 12:00 (final leg), B: 10 Oct 20:50 (sharp leg starts)
ZOOM = {"A": (1770292800, 1770357600), "B": (1760126400, 1760135400)}
DEFAULT_SIGNAL = (0.02, 15)                         # 2% drop from the 15-minute high on Binance (chosen before results)

def corridor(lt, scaled=True):
    if scaled:
        return dict(act=lt * 70 / 86, target=lt * 50 / 86, floor=lt * 30 / 86, ceiling=lt * 60 / 86)
    return dict(act=0.70, target=0.50, floor=0.30, ceiling=0.60)

def policies(c):
    lt = c.venue.lltv; s = c.meta["set"]
    out = []
    corrs = [("scaled", True)] + ([("literal", False)] if abs(lt - 0.86) > 1e-9 else [])
    for cn, sc in corrs:
        cr = corridor(lt, sc)
        for sig in [None, DEFAULT_SIGNAL]:
            for rel in [False, True]:
                for d in [30, 120, 300]:
                    nm = f"{'P3' if sig else 'P2'}-{cn}-{'relever' if rel else 'norelever'}-d{d}"
                    out.append(Policy(nm, **cr, signal=sig, relever=rel, delay=d))
    cr = corridor(lt, True)
    for X in [0.01, 0.02, 0.03, 0.05]:
        for Y in [5, 15, 60]:
            out.append(Policy(f"SENS-X{int(X*100)}-Y{Y}", **cr, signal=(X, Y), delay=120))
    cs = CRASH_START[s]
    for d in [30, 120, 300]:
        out.append(Policy(f"DECOMP-speedonly-d{d}", **cr, delay=d, active_from=cs))
        out.append(Policy(f"DECOMP-speedonly-signal-d{d}", **cr, delay=d, active_from=cs, signal=DEFAULT_SIGNAL))
    out.append(Policy("DECOMP-bufferonly-d120", **cr, delay=120, active_until=cs))
    if s == "B":
        for hhmm, ts in [("2100", 1760130000), ("2105", 1760130300), ("2110", 1760130600), ("2113", 1760130780)]:
            for d in [30, 120, 300]:
                out.append(Policy(f"WAKE-{hhmm}-d{d}", **cr, delay=d, active_from=ts))
    out.append(Policy("FILL-low-d120", **cr, delay=120, fill="low"))
    # speed without buffer: a fast keeper whose act line sits 3 pp under the liquidation line (target 6 pp under)
    for d in [30, 120, 300]:
        out.append(Policy(f"THIN-d{d}", act=lt - 0.03, target=lt - 0.06, floor=lt - 0.30, ceiling=lt - 0.07, delay=d))
        out.append(Policy(f"THIN-signal-d{d}", act=lt - 0.03, target=lt - 0.06, floor=lt - 0.30, ceiling=lt - 0.07, delay=d, signal=DEFAULT_SIGNAL))
    out.append(Policy("HOURLY-d120", **cr, delay=120, decision_every=3600, hourly_prices=True))
    out.append(Policy("HOURLY-signal-d120", **cr, delay=120, decision_every=3600, hourly_prices=True, signal=(0.02, 60)))
    return out

def bottom_time(symbol, s):
    k = klines(); x = k[(k.symbol == symbol) & (k.interval == "1m")]
    lo, hi = CRASH[s]; w = x[(x.open_time >= lo * 1000) & (x.open_time < hi * 1000)]
    r = w.loc[w.low.idxmin()]
    return int(r.open_time // 1000), float(r.low)

def px_close(symbol, t, mult=None):
    k = klines(); x = k[(k.symbol == symbol) & (k.interval == "1m")]
    r = x[x.open_time <= t * 1000].iloc[-1]
    return float(r.close) * (mult(t) if mult else 1.0)

def classify_failure(st, series, pol, c):
    """why a policy position was still liquidated"""
    if not st["liqs"]: return ""
    l = st["liqs"][0]; t = l["t"]
    why = []
    if pol.active_from and t < pol.active_from:
        return "liquidated before the agent was switched on (outside this variant's active window)"
    prev = series[series[:, 0] < t]
    pre_ltv = prev[-1, 4] if len(prev) else np.nan
    if l["pending"] > 0: why.append("sale in flight when liquidated (gap faster than the action delay)")
    if pol.act is not None and not np.isnan(pre_ltv) and pre_ltv < pol.act: why.append(f"oracle jump: LTV {pre_ltv:.1%} -> {l['ltv_before']:.1%} in one update")
    if st["reverts"] > 0: why.append("sales reverted on the 1% oracle ceiling")
    if st["blocked"] > 0: why.append("sales withheld: oracle above market by more than the 1% ceiling")
    near = [f for f in c.flows if t - 3600 <= f[0] <= t and f[1] in ("Borrow", "WithdrawCollateral")]
    if near: why.append("owner borrowed/withdrew within the hour before")
    if pol.active_from and t >= pol.active_from and not st["sales"]: why.append("no sale executed before liquidation")
    return "; ".join(why) or "slice/cooldown limits: sales too slow for the drop"

def baseline_coins(c, mult_fn):
    """'sell instead of borrow': coins held if the owner had sold for the cash instead of borrowing it"""
    p0 = mult_fn(c.t0); coins = c.C0 - c.D0 / p0
    for (t, kind, amt) in c.flows:
        p = mult_fn(t)
        if kind == "SupplyCollateral": coins += amt
        elif kind == "WithdrawCollateral": coins -= amt
        elif kind == "Borrow": coins -= amt / p
        elif kind == "Repay": coins += amt / p
    return coins

def main():
    cases = morpho_cases() + (aave_cases() if os.environ.get("NO_AAVE") != "1" else [])
    rw, rb = ratio_fn()
    rows, acts, liqrows, series_keep = [], [], [], {}
    p_today = {"BTCUSDT": today_price("BTCUSDT"), "ETHUSDT": today_price("ETHUSDT")}
    for c in cases:
        s = c.meta["set"]; sym = c.meta["price_symbol"]
        mult = rw if c.meta["asset"] == "wstETH" else None
        mkt, orc = inputs_for(c); mkt_h, _ = inputs_for(c, hourly=True)
        tb, pb_low = bottom_time(sym, s)
        pb = pb_low * (mult(tb) if mult else 1.0)
        pe = px_close(sym, c.t_end, mult)
        ptd, t_today = p_today[sym]; ptd = ptd * (mult(t_today) if mult else 1.0)
        price_at = lambda t: px_close(sym, int(t), mult)
        base_coins = baseline_coins(c, price_at)
        c.meta.update(C0=c.C0, D0=c.D0, t0=c.t0, t_end=c.t_end, bottom_ts=tb, p_bottom=pb, p_end=pe, p_today=ptd, p_t0=price_at(c.t0),
                      ltv_t0_oracle=c.D0 / (c.C0 * orc.at(c.t0)[0]) if c.C0 > 0 else np.nan, case_id=c.case_id)
        runs = [("ACTUAL", Policy("ACTUAL", enabled=False), True, mkt), ("NOACTION-model", Policy("NOACTION-model", enabled=False), False, mkt)]
        runs += [(p.name, p, False, mkt_h if p.hourly_prices else mkt) for p in policies(c)]
        for nm, pol, actual, m in runs:
            st, se = run(c, pol, m, orc, record_every=60, actual=actual)
            Cb, Db, adj_b, short_b = state_at(se, tb)
            adj = st["owner_cash"] - st["borrow_clamped"] - st["gas"]; short = st["withdraw_short"]
            # coins the owner really withdrew but could not in the policy world are marked at each valuation price
            eq_b = Cb * pb - Db + adj_b - short_b * pb; eq_e = st["C_end"] * pe - st["D_end"] + adj - short * pe
            D_today = st["D_end"] * math.exp(c.rate * (t_today - c.t_end) / YEAR)
            eq_t = st["C_end"] * ptd - D_today + adj - short * ptd
            r = dict(case_id=c.case_id, set=s, venue=c.meta["venue"], asset=c.meta["asset"], borrower=c.meta["borrower"], selection=c.meta["selection"],
                     policy=nm, act=pol.act, target=pol.target, delay_s=pol.delay if not actual else None, relever=pol.relever,
                     signal=str(pol.signal) if pol.signal else "", liquidated=len(st["liqs"]) > 0, n_liqs=len(st["liqs"]),
                     penalty_usd=st["penalty"], bad_debt_usd=st["bad_debt"], n_sales=len(st["sales"]), coll_sold=sum(x["q"] for x in st["sales"]),
                     usd_from_sales=sum(x["q"] * x["px"] for x in st["sales"]), n_buys=len(st["buys"]), coll_bought=sum(x["q"] for x in st["buys"]),
                     reverts=st["reverts"], blocked=st["blocked"], gas_usd=st["gas"], slippage_usd=st["slip_cost"], max_oracle_ltv=st["max_ltv"],
                     owner_cash_adj=st["owner_cash"], withdraw_short_coll=short, cash_adj_total=adj, borrow_clamped=st["borrow_clamped"], n_flow_clamps=len(st["flow_clamps"]),
                     coll_bottom=Cb, debt_bottom=Db, equity_bottom=eq_b, coll_end=st["C_end"], debt_end=st["D_end"], equity_end=eq_e,
                     debt_today=D_today, equity_today=eq_t, ltv_today_static=(D_today / (st["C_end"] * ptd)) if st["C_end"] > 1e-12 else 0.0,
                     static_today_above_line=bool(st["C_end"] > 1e-12 and D_today / (st["C_end"] * ptd) > c.venue.lltv), baseline_coins=base_coins, baseline_equity_bottom=base_coins * pb,
                     baseline_equity_end=base_coins * pe, baseline_equity_today=base_coins * ptd,
                     failure_reason=(classify_failure(st, se, pol, c) if pol.enabled else "no policy") if not actual else "",
                     refused=json.dumps(st["refused"]),
                     first_liq_ts=st["liqs"][0]["t"] if st["liqs"] else None)
            rows.append(r)
            for x in st["sales"]: acts.append(dict(case_id=c.case_id, policy=nm, ts=x["t"], side="sell", why=x["why"], coll=x["q"], px=x["px"], ltv_after=x["ltv_after"]))
            for x in st["buys"]: acts.append(dict(case_id=c.case_id, policy=nm, ts=x["t"], side="buy", why="relever", coll=x["q"], px=x["px"], ltv_after=x["ltv_after"]))
            for x in st["liqs"]: liqrows.append(dict(case_id=c.case_id, policy=nm, ts=x["t"], repaid_usd=x["repaid"], seized_coll=x["seized"], oracle_px=x["oracle"], ltv_before=x["ltv_before"]))
            if nm in ("ACTUAL", "P2-scaled-norelever-d120", "P3-scaled-norelever-d120", "P2-scaled-relever-d120", "P2-literal-norelever-d120", "DECOMP-speedonly-d120"):
                series_keep[(c.case_id, nm)] = se
        print(c.case_id, "done", flush=True)
        chart(c, mkt, orc, series_keep, pd.DataFrame(acts), pd.DataFrame(liqrows))
    res = pd.DataFrame(rows); res.to_csv(OUT + "/results.csv", index=False)
    pd.DataFrame(acts).to_csv(OUT + "/actions.csv", index=False); pd.DataFrame(liqrows).to_csv(OUT + "/sim_liquidations.csv", index=False)
    meta = pd.DataFrame([{k: v for k, v in c.meta.items()} for c in cases]); meta.to_csv(OUT + "/cases.csv", index=False)
    if os.environ.get("NO_AAVE") != "1": export_dataset(cases, res, pd.DataFrame(acts), pd.DataFrame(liqrows), meta)

COL = {"P2-scaled-norelever-d120": ("#2a78d6", "Plain rule (buffer), 2 min delay"),
       "ACTUAL": ("#eb6834", "Actual (real liquidations)"),
       "P3-scaled-norelever-d120": ("#1baf7a", "Plain rule + exchange signal"),
       "P2-scaled-relever-d120": ("#eda100", "Plain rule + re-lever")}

def chart(c, mkt, orc, series_keep, acts, liqs):
    s = c.meta["set"]; lt = c.venue.lltv; cr = corridor(lt, True)
    fig, ax = plt.subplots(3, 1, figsize=(12, 10), gridspec_kw=dict(height_ratios=[1.1, 1.3, 1.3]))
    t = mkt.t0 + 60 * np.arange(mkt.n); sel = (t >= c.t0) & (t <= c.t_end)
    td = pd.to_datetime(t[sel], unit="s")
    ax[0].plot(td, mkt.c[sel], color="#52514e", lw=1, label="Binance 1m close")
    om = (orc.t >= c.t0) & (orc.t <= c.t_end)
    ax[0].step(pd.to_datetime(orc.t[om], unit="s"), orc.p[om], where="post", color="#2a78d6", lw=1, alpha=0.8, label="Venue oracle (Chainlink rounds)")
    ax[0].set_ylabel(f"{c.meta['asset']} price, USD"); ax[0].legend(loc="upper right", frameon=False, fontsize=8)
    title = f"{c.case_id} | {c.meta['venue']} | {c.meta['selection']} | {c.meta['borrower']}"
    ax[0].set_title(title, fontsize=10, loc="left")
    for k, (a, (zt0, zt1)) in enumerate([(ax[1], (c.t0, c.t_end)), (ax[2], ZOOM[s])]):
        for nm, (col, lab) in COL.items():
            se = series_keep.get((c.case_id, nm))
            if se is None or not len(se): continue
            m = (se[:, 0] >= zt0) & (se[:, 0] <= zt1)
            ls = (0, (4, 3)) if "-relever-" in nm else ((0, (1, 1.5)) if nm.startswith("P3") else "-")
            a.plot(pd.to_datetime(se[m, 0], unit="s"), se[m, 4] * 100, color=col, lw=2 if nm != "ACTUAL" else 1.6, ls=ls, label=lab)
            if len(acts):
                aa = acts[(acts.case_id == c.case_id) & (acts.policy == nm) & (acts.ts >= zt0) & (acts.ts <= zt1)]
                for side, mk in [("sell", "v"), ("buy", "^")]:
                    x = aa[aa.side == side]
                    if len(x): a.scatter(pd.to_datetime(x.ts, unit="s"), x.ltv_after * 100, marker=mk, s=40, color=col, edgecolor="white", linewidth=0.8, zorder=5)
            if len(liqs):
                ll = liqs[(liqs.case_id == c.case_id) & (liqs.policy == nm) & (liqs.ts >= zt0) & (liqs.ts <= zt1)]
                if len(ll): a.scatter(pd.to_datetime(ll.ts, unit="s"), ll.ltv_before * 100, marker="X", s=70, color="#0b0b0b", zorder=6, label="liquidation" if nm == "ACTUAL" else None)
        a.axhline(lt * 100, color="#0b0b0b", ls="--", lw=1); a.text(pd.to_datetime(zt0, unit="s"), lt * 100 + 0.8, f"liquidation line {lt:.0%}", fontsize=8, color="#52514e")
        a.axhline(cr["act"] * 100, color="#9b9a95", ls=":", lw=1); a.axhline(cr["target"] * 100, color="#9b9a95", ls=":", lw=1)
        a.text(pd.to_datetime(zt0, unit="s"), cr["act"] * 100 + 0.8, f"act {cr['act']:.1%}", fontsize=8, color="#52514e")
        a.text(pd.to_datetime(zt0, unit="s"), cr["target"] * 100 + 0.8, f"target {cr['target']:.1%}", fontsize=8, color="#52514e")
        a.set_ylabel("LTV on venue oracle, %"); a.set_ylim(0, max(100, lt * 100 + 8))
        a.grid(axis="y", color="#e6e5e0", lw=0.6)
        if k == 0: a.legend(loc="lower left", frameon=False, fontsize=8, ncol=3)
        else: a.set_title("Zoom on the crash leg (markers: v sale, ^ re-buy, X liquidation)", fontsize=9, loc="left")
    for a in ax:
        a.spines[["top", "right"]].set_visible(False)
        a.xaxis.set_major_formatter(mdates.DateFormatter("%d %b\n%H:%M"))
    fig.tight_layout(); fig.savefig(f"{CH}/{c.case_id}.png", dpi=110); plt.close(fig)

def export_dataset(cases, res, acts, liqs, meta):
    meta.to_csv(DS + "/positions.csv", index=False)
    ev = []
    for c in cases:
        ev.append(dict(case_id=c.case_id, ts=c.t0, type="StartState", amount=c.C0, amount2=c.D0, source="T0 snapshot"))
        for (t, kind, amt) in c.flows: ev.append(dict(case_id=c.case_id, ts=t, type=kind, amount=amt, amount2=None, source="owner tx"))
        for (t, rp, sz) in c.real_liqs: ev.append(dict(case_id=c.case_id, ts=t, type="Liquidation", amount=sz, amount2=rp, source="real liquidation"))
    pd.DataFrame(ev).sort_values(["case_id", "ts"]).to_csv(DS + "/position_events.csv", index=False)
    hh = []
    for c in cases:
        h = c.hourly.copy(); h["case_id"] = c.case_id
        if "collateral" not in h: h = h.rename(columns={"coll_amt": "collateral", "debt_amt": "debt"})
        hh.append(h[["case_id", "ts", "collateral", "debt"]])
    pd.concat(hh).to_csv(DS + "/position_hourly_real.csv", index=False)
    k = klines(); k[k.interval == "1m"].drop(columns=["ts"]).to_parquet(DS + "/binance_1m.parquet", index=False)
    k[k.interval == "1h"].drop(columns=["ts"]).to_parquet(DS + "/binance_1h.parquet", index=False)
    pd.read_csv(DATA + "/oracle_updates.csv").to_csv(DS + "/oracle_rounds.csv", index=False)
    pd.read_csv(DATA + "/eth_ratios.csv").to_csv(DS + "/eth_ratios.csv", index=False)
    m = pd.read_csv(RAW + "/morpho_liqs.csv"); m["venue"] = "Morpho Base cbBTC/USDC"; m.to_csv(DS + "/liquidations_morpho_all.csv", index=False)
    a = pd.read_csv(RAW + "/aave_liqs.csv"); a["venue"] = "Aave V3 Ethereum"; a.to_csv(DS + "/liquidations_aave_oct2025_all.csv", index=False)
    res.to_csv(DS + "/sim_results.csv", index=False); acts.to_csv(DS + "/sim_actions.csv", index=False); liqs.to_csv(DS + "/sim_liquidations.csv", index=False)

if __name__ == "__main__":
    main()
