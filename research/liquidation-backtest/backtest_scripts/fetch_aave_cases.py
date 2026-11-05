"""Aave V3 Ethereum cases for Set B. Reads (archive, free drpc via Multicall3): reserve config, user account data hourly,
collateral aToken and debt-token balances hourly; owner Supply/Withdraw/Borrow/Repay events (mevblocker logs)."""
import sys, random, json, pandas as pd
sys.path.insert(0, sys.path[0]); from rpc import *
from eth_utils import keccak
def sel(sig): return "0x" + keccak(text=sig)[:4].hex()
def topic(sig): return "0x" + keccak(text=sig).hex()
POOL = "0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2"; ORACLE = "0x54586bE62E3c3580375aE3723C145253060Ca0C2"
ASSET = {"WETH": ("0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2", 18), "wstETH": ("0x7f39C581F595B53c5cb19bD0b3f8dA6c935E2Ca0", 18),
         "WBTC": ("0x2260FAC5E5542a773Aa44fBCfeDf7C193bc2C599", 8), "USDT": ("0xdAC17F958D2ee523a2206206994597C13D831ec7", 6),
         "USDC": ("0xA0b86991c6218b36c1d19D4a2E9Eb0cE3606eB48", 6)}
liqs = pd.read_csv(sys.argv[1]); OUT = sys.argv[2]
T0, T1 = 1759795200, 1760313600        # 7 Oct 00:00 -> 13 Oct 00:00 UTC
b0 = block_at("eth", T0); b1 = block_at("eth", T1)
print("blocks", b0, b1, flush=True)

# reserve data at b0 (aToken / variable debt token / config / rate)
res = {}
for k, (a, dec) in ASSET.items():
    r = eth_call("eth", POOL, sel("getReserveData(address)") + addr_arg(a), b0)
    cfg = u256(r, 0)
    res[k] = dict(asset=a, dec=dec, ltv=(cfg & 0xFFFF) / 1e4, lt=((cfg >> 16) & 0xFFFF) / 1e4, bonus=((cfg >> 32) & 0xFFFF) / 1e4 - 1,
                  var_rate_apr=u256(r, 4) / 1e27, atoken="0x" + r[2 + 64 * 8 + 24: 2 + 64 * 9], vdebt="0x" + r[2 + 64 * 10 + 24: 2 + 64 * 11])
    print(k, res[k], flush=True)
json.dump(res, open(OUT + "/aave_reserves.json", "w"), indent=1)

# candidate selection: WETH or wstETH collateral, USDC/USDT debt
c = liqs[liqs.collateral.isin(["WETH", "wstETH"]) & liqs.debt.isin(["USDC", "USDT"])]
per = c.groupby("borrower").agg(repaid=("debt_to_cover", "sum"), n=("tx", "count"), coll=("collateral", "first"), debt=("debt", "first"),
                                first_liq=("ts", "min")).sort_values("repaid", ascending=False)
# check composition at T0 for the top 15 and a random pool; keep positions where the liquidated collateral is >= 85% of
# collateral value and the liquidated debt asset is >= 85% of debt (so a single-asset model is faithful)
def snapshot(users_assets, block):
    calls = []
    for u, ca, da in users_assets:
        calls += [(POOL, sel("getUserAccountData(address)") + addr_arg(u)),
                  (res[ca]["atoken"], sel("balanceOf(address)") + addr_arg(u)),
                  (res[da]["vdebt"], sel("balanceOf(address)") + addr_arg(u)),
                  (POOL, sel("getUserEMode(address)") + addr_arg(u)),
                  (ORACLE, sel("getAssetPrice(address)") + addr_arg(res[ca]["asset"]))]
    out = multicall("eth", calls, block, chunk=250)
    rows = []
    for i, (u, ca, da) in enumerate(users_assets):
        acc, ab, db, em, px = out[5 * i: 5 * i + 5]
        if acc is None: rows.append(None); continue
        rows.append(dict(borrower=u, block=block, coll_base_usd=u256(acc, 0) / 1e8, debt_base_usd=u256(acc, 1) / 1e8, lt_avg=u256(acc, 3) / 1e4,
                         hf=u256(acc, 5) / 1e18, coll_amt=u256(ab) / 10 ** res[ca]["dec"], debt_amt=u256(db) / 10 ** res[da]["dec"],
                         emode=u256(em), coll_px=u256(px) / 1e8))
    return rows
cands = list(per.index[:15])
rng = random.Random(20261022)
pool = list(per.index[15:]); rng.shuffle(pool); cands += pool[:30]
snap = snapshot([(u, per.loc[u, "coll"], per.loc[u, "debt"]) for u in cands], b0)
ok = []
for u, s in zip(cands, snap):
    if s is None or s["coll_base_usd"] <= 0: continue
    s["coll_share"] = s["coll_amt"] * s["coll_px"] / s["coll_base_usd"]; s["debt_share"] = s["debt_amt"] / max(s["debt_base_usd"], 1e-9)
    if s["coll_share"] >= 0.85 and s["debt_share"] >= 0.85 and s["emode"] == 0: ok.append(u)
large = [u for u in per.index[:15] if u in ok][:3]
rand = [u for u in pool[:30] if u in ok][:3]
chosen = [(u, "largest") for u in large] + [(u, "random") for u in rand]
chosen.append(("0xbf3401cee68f45d1963407076c119f1b3867bee8", "good_cases (HF 1.04-1.10 for weeks)"))
print("chosen", chosen, flush=True)

lq_all = liqs
cases = []
ua = []
for u, kind in chosen:
    l = lq_all[lq_all.borrower == u]
    ca = l.collateral.mode()[0]; da = l.debt.mode()[0]
    ua.append((u, ca, da))
    cases.append(dict(set="B", venue="Aave V3 Ethereum", borrower=u, selection=kind, collateral=ca, debt=da, liq_repaid_usd=l.debt_to_cover.sum(),
                      liq_seized=l.collateral_seized.sum(), n_liqs=len(l), first_liq_ts=int(l.ts.min()),
                      lt=res[ca]["lt"], bonus=res[ca]["bonus"], borrow_apr=res[da]["var_rate_apr"]))
# hourly snapshots T0-1d .. T1
hours = list(range(T0 - 86400, T1 + 1, 3600))
hb = {}
for h in hours:
    hb[h] = block_at("eth", h)
rows = []
for h in hours:
    for s in snapshot(ua, hb[h]):
        if s: s["ts"] = h; rows.append(s)
    print("snap", h, flush=True)
pd.DataFrame(rows).to_csv(OUT + "/aave_case_hourly.csv", index=False)

# owner events (Supply/Withdraw/Borrow/Repay where the case address is onBehalfOf/user = topic2), plus liquidations
EV = {topic("Supply(address,address,address,uint256,uint16)"): "Supply", topic("Withdraw(address,address,address,uint256)"): "Withdraw",
      topic("Borrow(address,address,address,uint256,uint8,uint256,uint16)"): "Borrow", topic("Repay(address,address,address,uint256,bool)"): "Repay"}
users_t = ["0x" + addr_arg(u) for u, _, _ in ua]
logs = get_logs("eth", POOL, [list(EV.keys()), None, users_t], block_at("eth", T0 - 30 * 86400), b1, step=5000)
inv = {v["asset"].lower(): (k, v["dec"]) for k, v in res.items()}
ev = []
for l in logs:
    a = "0x" + l["topics"][1][-40:]; nm, dec = inv.get(a, (a, 18))
    name = EV[l["topics"][0]]
    amt_word = 1 if name in ("Supply", "Borrow") else 0
    ev.append(dict(borrower="0x" + l["topics"][2][-40:], block=int(l["blockNumber"], 16), tx=l["transactionHash"], type=name, asset=nm,
                   amount=u256(l["data"], amt_word) / 10 ** dec, use_atokens=bool(u256(l["data"], 1)) if name == "Repay" else False))
evdf = pd.DataFrame(ev)
bt = {b: block_ts("eth", int(b)) for b in evdf.block.unique()} if len(evdf) else {}
if len(evdf): evdf["ts"] = evdf.block.map(bt)
evdf.to_csv(OUT + "/aave_case_events.csv", index=False)
cdf = pd.DataFrame(cases)
last = evdf[evdf.ts < evdf.borrower.map(dict(zip(cdf.borrower, cdf.first_liq_ts)))].groupby("borrower").ts.max() if len(evdf) else {}
cdf["last_own_action_ts"] = cdf.borrower.map(last)
cdf.to_csv(OUT + "/aave_cases.csv", index=False)
print(cdf)
