"""Event-driven counterfactual simulator for one borrowing position under one policy.

Clock: static events (oracle updates, minute ticks, owner flows, forced real liquidations) merged in time order,
plus a small heap of in-flight agent transactions that land `delay` seconds after the decision.

Prices
  oracle  : real Chainlink round history (step function), the price the venue liquidates on and the executor checks
  market  : Binance 1m kline; the agent decides on the close of the last *completed* minute; a transaction that lands
            inside minute m fills at OHLC4 of m (or the low, in the pessimistic fill variant), less size-based slippage
Venue
  morpho  : liquidatable when debt / (coll * oracle) > LLTV; model liquidator closes the whole debt at the first
            oracle update that crosses (incentive LIF on repaid); this upper-bounds the penalty for a failed policy
  aave    : liquidatable when HF < 1; close factor 50%, 100% if HF < 0.95 or debt or collateral < $2,000 (v3.3 rule);
            repeated in the next block while HF < 1; bonus per collateral asset
Executor (PRIME design s3 / s4.2)
  reduce  : protective session starts when the executor sees LTV > act; inside it sells are not budget limited and are
            clamped so LTV after >= target; outside a session sales need LTV > floor, are clamped to the floor and the
            rolling 24 h discretionary budget; every swap must fill at >= oracle * (1 - 1%) or it reverts
  relever : refused within hold time after a sale, on a stale feed, above the ceiling, beyond the 24 h buy budget;
            fill must be <= oracle * (1 + 1%)
"""
import heapq, math
from dataclasses import dataclass, field, replace
import numpy as np

YEAR = 365 * 86400


@dataclass
class Venue:
    kind: str            # 'morpho' | 'aave'
    lltv: float          # liquidation LTV (Morpho LLTV or Aave liquidation threshold)
    incentive: float     # Morpho LIF - 1, or Aave liquidation bonus
    gas_usd: float       # cost per agent transaction (assumption)
    heartbeat: int       # oracle heartbeat used for the stale-feed check on relever


@dataclass
class Policy:
    name: str
    act: float = None
    target: float = None
    floor: float = None
    ceiling: float = None
    enabled: bool = True
    slice_max: float = 0.25          # share of current collateral per action
    cooldown: int = 120              # s between agent decisions
    delay: int = 120                 # s from decision to landing
    signal: tuple = None             # (drop X as fraction, window Y minutes) on Binance close
    disc_budget: float = 0.20        # rolling 24 h discretionary sell budget, share of collateral
    relever: bool = False
    hold: int = 86400
    buy_budget: float = 0.20
    active_from: int = None
    active_until: int = None
    fill: str = "ohlc4"              # 'ohlc4' | 'low'
    decision_every: int = 60         # 60 = every minute + every oracle update; 3600 = hourly agent on hourly closes
    hourly_prices: bool = False      # market price = hourly close (to show what minute data changes)
    exec_ceiling: float = 0.01


def slippage(notional):
    """Size-based slippage model (assumption): 0.05% below $100k; 0.05% + 0.1% per $1M above; cap 1%."""
    if notional < 1e5:
        return 0.0005
    return min(0.01, 0.0005 + 0.001 * notional / 1e6)


def max_notional_for_slip(s_allowed):
    if s_allowed < 0.0005:
        return 0.0
    if s_allowed >= 0.01:
        return float("inf")
    return max(0.999e5, (s_allowed - 0.0005) / 0.001 * 1e6)


class Market:
    """Binance minute bars as arrays; t in unix seconds."""
    def __init__(self, t0, o, h, l, c, hourly=False):
        self.t0 = int(t0[0]); self.o, self.h, self.l, self.c = o, h, l, c
        self.n = len(c); self.hourly = hourly
        if hourly:   # every minute sees only the close of the last *completed* hour (no look-ahead)
            idx = np.arange(self.n)
            prev_hour_last_min = (idx // 60) * 60 - 1
            hc = np.where(prev_hour_last_min >= 0, c[np.maximum(prev_hour_last_min, 0)], o[0])
            self.o = self.h = self.l = self.c = hc
    def idx(self, t):
        return min(max(int((t - self.t0) // 60), 0), self.n - 1)
    def last_close(self, t):            # close of the last completed minute (or hour, in hourly mode)
        if self.hourly: return self.c[self.idx(t)]
        return self.c[max(self.idx(t) - 1, 0)]
    def fill_px(self, t, mode):
        i = self.idx(t)
        if mode == "low":
            return self.l[i]
        return (self.o[i] + self.h[i] + self.l[i] + self.c[i]) / 4
    def max_close(self, t, minutes):
        i = self.idx(t) - (0 if self.hourly else 1)
        return self.c[max(i - minutes, 0): i + 1].max()


class Oracle:
    def __init__(self, t, p):
        self.t = np.asarray(t, dtype=np.int64); self.p = np.asarray(p, dtype=float)
    def at(self, t):
        i = np.searchsorted(self.t, t, side="right") - 1
        return self.p[max(i, 0)], self.t[max(i, 0)]


@dataclass
class Case:
    case_id: str
    venue: Venue
    C0: float
    D0: float
    t0: int
    t_end: int
    rate: float                 # continuous borrow APR
    flows: list                 # (t, kind, amount) kind in SupplyCollateral/WithdrawCollateral/Borrow/Repay
    real_liqs: list = field(default_factory=list)   # (t, repaid_usd, seized_coll) for the 'actual' replay


def run(case: Case, pol: Policy, mkt: Market, orc: Oracle, record_every=300, actual=False):
    v = case.venue
    C, D, t_last = case.C0, case.D0, case.t0
    st = dict(liqs=[], sales=[], buys=[], reverts=0, blocked=0, gas=0.0, penalty=0.0, bad_debt=0.0, slip_cost=0.0,
              max_ltv=0.0, flow_clamps=[], decisions=0, refused={}, owner_cash=0.0, borrow_clamped=0.0, withdraw_short=0.0, liq_bonus_paid=0.0)
    series = []
    pend = []            # heap (t_land, seq, order)
    seq = 0
    sess_agent = sess_exec = False; sess_start = None
    last_dec = -1e18; last_sale = -1e18
    sell_hist, buy_hist = [], []          # (t, fraction of collateral)
    restorable = getattr(case, "restorable0", 0.0)
    pending_q = {"sell": 0.0, "buy": 0.0}

    def accrue(t):
        nonlocal D, t_last
        if t > t_last and D > 0:
            D *= math.exp(case.rate * (t - t_last) / YEAR)
        t_last = max(t_last, t)

    def ltv(p):
        return D / (C * p) if C > 1e-12 else (0.0 if D <= 1e-9 else float("inf"))

    def budget_used(hist, t):
        return sum(f for (tt, f) in hist if tt > t - 86400)

    def liq_check(t, cause):
        nonlocal C, D
        p, _ = orc.at(t)
        n = 0
        while D > 1e-6 and C > 1e-12 and ltv(p) > v.lltv and n < 20:
            pre = ltv(p)
            if v.kind == "morpho":
                lif = 1 + v.incentive
                repaid = D; seized = repaid * lif / p
                if seized > C:
                    seized = C; repaid = C * p / lif
            else:
                hf = C * p * v.lltv / D
                close = 1.0 if (hf < 0.95 or D < 2000 or C * p < 2000) else 0.5
                repaid = D * close; seized = repaid * (1 + v.incentive) / p
                if seized > C:
                    seized = C; repaid = C * p / (1 + v.incentive)
            pen = seized * p - repaid
            C -= seized; D -= repaid
            if C <= 1e-12 and D > 1e-6:
                st["bad_debt"] += D; D = 0.0
            st["penalty"] += pen
            st["liqs"].append(dict(t=t, repaid=repaid, seized=seized, oracle=p, ltv_before=pre, cause=cause,
                                   pending=len(pend), recent_reverts=st["reverts"]))
            n += 1

    def apply_flow(t, kind, amt):
        nonlocal C, D
        p, _ = orc.at(t)
        if kind == "SupplyCollateral":
            C += amt
        elif kind == "WithdrawCollateral":
            # venue only allows a withdrawal that keeps the position healthy
            maxw = C - (D / (v.lltv * p) if D > 0 else 0.0)
            w = min(amt, max(maxw, 0.0), C)
            if w < amt - 1e-12: st["flow_clamps"].append((t, kind, amt - w)); st["withdraw_short"] += amt - w
            C -= w
        elif kind == "Borrow":
            maxb = max(v.lltv * C * p - D, 0.0)
            b = min(amt, maxb)
            if b < amt - 1e-6: st["flow_clamps"].append((t, kind, amt - b)); st["borrow_clamped"] += amt - b
            D += b
        elif kind == "Repay":
            r = min(amt, D)
            if r < amt - 1e-6: st["owner_cash"] += amt - r     # owner keeps the USDC that was not needed
            D -= r

    def active(t):
        if not pol.enabled or actual:
            return False
        if pol.active_from is not None and t < pol.active_from: return False
        if pol.active_until is not None and t >= pol.active_until: return False
        return True

    def decide(t):
        nonlocal seq, last_dec, sess_agent, pend
        if not active(t) or t - last_dec < pol.cooldown or C <= 1e-12:
            return
        p_o, upd = orc.at(t); p_m = mkt.last_close(t)
        # projected state after in-flight orders (agent knows what it sent)
        Cp = C - pending_q["sell"] + pending_q["buy"]
        Dp = D - pending_q["sell"] * p_m + pending_q["buy"] * p_m
        if Cp <= 1e-12: return
        L = Dp / (Cp * p_o)
        order = None
        # the agent reads the executor's protective-session state on-chain (no separate agent-side session)
        exec_active = sess_exec and sess_start is not None and (t - sess_start) <= 86400
        if L > pol.act or (exec_active and L > pol.target):
            tgt = pol.target
            pe = p_m * (1 - 0.001)
            denom = 1 - tgt * p_o / pe
            R = (Dp - tgt * p_o * Cp) / denom if denom > 0 else Dp
            q = R / pe
            order = ["sell", "protect", q]
        elif pol.signal is not None and D > 0:
            X, Y = pol.signal
            drop = 1 - p_m / mkt.max_close(t, Y)
            if drop >= X and L > pol.floor:
                Lm = Dp / (Cp * p_m)
                if Lm > pol.target:
                    tgt = pol.target; pe = p_m * (1 - 0.001)
                    denom = 1 - tgt * p_m / pe
                    R = (Dp - tgt * p_m * Cp) / denom if denom > 0 else Dp
                    q = R / pe
                    left = max(pol.disc_budget - budget_used(sell_hist, t), 0.0) * Cp
                    q = min(q, left)
                    if q > 0: order = ["sell", "signal", q]
        if order is None and pol.relever and L < pol.target and t - last_sale >= pol.hold and restorable > 1e-12 \
                and t - upd <= v.heartbeat:
            pb = p_m * 1.001
            denom = pb - pol.ceiling * p_o
            q = (pol.ceiling * p_o * Cp - Dp) / denom if denom > 0 else 0.0
            left = max(pol.buy_budget - budget_used(buy_hist, t), 0.0) * Cp
            q = min(q, left, restorable - pending_q["buy"])
            if q > 0: order = ["buy", "relever", q]
        if order is None:
            return
        side, why, q = order
        q = min(q, pol.slice_max * Cp)
        # executor-aware sizing: keep expected slippage + oracle/market gap inside the 1% ceiling (0.1% margin)
        gap = (p_o / p_m - 1) if side == "sell" else (p_m / p_o - 1)
        nmax = max_notional_for_slip(pol.exec_ceiling - 0.001 - gap)
        if nmax <= 0:
            st["blocked"] += 1; last_dec = t; return
        q = min(q, nmax / p_m)
        if q * p_m < 0.01:
            return
        if why != "protect" and q * p_m < 20 * v.gas_usd:     # discretionary orders must be worth >= 20x gas
            return
        last_dec = t; st["decisions"] += 1
        seq += 1
        heapq.heappush(pend, (t + pol.delay, seq, dict(side=side, why=why, q=q, t_dec=t)))
        pending_q[side] += q

    def refuse(r):
        st["refused"][r] = st["refused"].get(r, 0) + 1

    def land(t, od):
        nonlocal C, D, sess_exec, sess_start, last_sale, restorable
        pending_q[od["side"]] -= od["q"]
        if C <= 1e-12: return
        p_o, upd = orc.at(t); L = ltv(p_o)
        st["gas"] += v.gas_usd
        q = od["q"]
        if od["side"] == "sell":
            if L > pol.act:
                if not sess_exec: sess_start = t
                sess_exec = True
            if sess_exec and (L <= pol.target or t - sess_start > 86400):
                sess_exec = False
            if sess_exec:
                m = pol.target; disc = False
            else:
                if L <= pol.floor: refuse("sell: LTV at/below floor"); return
                left = max(pol.disc_budget - budget_used(sell_hist, t), 0.0) * C
                q = min(q, left); m = max(pol.floor, pol.target if od["why"] == "protect" else pol.floor); disc = True
            raw = mkt.fill_px(t, pol.fill)
            pf = raw * (1 - slippage(q * raw))
            if pf < p_o * (1 - pol.exec_ceiling):
                st["reverts"] += 1; refuse("sell: fill below oracle-1% (revert)"); return
            if L <= m: refuse("sell: already at target"); return
            qmax = (D - m * p_o * C) / (pf - m * p_o) if pf > m * p_o else C
            q = max(min(q, qmax, C, D / pf), 0.0)
            if q <= 0: refuse("sell: clamped to zero"); return
            C -= q; D -= q * pf
            st["slip_cost"] += q * (raw - pf)
            st["sales"].append(dict(t=t, q=q, px=pf, why=od["why"], ltv_after=ltv(p_o)))
            if disc: sell_hist.append((t, q / (C + q)))
            last_sale = t; restorable += q
        else:
            if t - last_sale < pol.hold: refuse("buy: hold time"); return
            if t - upd > v.heartbeat: refuse("buy: stale feed"); return
            raw = mkt.fill_px(t, pol.fill if pol.fill != "low" else "ohlc4")
            pb = raw * (1 + slippage(q * raw))
            if pb > p_o * (1 + pol.exec_ceiling):
                st["reverts"] += 1; refuse("buy: fill above oracle+1% (revert)"); return
            qmax = (pol.ceiling * p_o * C - D) / (pb - pol.ceiling * p_o) if pb > pol.ceiling * p_o else q
            left = max(pol.buy_budget - budget_used(buy_hist, t), 0.0) * C
            q = max(min(q, qmax, left, restorable), 0.0)
            if q <= 0: refuse("buy: clamped to zero"); return
            C += q; D += q * pb
            st["slip_cost"] += q * (pb - raw)
            st["buys"].append(dict(t=t, q=q, px=pb, ltv_after=ltv(p_o)))
            buy_hist.append((t, q / C)); restorable -= q

    # ---- static event list ---------------------------------------------------------------------------------
    ev = []
    for t in orc.t[(orc.t >= case.t0) & (orc.t <= case.t_end)]:
        ev.append((int(t), 0, "oracle", None))
    for (t, kind, amt) in case.flows:
        if case.t0 < t <= case.t_end: ev.append((int(t), 1, "flow", (kind, amt)))
    if actual:
        for (t, repaid, seized) in case.real_liqs:
            if case.t0 <= t <= case.t_end: ev.append((int(t), 1, "real_liq", (repaid, seized)))
    step = pol.decision_every
    for t in range(case.t0 - case.t0 % 60 + 60, case.t_end + 1, 60):
        ev.append((t, 3, "tick", None))
    ev.sort(key=lambda x: (x[0], x[1]))

    next_rec = case.t0
    for (t, pr, kind, payload) in ev:
        while pend and pend[0][0] <= t:
            tl, _, od = heapq.heappop(pend)
            accrue(tl); liq_check(tl, "pre-landing"); land(tl, od); liq_check(tl, "post-landing")
        accrue(t)
        if kind == "oracle":
            if not actual: liq_check(t, "oracle update")
            if step == 60: decide(t)
        elif kind == "flow":
            apply_flow(t, *payload)
            if not actual: liq_check(t, "owner flow")
        elif kind == "real_liq":
            repaid, seized = payload
            p, _ = orc.at(t)
            st["penalty"] += repaid * v.incentive      # protocol formula: bonus (Aave) / LIF-1 (Morpho) on repaid debt
            st["liqs"].append(dict(t=t, repaid=repaid, seized=seized, oracle=p, ltv_before=ltv(p), cause="real", pending=0, recent_reverts=0))
            C = max(C - seized, 0.0); D = max(D - repaid, 0.0)
        elif kind == "tick":
            if not actual: liq_check(t, "interest/tick")
            if step == 60 or (t - case.t0) % step == 0: decide(t)
        if C > 1e-12:
            p, _ = orc.at(t); st["max_ltv"] = max(st["max_ltv"], ltv(p))
        if t >= next_rec:
            p, _ = orc.at(t)
            series.append((t, C, D, p, ltv(p) if C > 1e-12 else np.nan, st["owner_cash"] - st["borrow_clamped"] - st["gas"], st["withdraw_short"]))
            next_rec = t + record_every
    accrue(case.t_end)
    st["C_end"], st["D_end"] = C, D; st["restorable_end"] = restorable
    return st, np.array(series)


def state_at(series, t):
    """C, D, cumulative equity adjustment at time t from a recorded series (last record <= t)."""
    i = np.searchsorted(series[:, 0], t, side="right") - 1
    return series[max(i, 0), 1], series[max(i, 0), 2], series[max(i, 0), 5], series[max(i, 0), 6]
