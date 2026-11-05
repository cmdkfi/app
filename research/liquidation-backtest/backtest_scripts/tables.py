"""Emit markdown tables for backtest.md from out/*.csv (numbers are generated, never typed by hand)."""
import pandas as pd, numpy as np, json, os, sys
B = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
r = pd.read_csv(B + "/out/results.csv"); m = pd.read_csv(B + "/out/cases.csv"); ag = pd.read_csv(B + "/out/aggregates.csv")
excl = set(m[m.exclude_from_aggregates == True].case_id)
mt = pd.read_csv(B + "/raw/morpho_case_txs.csv"); al = pd.read_csv(B + "/raw/aave_case_liqs_full.csv")
OUT = []
def P(s=""): OUT.append(s)
def usd(x):
    if pd.isna(x): return "-"
    a = abs(x)
    if a >= 1e6: return f"{'-' if x < 0 else ''}${a/1e6:.2f}M"
    if a >= 1e3: return f"{'-' if x < 0 else ''}${a/1e3:.1f}k"
    return f"{'-' if x < 0 else ''}${a:,.0f}" if a >= 10 else f"{'-' if x < 0 else ''}${a:.2f}"
def coll(x, asset):
    return f"{x:,.4f}" if x < 10 else f"{x:,.2f}"
def ts(t): return pd.Timestamp(int(t), unit="s").strftime("%d %b %H:%M:%S")
def explorer(c): return "https://basescan.org" if "morpho" in c else "https://etherscan.io"

# ---- cases table
for s, title in [("A", "Set A: Morpho Base cbBTC/USDC, 31 Jan - 6 Feb 2026"), ("B", "Set B: 10-11 Oct 2025")]:
    P(f"#### {title}"); P()
    P("| Case | Borrower | Venue / collateral | How picked | Start state (T0) | LTV at T0 (oracle) | Liquidation line | Days since own last action before 1st liquidation | Real liquidations in window | First liquidation tx |")
    P("|---|---|---|---|---|---|---|---|---|---|")
    for _, c in m[m.set == s].iterrows():
        ex = explorer(c.case_id)
        if "morpho" in c.case_id:
            L = mt[(mt.borrower == c.borrower) & (mt.type == "Liquidation") & (mt.ts >= c.t0) & (mt.ts <= c.t_end)].sort_values("ts")
            rep = L.repaid.sum() / 1e6
        else:
            L = al[(al.borrower == c.borrower) & (al.ts >= c.t0) & (al.ts <= c.t_end)].sort_values("ts"); rep = L.debt_to_cover.sum()
        tx = L.tx.iloc[0]
        ina = (c.first_liq_ts - c.last_own_action_ts) / 86400 if not pd.isna(c.last_own_action_ts) else np.nan
        note = " (excluded from aggregates)" if c.case_id in excl else ""
        P(f"| {c.case_id}{note} | [{c.borrower[:6]}...{c.borrower[-4:]}]({ex}/address/{c.borrower}) | {c.venue}, {c.asset} | {c.selection} | {coll(c.C0, c.asset)} {c.asset} / {usd(c.D0)} debt | {c.ltv_t0_oracle:.1%} | {c["lt"]:.0%} | {'>30 (no own action in the 30 d scanned)' if pd.isna(ina) else f'{ina:.1f}'} | {len(L)}, {usd(rep)} repaid, first {ts(L.ts.iloc[0])} | [{tx[:10]}...]({ex}/tx/{tx}) |")
    P()
# ---- per-position results
NAMES = [("ACTUAL", "Actual"), ("P2-scaled-norelever-d120", "Plain rule, 2 min"), ("P3-scaled-norelever-d120", "Rule + signal 2%/15 min"),
         ("P2-scaled-relever-d120", "Rule + re-lever"), ("BASE", "Hold, no loan")]
for s in ["A", "B"]:
    P(f"#### Per-position results, Set {s}"); P()
    P("| Case | Policy | Liquidated | Penalty paid | Collateral at end | Debt at end | Net equity at bottom | Net equity at end of window | Net equity today (22 Oct 2026, static mark) | Static LTV today |")
    P("|---|---|---|---|---|---|---|---|---|---|")
    for _, c in m[m.set == s].iterrows():
        for pol, nm in NAMES:
            if pol == "BASE":
                x = r[(r.case_id == c.case_id) & (r.policy == "ACTUAL")].iloc[0]
                P(f"| {c.case_id} | {nm} | - | - | {coll(x.baseline_coins, c.asset)} | - | {usd(x.baseline_equity_bottom)} | {usd(x.baseline_equity_end)} | {usd(x.baseline_equity_today)} | - |")
                continue
            x = r[(r.case_id == c.case_id) & (r.policy == pol)].iloc[0]
            P(f"| {c.case_id} | {nm} | {'yes (' + str(int(x.n_liqs)) + ')' if x.liquidated else 'no'} | {usd(x.penalty_usd)} | {coll(x.coll_end, c.asset)} | {usd(x.debt_end)} | {usd(x.equity_bottom)} | {usd(x.equity_end)} | {usd(x.equity_today)} | {x.ltv_today_static:.0%}{' (above line: mark not realistic)' if x.static_today_above_line else ''} |")
    P()
# ---- aggregates
def aggrow(p, s, label):
    a = ag[(ag.policy == p) & (ag.set == s)]
    if not len(a): return None
    a = a.iloc[0]
    return f"| {label} | {a.saved}/{a.n} | {usd(a.pen_actual)} | {usd(a.pen_policy)} | {usd(a.pen_avoided)} | {a.med_extra_coll_pct:+.1f}% | {usd(a.d_eq_bottom)} | {usd(a.d_eq_end)} | {usd(a.d_eq_today)} | {usd(a.slippage)} | {usd(a.gas)} | {int(a.n_sales)} | {int(a.reverts)} |"
VAR = [("NOACTION-model", "No action (model check)"),
       ("P2-scaled-norelever-d30", "Plain rule, scaled corridor, 30 s"), ("P2-scaled-norelever-d120", "Plain rule, scaled, 2 min"), ("P2-scaled-norelever-d300", "Plain rule, scaled, 5 min"),
       ("P2-scaled-relever-d30", "+ re-lever, 30 s"), ("P2-scaled-relever-d120", "+ re-lever, 2 min"), ("P2-scaled-relever-d300", "+ re-lever, 5 min"),
       ("P2-literal-norelever-d120", "Plain rule, literal 70/50, 2 min (Aave only)"), ("P2-literal-relever-d120", "Literal + re-lever, 2 min (Aave only)"),
       ("P3-scaled-norelever-d30", "Rule + signal, 30 s"), ("P3-scaled-norelever-d120", "Rule + signal, 2 min"), ("P3-scaled-norelever-d300", "Rule + signal, 5 min"),
       ("P3-scaled-relever-d120", "Rule + signal + re-lever, 2 min"),
       ("DECOMP-bufferonly-d120", "Buffer only (agent off from crash start)"), ("DECOMP-speedonly-d120", "No buffer, agent on from crash start, 2 min"),
       ("THIN-d30", "Thin-buffer fast keeper (act = line - 3 pp), 30 s"), ("THIN-d120", "Thin-buffer fast keeper, 2 min"), ("THIN-d300", "Thin-buffer fast keeper, 5 min"),
       ("FILL-low-d120", "Plain rule, fills at the minute low (pessimistic)"), ("HOURLY-d120", "Plain rule on hourly closes, hourly checks"), ("HOURLY-signal-d120", "Hourly + signal 2%/60 min")]
for s in ["A", "B"]:
    P(f"#### Aggregates, Set {s} ({'10 positions' if s == 'A' else '10 positions; 0x031f72 excluded'})"); P()
    P("| Variant | Saved from liquidation | Penalty, actual | Penalty, policy | Penalty avoided | Median extra collateral kept at end (% of T0 collateral) | Sum equity vs actual, bottom | ... end of window | ... today | Slippage paid | Gas | Sales | Reverted sales |")
    P("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for p, l in VAR:
        x = aggrow(p, s, l)
        if x: P(x)
    P()
# ---- signal sensitivity
P("#### Signal sensitivity (plain rule + signal, scaled corridor, 2 min delay)"); P()
P("| Signal: drop X% from the Y-minute high | Set A saved | Set A sales | Set A equity vs plain rule at end | Set B saved | Set B sales | Set B reverted | Set B equity vs plain rule at bottom | ... at end |")
P("|---|---|---|---|---|---|---|---|---|")
base = {s: ag[(ag.policy == "P2-scaled-norelever-d120") & (ag.set == s)].iloc[0] for s in "AB"}
for X in [1, 2, 3, 5]:
    for Y in [5, 15, 60]:
        a = ag[(ag.policy == f"SENS-X{X}-Y{Y}") & (ag.set == "A")].iloc[0]; b = ag[(ag.policy == f"SENS-X{X}-Y{Y}") & (ag.set == "B")].iloc[0]
        P(f"| {X}% / {Y} min | {a.saved}/10 | {int(a.n_sales)} | {usd(a.d_eq_end - base['A'].d_eq_end)} | {b.saved}/10 | {int(b.n_sales)} | {int(b.reverts)} | {usd(b.d_eq_bottom - base['B'].d_eq_bottom)} | {usd(b.d_eq_end - base['B'].d_eq_end)} |")
P()
# ---- failures
P("#### Positions that were still liquidated under a variant, and why (classifier output)"); P()
P("| Case | Variant | First liquidation | Reason |"); P("|---|---|---|---|")
f = r[r.liquidated & ~r.policy.isin(["ACTUAL", "NOACTION-model"]) & ~r.case_id.isin(excl)]
f = f[f.policy.isin(["THIN-d30", "THIN-d120", "DECOMP-speedonly-d120", "WAKE-2105-d120", "WAKE-2110-d30", "WAKE-2110-d120"])]
for _, x in f.sort_values(["policy", "case_id"]).iterrows():
    P(f"| {x.case_id} | {x.policy} | {ts(x.first_liq_ts)} | {x.failure_reason} |")
P()
# ---- tx appendix
P("#### Appendix: every real liquidation tx of the sampled cases inside the simulation window"); P()
for _, c in m.iterrows():
    ex = explorer(c.case_id)
    if "morpho" in c.case_id:
        L = mt[(mt.borrower == c.borrower) & (mt.type == "Liquidation") & (mt.ts >= c.t0) & (mt.ts <= c.t_end)].sort_values("ts")
    else:
        L = al[(al.borrower == c.borrower) & (al.ts >= c.t0) & (al.ts <= c.t_end)].sort_values("ts")
    P(f"- **{c.case_id}**: " + ", ".join(f"[{t[:10]}]({ex}/tx/{t}) ({ts(tt)})" for t, tt in zip(L.tx, L.ts)))
open(B + "/out/tables.md", "w").write("\n".join(OUT))
print(len(OUT), "lines")

# ---- managed continuation to today
T = pd.read_csv(B + "/out/today_managed.csv")
O2 = []
O2.append("#### Continuation to 22 Oct 2026 on Binance hourly data (proxy oracle = Binance hourly close)"); O2.append("")
O2.append("| Case | Actual leftover held passively: liquidated again? | equity today | Plain rule kept running: sales after window | equity today | Plain rule + re-lever: sales / re-buys after window | equity today |")
O2.append("|---|---|---|---|---|---|---|")
tot = {}
for cid in m.case_id:
    a = T[(T.case_id == cid) & (T.policy == "ACTUAL")].iloc[0]; p = T[(T.case_id == cid) & (T.policy == "P2-scaled-norelever-d120")].iloc[0]; q = T[(T.case_id == cid) & (T.policy == "P2-scaled-relever-d120")].iloc[0]
    f = lambda v: "-" if pd.isna(v) else str(int(v))
    O2.append(f"| {cid} | {'yes, ' + usd(a.penalty_after) + ' penalty' if a.liq_after else 'no'} | {usd(a.equity_today_managed)} | {f(p.n_sales_after)} | {usd(p.equity_today_managed)} | {f(q.n_sales_after)} / {f(q.n_buys_after)} | {usd(q.equity_today_managed)} |")
    if cid not in excl:
        s = cid[0]
        for k, v in [("a", a.equity_today_managed), ("p", p.equity_today_managed), ("q", q.equity_today_managed)]:
            tot[(s, k)] = tot.get((s, k), 0) + v
for s in "AB":
    O2.append(f"| **Set {s} total (excl. flagged)** | | {usd(tot[(s, 'a')])} | | {usd(tot[(s, 'p')])} | | {usd(tot[(s, 'q')])} |")
open(B + "/out/tables.md", "a").write("\n\n" + "\n".join(O2) + "\n")
