"""Select Morpho Base cbBTC/USDC cases and fetch their hourly state + every own transaction (api.morpho.org, free).
Selection: per borrower total USDC repaid by liquidators in the window; 5 largest + 5 random (seeded) for Set A,
2 largest + 2 random for the Morpho part of Set B."""
import sys, json, random, pandas as pd
sys.path.insert(0, sys.path[0]); from gql import gql
MK = "0x9103c3b4e834476c9a62ea009ba2c884ee42e94e6e314a26f04d312434191836"
liqs = pd.read_csv(sys.argv[1]); OUT = sys.argv[2]
SETS = {"A": dict(t0=1769558400, t1=1770595200, n_large=5, n_rand=5),   # 28 Jan 00:00 -> 9 Feb 00:00 2026
        "B": dict(t0=1759795200, t1=1760313600, n_large=2, n_rand=2)}   # 7 Oct 00:00 -> 13 Oct 00:00 2025
TXQ = """query($w: MarketTransactionFilters, $skip:Int){ marketTransactions(first:1000, skip:$skip, orderBy:Timestamp, orderDirection:Asc, where:$w){
 pageInfo{countTotal} items{ txHash timestamp blockNumber logIndex type
 data{ ... on MarketTransactionTransferData{assets shares} ... on MarketTransactionCollateralTransferData{assets}
       ... on MarketTransactionLiquidationData{ liquidator repaidAssets repaidShares seizedAssets badDebtAssets } } } } }"""
HQ = """query($u:String!, $o: TimeseriesOptions){ marketPosition(userAddress:$u, marketUniqueKey:"%s", chainId:8453){
 historicalState{ collateral(options:$o){x y} borrowAssets(options:$o){x y} borrowShares(options:$o){x y} } } }""" % MK
cases, txs, hist = [], [], []
for s, cfg in SETS.items():
    per = liqs[liqs.set == s].groupby("borrower").agg(repaid=("repaid_usdc", "sum"), seized=("seized_cbbtc", "sum"), n=("tx", "count"),
                                                      first_liq=("ts", "min")).sort_values("repaid", ascending=False)
    large = list(per.index[:cfg["n_large"]])
    rng = random.Random(20261022)
    rand = rng.sample(list(per.index[cfg["n_large"]:]), cfg["n_rand"])
    for kind, users in [("largest", large), ("random", rand)]:
        for u in users:
            # every own tx in this market, all time (to measure inactivity) -- positions are small in count
            items, skip = [], 0
            while True:
                d = gql(TXQ, {"w": {"marketUniqueKey_in": [MK], "chainId_in": [8453], "userAddress_in": [u]}, "skip": skip})["marketTransactions"]
                items += d["items"]; skip += 1000
                if skip >= d["pageInfo"]["countTotal"]: break
            for it in items:
                dd = it["data"] or {}
                txs.append(dict(set=s, borrower=u, tx=it["txHash"], ts=int(it["timestamp"]), block=int(it["blockNumber"]), log=it["logIndex"], type=it["type"],
                                assets=int(dd.get("assets") or 0), shares=int(dd.get("shares") or 0),
                                repaid=int(dd.get("repaidAssets") or 0), repaid_shares=int(dd.get("repaidShares") or 0),
                                seized=int(dd.get("seizedAssets") or 0), bad_debt=int(dd.get("badDebtAssets") or 0)))
            h = gql(HQ, {"u": u, "o": {"startTimestamp": cfg["t0"] - 86400, "endTimestamp": cfg["t1"], "interval": "HOUR"}})["marketPosition"]["historicalState"]
            df = pd.DataFrame({"ts": [p["x"] for p in h["collateral"]], "collateral": [int(p["y"]) / 1e8 for p in h["collateral"]]})
            df["debt"] = [int(p["y"]) / 1e6 for p in h["borrowAssets"]]
            df["borrow_shares"] = [int(p["y"]) for p in h["borrowShares"]]
            df["set"] = s; df["borrower"] = u
            hist.append(df.sort_values("ts"))
            own = [t for t in items if t["type"] != "Liquidation" and int(t["timestamp"]) < per.loc[u, "first_liq"]]
            last_own = max([int(t["timestamp"]) for t in own], default=None)
            cases.append(dict(set=s, venue="Morpho Base cbBTC/USDC", borrower=u, selection=kind, liq_repaid_usdc=per.loc[u, "repaid"],
                              liq_seized=per.loc[u, "seized"], n_liqs=int(per.loc[u, "n"]), first_liq_ts=int(per.loc[u, "first_liq"]),
                              last_own_action_ts=last_own, n_own_txs=len([t for t in items if t["type"] != "Liquidation"])))
            print(s, kind, u, round(per.loc[u, "repaid"]), len(items), flush=True)
pd.DataFrame(cases).to_csv(OUT + "/morpho_cases.csv", index=False)
pd.DataFrame(txs).to_csv(OUT + "/morpho_case_txs.csv", index=False)
pd.concat(hist).to_csv(OUT + "/morpho_case_hourly.csv", index=False)
