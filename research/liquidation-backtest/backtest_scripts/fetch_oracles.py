"""Chainlink round history read from the aggregators via getTimestamp/getAnswer(roundId) eth_calls.
Same data as AnswerUpdated events (each round == one on-chain update). Free drpc endpoint.
Output: data/oracle_updates.csv (chain, feed, aggregator, round_id, updated_at, answer)"""
import sys, pandas as pd; sys.path.insert(0, sys.path[0]); from rpc import *

def agg_of(chain, proxy, block):
    return "0x" + eth_call(chain, proxy, "0x245a7bfc", block)[-40:]

def ts_of(chain, agg, r):
    return u256(eth_call(chain, agg, "0xb633620c" + uint_arg(r)))

def first_round_at(chain, agg, ts, latest):
    lo, hi = 1, latest
    while hi - lo > 1:
        m = (lo + hi) // 2
        t = ts_of(chain, agg, m)
        if t == 0 or t < ts: lo = m
        else: hi = m
    return hi

def pull(chain, feed, agg, t0, t1, decimals=8, proxy=None, phase=None):
    latest = u256(eth_call(chain, agg, "0x668a0f02"))
    r0 = max(first_round_at(chain, agg, t0, latest) - 1, 1)
    r1 = first_round_at(chain, agg, t1, latest)
    calls = []
    if proxy:   # read through the proxy (aggregator has read access control, e.g. on Base)
        for r in range(r0, r1 + 1):
            calls += [(proxy, "0x9a6fc8f5" + uint_arg((phase << 64) | r))]
    else:
        for r in range(r0, r1 + 1):
            calls += [(agg, "0xb633620c" + uint_arg(r)), (agg, "0xb5ab58dc" + uint_arg(r))]
    res = multicall(chain, calls)
    rows = []
    for k, r in enumerate(range(r0, r1 + 1)):
        if proxy:
            x = res[k]
            if x is None: continue
            a, t = "0x" + x[2 + 64: 2 + 128], "0x" + x[2 + 192: 2 + 256]
        else:
            t, a = res[2 * k], res[2 * k + 1]
        if t is None or a is None or len(t) < 3: continue
        ans = int(a, 16)
        if ans >= 2 ** 255: ans -= 2 ** 256
        rows.append(dict(chain=chain, feed=feed, aggregator=agg, round_id=r, updated_at=int(t, 16), answer=ans / 10 ** decimals))
    print(feed, chain, t0, "rounds", r0, r1, len(rows), flush=True)
    return rows

if __name__ == "__main__":
    rows = []
    # Base: Morpho cbBTC/USDC oracle base feed = Chainlink BTC/USD proxy 0x64c9...848F (no quote feed -> USDC = $1)
    BASE_PROXY = "0x64c911996D3c6aC71f9b455B1E8E7266BcbD848F"
    for t0, t1 in [(1759708800, 1760400000), (1769472000, 1770681600)]:
        agg = agg_of("base", BASE_PROXY, base_block(t0)); assert agg == agg_of("base", BASE_PROXY, base_block(t1))
        phase = next(ph for ph in range(1, 10) if ("0x" + eth_call("base", BASE_PROXY, "0xc1597304" + uint_arg(ph))[-40:]).lower() == agg.lower())
        rows += pull("base", "BTC/USD", agg, t0, t1, proxy=BASE_PROXY, phase=phase)
    # Ethereum: the sources Aave's oracle actually used (looked up via AaveOracle.getSourceOfAsset at the 10 Oct block)
    b = block_at("eth", 1759708800)
    import json
    srcs = {}
    for name, src in [("ETH/USD (Aave WETH source)", "0x5424384b256154046e9667ddfaaa5e550145215e"),
                      ("BTC/USD (Aave WBTC adapter PEG_TO_BASE)", "0xb41e773f507f7a7ea890b1afb7d2b660c30c8b0a"),
                      ("WBTC/BTC (Aave WBTC adapter ASSET_TO_PEG)", "0xfdfd9c85ad200c506cf9e21f1fd8dd01932fbb23")]:
        srcs[name] = src
    # ETH/USD Aave source: check whether it is a proxy (aggregator()) or an adapter
    for name, src in srcs.items():
        try:
            agg = agg_of("eth", src, b); print(name, "proxy->", agg)
            rows += pull("eth", name, agg, 1759708800, 1760400000)
        except Exception as e:
            print(name, "not a proxy:", e)
    pd.DataFrame(rows).to_csv(sys.argv[1], index=False)
