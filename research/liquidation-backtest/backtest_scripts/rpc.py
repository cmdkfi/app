"""Minimal JSON-RPC helpers over free public endpoints (no keys)."""
import requests, time
CALL_RPCS = {"base": ["https://base.drpc.org"], "eth": ["https://eth.drpc.org"]}
LATEST_RPCS = {"base": ["https://base-rpc.publicnode.com", "https://mainnet.base.org", "https://base.drpc.org"],
               "eth": ["https://ethereum-rpc.publicnode.com", "https://eth.drpc.org"]}
LOG_RPCS = {"eth": ["https://rpc.mevblocker.io", "https://rpc.flashbots.net"], "base": ["https://base.drpc.org"]}

def _post(url, payload, timeout=90):
    r = requests.post(url, json=payload, timeout=timeout)
    return r.json()

def call(chain, method, params, tries=10, urls=None):
    if urls is None:
        latest = method == "eth_call" and params[-1] == "latest" or method == "eth_blockNumber"
        urls = LATEST_RPCS[chain] if latest else CALL_RPCS[chain]
    err = None
    for i in range(tries):
        try:
            j = _post(urls[i % len(urls)], {"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
            if "error" in j:
                err = j["error"]
                if "revert" in str(err).lower(): break
                time.sleep(min(1.5 * (i + 1), 10)); continue
            return j["result"]
        except Exception as e:
            err = str(e); time.sleep(1 + i)
    raise RuntimeError(f"{method} failed: {err}")

def batch_call(chain, calls, chunk=40, tries=8):
    """calls: list of (to, data, block). returns list of hex results (None on revert)."""
    out = []
    for s in range(0, len(calls), chunk):
        part = calls[s:s + chunk]
        payload = [{"jsonrpc": "2.0", "id": k, "method": "eth_call",
                    "params": [{"to": to, "data": data}, hex(b) if isinstance(b, int) else b]} for k, (to, data, b) in enumerate(part)]
        for i in range(tries):
            try:
                j = _post(CALL_RPCS[chain][0], payload)
                if isinstance(j, dict): raise RuntimeError(j)
                res = {x["id"]: x for x in j}
                if any("error" in x and "revert" not in str(x["error"]).lower() for x in res.values()):
                    raise RuntimeError([x["error"] for x in res.values() if "error" in x][:2])
                out += [res[k].get("result") for k in range(len(part))]
                break
            except Exception as e:
                if i == tries - 1: raise
                time.sleep(2 + 2 * i)
    return out

MULTICALL3 = "0xcA11bde05977b3631167028862bE2a173976CA11"
def multicall(chain, calls, block="latest", chunk=300):
    """calls: list of (to, calldata hex). Returns list of hex return data (None if the sub-call failed)."""
    from eth_abi import encode, decode
    out = []
    for s in range(0, len(calls), chunk):
        part = calls[s:s + chunk]
        data = "0x82ad56cb" + encode(["(address,bool,bytes)[]"], [[(to, True, bytes.fromhex(d[2:])) for to, d in part]]).hex()
        # allowFailure=True per call
        data = "0x82ad56cb" + encode(["(address,bool,bytes)[]"], [[(to, True, bytes.fromhex(d[2:])) for to, d in part]]).hex()
        res = eth_call(chain, MULTICALL3, data, block)
        dec = decode(["(bool,bytes)[]"], bytes.fromhex(res[2:]))[0]
        out += ["0x" + r.hex() if ok else None for ok, r in dec]
    return out

def eth_call(chain, to, data, block="latest"):
    if isinstance(block, int): block = hex(block)
    return call(chain, "eth_call", [{"to": to, "data": data}, block])

def block_ts(chain, n):
    return int(call(chain, "eth_getBlockByNumber", [hex(n), False])["timestamp"], 16)

BASE_GENESIS = 1686789347
def base_block(ts): return (ts - BASE_GENESIS) // 2 + 1

_ANCHOR = {}
def block_at(chain, ts):
    """first block with timestamp >= ts (secant search from a cached anchor, then local walk)"""
    if chain == "base": return base_block(ts)
    if chain not in _ANCHOR:
        hi = int(call(chain, "eth_blockNumber", []), 16); _ANCHOR[chain] = (hi, block_ts(chain, hi))
    ab, at = _ANCHOR[chain]
    g = ab - int((at - ts) / 12)
    for _ in range(6):
        tg = block_ts(chain, g)
        if abs(tg - ts) <= 24: break
        g -= int((tg - ts) / 12)
    tg = block_ts(chain, g)
    while tg < ts: g += 1; tg = block_ts(chain, g)
    while True:
        tp = block_ts(chain, g - 1)
        if tp >= ts: g -= 1
        else: break
    _ANCHOR[chain] = (g, tg)
    return g

def get_logs(chain, address, topics, b0, b1, step=2000):
    out, b = [], b0
    urls = LOG_RPCS[chain]
    while b <= b1:
        e = min(b + step - 1, b1)
        try:
            out += call(chain, "eth_getLogs", [{"address": address, "topics": topics, "fromBlock": hex(b), "toBlock": hex(e)}], tries=4, urls=urls)
            b = e + 1
        except RuntimeError:
            if step <= 50: raise
            step //= 2
    return out

def u256(h, i=0):
    return int(h[2 + 64 * i: 2 + 64 * (i + 1)], 16)

def addr_arg(a): return a[2:].lower().rjust(64, "0")
def uint_arg(x): return hex(x)[2:].rjust(64, "0")
