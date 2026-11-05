"""wstETH->stETH ratio (stETH.getPooledEthByShares(1e18), the RATIO_PROVIDER of Aave's wstETH CAPO adapter) and WBTC/BTC at daily blocks."""
import sys, pandas as pd; sys.path.insert(0, sys.path[0]); from rpc import *
b0, b1, t0 = int(sys.argv[1]), int(sys.argv[2]), 1759795200
rows = []
for i, b in enumerate(range(b0, b1 + 1, 7150)):
    r = u256(eth_call("eth", "0xae7ab96520de3a18e5e111b5eaab095312d7fe84", "0x7a28fb88" + uint_arg(10**18), b)) / 1e18
    w = eth_call("eth", "0xfdfd9c85ad200c506cf9e21f1fd8dd01932fbb23", "0x50d25bcd", b)
    rows.append(dict(block=b, ts=block_ts("eth", b), wsteth_steth=r, wbtc_btc=(u256(w) / 1e8) if w else None))
    print(rows[-1], flush=True)
pd.DataFrame(rows).to_csv(sys.argv[3], index=False)
