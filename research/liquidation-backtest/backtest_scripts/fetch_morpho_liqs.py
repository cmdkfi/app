import sys, json, pandas as pd
sys.path.insert(0, sys.path[0]); from gql import gql
MK = "0x9103c3b4e834476c9a62ea009ba2c884ee42e94e6e314a26f04d312434191836"
Q = """query($w: MarketTransactionFilters, $skip:Int){ marketTransactions(first:1000, skip:$skip, orderBy:Timestamp, orderDirection:Asc, where:$w){
 pageInfo{count countTotal} items{ txHash timestamp blockNumber logIndex type user{address} data{ ... on MarketTransactionLiquidationData { liquidator repaidAssets repaidShares seizedAssets badDebtAssets } } } } }"""
def pull(t0, t1, types=("Liquidation",), users=None):
    out, skip = [], 0
    w = {"marketUniqueKey_in":[MK], "chainId_in":[8453], "type_in":list(types), "timestamp_gte":t0, "timestamp_lte":t1}
    if users: w["userAddress_in"] = users
    while True:
        d = gql(Q, {"w": w, "skip": skip})["marketTransactions"]
        out += d["items"]; skip += 1000
        if skip >= d["pageInfo"]["countTotal"]: break
    return out
rows=[]
for label,t0,t1 in [("A",1769817600,1770422400),("B",1760054400,1760227200)]:
    items = pull(t0,t1)
    print(label, len(items))
    for it in items:
        d = it["data"]
        rows.append(dict(set=label, tx=it["txHash"], ts=int(it["timestamp"]), block=int(it["blockNumber"]), log=it["logIndex"], borrower=it["user"]["address"].lower(),
            liquidator=d["liquidator"], repaid_usdc=int(d["repaidAssets"])/1e6, repaid_shares=int(d["repaidShares"]), seized_cbbtc=int(d["seizedAssets"])/1e8, bad_debt_usdc=int(d["badDebtAssets"])/1e6))
df = pd.DataFrame(rows); df.to_csv(sys.argv[1], index=False)
print(df.groupby("set").agg(n=("tx","count"), borrowers=("borrower","nunique"), repaid=("repaid_usdc","sum"), seized=("seized_cbbtc","sum")))
