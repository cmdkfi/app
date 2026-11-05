#!/usr/bin/env python3 -I
"""Keep the executor's fixed Uniswap v3 pools at the Morpho market oracle price (Base Sepolia, testnet only).

Prototype P1, 26 Oct 2026; adapted from research_notes/Measurements/U_artifacts/rebalance_pool.py (measurement U).
Venue row, 3 Nov 2026: budget guard, --status, L1 data fee per run, one RUN line per run for scripts/rebalance.sh.
Reads the test wallet key from ~/.config/prime/base_sepolia_eoa.json (never printed). RPC from $RPC.
Addresses from $DEPLOYMENTS: deployments/testnet.json (default) or the old flat executor/deployments.json.

  rebalance_pool.py --market live [--tolerance 0.0025] [--max-cost-usdc 6] [--reserve-usdc 8] [--band-usdc 10] [--remint] [--dry-run]
  rebalance_pool.py --market demo [--set-price 1600 [--max-step 0.019]] [--tolerance 0.0025] [--remint] [--dry-run]
  rebalance_pool.py --market live|demo --status        reads only: tick, oracle, gap, our band, wallet, L1 fee, repair cost
  rebalance_pool.py --market live|demo --close-bands [--dry-run]   burn and collect all our positions on the pool, clear state

Reference price: the market's Morpho oracle, which is what the executor's minimum output is computed from
(measurement U used the Chainlink ETH/USD answer; the live Morpho oracle also divides by USDC/USD).

live: USDC(token0)/WETH(token1) 0.01% pool 0x5718...156c. When the pool is off the oracle (or the tick left our band):
      burn our band first, then the limit-priced swap to the oracle against what remains, then a two-sided +-1% band
      at the oracle (27 Oct order change; U swapped through its own band first). State: state/live.json.
      Budget guard (results.md item 40 and §4.8): before anything is sent, the repair cost is computed from the
      pool's initialised ticks with our band taken out (it is burnt first), and cross-checked with QuoterV2 when our
      band is not on the path. The run is refused (exit 3) when the cost is above --max-cost-usdc, or when it would
      leave the wallet (plus what the band burn returns) under --reserve-usdc. A new band keeps reserve + max cost
      in the wallet, so the next repair can still be paid; --band-usdc caps its USDC (and so its WETH) below that.
demo: WETH(token0)/dUSDC(token1) 0.01% pool from the deployments, MockOracle as reference.
      dUSDC is mintable and test WETH is scarce, so the layout differs from U:
        anchor  a small full-range position, so moving the price to a new mock price costs only dust;
        sell    a deep dUSDC-only band just below the price (absorbs the executor's collateral sales);
        buy     a small WETH-only band just above the price (for openFromStables).
      A move: burn sell and buy bands, swap through the anchor to the oracle with the price limit, mint both again.
      --set-price USD moves the pool and the MockOracle together in steps of at most 1.9% (the test wallet owns the
      oracle); --max-step F (a fraction, default 0.019) changes that limit, e.g. 1 for the gap scene's one-move drop
      (scripts/SCENES.md). On the last step the bands are minted at the new pool price before the oracle is set, so
      a sale the new price triggers meets the bands and not the anchor alone (results.md item 42). State: state/demo.json.
      The guard applies when a repair spends WETH; dUSDC is minted. --set-price moves are not guarded.

Exit codes: 0 done, 1 error, 3 refused by the budget guard, 4 another run holds the lock.
"""
import argparse, fcntl, json, math, os, subprocess, time

RPC = os.environ.get('RPC', 'https://base-sepolia-rpc.publicnode.com')
CAST = os.path.expanduser('~/.foundry/bin/cast')
HERE = os.path.dirname(os.path.abspath(__file__))
STATE_DIR = os.environ.get('STATE_DIR', os.path.join(HERE, 'state'))
_NEW_DEP = os.path.join(HERE, '..', 'deployments', 'testnet.json')
_OLD_DEP = os.path.join(HERE, '..', 'executor', 'deployments.json')
DEPLOYMENTS = os.environ.get('DEPLOYMENTS', _NEW_DEP if os.path.exists(_NEW_DEP) else _OLD_DEP)
CHAIN_ID = 84532                      # Base Sepolia (an anvil fork of it keeps this id); anything else is refused
# Base Sepolia contracts the old flat file does not list
_BASE_DEFAULTS = {'router': '0x94cC0AaC535CCDB3C01d6787D6413C739ae12bc4', 'npm': '0x27F971cb582BF9E50F397e4d29a5C7A34f11faA2',
                  'quoter': '0xC5290058841028F1614F3A6F0F5816cAd0df5E27', 'gasPriceOracle': '0x420000000000000000000000000000000000000F',
                  'irm': '0x46415998764C29aB2a25CbeA6254146D50D22687', 'lltv': 860000000000000000}


def load_deployments(path=None):
    """Either shape, returned flat with the old file's keys (executor/deployments.json) plus router, npm, quoter,
    gasPriceOracle, irm, lltv and backupPool."""
    d = json.load(open(path or DEPLOYMENTS))
    if 'base' not in d:                                     # old flat shape
        out = dict(_BASE_DEFAULTS); out.update(d); out.setdefault('backupPool', d['livePool']); return out
    b = d['base']; mk = b['markets']; t = b['tokens']; u = b['uniswapV3']
    return {'chainId': b['chainId'], 'executor': b['executor'], 'block': b['executorDeployBlock'],
            'cooldownSeconds': b['cooldownSeconds'], 'morpho': b['morpho'], 'irm': b['irm'],
            'usdc': t['usdc'], 'weth': t['weth'], 'dusdc': t['dusdc'],
            'liveMarketId': mk['live']['id'], 'backupMarketId': mk['backup']['id'], 'demoMarketId': mk['demo']['id'],
            'liveOracle': mk['live']['oracle'], 'backupOracle': mk['backup']['oracle'], 'mockOracle': b['mockOracle']['address'],
            'livePool': mk['live']['pool'], 'backupPool': mk['backup']['pool'], 'demoPool': mk['demo']['pool'],
            'router': u['swapRouter02'], 'npm': u['nonfungiblePositionManager'], 'quoter': u['quoterV2'],
            'gasPriceOracle': b['gasPriceOracle'], 'lltv': int(mk['live']['lltv'])}


DEP = load_deployments()
WETH, USDC, DUSDC = DEP['weth'], DEP['usdc'], DEP['dusdc']
ROUTER, NPM, QUOTER, GPO = DEP['router'], DEP['npm'], DEP['quoter'], DEP['gasPriceOracle']
LIVE_POOL, LIVE_ORACLE, MOCK_ORACLE = DEP['livePool'], DEP['liveOracle'], DEP['mockOracle']
FEE = 100; SPACING = 1; MIN_TICK = -887272; MAX_TICK = 887272
Q = 2 ** 96; MAXU128 = 2 ** 128 - 1
LIVE_BAND = 100                       # +-1% (U)
DEMO_SELL_TICKS = 200                 # sell band covers 2% below the price
DEMO_BUY_TICKS = 100                  # buy band covers 1% above the price
DEMO_SELL_DUSDC = 20_000 * 10 ** 6    # minted; depth for sales of about $10,000 per 1% of price move
DEMO_BUY_WETH = 5 * 10 ** 15          # 0.005 WETH
DEMO_ANCHOR_WETH = 5 * 10 ** 15       # 0.005 WETH full range (plus dUSDC at the price)
ETH_RESERVE = 2 * 10 ** 15            # keep 0.002 ETH for gas when wrapping
RESERVE_USDC = 2_000_000; RESERVE_WETH = 10 ** 15   # hard floors inside a swap: as U
L1_TX_BYTES = 400                     # a typical unsigned rebalancer transaction (mint, swap), for the L1 fee bound

COSTS = {'txs': [], 'l2_wei': 0, 'l1_wei': 0, 'swap_in': {}, 'swap_out': {}}
SUMMARY = {}


class Refused(SystemExit):
    """The budget guard's refusal: exit code 3, with the reason in the RUN line."""
    def __init__(self, msg):
        print('REFUSED:', msg); SUMMARY['why'] = msg; super().__init__(3)


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
    gas = int(j['gasUsed'], 16); l2 = gas * int(j.get('effectiveGasPrice', '0x0'), 16); l1 = int(j.get('l1Fee') or '0x0', 16)
    COSTS['txs'].append(j['transactionHash']); COSTS['l2_wei'] += l2; COSTS['l1_wei'] += l1
    print('  %s: tx %s status %s gas %d, fee %d wei (L2 %d + L1 %d)' % (label, j['transactionHash'], int(j['status'], 16), gas, l2 + l1, l2, l1))
    if int(j['status'], 16) != 1: raise SystemExit('  reverted')
    return j


def bal(token, who): return int(call(token, 'balanceOf(address)(uint256)', who)[0])


def eth_bal(who): return int(subprocess.run([CAST, 'balance', who, '--rpc-url', RPC], capture_output=True, text=True, check=True).stdout.strip())


def l1_fee():
    """Base's GasPriceOracle, read every run (results.md item 12: 290 wei to 2.44e12 wei per transaction within 27 Oct)."""
    r = {'l1BaseFee': int(call(GPO, 'l1BaseFee()(uint256)')[0]), 'blobBaseFee': int(call(GPO, 'blobBaseFee()(uint256)')[0]),
         'baseFeeScalar': int(call(GPO, 'baseFeeScalar()(uint32)')[0]), 'blobBaseFeeScalar': int(call(GPO, 'blobBaseFeeScalar()(uint32)')[0]),
         'l2BaseFee': int(subprocess.run([CAST, 'base-fee', '--rpc-url', RPC], capture_output=True, text=True, check=True).stdout.strip())}
    try: r['upperBound'] = int(call(GPO, 'getL1FeeUpperBound(uint256)(uint256)', L1_TX_BYTES)[0])
    except subprocess.CalledProcessError: r['upperBound'] = None      # before Fjord
    print('L1 data fee (GasPriceOracle): l1BaseFee %d wei, blobBaseFee %d wei, scalars %d/%d, upper bound for a %d-byte tx '
          '%s wei; L2 base fee %d wei' % (r['l1BaseFee'], r['blobBaseFee'], r['baseFeeScalar'], r['blobBaseFeeScalar'],
                                          L1_TX_BYTES, r['upperBound'], r['l2BaseFee']))
    SUMMARY['l1_bound_wei'] = r['upperBound']
    return r


class Market:
    def __init__(self, name, dry=False):
        self.name, self.dry = name, dry
        if name == 'live':
            self.pool, self.oracle, self.loan, self.coll = LIVE_POOL, LIVE_ORACLE, USDC, WETH
        else:
            self.pool, self.oracle, self.loan, self.coll = DEP['demoPool'], MOCK_ORACLE, DUSDC, WETH
        self.t0 = call(self.pool, 'token0()(address)')[0].lower()
        self.t1 = call(self.pool, 'token1()(address)')[0].lower()
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

    def human(self, token, raw):
        t = token.lower()
        if t == WETH.lower(): return '%.6f WETH' % (raw / 1e18)
        return '%.6f %s' % (raw / 1e6, 'USDC' if t == USDC.lower() else 'dUSDC')

    def value_usd(self, token, raw, o):
        return raw / 1e18 * self.usd(o) if token.lower() == WETH.lower() else raw / 1e6


# ---- repair cost from the pool's initialised ticks (results.md §4.8; prototype AFTER_FUNDING.md step 2b) ----

def sqrt_at(t): return 1.0001 ** (t / 2)


def position(token_id):
    p = call(NPM, 'positions(uint256)(uint96,address,address,address,uint24,int24,int24,uint128,uint256,uint256,uint128,uint128)', token_id)
    return {'lo': int(p[5]), 'hi': int(p[6]), 'L': int(p[7]), 'owed0': int(p[10]), 'owed1': int(p[11])}


def position_amounts(pos, sq):
    """Raw token0, token1 a burn returns at the current price (fees accrued since the last poke not included)."""
    s, sa, sb, L = sq / Q, sqrt_at(pos['lo']), sqrt_at(pos['hi']), pos['L']
    if s <= sa: a0, a1 = L * (1 / sa - 1 / sb), 0.0
    elif s >= sb: a0, a1 = 0.0, L * (sb - sa)
    else: a0, a1 = L * (1 / s - 1 / sb), L * (s - sa)
    return int(a0) + pos['owed0'], int(a1) + pos['owed1']


def initialised(pool, a, b):
    """Initialised ticks in [a, b] (tick spacing 1: bitmap word = tick >> 8, bit = tick & 255)."""
    if b < a: return []
    if (b >> 8) - (a >> 8) > 400: raise SystemExit('repair path spans %d bitmap words; not walking it' % ((b >> 8) - (a >> 8)))
    out = []
    for w in range(a >> 8, (b >> 8) + 1):
        bm = int(call(pool, 'tickBitmap(int16)(uint256)', w)[0])
        out += [w * 256 + i for i in range(256) if bm >> i & 1 and a <= w * 256 + i <= b]
    return out


def walk(m, sq, tick, liq, tsq, exclude=()):
    """Swap from sqrt price sq to tsq computed from the pool's tick liquidity, with the positions in exclude
    (dicts lo, hi, L) taken out as if burnt. Returns (token in, token out, raw in with the 0.01% fee, raw out, ticks crossed)."""
    s, t = sq / Q, tsq / Q
    down = t < s                                            # token0 in, raw price falls
    tin, tout = (m.t0, m.t1) if down else (m.t1, m.t0)
    if tsq == sq: return tin, tout, 0, 0, 0
    ttick = math.floor(2 * math.log(t) / math.log(1.0001))
    adj, L = {}, liq
    for p in exclude:
        if not p['L']: continue
        adj[p['lo']] = adj.get(p['lo'], 0) - p['L']; adj[p['hi']] = adj.get(p['hi'], 0) + p['L']
        if p['lo'] <= tick < p['hi']: L -= p['L']
    a, b = (ttick + 1, tick) if down else (tick + 1, ttick)  # the ticks the price crosses on the way
    path = sorted(set(initialised(m.pool, a, b)) | {k for k in adj if a <= k <= b}, reverse=down)
    amt_in = amt_out = 0.0; cur = s
    for ti in path + [None]:
        nxt = t if ti is None else sqrt_at(ti)
        Lf = max(L, 0)
        if down: amt_in += Lf * (1 / nxt - 1 / cur); amt_out += Lf * (cur - nxt)
        else: amt_in += Lf * (nxt - cur); amt_out += Lf * (1 / cur - 1 / nxt)
        cur = nxt
        if ti is not None:
            net = int(call(m.pool, 'ticks(int24)(uint128,int128,uint256,uint256,int56,uint160,uint32,bool)', ti)[1]) + adj.get(ti, 0)
            L = L - net if down else L + net
    return tin, tout, int(amt_in / (1 - FEE / 1e6)) + 1, int(amt_out), len(path)


def quoter(tin, tout, tsq):
    """QuoterV2 on the pool as it is (our positions in place): the output up to the price limit, then the input for it."""
    sig_in = 'quoteExactInputSingle((address,address,uint256,uint24,uint160))(uint256,uint160,uint32,uint256)'
    sig_out = 'quoteExactOutputSingle((address,address,uint256,uint24,uint160))(uint256,uint160,uint32,uint256)'
    try:
        out = int(call(QUOTER, sig_in, '(%s,%s,%d,%d,%d)' % (tin, tout, 10 ** 30, FEE, tsq))[0])
        if out == 0: return 0
        return int(call(QUOTER, sig_out, '(%s,%s,%d,%d,%d)' % (tin, tout, out, FEE, tsq))[0])
    except subprocess.CalledProcessError:
        return None


def plan_repair(m, o, sq, tick, liq, burnt):
    """The repair a run would pay: the swap to the oracle after burning the positions in burnt."""
    tsq = m.sqrt_x96(o)
    tin, tout, amt, out, crossed = walk(m, sq, tick, liq, tsq, burnt)
    ttick = m.tick_of(o)
    lo, hi = min(tick, ttick), max(tick, ttick)
    on_path = any(p['L'] and p['lo'] <= hi and p['hi'] > lo for p in burnt)
    qa = quoter(tin, tout, tsq)
    cost = amt if on_path or qa is None else max(amt, qa)    # the quote is exact when our positions are not on the path
    return {'tin': tin, 'tout': tout, 'walk_in': amt, 'walk_out': out, 'crossed': crossed, 'quoter_in': qa, 'on_path': on_path,
            'in': cost, 'usd': m.value_usd(tin, cost, o), 'target_tick': ttick}


def print_plan(m, p):
    q = 'n/a' if p['quoter_in'] is None else m.human(p['tin'], p['quoter_in'])
    print('repair: %s in for about %s out, to tick %d (%d initialised ticks crossed; tick walk with our positions burnt first); '
          'QuoterV2 with them in place %s%s; cost used %s = $%.4f' % (
              m.human(p['tin'], p['walk_in']), m.human(p['tout'], p['walk_out']), p['target_tick'], p['crossed'], q,
              ' (our positions lie on the path, so the quote overstates)' if p['on_path'] else '', m.human(p['tin'], p['in']), p['usd']))


def guard(m, me, p, back, a):
    """back: raw amounts {token: raw} the burn returns to the wallet before the swap. Raises Refused."""
    if p['in'] == 0: return
    if p['tin'] == DUSDC.lower(): print('guard: dUSDC is minted, no budget check'); return
    if p['usd'] > a.max_cost_usdc:
        raise Refused('repair cost $%.4f (%s) above --max-cost-usdc %.2f; claim faucet USDC or raise the limit' % (
            p['usd'], m.human(p['tin'], p['in']), a.max_cost_usdc))
    have = bal(p['tin'], me) + back.get(p['tin'], 0)
    if p['tin'] == USDC.lower():
        left = (have - p['in']) / 1e6
        if left < a.reserve_usdc:
            raise Refused('repair cost %.6f USDC would leave %.6f USDC (wallet plus band burn), under --reserve-usdc %.2f' % (
                p['in'] / 1e6, left, a.reserve_usdc))
        print('guard: cost $%.4f <= %.2f, wallet USDC after %.6f >= reserve %.2f: pass' % (p['usd'], a.max_cost_usdc, left, a.reserve_usdc))
    else:
        if m.name == 'live' and have - p['in'] < RESERVE_WETH:
            raise Refused('repair needs %s; the wallet would keep %s, under %s (wrap ETH first)' % (
                m.human(WETH, p['in']), m.human(WETH, have - p['in']), m.human(WETH, RESERVE_WETH)))
        print('guard: cost $%.4f <= %.2f: pass' % (p['usd'], a.max_cost_usdc))


def swap_to_oracle(m, me, o, dry, reserve_usdc=RESERVE_USDC, plan=None):
    """Limit-priced exact-input swap that stops at the oracle price (U, step 2), sized from the tick walk + 2%.
    plan: (token in, token out, raw in) a dry run modelled with our burnt bands taken out, used instead of the walk."""
    tsq = m.sqrt_x96(o)
    if plan: tin, tout, need = plan
    else:
        _, _, sq, tick, liq = m.read()
        tin, tout, need, _, _ = walk(m, sq, tick, liq, tsq)
    need = int(need * 1.02) + 1
    if tin == DUSDC.lower():
        have = bal(DUSDC, me)
        if have < need: send('mint dUSDC for the swap', DUSDC, 'mint(address,uint256)', me, need - have, dry=dry)
        amt = need
    else:
        reserve = reserve_usdc if tin == USDC.lower() else RESERVE_WETH
        wrap = m.name == 'demo' and tin == WETH.lower()
        if wrap: wrap_if_short(me, need, dry); reserve = 0
        amt = need if wrap and dry else min(need, max(bal(tin, me) - reserve, 0))   # a dry run does not wrap
    print('swap %s -> %s: up to %s (tick walk + 2%%), limit at the oracle' % (tin, tout, m.human(tin, amt)))
    if amt <= 0: print('  nothing to swap with'); return
    b_in, b_out = (0, 0) if dry else (bal(tin, me), bal(tout, me))
    send('approve router', tin, 'approve(address,uint256)', ROUTER, amt, dry=dry)
    send('swap to oracle', ROUTER, 'exactInputSingle((address,address,uint24,address,uint256,uint256,uint160))',
         '(%s,%s,%d,%s,%d,0,%d)' % (tin, tout, FEE, me, amt, tsq), dry=dry)
    if not dry:
        spent, got = b_in - bal(tin, me), bal(tout, me) - b_out
        COSTS['swap_in'][tin] = COSTS['swap_in'].get(tin, 0) + spent; COSTS['swap_out'][tout] = COSTS['swap_out'].get(tout, 0) + got
        print('  spent %s, received %s' % (m.human(tin, spent), m.human(tout, got)))


def wrap_if_short(me, need, dry):
    have = bal(WETH, me)
    if have >= need: return
    eth = eth_bal(me)
    amt = need - have
    if dry: print('  dry-run: wrap %.6f ETH (wallet %.6f ETH, keeps %.6f)%s' % (amt / 1e18, eth / 1e18, ETH_RESERVE / 1e18,
                                                                         '' if eth - amt >= ETH_RESERVE else '; NOT ENOUGH ETH, a real run stops here'))
    if eth - amt < ETH_RESERVE and not dry: raise SystemExit('not enough ETH to wrap %d wei (have %d, keep %d)' % (amt, eth, ETH_RESERVE))
    send('wrap ETH', WETH, 'deposit()', value=amt, dry=dry)


def deadline():
    """Latest block time + 600 s: the chain's clock, not this machine's (an anvil fork may run ahead)."""
    return int(subprocess.run([CAST, 'block', 'latest', '-f', 'timestamp', '--rpc-url', RPC], capture_output=True, text=True, check=True).stdout.strip()) + 600


def burn(label, token_id, me, dry):
    L = position(token_id)['L']
    if L: send('decreaseLiquidity ' + label, NPM, 'decreaseLiquidity((uint256,uint128,uint256,uint256,uint256))', '(%d,%d,0,0,%d)' % (token_id, L, deadline()), dry=dry)
    send('collect ' + label, NPM, 'collect((uint256,address,uint128,uint128))', '(%d,%s,%d,%d)' % (token_id, me, MAXU128, MAXU128), dry=dry)


def mint(label, m, lo, hi, a0, a1, me, dry):
    """NonfungiblePositionManager.mint; amounts are token0/token1 desired. Returns the token id."""
    if a0: send('approve token0 to NPM', m.t0, 'approve(address,uint256)', NPM, a0, dry=dry)
    if a1: send('approve token1 to NPM', m.t1, 'approve(address,uint256)', NPM, a1, dry=dry)
    j = send('mint ' + label, NPM, 'mint((address,address,uint24,int24,int24,uint256,uint256,uint256,uint256,address,uint256))',
             '(%s,%s,%d,%d,%d,%d,%d,0,0,%s,%d)' % (m.t0, m.t1, FEE, lo, hi, a0, a1, me, deadline()), dry=dry)
    if not j: return None
    tid = [int(l['topics'][3], 16) for l in j['logs'] if l['address'].lower() == NPM.lower() and l['topics'][0].startswith('0xddf252ad')][0]
    print('  %s position %d, ticks [%d, %d)' % (label, tid, lo, hi))
    return tid


def ours(m, st):
    """Our positions on this market's pool, from the state file: [(label, token id, position)]."""
    out = []
    for k in (('band',) if m.name == 'live' else ('anchor', 'sell', 'buy')):
        v = st.get(k)
        if v: tid = v if isinstance(v, int) else v['token_id']; out.append((k, tid, position(tid)))
    return out


def back_amounts(m, mine, sq):
    back = {}
    for _, _, p in mine:
        a0, a1 = position_amounts(p, sq); back[m.t0] = back.get(m.t0, 0) + a0; back[m.t1] = back.get(m.t1, 0) + a1
    return back


def run_live(m, me, a):
    o, po, sq, tick, liq = m.read()
    dev = po / o - 1
    print('live: oracle $%.2f pool $%.2f deviation %+.3f%% tick %d L %.3e' % (m.usd(o), m.usd(po), 100 * dev, tick, liq))
    st = m.load().get('band')
    burn_band = bool(st) and (a.remint or abs(dev) > a.tolerance or not (st['lo'] <= tick < st['hi']))
    # the budget guard runs before anything is sent: the cost after burning our band, against wallet + what the burn returns
    if abs(dev) > a.tolerance:
        mine = ours(m, {'band': st}) if burn_band else []
        p = plan_repair(m, o, sq, tick, liq, [x[2] for x in mine]); print_plan(m, p)
        SUMMARY['repair_quote'] = m.human(p['tin'], p['in'])
        guard(m, me, p, back_amounts(m, mine, sq), a)
    # our band first: a repair swap through our own band buys our own liquidity back (27 Oct: 45.5 USDC with the band
    # between the pool and the oracle, about 8 without it). Burn it, repair against what remains, mint a new band at the oracle.
    if burn_band:
        burn('band', st['token_id'], me, a.dry_run); st = None; m.save({})
        o, po, sq, tick, liq = m.read(); print('band burned: pool $%.2f deviation %+.3f%% tick %d L %.3e' % (m.usd(po), 100 * (po / o - 1), tick, liq))
        SUMMARY['action'] = 'reband'
    # the amount comes from the in-range liquidity, which changes when the price crosses initialised ticks: repeat (max 3)
    for _ in range(3):
        if abs(po / o - 1) <= a.tolerance or a.dry_run: break
        p = plan_repair(m, o, sq, tick, liq, []); guard(m, me, p, {}, a)       # exact now that the band is burnt
        swap_to_oracle(m, me, o, a.dry_run, reserve_usdc=int(a.reserve_usdc * 1e6))
        o, po, sq, tick, liq = m.read(); print('after swap: pool $%.2f deviation %+.3f%%' % (m.usd(po), 100 * (po / o - 1)))
        SUMMARY['action'] = 'repair'
    if st is None:
        ot = m.tick_of(o); lo, hi = ot - LIVE_BAND, ot + LIVE_BAND
        keep = int((a.reserve_usdc + a.max_cost_usdc) * 1e6)          # the next repair must still be payable
        aw = max(bal(WETH, me) - RESERVE_WETH, 0); au = max(bal(USDC, me) - keep, 0)
        if a.band_usdc is not None: au = min(au, int(a.band_usdc * 1e6))   # cap (6 Nov: 10 USDC + matching WETH)
        v = min(aw / 1e18 * m.usd(o), au / 1e6); aw = int(v / m.usd(o) * 1e18); au = int(v * 1e6)
        print('mint +-1%% band [%d, %d) with %.6f WETH + %.6f USDC (keeps at least %.2f USDC, reserve + max cost, in the wallet; '
              'cap %s)' % (lo, hi, aw / 1e18, au / 1e6, keep / 1e6, 'none' if a.band_usdc is None else '%.2f USDC' % a.band_usdc))
        if aw > 0 and au > 0:
            tid = mint('band', m, lo, hi, au, aw, me, a.dry_run)       # USDC is token0 here
            if tid: m.save({'band': {'token_id': tid, 'lo': lo, 'hi': hi}})
        pre = SUMMARY['action'] + '+' if 'action' in SUMMARY else ''
        SUMMARY['action'] = pre + ('band' if aw > 0 and au > 0 else 'no-band(USDC_under_reserve+max_cost)')
    else:
        print('tick %d inside band [%d, %d), position %d kept' % (tick, st['lo'], st['hi'], st['token_id']))
        SUMMARY.setdefault('action', 'kept')


DEMO_STEP = 0.019                     # pool and oracle move together in steps under the agent's 2% gap screen


def mint_demo_bands(m, me, a, st):
    """Sell band (dUSDC only, the 2% below the pool's tick) and buy band (WETH only, the 1% above)."""
    _, po, sq, tick, liq = m.read()
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


def move_demo_price(m, me, a, st):
    """Move the demo pool and the MockOracle together: burn the bands, then per step of at most 1.9% swap the pool to
    the next price and set the oracle to it, so no block shows a gap above 1.9% (27 Oct: setting the oracle first left
    an unsettled pool for 37 blocks, and the agent's reduce ran out of gas against it). Before the last setPrice the
    bands are minted at the new pool price, so the sale the new price triggers meets them (results.md item 42)."""
    # a dry run burns nothing, so it walks each step with our bands taken out, as the real run meets the pool after the burn
    burnt = [position(st[side]['token_id']) for side in ('sell', 'buy') if side in st] if a.dry_run else []
    for side in ('sell', 'buy'):
        if side in st: burn(side, st[side]['token_id'], me, a.dry_run); del st[side]; m.save(st)
    target = int(round(a.set_price * 1e6)) * 10 ** 18
    step = a.max_step                       # DEMO_STEP unless --max-step is given (gap scene: one move)
    o = int(call(MOCK_ORACLE, 'price()(uint256)')[0])
    if a.dry_run: _, _, sq0, tick0, liq0 = m.read(); walked = 0; n = 0
    while o != target:
        up = target > o
        last = abs(target / o - 1) <= step
        nxt = target if last else int(o * (1 + step)) if up else int(o * (1 - step))
        plan = None
        if a.dry_run:                       # the walk from the pool as it is to this step, less the steps before
            tin, tout, cum, _, crossed = walk(m, sq0, tick0, liq0, m.sqrt_x96(nxt), burnt)
            plan = (tin, tout, cum - walked); walked = cum; n += 1; t = m.tick_of(nxt)
            print('step %d: pool and oracle to $%.2f (tick %d): swap about %s in (tick walk with our bands burnt, %d initialised ticks '
                  'from the start)%s' % (n, m.usd(nxt), t, m.human(tin, plan[2]), crossed,
                                         '; then bands sell [%d, %d) buy [%d, %d)' % (t - DEMO_SELL_TICKS, t, t + 1, t + 1 + DEMO_BUY_TICKS) if last else ''))
        swap_to_oracle(m, me, nxt, a.dry_run, plan=plan)                    # the pool first, to the next price
        if last: mint_demo_bands(m, me, a, st)                              # bands in place before the oracle moves
        send('MockOracle.setPrice $%.2f' % m.usd(nxt), MOCK_ORACLE, 'setPrice(uint256)', nxt, dry=a.dry_run)
        o = nxt
    SUMMARY['action'] = 'set-price-%.2f' % a.set_price


def run_demo(m, me, a):
    st = m.load()
    if a.set_price:
        move_demo_price(m, me, a, st)
        if a.dry_run: print('dry-run: a real run ends here with the bands just minted (within tolerance, bands kept)'); return
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
        print('within tolerance, bands kept'); SUMMARY.setdefault('action', 'kept'); return
    if abs(dev) > 1e-5:
        mine = [x for x in ours(m, st) if x[0] != 'anchor']
        p = plan_repair(m, o, sq, tick, liq, [x[2] for x in mine]); print_plan(m, p)
        SUMMARY['repair_quote'] = m.human(p['tin'], p['in'])
        guard(m, me, p, back_amounts(m, mine, sq), a)
    for side in ('sell', 'buy'):
        if side in st: burn(side, st[side]['token_id'], me, a.dry_run); del st[side]; m.save(st)
    o, po, sq, tick, liq = m.read()
    if abs(po / o - 1) > 1e-5:
        swap_to_oracle(m, me, o, a.dry_run)
        if not a.dry_run: o, po, sq, tick, liq = m.read()
    print('pool $%.4f at oracle $%.4f (deviation %+.4f%%), tick %d' % (m.usd(po), m.usd(o), 100 * (po / o - 1), tick))
    mint_demo_bands(m, me, a, st)
    SUMMARY.setdefault('action', 'repair+bands')


def owned(m, me):
    """Every NonfungiblePositionManager position the wallet holds on this market's pool (its token pair and fee),
    from the wallet's NFTs rather than the state file: [(token id, position)]."""
    out = []
    for i in range(int(call(NPM, 'balanceOf(address)(uint256)', me)[0])):
        tid = int(call(NPM, 'tokenOfOwnerByIndex(address,uint256)(uint256)', me, i)[0])
        p = call(NPM, 'positions(uint256)(uint96,address,address,address,uint24,int24,int24,uint128,uint256,uint256,uint128,uint128)', tid)
        if p[2].lower() == m.t0 and p[3].lower() == m.t1 and int(p[4]) == FEE:
            out.append((tid, {'lo': int(p[5]), 'hi': int(p[6]), 'L': int(p[7]), 'owed0': int(p[10]), 'owed1': int(p[11])}))
    return out


def close_bands(m, me, a):
    """--close-bands (3 Nov close-out): burn and collect every position of the wallet on this market's pool that holds
    liquidity or owed tokens, the state file's and any other, then clear the state file. No swap, no re-mint, no guard."""
    o, po, sq, tick, liq = m.read()
    print('%s: pool %s tick %d ($%.2f), oracle $%.2f, in-range L %.3e' % (m.name, m.pool, tick, m.usd(po), m.usd(o), liq))
    named = {tid: label for label, tid, _ in ours(m, m.load())}
    mine = owned(m, me)
    full = [(tid, p) for tid, p in mine if p['L'] or p['owed0'] or p['owed1']]
    print('wallet holds %d positions on this pool, %d with liquidity or owed tokens' % (len(mine), len(full)))
    for tid in named:
        if tid not in dict(mine): print('  state file names %s %d, which the wallet does not hold on this pool' % (named[tid], tid))
    for tid, p in full:
        a0, a1 = position_amounts(p, sq)
        print('close %s %d [%d, %d) L %d: a burn returns about %s + %s (plus fees since the last poke)' % (
            named.get(tid, 'position'), tid, p['lo'], p['hi'], p['L'], m.human(m.t0, a0), m.human(m.t1, a1)))
        burn('%s %d' % (named.get(tid, 'position'), tid), tid, me, a.dry_run)
    m.save({})
    if not a.dry_run:
        left = [tid for tid, p in owned(m, me) if p['L'] or p['owed0'] or p['owed1']]
        print('after: positions with liquidity or owed tokens on this pool: %s' % (left or 'none'))
        print('wallet %s: %.6f ETH, %.6f WETH, %.6f USDC, %.6f dUSDC' % (me, eth_bal(me) / 1e18, bal(WETH, me) / 1e18, bal(USDC, me) / 1e6, bal(DUSDC, me) / 1e6))
    SUMMARY['action'] = 'close-bands'


def status(m, me, a):
    """Reads only: nothing is sent and no state is written."""
    o, po, sq, tick, liq = m.read()
    dev = po / o - 1
    print('%s: pool %s, oracle %s' % (m.name, m.pool, m.oracle))
    print('pool tick %d ($%.2f), oracle $%.2f (tick %d), gap %+.3f%%, tolerance %.2f%%, in-range L %.3e' % (
        tick, m.usd(po), m.usd(o), m.tick_of(o), 100 * dev, 100 * a.tolerance, liq))
    mine = ours(m, m.load())
    for label, tid, p in mine:
        a0, a1 = position_amounts(p, sq)
        where = 'in range' if p['lo'] <= tick < p['hi'] else 'out of range'
        print('our %s: position %d [%d, %d) L %d, %s; a burn returns %s + %s' % (label, tid, p['lo'], p['hi'], p['L'], where, m.human(m.t0, a0), m.human(m.t1, a1)))
    if not mine: print('our positions: none in %s' % m.state_file)
    print('wallet %s: %.6f ETH, %.6f WETH, %.6f USDC, %.6f dUSDC' % (me, eth_bal(me) / 1e18, bal(WETH, me) / 1e18, bal(USDC, me) / 1e6, bal(DUSDC, me) / 1e6))
    if abs(dev) > a.tolerance:
        burnt = [x for x in mine if x[0] != 'anchor']
        p = plan_repair(m, o, sq, tick, liq, [x[2] for x in burnt]); print_plan(m, p)
        SUMMARY['repair_quote'] = m.human(p['tin'], p['in'])
        try: guard(m, me, p, back_amounts(m, burnt, sq), a); print('status: a run would repair')
        except Refused: print('status: a run would be refused')
    else:
        print('status: within tolerance; repair cost 0')
    SUMMARY['action'] = 'status'


def run_line(m, o, po, tick):
    c = COSTS
    swaps = ','.join(m.human(t, v).replace(' ', '') for t, v in c['swap_in'].items()) or '0'
    why = ('(%s)' % SUMMARY['why'].replace(' ', '_')) if 'why' in SUMMARY else ''
    return ('RUN %s market=%s tick=%d oracle=%.2f pool=%.2f gap=%+.3f%% action=%s%s repair_quote=%s swap_in=%s txs=%d '
            'fee_eth=%.9f l1_fee_wei=%d l1_bound_wei=%s' % (
                time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), m.name, tick, m.usd(o), m.usd(po), 100 * (po / o - 1),
                SUMMARY.get('action', '?'), why, SUMMARY.get('repair_quote', '0').replace(' ', ''), swaps, len(c['txs']),
                (c['l2_wei'] + c['l1_wei']) / 1e18, c['l1_wei'], SUMMARY.get('l1_bound_wei')))


def lock():
    """One sending run at a time: the timer and a manual run share the wallet's nonce."""
    os.makedirs(STATE_DIR, exist_ok=True)
    fd = os.open(STATE_DIR, os.O_RDONLY)
    try: fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError: os.close(fd); print('another rebalancer run holds the lock on %s' % STATE_DIR); raise SystemExit(4)
    return fd


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--market', choices=['live', 'demo'], required=True)
    ap.add_argument('--tolerance', type=float, default=0.0025)
    ap.add_argument('--max-cost-usdc', type=float, default=6.0, help='refuse a repair whose swap input is worth more (USD)')
    ap.add_argument('--reserve-usdc', type=float, default=8.0, help='refuse a repair that leaves less USDC in the wallet')
    ap.add_argument('--band-usdc', type=float, help='live only: at most this much USDC (plus WETH of equal value) in a new band; '
                    'default: all USDC above --reserve-usdc + --max-cost-usdc')
    ap.add_argument('--remint', action='store_true')
    ap.add_argument('--set-price', type=float, help='demo only: MockOracle price in USD per ETH, set first')
    ap.add_argument('--max-step', type=float, default=DEMO_STEP, help='with --set-price: largest move per step as a fraction '
                    '(default %(default)s, under the agent\'s 2%% gap screen); 1 allows any drop in one move (the gap scene)')
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--status', action='store_true', help='read only: print the state and the repair cost, send nothing')
    ap.add_argument('--close-bands', action='store_true', help='burn and collect all our positions on the pool, clear the state file')
    a = ap.parse_args(); _, me = key()
    if not 0 < a.max_step <= 1: raise SystemExit('--max-step must be in (0, 1], got %s' % a.max_step)
    if a.status: a.dry_run = True
    chain = int(subprocess.run([CAST, 'chain-id', '--rpc-url', RPC], capture_output=True, text=True, check=True).stdout.strip())
    if chain != CHAIN_ID: raise SystemExit('chain id %d is not Base Sepolia (%d): testnet only' % (chain, CHAIN_ID))
    m = Market(a.market, a.dry_run)
    print('rpc %s, deployments %s, wallet %s: %.6f ETH, %.6f WETH, %.6f USDC' % (
        RPC, os.path.abspath(DEPLOYMENTS), me, eth_bal(me) / 1e18, bal(WETH, me) / 1e18, bal(USDC, me) / 1e6))
    l1_fee()
    fd = None if a.dry_run else lock()
    before = m.snapshot()
    try:
        (status if a.status else close_bands if a.close_bands else run_live if a.market == 'live' else run_demo)(m, me, a)
        if a.dry_run and not a.status: SUMMARY['action'] = 'dry-run:' + SUMMARY.get('action', '?')
    except SystemExit as e:
        if not isinstance(e, Refused): SUMMARY['action'] = 'error'; SUMMARY['why'] = str(e.code).strip()[:160]
        else: SUMMARY['action'] = 'refused'
        raise
    except Exception as e:
        SUMMARY['action'] = 'error'; SUMMARY['why'] = repr(e)[:160]
        raise
    finally:
        try:
            o, po, sq, tick, liq = m.read(); print(run_line(m, o, po, tick))
        except Exception as e:                              # the RUN line must not hide the run's own error
            print('RUN %s market=%s action=%s read-failed %s' % (time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), m.name, SUMMARY.get('action'), e))
        if fd is not None: os.close(fd)
        if a.dry_run:
            if m.snapshot() != before: raise SystemExit('dry run changed %s' % m.state_file)
            print('dry-run: %s unchanged' % m.state_file)


if __name__ == '__main__':
    main()
