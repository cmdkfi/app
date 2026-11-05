"""Aave V3 Ethereum Core Pool LiquidationCall events, 10 Oct 00:00 - 12 Oct 00:00 UTC 2025 (free RPC: mevblocker / flashbots)."""
import sys, pandas as pd; sys.path.insert(0, sys.path[0]); from rpc import *
POOL = "0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2"
TOPIC = "0xe413a321e8681d831f4dbccbca790d2952b56f977908e45be37335533e005286"
TOK = {"0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2": ("WETH", 18), "0x7f39c581f595b53c5cb19bd0b3f8da6c935e2ca0": ("wstETH", 18),
       "0x2260fac5e5542a773aa44fbcfedf7c193bc2c599": ("WBTC", 8), "0xdac17f958d2ee523a2206206994597c13d831ec7": ("USDT", 6),
       "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48": ("USDC", 6), "0x6b175474e89094c44da98b954eedeac495271d0f": ("DAI", 18),
       "0x40d16fc0246ad3160ccc09b8d0d3a2cd28ae6c2f": ("GHO", 18), "0xcbb7c0000ab88b473b1f5afd9ef808440eed33bf": ("cbBTC", 8),
       "0xcd5fe23c85820f7b72d0926fc9b05b43e359b7ee": ("weETH", 18), "0xdc035d45d973e3ec169d2276ddab16f1e407384f": ("USDS", 18),
       "0x4c9edd5852cd905f086c759e8383e09bff1e68b3": ("USDe", 18), "0x9d39a5de30e57443bff2a8307a4256c8797a3497": ("sUSDe", 18)}
b0, b1 = block_at("eth", 1760054400), block_at("eth", 1760227200)
logs = get_logs("eth", POOL, [TOPIC], b0, b1, step=2000)
rows = []
blk_ts = {}
for l in logs:
    c = "0x" + l["topics"][1][-40:]; d = "0x" + l["topics"][2][-40:]; u = "0x" + l["topics"][3][-40:]
    data = l["data"]
    rows.append(dict(block=int(l["blockNumber"], 16), tx=l["transactionHash"], log=int(l["logIndex"], 16), borrower=u,
                     collateral=TOK.get(c, (c, 18))[0], debt=TOK.get(d, (d, 18))[0],
                     debt_to_cover=u256(data, 0) / 10 ** TOK.get(d, (d, 18))[1],
                     collateral_seized=u256(data, 1) / 10 ** TOK.get(c, (c, 18))[1], liquidator="0x" + data[2 + 64 * 2 + 24: 2 + 64 * 3]))
df = pd.DataFrame(rows)
# timestamps: one call per distinct block
for b in sorted(df.block.unique()):
    blk_ts[b] = block_ts("eth", int(b))
df["ts"] = df.block.map(blk_ts)
df.to_csv(sys.argv[1], index=False)
print(len(df), df.borrower.nunique())
print(df.groupby(["collateral", "debt"]).size().sort_values(ascending=False).head(15))
