"""Build simulator inputs (cases, market arrays, oracle series) from the raw pulls."""
import json, math, numpy as np, pandas as pd, os, sys
sys.path.insert(0, os.path.dirname(__file__))
from sim import Venue, Case, Market, Oracle, YEAR

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW, DATA = BASE + "/raw", BASE + "/data"

WIN = {"A": dict(t0=1769558400, t_end=1770508800, bottom=None, label="28 Jan - 8 Feb 2026"),     # 28 Jan 00:00 -> 8 Feb 00:00
       "B": dict(t0=1759795200, t_end=1760313600, bottom=None, label="7 Oct - 13 Oct 2025")}     # 7 Oct 00:00 -> 13 Oct 00:00
CRASH = {"A": (1769817600, 1770422400), "B": (1760054400, 1760227200)}   # windows used for 'bottom' search
TODAY_TS = 1761091200  # placeholder, replaced below
MORPHO_LIF = 1 / (0.3 * 0.86 + 0.7)   # = 1.0438 (Morpho docs, LLTV 86%)
GAS = {"morpho": 0.03, "aave": 5.0}   # USD per agent transaction (assumption: Base ~$0.01-0.05; Ethereum ~$2-10 in Oct 2025)

_k = None
def klines():
    global _k
    if _k is None: _k = pd.read_parquet(DATA + "/binance_klines.parquet")
    return _k

def market(symbol, t0, t1, mult=None, hourly=False):
    k = klines(); x = k[(k.symbol == symbol) & (k.interval == "1m")].sort_values("open_time")
    x = x[(x.open_time >= (t0 - 7200) * 1000) & (x.open_time <= (t1 + 3600) * 1000)]
    t = (x.open_time.values // 1000).astype(np.int64)
    o, h, l, c = [x[c].values.astype(float) for c in ["open", "high", "low", "close"]]
    if mult is not None:
        m = mult(t); o, h, l, c = o * m, h * m, l * m, c * m
    assert np.all(np.diff(t) == 60), "gap in minute data"
    return Market(t, o, h, l, c, hourly=hourly)

def today_price(symbol):
    k = klines(); x = k[(k.symbol == symbol) & (k.interval == "1h")].sort_values("open_time")
    r = x.iloc[-1]; return float(r.close), int(r.open_time // 1000) + 3600

def ratio_fn():
    r = pd.read_csv(DATA + "/eth_ratios.csv")
    return (lambda t: np.interp(t, r.ts.values, r.wsteth_steth.values)), (lambda t: np.interp(t, r.ts.values, r.wbtc_btc.values))

def oracle(feed, chain, mult=None):
    o = pd.read_csv(DATA + "/oracle_updates.csv")
    x = o[(o.feed == feed) & (o.chain == chain)].sort_values(["updated_at", "round_id"]).drop_duplicates("updated_at", keep="last")
    t = x.updated_at.values.astype(np.int64); p = x.answer.values.astype(float)
    if mult is not None: p = p * mult(t)
    return Oracle(t, p)

def morpho_rate(hist, t0, t1):
    """continuous APR from the market's borrow share price (debt / borrowShares) of the largest position"""
    best = None
    for b, g in hist.groupby("borrower"):
        g = g[(g.ts >= t0) & (g.ts <= t1) & (g.borrow_shares > 0)]
        if len(g) < 2: continue
        if best is None or g.borrow_shares.max() > best.borrow_shares.max(): best = g
    g = best.sort_values("ts")
    sp0 = g.debt.iloc[0] / g.borrow_shares.iloc[0]; sp1 = g.debt.iloc[-1] / g.borrow_shares.iloc[-1]
    return math.log(sp1 / sp0) / ((g.ts.iloc[-1] - g.ts.iloc[0]) / YEAR)

def morpho_cases():
    cases = pd.read_csv(RAW + "/morpho_cases.csv"); txs = pd.read_csv(RAW + "/morpho_case_txs.csv"); hist = pd.read_csv(RAW + "/morpho_case_hourly.csv")
    out = []
    for s in ["A", "B"]:
        w = WIN[s]; rate = morpho_rate(hist[hist.set == s], w["t0"], w["t_end"])
        for _, r in cases[cases.set == s].iterrows():
            h = hist[(hist.borrower == r.borrower) & (hist.set == s)].sort_values("ts")
            h0 = h[h.ts == w["t0"]].iloc[0]
            tx = txs[(txs.borrower == r.borrower) & (txs.set == s)].sort_values(["ts", "log"])
            flows, liqs = [], []
            for _, x in tx[(tx.ts > w["t0"]) & (tx.ts <= w["t_end"])].iterrows():
                if x.type in ("SupplyCollateral", "WithdrawCollateral"): flows.append((int(x.ts), x.type, x.assets / 1e8))
                elif x.type in ("Borrow", "Repay"): flows.append((int(x.ts), x.type, x.assets / 1e6))
                elif x.type == "Liquidation": liqs.append((int(x.ts), x.repaid / 1e6, x.seized / 1e8))
            v = Venue("morpho", 0.86, MORPHO_LIF - 1, GAS["morpho"], 1200)
            c = Case(f"{s}-morpho-{r.borrower[:8]}", v, float(h0.collateral), float(h0.debt), w["t0"], w["t_end"], rate, flows, liqs)
            c.meta = dict(set=s, venue="Morpho Base cbBTC/USDC", borrower=r.borrower, selection=r.selection, asset="cbBTC", price_symbol="BTCUSDT",
                          liq_repaid=r.liq_repaid_usdc, n_liqs=int(r.n_liqs), first_liq_ts=int(r.first_liq_ts),
                          last_own_action_ts=r.last_own_action_ts, lt=0.86, incentive=MORPHO_LIF - 1, rate=rate,
                          oracle="Chainlink BTC/USD (Base) via MorphoChainlinkOracleV2 0x663B...39B9; USDC=$1")
            c.hourly = h
            out.append(c)
    return out

def aave_cases():
    cases = pd.read_csv(RAW + "/aave_cases.csv"); h = pd.read_csv(RAW + "/aave_case_hourly.csv")
    ev = pd.read_csv(RAW + "/aave_case_events.csv"); lf = pd.read_csv(RAW + "/aave_case_liqs_full.csv")
    at = pd.read_csv(RAW + "/aave_case_atoken_transfers.csv")
    w = WIN["B"]; out = []
    for _, r in cases.iterrows():
        hh = h[h.borrower == r.borrower].sort_values("ts")
        h0 = hh[hh.ts == w["t0"]].iloc[0]
        e = ev[(ev.borrower == r.borrower) & (ev.ts > w["t0"]) & (ev.ts <= w["t_end"])].sort_values("ts")
        flows = []
        for _, x in e.iterrows():
            if x.asset == r.collateral and x.type == "Supply": flows.append((int(x.ts), "SupplyCollateral", x.amount))
            elif x.asset == r.collateral and x.type == "Withdraw": flows.append((int(x.ts), "WithdrawCollateral", x.amount))
            elif x.asset == r.debt and x.type == "Borrow": flows.append((int(x.ts), "Borrow", x.amount))
            elif x.asset == r.debt and x.type == "Repay":
                flows.append((int(x.ts), "Repay", x.amount))   # useATokens repays burn the debt asset's aToken, not this collateral
        # aToken transfers (not mint/burn, not inside a liquidation tx) are owner collateral flows too
        for _, x in at[(at.borrower == r.borrower) & (~at.in_liq_tx) & (at.ts > w["t0"]) & (at.ts <= w["t_end"])].iterrows():
            flows.append((int(x.ts), "SupplyCollateral" if x.side == "in" else "WithdrawCollateral", x.amount))
        flows.sort()
        fee = at[(at.borrower == r.borrower) & (at.in_liq_tx)].groupby("tx").amount.sum()   # protocol fee share of the bonus
        l = lf[(lf.borrower == r.borrower) & (lf.ts >= w["t0"]) & (lf.ts <= w["t_end"])].sort_values(["block", "log"])
        liqs = [(int(x.ts), x.debt_to_cover, x.collateral_seized + (fee.get(x.tx, 0.0) if k == 0 else 0.0))
                for k, (_, x) in enumerate(l.iterrows()) if x.collateral == r.collateral and x.debt == r.debt]
        # fee is per tx; attach it once per tx
        seen = set(); liqs = []
        for _, x in l.iterrows():
            if x.collateral != r.collateral or x.debt != r.debt: continue
            f = fee.get(x.tx, 0.0) if x.tx not in seen else 0.0; seen.add(x.tx)
            liqs.append((int(x.ts), x.debt_to_cover, x.collateral_seized + f))
        # other collateral (not modelled as a price-moving asset): constant credit at its LT-weighted value
        main_val = h0.coll_amt * h0.coll_px
        other_lt_credit = max(h0.lt_avg * h0.coll_base_usd - main_val * r["lt"], 0.0) if h0.coll_amt * h0.coll_px / h0.coll_base_usd < 0.98 else 0.0
        v = Venue("aave", r["lt"], r.bonus, GAS["aave"], 3600)
        c = Case(f"B-aave-{r.borrower[:8]}", v, float(h0.coll_amt), float(h0.debt_amt) - other_lt_credit, w["t0"], w["t_end"], float(r.borrow_apr), flows, liqs)
        sym = "BTCUSDT" if r.collateral == "WBTC" else "ETHUSDT"
        c.meta = dict(set="B", venue="Aave V3 Ethereum", borrower=r.borrower, selection=r.selection, asset=r.collateral, price_symbol=sym,
                      liq_repaid=r.liq_repaid_usd, n_liqs=int(r.n_liqs), first_liq_ts=int(r.first_liq_ts), last_own_action_ts=r.last_own_action_ts,
                      lt=r["lt"], incentive=r.bonus, rate=float(r.borrow_apr), debt_asset=r.debt,
                      other_collateral_lt_credit=other_lt_credit,
                      exclude_from_aggregates=bool(other_lt_credit > 0),
                      exclude_reason="11% of collateral is another asset; single-asset model does not reproduce its real liquidation" if other_lt_credit > 0 else "",
                      coll_share_T0=float(h0.coll_amt * h0.coll_px / h0.coll_base_usd), debt_share_T0=float(h0.debt_amt / h0.debt_base_usd),
                      oracle={"WETH": "Aave source ETH/USD 0x5424...215e (Chainlink)", "wstETH": "Aave CAPO wstETH adapter = ETH/USD source x stETH ratio",
                              "WBTC": "Aave wBTC/BTC/USD adapter = WBTC/BTC x BTC/USD 0xb41e...0a"}[r.collateral])
        c.hourly = hh
        out.append(c)
    return out

def inputs_for(c, hourly=False):
    s = c.meta["set"]; w = WIN[s]
    rw, rb = ratio_fn()
    if c.meta["venue"].startswith("Morpho"):
        mkt = market("BTCUSDT", w["t0"], w["t_end"], hourly=hourly); orc = oracle("BTC/USD", "base")
    elif c.meta["asset"] == "WETH":
        mkt = market("ETHUSDT", w["t0"], w["t_end"], hourly=hourly); orc = oracle("ETH/USD (Aave WETH source)", "eth")
    elif c.meta["asset"] == "wstETH":
        mkt = market("ETHUSDT", w["t0"], w["t_end"], mult=rw, hourly=hourly); orc = oracle("ETH/USD (Aave WETH source)", "eth", mult=rw)
    elif c.meta["asset"] == "WBTC":
        mkt = market("BTCUSDT", w["t0"], w["t_end"], hourly=hourly); orc = oracle("BTC/USD (Aave WBTC adapter PEG_TO_BASE)", "eth", mult=rb)
    return mkt, orc
