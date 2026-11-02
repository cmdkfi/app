#!/usr/bin/env python3 -I
"""Keep the executor's fixed Uniswap v3 pools at the Morpho market oracle price (Base Sepolia, testnet only).

Prototype P1, 26 Oct 2026; adapted from research_notes/Measurements/U_artifacts/rebalance_pool.py (measurement U).
Reads the test wallet key from ~/.config/prime/base_sepolia_eoa.json (never printed). RPC from $RPC.

  rebalance_pool.py --market live [--tolerance 0.0025] [--remint] [--dry-run]
  rebalance_pool.py --market demo [--set-price 1600] [--tolerance 0.0025] [--remint] [--dry-run]

Reference price: the market's Morpho oracle, which is what the executor's minimum output is computed from
(measurement U used the Chainlink ETH/USD answer; the live Morpho oracle also divides by USDC/USD).

live: USDC(token0)/WETH(token1) 0.01% pool 0x5718...156c. When the pool is off the oracle (or the tick left our band):
      burn our band first, then the limit-priced swap to the oracle against what remains, then a two-sided +-1% band
      at the oracle (27 Oct order change; U swapped through its own band first). State: state/live.json.
demo: WETH(token0)/dUSDC(token1) 0.01% pool from prototype/executor/deployments.json, MockOracle as reference.
      dUSDC is mintable and test WETH is scarce, so the layout differs from U:
        anchor  a small full-range position, so moving the price to a new mock price costs only dust;
        sell    a deep dUSDC-only band just below the price (absorbs the executor's collateral sales);
        buy     a small WETH-only band just above the price (for openFromStables).
      A move: burn sell and buy bands, swap through the anchor to the oracle with the price limit, mint both again.
      --set-price USD moves the pool and the MockOracle together in steps of at most 1.9% (the test wallet owns the
      oracle), then re-mints the bands. State: state/demo.json.
"""
import argparse, json, math, os, subprocess, time

RPC = os.environ.get('RPC', 'https://base-sepolia-rpc.publicnode.com')
CAST = os.path.expanduser('~/.foundry/bin/cast')
HERE = os.path.dirname(os.path.abspath(__file__))
STATE_DIR = os.environ.get('STATE_DIR', os.path.join(HERE, 'state'))
DEPLOYMENTS = os.environ.get('DEPLOYMENTS', os.path.join(HERE, '..', 'executor', 'deployments.json'))
WETH = '0x4200000000000000000000000000000000000006'
USDC = '0x036CbD53842c5426634e7929541eC2318f3dCF7e'
DUSDC = '0xeb68Fd5bAAAfD4e46b43bAa7fEAb3216B87b10d5'
ROUTER = '0x94cC0AaC535CCDB3C01d6787D6413C739ae12bc4'
NPM = '0x27F971cb582BF9E50F397e4d29a5C7A34f11faA2'
LIVE_POOL = '0x57183717A087d2fe3Ad890873877244c3B96156c'
LIVE_ORACLE = '0x7D0ebc71EBBEC6F215C2Ab818F9dB538A52f6a3E'
MOCK_ORACLE = '0x91Ec8d7177b876017773cf787B2caB0Fe6Ac6b2f'
FEE = 100; SPACING = 1; MIN_TICK = -887272; MAX_TICK = 887272
Q = 2 ** 96; MAXU128 = 2 ** 128 - 1
LIVE_BAND = 100                       # +-1% (U)
DEMO_SELL_TICKS = 200                 # sell band covers 2% below the price
DEMO_BUY_TICKS = 100                  # buy band covers 1% above the price
DEMO_SELL_DUSDC = 20_000 * 10 ** 6    # minted; depth for sales of about $10,000 per 1% of price move
DEMO_BUY_WETH = 5 * 10 ** 15          # 0.005 WETH
DEMO_ANCHOR_WETH = 5 * 10 ** 15       # 0.005 WETH full range (plus dUSDC at the price)
ETH_RESERVE = 2 * 10 ** 15            # keep 0.002 ETH for gas when wrapping
RESERVE_USDC = 2_000_000; RESERVE_WETH = 10 ** 15   # live mode: as U


def key():
    with open(os.path.expanduser('~/.config/prime/base_sepolia_eoa.json')) as f:
        d = json.load(f)['data']; d = d[0] if isinstance(d, list) else d
    return d['private_key'], d['address']


def call(to, sig, *args):
    out = subprocess.run([CAST, 'call', to, sig, *map(str, args), '--rpc-url', RPC], capture_output=True, text=True, check=True).stdout
    return [l.split()[0] for l in out.strip().split('\n')]


def send(label, to, sig, *args, value=None, dry=False):
    if dry:
        print('  dry-run:', label); return None
    k, me = key()
    # cast's own estimate can be too tight: an NPM mint ran out of gas at exactly the estimate (ReentrancySentryOOG)
    est = [CAST, 'estimate', to, sig, *map(str, args), '--rpc-url', RPC, '--from', me] + (['--value', str(value)] if value else [])
    e = subprocess.run(est, capture_output=True, text=True)
    if e.returncode != 0:
        raise SystemExit('  %s: estimate FAILED %s' % (label, (e.stderr.strip() or e.stdout.strip())[-400:]))
    cmd = [CAST, 'send', to, sig, *map(str, args), '--rpc-url', RPC, '--private-key', k, '--json',
           '--gas-limit', str(int(int(e.stdout.strip()) * 1.3))]
    if value: cmd += ['--value', str(value)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit('  %s: FAILED %s' % (label, (r.stderr.strip() or r.stdout.strip())[-400:]))
    j = json.loads(r.stdout)
    print('  %s: tx %s status %s gas %d' % (label, j['transactionHash'], int(j['status'], 16), int(j['gasUsed'], 16)))
    if int(j['status'], 16) != 1: raise SystemExit('  reverted')
    return j


def bal(token, who): return int(call(token, 'balanceOf(address)(uint256)', who)[0])


class Market:
    def __init__(self, name, dry=False):
        self.name, self.dry = name, dry
        if name == 'live':
            self.pool, self.oracle, self.loan, self.coll = LIVE_POOL, LIVE_ORACLE, USDC, WETH
        else:
            dep = json.load(open(DEPLOYMENTS))
            self.pool, self.oracle, self.loan, self.coll = dep['demoPool'], MOCK_ORACLE, DUSDC, WETH
        self.t0 = call(self.pool, 'token0()(address)')[0].lower()
        self.coll0 = self.t0 == self.coll.lower()          # collateral is token0
        self.state_file = os.path.join(STATE_DIR, name + '.json')

    # oracle price o: loan units per collateral unit, 1e36 scale; raw pool price p = token1 per token0
    def o_to_raw(self, o): return o / 1e36 if self.coll0 else 1e36 / o
    def raw_to_o(self, p): return p * 1e36 if self.coll0 else 1e36 / p
    def usd(self, o): return o / 1e24                     # 6-decimal loan, 18-decimal collateral
    def sqrt_x96(self, o): return int(math.sqrt(self.o_to_raw(o)) * Q)
    def tick_of(self, o): return math.floor(math.log(self.o_to_raw(o)) / math.log(1.0001))

    def read(self):
        o = int(call(self.oracle, 'price()(uint256)')[0])
        s = call(self.pool, 'slot0()(uint160,int24,uint16,uint16,uint16,uint8,bool)')
        sq, tick = int(s[0]), int(s[1])
        liq = int(call(self.pool, 'liquidity()(uint128)')[0])
        return o, self.raw_to_o((sq / Q) ** 2), sq, tick, liq

    def load(self):
        return json.load(open(self.state_file)) if os.path.exists(self.state_file) else {}

    def save(self, st):
        # a dry run sends nothing, so it records nothing: a saved {"anchor": null} made the next real run skip the
        # anchor, and a dropped "sell"/"buy" lost the band's token id (found 27 Oct)
        if self.dry: print('  dry-run: state not saved'); return
        os.makedirs(STATE_DIR, exist_ok=True); json.dump(st, open(self.state_file, 'w'), indent=1)

    def snapshot(self):
        return open(self.state_file, 'rb').read() if os.path.exists(self.state_file) else None


def swap_to_oracle(m, me, o, sq, liq, dry):
    """Limit-priced exact-input swap that stops at the oracle price (U, step 2)."""
    tsq = m.sqrt_x96(o)
    sqP, sqT = sq / Q, tsq / Q
    zero_for_one = sqT < sqP                                # token0 in: raw price falls
    tin = m.t0 if zero_for_one else call(m.pool, 'token1()(address)')[0].lower()
    tout = call(m.pool, 'token1()(address)')[0].lower() if zero_for_one else m.t0
    need = int(liq * ((1 / sqT - 1 / sqP) if zero_for_one else (sqT - sqP)) * 1.5) + 1
    if tin == DUSDC.lower():
        have = bal(DUSDC, me)
        if have < need: send('mint dUSDC for the swap', DUSDC, 'mint(address,uint256)', me, need - have, dry=dry)
        amt = need
    else:
        reserve = RESERVE_USDC if tin == USDC.lower() else RESERVE_WETH
        if m.name == 'demo' and tin == WETH.lower(): wrap_if_short(me, need, dry); reserve = 0
        amt = min(need, max(bal(tin, me) - reserve, 0))
    print('swap %s -> %s: up to %d (L-estimate x1.5 %d), limit at the oracle' % (tin, tout, amt, need))
    if amt <= 0: print('  nothing to swap with'); return
    send('approve router', tin, 'approve(address,uint256)', ROUTER, amt, dry=dry)
    send('swap to oracle', ROUTER, 'exactInputSingle((address,address,uint24,address,uint256,uint256,uint160))',
         '(%s,%s,%d,%s,%d,0,%d)' % (tin, tout, FEE, me, amt, tsq), dry=dry)


def wrap_if_short(me, need, dry):
    have = bal(WETH, me)
    if have >= need: return
    eth = int(subprocess.run([CAST, 'balance', me, '--rpc-url', RPC], capture_output=True, text=True, check=True).stdout.strip())
    amt = need - have
    if eth - amt < ETH_RESERVE: raise SystemExit('not enough ETH to wrap %d wei (have %d, keep %d)' % (amt, eth, ETH_RESERVE))
    send('wrap ETH', WETH, 'deposit()', value=amt, dry=dry)


def burn(label, token_id, me, dry):
    pos = call(NPM, 'positions(uint256)(uint96,address,address,address,uint24,int24,int24,uint128,uint256,uint256,uint128,uint128)', token_id)
    L = int(pos[7])
    if L: send('decreaseLiquidity ' + label, NPM, 'decreaseLiquidity((uint256,uint128,uint256,uint256,uint256))', '(%d,%d,0,0,%d)' % (token_id, L, int(time.time()) + 600), dry=dry)
    send('collect ' + label, NPM, 'collect((uint256,address,uint128,uint128))', '(%d,%s,%d,%d)' % (token_id, me, MAXU128, MAXU128), dry=dry)


def mint(label, m, lo, hi, a0, a1, me, dry):
    """NonfungiblePositionManager.mint; amounts are token0/token1 desired. Returns the token id."""
    t1 = call(m.pool, 'token1()(address)')[0]
    if a0: send('approve token0 to NPM', m.t0, 'approve(address,uint256)', NPM, a0, dry=dry)
    if a1: send('approve token1 to NPM', t1, 'approve(address,uint256)', NPM, a1, dry=dry)
    j = send('mint ' + label, NPM, 'mint((address,address,uint24,int24,int24,uint256,uint256,uint256,uint256,address,uint256))',
             '(%s,%s,%d,%d,%d,%d,%d,0,0,%s,%d)' % (m.t0, t1, FEE, lo, hi, a0, a1, me, int(time.time()) + 600), dry=dry)
    if not j: return None
    tid = [int(l['topics'][3], 16) for l in j['logs'] if l['address'].lower() == NPM.lower() and l['topics'][0].startswith('0xddf252ad')][0]
    print('  %s position %d, ticks [%d, %d)' % (label, tid, lo, hi))
    return tid


def run_live(m, me, a):
    o, po, sq, tick, liq = m.read()
    dev = po / o - 1
    print('live: oracle $%.2f pool $%.2f deviation %+.3f%% tick %d L %.3e' % (m.usd(o), m.usd(po), 100 * dev, tick, liq))
    # our band first: a repair swap through our own band buys our own liquidity back (27 Oct: 45.5 USDC with the band
    # between the pool and the oracle, about 8 without it). Burn it, repair against what remains, mint a new band at the oracle.
    st = m.load().get('band')
    if st and (a.remint or abs(dev) > a.tolerance or not (st['lo'] <= tick < st['hi'])):
        burn('band', st['token_id'], me, a.dry_run); st = None; m.save({})
        o, po, sq, tick, liq = m.read(); print('band burned: pool $%.2f deviation %+.3f%% tick %d L %.3e' % (m.usd(po), 100 * (po / o - 1), tick, liq))
    # the amount comes from the in-range liquidity, which changes when the price crosses initialised ticks: repeat (max 3)
    for _ in range(3):
        if abs(po / o - 1) <= a.tolerance or a.dry_run: break
        swap_to_oracle(m, me, o, sq, liq, a.dry_run)
        o, po, sq, tick, liq = m.read(); print('after swap: pool $%.2f deviation %+.3f%%' % (m.usd(po), 100 * (po / o - 1)))
    if st is None:
        ot = m.tick_of(o); lo, hi = ot - LIVE_BAND, ot + LIVE_BAND
        aw = max(bal(WETH, me) - RESERVE_WETH, 0); au = max(bal(USDC, me) - RESERVE_USDC, 0)
        v = min(aw / 1e18 * m.usd(o), au / 1e6); aw = int(v / m.usd(o) * 1e18); au = int(v * 1e6)
        print('mint +-1%% band [%d, %d) with %.6f WETH + %.2f USDC' % (lo, hi, aw / 1e18, au / 1e6))
        if aw > 0 and au > 0:
            tid = mint('band', m, lo, hi, au, aw, me, a.dry_run)       # USDC is token0 here
            if tid: m.save({'band': {'token_id': tid, 'lo': lo, 'hi': hi}})
    else:
        print('tick %d inside band [%d, %d), position %d kept' % (tick, st['lo'], st['hi'], st['token_id']))


DEMO_STEP = 0.019                     # pool and oracle move together in steps under the agent's 2% gap screen


def move_demo_price(m, me, a, st):
    """Move the demo pool and the MockOracle together: burn the bands, then per step of at most 1.9% swap the pool to
    the next price and set the oracle to it, so no block shows a gap above 1.9% (27 Oct: setting the oracle first left
    an unsettled pool for 37 blocks, and the agent's reduce ran out of gas against it)."""
    for side in ('sell', 'buy'):
        if side in st: burn(side, st[side]['token_id'], me, a.dry_run); del st[side]; m.save(st)
    target = int(round(a.set_price * 1e6)) * 10 ** 18
    o = int(call(MOCK_ORACLE, 'price()(uint256)')[0])
    while o != target:
        up = target > o
        nxt = target if abs(target / o - 1) <= DEMO_STEP else int(o * (1 + DEMO_STEP)) if up else int(o * (1 - DEMO_STEP))
        _, po, sq, tick, liq = m.read()
        swap_to_oracle(m, me, nxt, sq, liq, a.dry_run)                      # the pool first, to the next price
        send('MockOracle.setPrice $%.2f' % m.usd(nxt), MOCK_ORACLE, 'setPrice(uint256)', nxt, dry=a.dry_run)
        o = nxt
        if a.dry_run: break


def run_demo(m, me, a):
    st = m.load()
    if a.set_price:
        move_demo_price(m, me, a, st)
    o, po, sq, tick, liq = m.read()
    dev = po / o - 1
    print('demo: oracle $%.2f pool $%.2f deviation %+.3f%% tick %d L %.3e' % (m.usd(o), m.usd(po), 100 * dev, tick, liq))
    if 'anchor' not in st:
        wrap_if_short(me, DEMO_ANCHOR_WETH, a.dry_run)
        a1 = int(DEMO_ANCHOR_WETH * m.usd(po) / 1e12 * 1.02)        # dUSDC for the anchor at the pool's price
        send('mint dUSDC for the anchor', DUSDC, 'mint(address,uint256)', me, a1, dry=a.dry_run)
        st['anchor'] = mint('anchor', m, MIN_TICK, MAX_TICK, DEMO_ANCHOR_WETH, a1, me, a.dry_run)
        m.save(st)
        o, po, sq, tick, liq = m.read(); dev = po / o - 1
    if not (a.remint or abs(dev) > a.tolerance or 'sell' not in st):
        print('within tolerance, bands kept'); return
    for side in ('sell', 'buy'):
        if side in st: burn(side, st[side]['token_id'], me, a.dry_run); del st[side]; m.save(st)
    o, po, sq, tick, liq = m.read()
    if abs(po / o - 1) > 1e-5:
        swap_to_oracle(m, me, o, sq, liq, a.dry_run)
        if not a.dry_run: o, po, sq, tick, liq = m.read()
    print('pool $%.4f at oracle $%.4f (deviation %+.4f%%), tick %d' % (m.usd(po), m.usd(o), 100 * (po / o - 1), tick))
    # WETH is token0: ticks below the current one hold only dUSDC (token1), ticks above only WETH (token0)
    have = bal(DUSDC, me)
    if have < DEMO_SELL_DUSDC: send('mint dUSDC for the sell band', DUSDC, 'mint(address,uint256)', me, DEMO_SELL_DUSDC - have, dry=a.dry_run)
    lo, hi = tick - DEMO_SELL_TICKS, tick
    tid = mint('sell', m, lo, hi, 0, DEMO_SELL_DUSDC, me, a.dry_run)
    if tid: st['sell'] = {'token_id': tid, 'lo': lo, 'hi': hi}; m.save(st)
    wrap_if_short(me, DEMO_BUY_WETH, a.dry_run)
    lo, hi = tick + 1, tick + 1 + DEMO_BUY_TICKS
    tid = mint('buy', m, lo, hi, DEMO_BUY_WETH, 0, me, a.dry_run)
    if tid: st['buy'] = {'token_id': tid, 'lo': lo, 'hi': hi}; m.save(st)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--market', choices=['live', 'demo'], required=True)
    ap.add_argument('--tolerance', type=float, default=0.0025)
    ap.add_argument('--remint', action='store_true')
    ap.add_argument('--set-price', type=float, help='demo only: MockOracle price in USD per ETH, set first')
    ap.add_argument('--dry-run', action='store_true')
    a = ap.parse_args(); _, me = key()
    m = Market(a.market, a.dry_run)
    print('rpc %s, wallet %s: %.6f ETH, %.6f WETH' % (RPC, me, int(subprocess.run([CAST, 'balance', me, '--rpc-url', RPC], capture_output=True, text=True).stdout.strip()) / 1e18, bal(WETH, me) / 1e18))
    before = m.snapshot()
    (run_live if a.market == 'live' else run_demo)(m, me, a)
    if a.dry_run:
        if m.snapshot() != before: raise SystemExit('dry run changed %s' % m.state_file)
        print('dry-run: %s unchanged' % m.state_file)


if __name__ == '__main__':
    main()
