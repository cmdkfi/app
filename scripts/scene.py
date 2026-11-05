#!/usr/bin/env python3 -I
"""Crash scenes on the demo market (README step 10): orchestrator and recorder. Base Sepolia only (chain id 84532).

Three twins hold the same position on the demo market (0.002 WETH at 50% LTV) and live through the same price path:
  ai-agent     vault v1's Base address (near.vault_base_address in deployments/testnet.json): the live enclave agent
               acts on it; this script only observes it (the executor's Reduced events for its address).
  keeper       the test wallet's own executor position: this script applies the agent's protective rule to it each
               step (LTV above act, or a protective session still above target: reduce to target through the executor).
  unprotected  vault v2's Base address: nobody acts; the scripted liquidator liquidates it when Morpho allows.
After each step the scripted liquidator (scripts/liquidate.py) runs for every twin past Morpho's liquidation line.

  scene.py plan   --scene gradual-crash|gap|whipsaw --start-price USD [--step-wait-secs 180] [--hold-secs 600]
  scene.py run    --scene NAME [--dry-run] [--resume] [--step-wait-secs 180] [--hold-secs 600] [--out evidence/scenes]
                  [--ai 0x..] [--unprotected 0x..] [--keeper 0x..]
  scene.py reset  --to-price USD [--dry-run]       price back in steps of at most 1.9%, then the twins' state
  scene.py status                                  reads only: the twins' LTV, collateral, debt, cash; oracle and pool

Price moves: rebalance_pool.run_demo with --set-price (pool and MockOracle together, steps of at most 1.9%); the gap
scene's one large move passes max_step 1. Plan and data format: the planning repo's tasks/scenes-plan.md;
how to run a scene: scripts/SCENES.md. The key is read only by rebalance_pool.key() (never printed).
--dry-run reads only: it prints the plan, the projected keeper and liquidator actions and one step of the price
move in rebalance_pool's own dry-run, sends nothing and writes nothing.
Exit codes: 0 done; 1 a step failed after 3 retries (the files hold what was recorded; rerun with --resume);
2 refused before anything was sent (prerequisites, existing file).
"""
import argparse, copy, datetime, json, math, os, re, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import rebalance_pool as rp   # noqa: E402  (shared cast helpers, deployments loader, the set-price move)
import liquidate as lq        # noqa: E402  (Morpho share math, IRM accrual, LIF)

DEP = rp.DEP
with open(rp.DEPLOYMENTS) as _f: RAW = json.load(_f)
MORPHO, EXECUTOR, DEMO_ID = DEP['morpho'], DEP['executor'], DEP['demoMarketId'].lower()
WAD, ORACLE_SCALE = lq.WAD, lq.ORACLE_SCALE
TARGET_TOLERANCE = WAD // 1000          # CorridorMath.TARGET_TOLERANCE (agent/src/logic.rs TARGET_TOLERANCE)
SESSION_SECS = 86_400                   # Executor SESSION (logic.rs SESSION_SECS)
SLICE_MAX = 1_000_000_000               # vault v1's slice_max on demo (get_state, read 5 Nov): 1,000 dUSDC
RETRIES = 3; RETRY_PAUSE = 10
LOG_SPAN = 49_000                       # the public RPC answers eth_getLogs over at most 50,000 blocks
UNPROTECTED_DEFAULT = '0xfc85641356fe56b8851f715c3c81ad2b511a67cf'   # vault v2's Base address
TWINS = ('ai-agent', 'keeper', 'unprotected')
KIND = {'ai-agent': 'vault v1, live enclave agent',
        'keeper': "test wallet's own executor position; scripts/scene.py applies the agent's protective rule",
        'unprotected': 'vault v2, no agent (approves no measurement)'}
BY = {'ai-agent': 'agent', 'keeper': 'keeper', 'unprotected': 'unexpected caller'}
REDUCED_T0 = '0x04fa3a3c6cc1ec1dc4efb8381c2d7e0d0c847d25575a7a0028650d21897d72bf'    # Reduced(address,bytes32,uint256,uint256,uint256,uint256,bool)
LIQUIDATE_T0 = '0xa4946ede45d0c6f06a0f5ce92c9ad3b4751452d2fe0e25010783bcab57a67e41'  # Morpho Liquidate(bytes32,address,address,uint256,uint256,uint256,uint256,uint256)
CORRIDOR_SIG = 'corridors(address,bytes32)(uint64,uint64,uint64,uint64,bool,bool,bool)'
# legs per scene, as fractions of the start price (tasks/scenes-plan.md "Price paths")
SCENES = {'gradual-crash': [('to', 0.55), ('hold',)],
          'gap': [('to', 0.75), ('hold',), ('gap', 0.55), ('hold',)],
          'whipsaw': [('to', 0.68), ('hold',), ('to', 1.0), ('hold',)]}


# ------------------------------------------------------------------ pure: price path

def oracle_int(usd):
    """The MockOracle integer rebalance_pool.move_demo_price sets for a price in USD (1e36 scale, 6/18 decimals)."""
    return int(round(usd * 1e6)) * 10 ** 18


def fits(prev_usd, next_usd, max_step):
    """move_demo_price's own test: the move is one step when |target / oracle - 1| <= max_step."""
    return abs(oracle_int(next_usd) / oracle_int(prev_usd) - 1) <= max_step


def steps_to(start, end, max_step):
    """Prices (USD, cents) from start to end in the fewest equal-ratio steps that each pass `fits`."""
    if round(end, 2) == round(start, 2): return []
    limit = 1 + max_step if end > start else 1 - max_step
    n = 1 if limit <= 0 else max(1, math.ceil(math.log(end / start) / math.log(limit) - 1e-12))
    while True:
        ps = [round(start * (end / start) ** (k / n), 2) for k in range(1, n)] + [round(end, 2)]
        if all(fits(a, b, max_step) for a, b in zip([start] + ps, ps)): return ps
        n += 1
        if n > 1000: raise ValueError('no path from %.2f to %.2f in cent prices with steps of at most %s' % (start, end, max_step))


def plan_scene(scene, start, step_wait=180, hold=600, max_step=rp.DEMO_STEP):
    """Rows {step, kind (move|gap|hold), price, max_step, wait}; step 0 (the start) is not a row."""
    rows, price = [], start
    for leg in SCENES[scene]:
        if leg[0] == 'hold':
            rows.append({'kind': 'hold', 'price': round(price, 2), 'max_step': None, 'wait': hold})
        elif leg[0] == 'gap':
            end = round(start * leg[1], 2)
            rows.append({'kind': 'gap', 'price': end, 'max_step': 1.0, 'wait': step_wait}); price = end
        else:
            for p in steps_to(price, start * leg[1], max_step):
                rows.append({'kind': 'move', 'price': p, 'max_step': max_step, 'wait': step_wait})
            price = start * leg[1]
    for i, r in enumerate(rows): r['step'] = i + 1
    return rows


def crossings(rows, start, ltv0=0.5, lines=(0.70, 0.86)):
    """First step at which a position opened at ltv0 with no action crosses each line (debt fixed, price moves)."""
    out = {}
    for line in lines:
        out[line] = next((r['step'] for r in rows if ltv0 * start / r['price'] > line), None)
    return out


# ------------------------------------------------------------------ pure: position math (Morpho, CorridorMath, the agent)

def accrue(tba, rate, dt):
    """Morpho _accrueInterest on totalBorrowAssets (fee 0 on the demo market), as liquidate.read and ltv.sh do."""
    return tba + lq.w_mul_down(tba, lq.w_taylor(rate, dt)) if rate and dt > 0 and tba else tba


def ltv_wad(debt, value):
    """CorridorMath.ltv: rounded up; None when there is debt and the collateral is worth 0."""
    if debt == 0: return 0
    if value == 0: return None
    return (debt * WAD + value - 1) // value


def numbers(shares, coll, tba, tbs, price, lltv):
    """Debt (SharesMathLib.toAssetsUp), value, LTV and Morpho's liquidation test (debt > value x LLTV)."""
    debt = lq.to_assets_up(shares, tba, tbs)
    value = lq.mul_div_down(coll, price, ORACLE_SCALE)
    return {'debt': debt, 'value': value, 'ltv': ltv_wad(debt, value), 'liquidatable': debt > lq.w_mul_down(value, lltv)}


def repay_to_ltv(debt, value, t):
    """CorridorMath.repayToLtv = agent/src/logic.rs amount_to: (D - tV) / (1 - t), 0 if at or below t, capped at D."""
    if t >= WAD: return 0
    dw, tv = debt * WAD, t * value
    if dw <= tv: return 0
    return min((dw - tv) // (WAD - t), debt)


def keeper_decision(debt, value, corridor, session_start, now, slice_max=SLICE_MAX):
    """The agent's protective rule without the model: agent/src/logic.rs `protective` (LTV above act, or a live
    session with LTV more than TARGET_TOLERANCE above target) and the protective branch of `decide_market`
    (reduce(amount_to(target)), clamped to the slice maximum and the debt). Returns (amount, reason)."""
    if not corridor.get('set'): return 0, 'no corridor in force'
    if debt == 0: return 0, 'no debt'
    if value == 0: return 0, 'collateral worth 0 at the oracle: nothing to sell'
    ltv = ltv_wad(debt, value)
    live = session_start != 0 and now < session_start + SESSION_SECS
    if ltv > corridor['act']: why = 'LTV %.4f above act %.2f' % (ltv / 1e18, corridor['act'] / 1e18)
    elif live and ltv > corridor['target'] + TARGET_TOLERANCE:
        why = 'protective session (started %d) with LTV %.4f above target %.2f' % (session_start, ltv / 1e18, corridor['target'] / 1e18)
    else: return 0, 'LTV %.4f: not above act %.2f, no live session above target' % (ltv / 1e18, corridor['act'] / 1e18)
    amt = min(repay_to_ltv(debt, value, corridor['target']), slice_max, debt)
    return amt, why + ': reduce to target'


def lif_of(lltv):
    """Morpho's liquidation incentive factor, as liquidate.py computes it (1.0438 at LLTV 86%)."""
    return min(lq.MAX_LIF, lq.w_div_down(WAD, WAD - lq.w_mul_down(lq.LCF, WAD - lltv)))


def project(state, rows, corridor, lltv):
    """Dry-run projection without interest: the keeper rule on 'keeper' (sold at the oracle; the executor's fill
    may be up to 1% worse), then a liquidation of 'keeper' and 'unprotected' when Morpho would allow one (repay all,
    seize debt x LIF / price, or all collateral). On a gap row the liquidation comes first, as in the run (a real
    liquidator acts in the block after setPrice). The AI twin is not projected (its actions are the agent's).
    state: {twin: {'coll', 'debt'}}. Returns one dict per row: {step, price, twins: {name: (ltv, [events])}}."""
    st = copy.deepcopy(state); session = 0; t = 1; out = []; lif = lif_of(lltv)
    for r in rows:
        o = oracle_int(r['price']); t += r['wait'] or 0
        row = {'step': r['step'], 'price': r['price'], 'twins': {}}
        for name in ('keeper', 'unprotected'):
            c, d = st[name]['coll'], st[name]['debt']; ev = []

            def liq(c, d):
                v = lq.mul_div_down(c, o, ORACLE_SCALE)
                if d and d > lq.w_mul_down(v, lltv):
                    seize = min(c, lq.mul_div_down(lq.w_mul_down(d, lif), ORACLE_SCALE, o))
                    ev.append(('liquidation', d, seize)); return c - seize, 0
                return c, d
            if r['kind'] == 'gap': c, d = liq(c, d)
            if name == 'keeper':
                amt, _ = keeper_decision(d, lq.mul_div_down(c, o, ORACLE_SCALE), corridor, session, t)
                if amt:
                    if session == 0 or t >= session + SESSION_SECS: session = t
                    sold = lq.mul_div_up(amt, ORACLE_SCALE, o); c -= sold; d -= amt; ev.append(('reduce', amt, sold))
            c, d = liq(c, d)
            st[name] = {'coll': c, 'debt': d}
            row['twins'][name] = (ltv_wad(d, lq.mul_div_down(c, o, ORACLE_SCALE)), ev)
        out.append(row)
    return out


def outcome_row(coll, debt, cash, price, liquidated):
    """The plan's outcome: collateral kept, debt and cash (loan token) in raw units; net value = collateral at the
    end price + cash - debt, in USD (dUSDC counted at 1 USD)."""
    net = (lq.mul_div_down(coll, price, ORACLE_SCALE) + cash - debt) / 1e6
    return {'collateral_kept_wei': str(coll), 'debt_units': str(debt), 'cash_units': str(cash),
            'net_value_usd_at_end': round(net, 6), 'liquidated': bool(liquidated)}


# ------------------------------------------------------------------ pure: events, liquidator output

def _words(data):
    h = data[2:]; return [int(h[i:i + 64], 16) for i in range(0, len(h), 64)]


def decode_reduced(log):
    w = _words(log['data'])
    return {'caller': '0x' + log['topics'][1][-40:], 'market': log['topics'][2].lower(), 'block': int(log['blockNumber'], 16),
            'log_index': int(log['logIndex'], 16), 'tx': log['transactionHash'], 'repaid': w[0], 'sold': w[1],
            'ltv_before': w[2], 'ltv_after': w[3], 'protective': bool(w[4])}


def decode_liquidate(log):
    w = _words(log['data'])
    return {'market': log['topics'][1].lower(), 'caller': '0x' + log['topics'][2][-40:], 'borrower': '0x' + log['topics'][3][-40:],
            'block': int(log['blockNumber'], 16), 'log_index': int(log['logIndex'], 16), 'tx': log['transactionHash'],
            'repaid': w[0], 'repaid_shares': w[1], 'seized': w[2], 'bad_debt': w[3]}


def actions_of(name, addr, reduced, liquidations, liquidator):
    """One twin's actions in the plan's shape, from decoded Reduced and Liquidate events, in chain order."""
    a = addr.lower(); out = []
    for e in reduced:
        if e['caller'].lower() == a and e['market'] == DEMO_ID:
            out.append(((e['block'], e['log_index']), {'block': e['block'], 'kind': 'reduce', 'tx': e['tx'], 'repaid_units': str(e['repaid']),
                                                       'sold_wei': str(e['sold']), 'by': BY[name], 'protective': e['protective']}))
    for e in liquidations:
        if e['borrower'].lower() == a and e['market'] == DEMO_ID:
            by = 'liquidator' if e['caller'].lower() == liquidator.lower() else 'other liquidator %s' % e['caller']
            out.append(((e['block'], e['log_index']), {'block': e['block'], 'kind': 'liquidation', 'tx': e['tx'], 'repaid_units': str(e['repaid']),
                                                       'sold_wei': str(e['seized']), 'by': by, 'bad_debt_units': str(e['bad_debt'])}))
    return [x for _, x in sorted(out, key=lambda p: p[0])]


def parse_liquidator(stdout):
    """liquidate.py's output: the Morpho.liquidate hash and every transaction it sent (its 'transactions:' line)."""
    m = re.search(r'Morpho\.liquidate: tx (0x[0-9a-fA-F]{64})', stdout)
    t = re.search(r'^transactions:(.*)$', stdout, re.M)
    return (m.group(1) if m else None), (t.group(1).split() if t else [])


# ------------------------------------------------------------------ pure: Markdown

def _pct(x): return 'n/a' if x is None else '%.2f %%' % (100 * x)
def _weth(w): return '%.9f' % (int(w) / 1e18)
def _usdc(u): return '%.6f' % (int(u) / 1e6)


def render_md(rec):
    L = []; p = L.append
    start = rec['start_price_usd']
    p('# Scene `%s` on the demo market (Base Sepolia)' % rec['scene']); p('')
    p('Started at block %d (%s), start price %.2f USD (MockOracle %s). Liquidator %s. Data: `%s.json`; written by '
      '`scripts/scene.py`.' % (rec['started_block'], rec['started_utc'], start, DEP['mockOracle'], rec['liquidator'], rec['scene']))
    if not all(rec['twins'][n].get('outcome') for n in TWINS): p(''); p('**Incomplete:** the scene has not reached its end (see Notes).')
    p(''); p('## Twins'); p('')
    p('| twin | address | kind |'); p('|---|---|---|')
    for n in TWINS: p('| %s | `%s` | %s |' % (n, rec['twins'][n]['address'], rec['twins'][n]['kind']))
    p(''); p('## Price path'); p('')
    p('| step | block | UTC | oracle price (USD) | from start | transactions |'); p('|---:|---:|---|---:|---:|---:|')
    for s in rec['price_path']:
        p('| %d | %d | %s | %.2f | %+.2f %% | %d |' % (s['step'], s['block'], s['utc'], s['price_usd'], 100 * (s['price_usd'] / start - 1), len(s['tx'])))
    p(''); p('## LTV (block x twin)'); p('')
    p('| block | oracle price (USD) | %s |' % ' | '.join(TWINS)); p('|---:|---:|%s' % '---:|' * len(TWINS))
    rows = {}
    for n in TWINS:
        for r in rec['twins'][n]['ltv']: rows.setdefault(r['block'], {})[n] = r
    for b in sorted(rows):
        price = next(iter(rows[b].values())).get('price_usd')
        p('| %d | %s | %s |' % (b, '%.2f' % price if price is not None else '', ' | '.join(_pct(rows[b][n]['ltv']) if n in rows[b] else '' for n in TWINS)))
    p(''); p('## Actions'); p('')
    acts = sorted(((a['block'], n, a) for n in TWINS for a in rec['twins'][n]['actions']), key=lambda x: x[0])
    if acts:
        p('| block | twin | kind | by | repaid (dUSDC) | collateral sold or seized (WETH) | tx |'); p('|---:|---|---|---|---:|---:|---|')
        for b, n, a in acts: p('| %d | %s | %s | %s | %s | %s | `%s` |' % (b, n, a['kind'], a['by'], _usdc(a['repaid_units']), _weth(a['sold_wei']), a['tx']))
    else: p('None recorded.')
    p(''); p('## Outcome'); p('')
    p('| twin | collateral kept (WETH) | debt (dUSDC) | cash (dUSDC) | net value at the end price (USD) | liquidated |'); p('|---|---:|---:|---:|---:|---|')
    for n in TWINS:
        o = rec['twins'][n].get('outcome')
        if o: p('| %s | %s | %s | %s | %.6f | %s |' % (n, _weth(o['collateral_kept_wei']), _usdc(o['debt_units']), _usdc(o['cash_units']),
                                                     o['net_value_usd_at_end'], 'yes' if o['liquidated'] else 'no'))
        else: p('| %s | | | | | |' % n)
    p(''); p('Net value = collateral x the end oracle price + cash - debt; dUSDC counted at 1 USD.')
    p(''); p('## Transactions'); p('')
    for s in rec['price_path']:
        if s['tx']: p('- step %d price move: %s' % (s['step'], ', '.join('`%s`' % h for h in s['tx'])))
    for b, n, a in acts: p('- block %d %s %s (%s): `%s`' % (b, n, a['kind'], a['by'], a['tx']))
    if rec['notes']:
        p(''); p('## Notes'); p('')
        for line in rec['notes'].split('\n'): p('- ' + line)
    return '\n'.join(L) + '\n'


# ------------------------------------------------------------------ chain reads (pinned to one block)

def cast_out(*args):
    return subprocess.run([rp.CAST, *map(str, args), '--rpc-url', rp.RPC], capture_output=True, text=True, check=True).stdout


def cc(block, to, sig, *args):
    """cast call at a block; the first word of each output line, as rebalance_pool.call."""
    out = cast_out('call', to, sig, *args, '--block', block)
    return [l.split()[0] for l in out.strip().split('\n')]


def check_chain():
    chain = int(cast_out('chain-id').strip())
    if chain != rp.CHAIN_ID: raise SystemExit('chain id %d is not Base Sepolia (%d): testnet only' % (chain, rp.CHAIN_ID))


def utc(ts): return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')


_MP = {}


def market_params():
    if not _MP:
        p = rp.call(MORPHO, 'idToMarketParams(bytes32)(address,address,address,address,uint256)', DEMO_ID)
        _MP.update({'loan': p[0], 'coll': p[1], 'oracle': p[2], 'irm': p[3], 'lltv': int(p[4])})
    return _MP


def corridor_of(addr, block='latest'):
    c = cc(block, EXECUTOR, CORRIDOR_SIG, addr, DEMO_ID)
    return {'target': int(c[0]), 'act': int(c[1]), 'floor': int(c[2]), 'sell_budget': int(c[3]),
            'migrate_allowed': c[4] == 'true', 'exit_allowed': c[5] == 'true', 'set': c[6] == 'true'}


def observe(tw):
    """Every twin at one block: collateral, debt accrued to the block, value, LTV, Morpho's liquidation test, cash
    (loan token at the address); the oracle price; the keeper's sessionStart."""
    blk, ts = (int(x) for x in cast_out('block', 'latest', '-f', 'number,timestamp').split())
    mp = market_params()
    mk = [int(x) for x in cc(blk, MORPHO, 'market(bytes32)%s' % lq.MARKET_T, DEMO_ID)]
    price = int(cc(blk, mp['oracle'], 'price()(uint256)')[0])
    tba = mk[2]
    if int(mp['irm'], 16) and ts > mk[4] and tba:
        rate = int(cc(blk, mp['irm'], 'borrowRateView(%s,%s)(uint256)' % (lq.MP_T, lq.MARKET_T),
                      '(%s,%s,%s,%s,%d)' % (mp['loan'], mp['coll'], mp['oracle'], mp['irm'], mp['lltv']), '(%s)' % ','.join(map(str, mk)))[0])
        tba = accrue(tba, rate, ts - mk[4])
    o = {'block': blk, 'ts': ts, 'utc': utc(ts), 'price': price, 'twins': {}}
    for n, addr in tw.items():
        pos = cc(blk, MORPHO, 'position(bytes32,address)(uint256,uint128,uint128)', DEMO_ID, addr)
        x = numbers(int(pos[1]), int(pos[2]), tba, mk[3], price, mp['lltv'])
        x.update({'coll': int(pos[2]), 'shares': int(pos[1]), 'cash': int(cc(blk, mp['loan'], 'balanceOf(address)(uint256)', addr)[0])})
        o['twins'][n] = x
    o['session'] = int(cc(blk, EXECUTOR, 'sessionStart(address,bytes32)(uint256)', tw['keeper'], DEMO_ID)[0])
    return o


def logs(addr, topic0, frm, to, *topics):
    out, a = [], frm
    while a <= to:
        b = min(a + LOG_SPAN - 1, to)
        out += json.loads(cast_out('logs', '--json', '--from-block', a, '--to-block', b, '--address', addr, topic0, *topics))
        a = b + 1
    return out


def scan(tw, frm, liquidator):
    """Every twin's reduces and liquidations on the demo market since block frm, from the chain's events."""
    to = int(cast_out('block-number').strip())
    reduced = [decode_reduced(l) for a in tw.values() for l in logs(EXECUTOR, REDUCED_T0, frm, to, '0x' + a[2:].lower().rjust(64, '0'))]
    liq = [decode_liquidate(l) for l in logs(MORPHO, LIQUIDATE_T0, frm, to, DEMO_ID)]
    return {n: actions_of(n, a, reduced, liq, liquidator) for n, a in tw.items()}


def print_obs(o, tw, corridors=None):
    mp = market_params()
    print('block %d (%s): oracle %.2f USD, LLTV %.2f, liquidation incentive %.4f' % (o['block'], o['utc'], o['price'] / 1e24, mp['lltv'] / 1e18, lif_of(mp['lltv']) / 1e18))
    print('  %-12s %-42s %14s %12s %10s %12s %s' % ('twin', 'address', 'collateral', 'debt', 'LTV', 'cash', 'liquidatable'))
    for n in TWINS:
        x = o['twins'][n]
        cash = '%.6f' % (x['cash'] / 1e6) + ('*' if n == 'keeper' else '')
        print('  %-12s %-42s %14s %12s %10s %12s %s' % (n, tw[n], _weth(x['coll']), _usdc(x['debt']), _pct(None if x['ltv'] is None else x['ltv'] / 1e18), cash,
                                                    'yes' if x['liquidatable'] else 'no'))
    print('  * the keeper is the test wallet, whose dUSDC also funds the pool bands and the liquidator: its balance is not the twin\'s cash')
    print('  keeper sessionStart %d%s' % (o['session'], ' (none)' if o['session'] == 0 else ' (%s)' % utc(o['session'])))
    for n, c in (corridors or {}).items():
        print('  corridor %-12s target %.2f act %.2f floor %.2f sell budget %.2f set %s' % (n, c['target'] / 1e18, c['act'] / 1e18, c['floor'] / 1e18, c['sell_budget'] / 1e18, c['set']))


# ------------------------------------------------------------------ actions (send only when not dry)

def move_price(usd, max_step, dry):
    """rebalance_pool's --set-price move (run_demo), in process; returns the hashes it sent."""
    _, me = rp.key()
    a = argparse.Namespace(market='demo', set_price=usd, max_step=max_step, dry_run=dry, tolerance=0.0025, remint=False,
                           max_cost_usdc=6.0, reserve_usdc=8.0, band_usdc=None, status=False, close_bands=False)
    n0 = len(rp.COSTS['txs'])
    rp.run_demo(rp.Market('demo', dry), me, a)
    return rp.COSTS['txs'][n0:]


def keeper_act(tw, corridor, dry, note):
    """Read the keeper afresh, apply the rule, send the reduce. A revert is recorded, not retried."""
    o = observe({'keeper': tw['keeper']})
    x = o['twins']['keeper']
    amt, why = keeper_decision(x['debt'], x['value'], corridor, o['session'], o['ts'])
    print('keeper at block %d: debt %s, value %s, LTV %s; %s; amount %s dUSDC' % (o['block'], _usdc(x['debt']), _usdc(x['value']),
                                                                              _pct(None if x['ltv'] is None else x['ltv'] / 1e18), why, _usdc(amt)))
    if not amt: return None
    if dry: print('  dry-run: would send reduce(bytes32,uint256,uint256) %s %d <block time + 600> to %s' % (DEMO_ID, amt, EXECUTOR)); return None
    n0 = len(rp.COSTS['txs'])
    try:
        return rp.send('keeper reduce', EXECUTOR, 'reduce(bytes32,uint256,uint256)', DEMO_ID, amt, rp.deadline())
    except SystemExit as e:
        msg = str(e.code).strip()
        if 'revert' not in msg.lower(): raise
        note('keeper reduce of %s dUSDC at block %d refused: %s%s' % (_usdc(amt), o['block'], msg[-200:],
                                                                    ' (tx %s)' % rp.COSTS['txs'][-1] if len(rp.COSTS['txs']) > n0 else ''))
        return None


def liquidate(addr, dry, note):
    cmd = [sys.executable, '-I', os.path.join(HERE, 'liquidate.py'), '--market', 'demo', '--borrower', addr] + (['--dry-run'] if dry else [])
    r = subprocess.run(cmd, capture_output=True, text=True)
    for line in (r.stdout + r.stderr).strip().split('\n'): print('  | ' + line)
    if r.returncode == 3: note('liquidator could not fund a repayment for %s (liquidate.py exit 3)' % addr); return None
    if r.returncode != 0: raise SystemExit('liquidate.py exit %d: %s' % (r.returncode, (r.stderr or r.stdout).strip()[-300:]))
    return parse_liquidator(r.stdout)


class StepFailed(Exception):
    pass


def retry(label, fn):
    for i in range(RETRIES + 1):
        try: return fn()
        except (subprocess.CalledProcessError, SystemExit, OSError, ValueError, KeyError, IndexError) as e:
            msg = (e.stderr.strip() if isinstance(e, subprocess.CalledProcessError) and e.stderr else str(getattr(e, 'code', e))).strip()[-300:]
            print('%s failed (attempt %d of %d): %s' % (label, i + 1, RETRIES + 1, msg))
            if i == RETRIES: raise StepFailed('%s: %s' % (label, msg))
            time.sleep(RETRY_PAUSE)


# ------------------------------------------------------------------ the recorder

def new_record(scene, tw, o, liquidator):
    rec = {'scene': scene, 'market': 'demo', 'started_block': o['block'], 'started_utc': o['utc'], 'start_price_usd': round(o['price'] / 1e24, 6),
           'price_path': [], 'twins': {n: {'address': tw[n], 'kind': KIND[n], 'ltv': [], 'actions': [], 'outcome': {}} for n in TWINS},
           'liquidator': liquidator, 'notes': ''}
    return rec


def add_note(rec, text):
    print('NOTE', text); rec['notes'] = (rec['notes'] + '\n' if rec['notes'] else '') + text


def record_obs(rec, o):
    for n in TWINS:
        x = o['twins'][n]
        row = {'block': o['block'], 'ltv': None if x['ltv'] is None else round(x['ltv'] / 1e18, 6), 'collateral_wei': str(x['coll']),
               'debt_units': str(x['debt']), 'price_usd': round(o['price'] / 1e24, 6)}
        rows = [r for r in rec['twins'][n]['ltv'] if r['block'] != o['block']]
        rec['twins'][n]['ltv'] = sorted(rows + [row], key=lambda r: r['block'])


def path_entry(step, o, txs):
    return {'step': step, 'block': o['block'], 'utc': o['utc'], 'price_usd': round(o['price'] / 1e24, 6), 'tx': list(txs)}


def write(rec, out):
    os.makedirs(out, exist_ok=True)
    j = os.path.join(out, rec['scene'] + '.json')
    with open(j + '.tmp', 'w') as f: json.dump(rec, f, indent=1); f.write('\n')
    os.replace(j + '.tmp', j)
    with open(os.path.join(out, rec['scene'] + '.md'), 'w') as f: f.write(render_md(rec))


def finish(rec, o):
    for n in TWINS:
        x = o['twins'][n]; t = rec['twins'][n]
        # the keeper's address is the test wallet, whose dUSDC is shared with the bands and the liquidator: its cash is
        # the dUSDC its open borrowed (debt at the start); a reduce repays from sold collateral and leaves cash alone
        cash = int(t['ltv'][0]['debt_units']) if n == 'keeper' else x['cash']
        t['outcome'] = outcome_row(x['coll'], x['debt'], cash, o['price'], any(a['kind'] == 'liquidation' for a in t['actions']))


def addresses(a):
    _, me = rp.key()
    return {'ai-agent': a.ai or RAW['near']['vault_base_address'], 'keeper': a.keeper or me, 'unprotected': a.unprotected}, me


def prerequisites(o, corridors, positions=True):
    """Reasons a run refuses: a twin without an open position (not checked on --resume: a twin may be liquidated by
    then), no keeper corridor, or a keeper corridor other than the AI twin's."""
    bad = []
    for n in TWINS if positions else ():
        x = o['twins'][n]
        if x['coll'] == 0 or x['debt'] == 0: bad.append('%s has no open position on demo (collateral %d, debt %d)' % (n, x['coll'], x['debt']))
    k, ai = corridors['keeper'], corridors['ai-agent']
    if not k['set']: bad.append("the keeper (test wallet) has no corridor on demo")
    elif [k[f] for f in ('target', 'act', 'floor', 'sell_budget')] != [ai[f] for f in ('target', 'act', 'floor', 'sell_budget')]:
        bad.append("the keeper's corridor differs from the AI twin's")
    return bad


IMMEDIATE = 'immediately after the gap move'


def liquidation_pass(o, tw, note, immediate=False):
    """liquidate.py for every twin past Morpho's line in reading o; True if a liquidation was sent. With immediate (the
    gap row), each liquidation is noted 'immediately after the gap move' with its hash (mark_immediate reads it)."""
    sent = False
    for n in TWINS:
        if not o['twins'][n]['liquidatable']: continue
        print('%s is past the liquidation line at block %d: liquidate.py --market demo --borrower %s' % (n, o['block'], tw[n]))
        res = retry('liquidate ' + n, lambda: liquidate(tw[n], False, note))
        if res and res[0]:
            sent = True
            if immediate: note('%s liquidated %s, tx %s (reading at block %d)' % (n, IMMEDIATE, res[0], o['block']))
    return sent


def mark_immediate(acts, notes):
    """Add note 'immediately after the gap move' to the liquidations whose hash the notes name that way."""
    txs = {t.lower() for t in re.findall(r'%s, tx (0x[0-9a-fA-F]{64})' % IMMEDIATE, notes)}
    for lst in acts.values():
        for x in lst:
            if x['kind'] == 'liquidation' and x['tx'].lower() in txs: x['note'] = IMMEDIATE
    return acts


def run(a):
    check_chain()
    tw, me = addresses(a)
    path = os.path.join(a.out, a.scene + '.json')
    corridors = {n: corridor_of(tw[n]) for n in ('ai-agent', 'keeper')}
    o = retry('observe', lambda: observe(tw))
    print('scene %s: rpc %s, executor %s, demo market %s, liquidator %s' % (a.scene, rp.RPC, EXECUTOR, DEMO_ID, me))
    print_obs(o, tw, corridors)
    bad = prerequisites(o, corridors, positions=not a.resume)
    if a.dry_run: return dry_run(a, tw, o, corridors, bad)
    if bad: print('REFUSED, nothing sent:\n  ' + '\n  '.join(bad)); raise SystemExit(2)
    if a.resume:
        if not os.path.exists(path): print('REFUSED: --resume but %s does not exist' % path); raise SystemExit(2)
        with open(path) as f: rec = json.load(f)
        if rec['scene'] != a.scene or all(rec['twins'][n].get('outcome') for n in TWINS):
            print('REFUSED: %s is another scene or already complete' % path); raise SystemExit(2)
        if any(rec['twins'][n]['address'].lower() != tw[n].lower() for n in TWINS):
            print('REFUSED: the twin addresses differ from those in %s' % path); raise SystemExit(2)
        add_note(rec, 'resumed at block %d (%s) after step %d' % (o['block'], o['utc'], rec['price_path'][-1]['step']))
    else:
        if os.path.exists(path): print('REFUSED: %s exists (evidence is not overwritten); move it or pass --resume' % path); raise SystemExit(2)
        rec = new_record(a.scene, tw, o, me)
        record_obs(rec, o); rec['price_path'].append(path_entry(0, o, []))
        for n in ('ai-agent', 'unprotected'):
            add_note(rec, '%s holds %s dUSDC at the start (its cash; the open borrowed %s)' % (n, _usdc(o['twins'][n]['cash']), _usdc(o['twins'][n]['debt'])))
        add_note(rec, "keeper cash = the dUSDC its open borrowed (debt at the start, %s): the test wallet's dUSDC balance also "
                      "holds the pool bands' and the liquidator's dUSDC" % _usdc(o['twins']['keeper']['debt']))
        c = corridors['keeper']
        add_note(rec, 'corridor of the AI twin and the keeper: target %.2f, act %.2f, floor %.2f, sell budget %.2f' % (
            c['target'] / 1e18, c['act'] / 1e18, c['floor'] / 1e18, c['sell_budget'] / 1e18))
        write(rec, a.out)
    rows = plan_scene(a.scene, rec['start_price_usd'], a.step_wait_secs, a.hold_secs)
    done = rec['price_path'][-1]['step']
    fd = rp.lock()                                           # the Mac's rebalancer lock; pause the container's timer too (SCENES.md)
    try:
        for r in rows[done:]:
            k = r['step']; n0 = len(rp.COSTS['txs'])
            try:
                print('== step %d of %d: %s to %.2f USD (%+.2f %% from start)' % (k, len(rows), r['kind'], r['price'], 100 * (r['price'] / rec['start_price_usd'] - 1)))
                if r['kind'] != 'hold':
                    cur = int(retry('read oracle', lambda: rp.call(DEP['mockOracle'], 'price()(uint256)'))[0])
                    if cur != oracle_int(r['price']): retry('price move', lambda: move_price(r['price'], r['max_step'], False))
                    else: print('oracle already at %.2f: no move' % r['price'])
                move_txs = rp.COSTS['txs'][n0:]                  # every attempt's, before the keeper sends anything
                step_note = lambda t: add_note(rec, 'step %d: %s' % (k, t))
                if r['kind'] == 'gap':
                    # a real liquidator acts in the block after setPrice: the liquidator pass first, no wait, no keeper
                    o = retry('observe', lambda: observe(tw)); record_obs(rec, o); print_obs(o, tw)
                    print('gap move: the liquidator pass at once, before the wait and the keeper')
                    liquidation_pass(o, tw, step_note, immediate=True)
                if r['wait']: print('waiting %d s (until %s)' % (r['wait'], utc(time.time() + r['wait']))); time.sleep(r['wait'])
                o = retry('observe', lambda: observe(tw)); record_obs(rec, o); print_obs(o, tw)
                acted = retry('keeper', lambda: keeper_act(tw, corridors['keeper'], False, step_note))
                acted = liquidation_pass(o, tw, step_note) or acted
                if acted: o = retry('observe', lambda: observe(tw)); record_obs(rec, o); print_obs(o, tw)
                acts = retry('scan events', lambda: scan(tw, rec['started_block'], me))
                mark_immediate(acts, rec['notes'])
                for n in TWINS: rec['twins'][n]['actions'] = acts[n]
                rec['price_path'].append(path_entry(k, o, move_txs))
                if k == len(rows): finish(rec, o)
                write(rec, a.out)
            except (StepFailed, KeyboardInterrupt) as e:
                sent = rp.COSTS['txs'][n0:]
                add_note(rec, 'step %d failed (%s); transactions sent in that step: %s; rerun with --resume' % (
                    k, 'interrupted' if isinstance(e, KeyboardInterrupt) else e, ' '.join(sent) or 'none'))
                write(rec, a.out)
                print('FAILED at step %d; %s and .md hold what was recorded' % (k, path)); raise SystemExit(1)
    finally:
        os.close(fd)
    print('scene %s done: %s, %s' % (a.scene, path, path[:-5] + '.md'))
    for n in TWINS: print('  %-12s %s' % (n, json.dumps(rec['twins'][n]['outcome'])))


def dry_run(a, tw, o, corridors, bad):
    for b in bad: print('WARNING (a real run refuses): ' + b)
    start = o['price'] / 1e24
    rows = plan_scene(a.scene, start, a.step_wait_secs, a.hold_secs)
    print_plan(rows, start)
    path = os.path.join(a.out, a.scene + '.json')
    print('a real run writes %s and %s after every step%s' % (path, path[:-5] + '.md', ' (EXISTS: a real run refuses without --resume)' if os.path.exists(path) else ''))
    print('-- step 1 as rebalance_pool would run it (its dry-run: reads only)')
    move_price(rows[0]['price'], rows[0]['max_step'], True)
    print('-- the keeper rule now')
    keeper_act(tw, corridors['keeper'], True, print)
    print('-- the liquidator now (liquidate.py --dry-run, reads only)')
    for n in TWINS: print('%s:' % n); liquidate(tw[n], True, print)
    print('-- projection from the current positions, no interest; the AI twin is the agent\'s and is not projected')
    state = {n: {'coll': o['twins'][n]['coll'], 'debt': o['twins'][n]['debt']} for n in ('keeper', 'unprotected')}
    for r, pr in zip(rows, project(state, rows, corridors['keeper'], market_params()['lltv'])):
        ai_ltv = ltv_wad(o['twins']['ai-agent']['debt'], lq.mul_div_down(o['twins']['ai-agent']['coll'], oracle_int(r['price']), ORACLE_SCALE))
        ev = []
        for n in ('keeper', 'unprotected'):
            for kind, rep, coll in pr['twins'][n][1]:
                if kind == 'reduce': ev.append('%s: reduce(%s, %d, deadline) repays %s dUSDC, sells about %s WETH' % (n, DEMO_ID[:10] + '...', rep, _usdc(rep), _weth(coll)))
                else: ev.append('%s: liquidate.py --market demo --borrower %s (repays %s, seizes about %s WETH)' % (n, tw[n], _usdc(rep), _weth(coll)))
        print('step %2d %-4s %9.2f  %s; wait %d s; LTV after: ai-agent (no action) %s, keeper %s, unprotected %s%s' % (
            r['step'], r['kind'], r['price'],
            ('set-price %.2f max-step %s' % (r['price'], r['max_step']) + ('; liquidator pass at once, then' if r['kind'] == 'gap' else ''))
            if r['kind'] != 'hold' else 'hold, no move',
            r['wait'], _pct(None if ai_ltv is None else ai_ltv / 1e18),
            *[_pct(None if pr['twins'][n][0] is None else pr['twins'][n][0] / 1e18) for n in ('keeper', 'unprotected')],
            ''.join('\n          would send: ' + e for e in ev)))
    print('dry-run: nothing sent, nothing written')


def print_plan(rows, start):
    print('scene plan from %.2f USD: price moves %d, holds %d' % (start, sum(r['kind'] != 'hold' for r in rows), sum(r['kind'] == 'hold' for r in rows)))
    print('  %4s  %-4s  %12s  %10s  %10s  %8s  %6s' % ('step', 'kind', 'target USD', 'from start', 'this move', 'max step', 'wait s'))
    prev = start
    for r in rows:
        print('  %4d  %-4s  %12.2f  %+9.2f%%  %+9.2f%%  %8s  %6d' % (r['step'], r['kind'], r['price'], 100 * (r['price'] / start - 1),
                                                                 100 * (r['price'] / prev - 1), '-' if r['max_step'] is None else '%.3f' % r['max_step'], r['wait']))
        prev = r['price']
    for r in rows:
        if r['kind'] == 'gap':
            print('step %d (gap): move, then at once (no wait) the liquidator for every twin past the line in the first block after '
                  'the move; then wait, read, keeper, liquidator, read again. Other rows: move, wait, read, keeper, liquidator.' % r['step'])
    total = sum(r['wait'] for r in rows)
    lltv = int(RAW['base']['markets']['demo']['lltv']) / 1e18
    c = crossings(rows, start, 0.5, (0.70, lltv))
    print('planned waits %d s (%.1f min), plus the transactions of each move' % (total, total / 60))
    print('a position at 50%% with no action: above the act line 0.70 from step %s, past LLTV %.2f from step %s' % (c[0.70], lltv, c[lltv]))


def reset(a):
    check_chain()
    tw, _ = addresses(a)
    fd = None if a.dry_run else rp.lock()
    try:
        print('reset: pool and MockOracle to %.2f USD in steps of at most %.1f %%%s' % (a.to_price, 100 * rp.DEMO_STEP, ' (dry-run)' if a.dry_run else ''))
        txs = move_price(a.to_price, rp.DEMO_STEP, a.dry_run)
        if txs: print('transactions: ' + ' '.join(txs))
    finally:
        if fd is not None: os.close(fd)
    status(a)
    print('positions stay open; closing them is manual (scripts/SCENES.md "Between scenes")')


def status(a):
    check_chain()
    tw, _ = addresses(a)
    o = observe(tw)
    print_obs(o, tw, {n: corridor_of(tw[n]) for n in TWINS})
    _, po, _, tick, _ = rp.Market('demo', True).read()
    print('demo pool %s: %.2f USD (tick %d), %+.3f %% from the oracle' % (DEP['demoPool'], po / 1e24, tick, 100 * (po / o['price'] - 1)))


def main():
    ap = argparse.ArgumentParser(description='Crash scenes on the demo market (Base Sepolia only)')
    sub = ap.add_subparsers(dest='cmd', required=True)
    p = sub.add_parser('plan'); p.add_argument('--scene', choices=sorted(SCENES), required=True); p.add_argument('--start-price', type=float, required=True)
    r = sub.add_parser('run'); r.add_argument('--scene', choices=sorted(SCENES), required=True)
    r.add_argument('--dry-run', action='store_true'); r.add_argument('--resume', action='store_true')
    r.add_argument('--out', default=os.path.join(ROOT, 'evidence', 'scenes'))
    s = sub.add_parser('reset'); s.add_argument('--to-price', type=float, required=True); s.add_argument('--dry-run', action='store_true')
    st = sub.add_parser('status')
    for x in (p, r):
        x.add_argument('--step-wait-secs', type=int, default=180); x.add_argument('--hold-secs', type=int, default=600)
    for x in (r, s, st):
        x.add_argument('--ai', help='AI twin address (default near.vault_base_address, vault v1)')
        x.add_argument('--unprotected', default=UNPROTECTED_DEFAULT, help='unprotected twin address (default vault v2)')
        x.add_argument('--keeper', help="keeper twin address (default the test wallet's; it must be the key file's to send)")
    a = ap.parse_args()
    if a.cmd == 'plan':
        if a.start_price <= 0: raise SystemExit('--start-price must be positive')
        print_plan(plan_scene(a.scene, a.start_price, a.step_wait_secs, a.hold_secs), a.start_price)
    elif a.cmd == 'run':
        if a.keeper and a.keeper.lower() != rp.key()[1].lower() and not a.dry_run: raise SystemExit('--keeper must be the key file\'s address to send')
        run(a)
    elif a.cmd == 'reset':
        if a.to_price <= 0: raise SystemExit('--to-price must be positive')
        reset(a)
    else:
        status(a)


if __name__ == '__main__':
    main()
