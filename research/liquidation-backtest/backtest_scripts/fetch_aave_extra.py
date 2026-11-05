"""Second pass for the Aave cases: (a) LiquidationCall for the case addresses over the full sim window (7-13 Oct);
(b) collateral aToken transfers to/from the case address that are not mints/burns and not inside a liquidation tx
(aToken sales via 1inch limit orders, WETH-gateway withdrawals, migrations)."""
import sys, json, pandas as pd; sys.path.insert(0, sys.path[0]); from rpc import *
from eth_utils import keccak
POOL = "0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2"
LIQ = "0xe413a321e8681d831f4dbccbca790d2952b56f977908e45be37335533e005286"
TRANSFER = "0x" + keccak(text="Transfer(address,address,uint256)").hex()
res = json.load(open(sys.argv[1] + "/aave_reserves.json")); cases = pd.read_csv(sys.argv[1] + "/aave_cases.csv")
T0, T1 = 1759795200, 1760313600
b0, b1 = block_at("eth", T0), block_at("eth", T1)
DEC = {v["asset"].lower(): (k, v["dec"]) for k, v in res.items()}
liq, tr = [], []
for _, c in cases.iterrows():
    U = "0x" + addr_arg(c.borrower)
    for l in get_logs("eth", POOL, [LIQ, None, None, U], b0, b1, step=5000):
        ca, cd = DEC.get("0x" + l["topics"][1][-40:], ("?", 18)), DEC.get("0x" + l["topics"][2][-40:], ("?", 18))
        liq.append(dict(borrower=c.borrower, block=int(l["blockNumber"], 16), tx=l["transactionHash"], log=int(l["logIndex"], 16),
                        collateral=ca[0], debt=cd[0], debt_to_cover=u256(l["data"], 0) / 10 ** cd[1], collateral_seized=u256(l["data"], 1) / 10 ** ca[1]))
    at = res[c.collateral]["atoken"]; dec = res[c.collateral]["dec"]
    for side, tp in [("out", [TRANSFER, U]), ("in", [TRANSFER, None, U])]:
        for l in get_logs("eth", at, tp, b0, b1, step=5000):
            fr, to = "0x" + l["topics"][1][-40:], "0x" + l["topics"][2][-40:]
            if fr == "0x" + "0" * 40 or to == "0x" + "0" * 40: continue
            tr.append(dict(borrower=c.borrower, block=int(l["blockNumber"], 16), tx=l["transactionHash"], side=side, counterparty=to if side == "out" else fr,
                           asset=c.collateral, amount=u256(l["data"], 0) / 10 ** dec))
    print(c.borrower, "liqs", sum(1 for x in liq if x["borrower"] == c.borrower), "transfers", sum(1 for x in tr if x["borrower"] == c.borrower), flush=True)
L = pd.DataFrame(liq); T = pd.DataFrame(tr)
bts = {b: block_ts("eth", int(b)) for b in set(L.block) | set(T.block if len(T) else [])}
L["ts"] = L.block.map(bts); L.to_csv(sys.argv[1] + "/aave_case_liqs_full.csv", index=False)
if len(T):
    T["ts"] = T.block.map(bts); T["in_liq_tx"] = T.tx.isin(set(L.tx))
T.to_csv(sys.argv[1] + "/aave_case_atoken_transfers.csv", index=False)
print(T.groupby(["borrower", "side", "in_liq_tx"]).amount.agg(["count", "sum"]) if len(T) else "no transfers")
