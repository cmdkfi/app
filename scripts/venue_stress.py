#!/usr/bin/env python3 -I
"""Venue stress scene on the live market (README step 10, last sentence): orchestrator and recorder. Base Sepolia only.

The test wallet borrows most of the live market's free USDC, so utilization goes above the agent's 95% screen
(agent/src/logic.rs UTILIZATION_SCREEN) and the borrow rate rises. The live enclave agent watches vault v1, which
borrows on the live market. This script records what the agent then does: on the judge's `leave_market` for live, the
agent's code picks the backup market and calls Executor.migrate through the vault; on another answer it reduces or
does nothing. The script observes v1 only (the executor's Migrated, Reduced and Exited events with caller v1) and does
not assume the migrate. At the end it repays the wallet's borrow and withdraws its collateral.

  venue_stress.py status                         reads only: both markets, v1, the wallet, corridors, the run's sizing
  venue_stress.py run [--dry-run] [--borrow-keep-units 100000] [--collateral-ltv 0.5] [--timeout-s 1800] [--poll-s 15]
                      [--out-dir evidence/scenes]
  venue_stress.py unwind [--dry-run] [--out-dir evidence/scenes]
                                                 repay the wallet's live borrow by shares and withdraw its collateral

Positions: v1 = near.vault_base_address in deployments/testnet.json; wallet = the test wallet (rebalance_pool.key(),
never printed), the lender on both markets. How to run the scene: scripts/SCENES.md, section "Venue stress".
--dry-run reads only: it prints the plan, the sizing, the preconditions and the `before` reading, sends nothing and
writes nothing.
Exit codes: 0 done; 1 a step failed after something was sent (the files hold what was recorded; run `unwind`);
2 refused before anything was sent (preconditions, existing file); 4 another run holds the rebalancer lock.
"""
import argparse, hashlib, json, os, subprocess, sys, time, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import rebalance_pool as rp   # noqa: E402  (send, lock, call, key, deployments loader)
import liquidate as lq        # noqa: E402  (Morpho share math, MP_T, MARKET_T)
import scene as sc            # noqa: E402  (cast reads pinned to a block, logs, decoders, accrual, LTV rounding)

DEP, RAW = rp.DEP, sc.RAW
MORPHO, EXECUTOR = DEP['morpho'], DEP['executor']
MARKETS = {'live': DEP['liveMarketId'].lower(), 'backup': DEP['backupMarketId'].lower()}
NAMES = {v: k for k, v in MARKETS.items()}
NAMES[DEP['demoMarketId'].lower()] = 'demo'
WAD, ORACLE_SCALE = lq.WAD, lq.ORACLE_SCALE
SECONDS_PER_YEAR = 31_536_000            # APR = rate per second x 31,536,000 / 1e18 (agent/src/chain.rs borrow_apr)
UTILIZATION_SCREEN = 0.95                # agent/src/logic.rs UTILIZATION_SCREEN
MAX_MOVES = 2                            # Executor MAX_MOVES per rolling 24 h (logic.rs MAX_MOVES)
SCENE = 'venue-stress'                   # --scene renames the output files (the exit variant: venue-stress-exit)
TAG = 'v1'                               # --tag: how the watched vault is named in the output (its record key stays 'v1')
EXPECT = 'migrate'                       # --expect migrate|exit: which agent action the scene is set up to produce
MIGRATED_T0 = '0xb4aa34cc6887fca05d7be0cfbb44e20fd962986bb5c97095895949823704ab37'   # Migrated(address,bytes32,bytes32,uint256,uint256,uint256)
EXITED_T0 = '0xb7cd236bc6dcde82ab6894bc063892f7f56ee39a7fb5a06ad6ea6405c00120c8'     # Exited(address,bytes32,uint256,uint256,uint256,uint256)
REDUCED_T0 = sc.REDUCED_T0
CONFIG_SIG = 'config(bytes32)(((address,address,address,address,uint256),address,address[2],uint32[2]))'
FEED_SIG = 'latestRoundData()(uint80,int256,uint256,uint256,uint80)'
TRANSCRIPTS_URL = RAW['agent']['evidenceProxy'] + RAW['agent']['transcriptsPath']    # https://relayer.cmdk.fi/agent/transcripts
HTTP_TIMEOUT = 20
USER_AGENT = 'cmdk-venue-stress/1'       # the relayer's proxy answers 403 to urllib's default User-Agent
FEED_NAMES = {RAW['base']['chainlink']['ethUsd']['address'].lower(): 'ETH/USD', RAW['base']['chainlink']['usdcUsd']['address'].lower(): 'USDC/USD'}


# ------------------------------------------------------------------ pure: rates, sizing, comparison

def apr(rate_per_sec):
    """Borrow APR (simple, not compounded) from the IRM's per-second WAD rate: rate x 31,536,000 / 1e18."""
    return rate_per_sec * SECONDS_PER_YEAR / 1e18


def utilization(borrow, supply):
    """Borrowed / supplied (the agent's MarketSnap.utilization, on totals accrued to the block); 0 with no supply."""
    return borrow / supply if supply else 0.0


def ltv_w(f):
    """A fraction such as 0.5 as WAD, through micro-units so 0.1 + 0.2 style float noise does not reach the wei."""
    return int(round(f * 1e6)) * 10 ** 12


def sizing(supply, borrowed, price, keep_units, collateral_ltv):
    """The wallet's borrow and collateral. borrow = free liquidity (supply - borrowed) - keep_units; collateral = the
    borrow's value at the live oracle / collateral_ltv, rounded up to the wei. Returns the borrow (may be 0 or
    negative: the run refuses), the collateral, the wallet's LTV (CorridorMath rounding) and the utilization after."""
    free = supply - borrowed
    borrow = free - keep_units
    lw = ltv_w(collateral_ltv)
    coll = -(-borrow * ORACLE_SCALE * WAD // (price * lw)) if borrow > 0 and lw > 0 else 0
    value = lq.mul_div_down(coll, price, ORACLE_SCALE)
    return {'free_liquidity': free, 'borrow': borrow, 'collateral': coll, 'collateral_ltv_wad': lw,
            'wallet_ltv': sc.ltv_wad(max(borrow, 0), value), 'utilization_before': utilization(borrowed, supply),
            'utilization_after': utilization(borrowed + max(borrow, 0), supply), 'free_after': free - max(borrow, 0)}


def ltv_after_move(src_debt, src_coll, dest_debt, dest_coll, dest_price, fraction=WAD):
    """agent/src/logic.rs ltv_after_move: the destination's LTV after `fraction` of the source moves (debt rounded up,
    collateral down), at the destination's oracle."""
    debt = dest_debt + (src_debt * fraction + WAD - 1) // WAD
    coll = dest_coll + src_coll * fraction // WAD
    return sc.ltv_wad(debt, lq.mul_div_down(coll, dest_price, ORACLE_SCALE))


def migrate_fraction(dest_liquidity, debt):
    """agent/src/logic.rs migrate_fraction: WAD when the destination's free liquidity covers the debt plus 1%, else 99%
    of what it covers."""
    if debt == 0: return 0
    if dest_liquidity >= debt + debt // 100: return WAD
    return dest_liquidity * WAD // debt * 99 // 100


def compare(before, after, tolerance=1):
    """v1's collateral and debt summed across live and backup, before and after. before/after: {market: {'coll', 'debt'}}
    with integers. Collateral must be identical; debt equal up to `tolerance` units (Morpho rounds shares to assets up
    on each market, so a move can add one unit)."""
    cb = sum(int(x['coll']) for x in before.values()); ca = sum(int(x['coll']) for x in after.values())
    db = sum(int(x['debt']) for x in before.values()); da = sum(int(x['debt']) for x in after.values())
    return {'collateral_before_wei': str(cb), 'collateral_after_wei': str(ca), 'collateral_diff_wei': str(ca - cb),
            'debt_before_units': str(db), 'debt_after_units': str(da), 'debt_diff_units': str(da - db),
            'collateral_identical': ca == cb, 'debt_equal_within_units': tolerance, 'debt_equal': abs(da - db) <= tolerance}


# ------------------------------------------------------------------ pure: events, transcripts

def decode_migrated(log):
    """Executor Migrated(address indexed caller, bytes32 indexed from, bytes32 indexed to, uint256 fraction,
    uint256 debtMoved, uint256 collateralMoved)."""
    w = sc._words(log['data'])
    return {'caller': '0x' + log['topics'][1][-40:].lower(), 'from': log['topics'][2].lower(), 'to': log['topics'][3].lower(),
            'block': int(log['blockNumber'], 16), 'log_index': int(log['logIndex'], 16), 'tx': log['transactionHash'],
            'fraction': w[0], 'debt_moved': w[1], 'collateral_moved': w[2]}


def decode_exited(log):
    """Executor Exited(address indexed caller, bytes32 indexed market, uint256 repaid, uint256 fromIdle,
    uint256 collateralSold, uint256 collateralOut)."""
    w = sc._words(log['data'])
    return {'caller': '0x' + log['topics'][1][-40:].lower(), 'market': log['topics'][2].lower(), 'block': int(log['blockNumber'], 16),
            'log_index': int(log['logIndex'], 16), 'tx': log['transactionHash'], 'repaid': w[0], 'from_idle': w[1],
            'sold': w[2], 'collateral_out': w[3]}


def name_of(mid): return NAMES.get(mid.lower(), mid.lower())


def actions_of(addr, migrated, reduced, exited):
    """v1's actions in chain order, from decoded events; `by` is `agent` for caller v1 (only v1's events are scanned)."""
    a = addr.lower(); out = []
    by = lambda e: 'agent' if e['caller'].lower() == a else 'unexpected caller %s' % e['caller']
    for e in migrated:
        if e['caller'].lower() != a: continue
        out.append(((e['block'], e['log_index']), {'block': e['block'], 'kind': 'migrate', 'tx': e['tx'], 'by': by(e),
                    'from': name_of(e['from']), 'to': name_of(e['to']), 'fraction': str(e['fraction']),
                    'debt_moved_units': str(e['debt_moved']), 'collateral_moved_wei': str(e['collateral_moved'])}))
    for e in reduced:
        if e['caller'].lower() != a: continue
        out.append(((e['block'], e['log_index']), {'block': e['block'], 'kind': 'reduce', 'tx': e['tx'], 'by': by(e),
                    'market': name_of(e['market']), 'repaid_units': str(e['repaid']), 'sold_wei': str(e['sold']),
                    'protective': e['protective'], 'ltv_before': round(e['ltv_before'] / 1e18, 6), 'ltv_after': round(e['ltv_after'] / 1e18, 6)}))
    for e in exited:
        if e['caller'].lower() != a: continue
        out.append(((e['block'], e['log_index']), {'block': e['block'], 'kind': 'exit', 'tx': e['tx'], 'by': by(e),
                    'market': name_of(e['market']), 'repaid_units': str(e['repaid']), 'from_idle_units': str(e['from_idle']),
                    'sold_wei': str(e['sold']), 'collateral_out_wei': str(e['collateral_out'])}))
    return [x for _, x in sorted(out, key=lambda p: p[0])]


def new_transcripts(first, second):
    """Index entries in the second GET /transcripts that the first did not list (either may be None: a failed fetch)."""
    if not second: return []
    seen = {t['name'] for t in (first or {}).get('transcripts', [])}
    return [t for t in second.get('transcripts', []) if t['name'] not in seen]


def transcript_summary(entry, raw):
    """What the record keeps of one transcript: name, sha256 of the bytes served (and the index's), time, trigger, the
    fired screens, the judge's outcome and answer, the decisions, the chosen market and the proposal."""
    sha = hashlib.sha256(raw).hexdigest()
    t = json.loads(raw)
    j = t.get('judge') or {}
    parsed = (j.get('call') or {}).get('parsed') or {}
    p = t.get('proposal') or {}
    base = p.get('base') if isinstance(p.get('base'), dict) else {}
    return {'name': entry['name'], 'sha256': sha, 'sha256_index': entry.get('sha256'), 'vault': t.get('vault', {}).get('tag', entry.get('vault')),
            'loop': t.get('loop', entry.get('loop')), 'time_utc': t.get('time_utc'),
            'trigger': {'kind': (t.get('trigger') or {}).get('kind'), 'new_screens': (t.get('trigger') or {}).get('new_screens', [])},
            'block': ((t.get('inputs') or {}).get('snapshot') or {}).get('block'),
            'screens': ['%s:%s' % (s.get('market'), s.get('screen')) for s in t.get('screens') or []],
            'judge': None if not j else {'outcome': j.get('outcome'), 'accepted': j.get('accepted'), 'ignored': j.get('ignored'),
                                         'trigger': j.get('trigger'), 'judgment_market': j.get('judgment_market'),
                                         'answer': {k: parsed.get(k) for k in ('risk', 'action', 'market', 'reasons')} if parsed else None},
            'decisions': [{k: d.get(k) for k in ('market', 'action', 'to', 'fraction', 'amount', 'protective', 'basis')} for d in t.get('decisions') or []],
            'chosen': t.get('chosen'),
            'proposal': None if not p else {'method': p.get('method'), 'args': p.get('args'), 'sent': p.get('sent'),
                                            'why_not_proposed': p.get('why_not_proposed'), 'why_not_sent': p.get('why_not_sent'),
                                            'near_tx': p.get('near_tx'), 'base_tx': base.get('hash'), 'base_status': base.get('status'),
                                            'base_block': base.get('block'), 'error': p.get('error') or p.get('near_error')}}


# ------------------------------------------------------------------ pure: the record and Markdown

def _pct(x): return 'n/a' if x is None else '%.2f %%' % (100 * x)
def _weth(w): return '%.9f' % (int(w) / 1e18)
def _usdc(u): return '%.6f' % (int(u) / 1e6)
def _ltv(w): return None if w is None else round(w / 1e18, 6)


def phase_entry(o):
    """One reading in the record's shape: block, ts, utc, both markets' totals, the per-position dicts (strings for raw
    units, so a JavaScript reader keeps every digit), v1's movesInWindow and the wallet's free WETH."""
    mk = {n: {'supply_units': str(m['supply']), 'borrow_units': str(m['borrow']), 'utilization': round(m['utilization'], 6),
              'free_liquidity_units': str(m['free']), 'borrow_rate_per_sec': str(m['rate']), 'borrow_apr': round(m['apr'], 6),
              'oracle_price': str(m['price'])} for n, m in o['markets'].items()}
    pos = {who: {n: {'coll': str(x['coll']), 'debt': str(x['debt']), 'ltv': _ltv(x['ltv']), 'shares': str(x['shares']), 'cash': str(x['cash'])}
                 for n, x in per.items()} for who, per in o['positions'].items()}
    return {'block': o['block'], 'ts': o['ts'], 'utc': o['utc'], 'markets': mk, 'positions': pos,
            'moves_in_window': o['moves_in_window'], 'wallet_weth_wei': str(o['wallet_weth'])}


def new_record(o, v1, wallet, sz, args):
    return {'scene': SCENE, 'tag': TAG, 'expect': EXPECT, 'market': 'live', 'destination': 'backup' if EXPECT == 'migrate' else None, 'markets': dict(MARKETS), 'executor': EXECUTOR,
            'started_block': o['block'], 'started_utc': o['utc'], 'addresses': {'v1': v1, 'wallet': wallet},
            'sizing': {'borrow_units': str(sz['borrow']), 'collateral_wei': str(sz['collateral']), 'keep_units': str(args.borrow_keep_units),
                       'collateral_ltv': args.collateral_ltv, 'wallet_ltv': _ltv(sz['wallet_ltv']),
                       'utilization_after': round(sz['utilization_after'], 6), 'free_liquidity_units': str(sz['free_liquidity'])},
            'corridors': {}, 'backup_feeds': [], 'phases': {}, 'comparison': {}, 'actions': [], 'transcripts': [], 'transactions': [], 'outcome': None, 'notes': ''}


def add_note(rec, text):
    print('NOTE', text); rec['notes'] = (rec['notes'] + '\n' if rec['notes'] else '') + text


PHASES = (('before', 'before the borrow'), ('after_borrow', 'after the borrow'), ('after', "after the agent's action"))


def render_md(rec):
    L = []; p = L.append
    ph = rec['phases']
    tag = rec.get('tag', 'v1'); scene = rec.get('scene', SCENE)
    p('# Scene `%s` on the live market (Base Sepolia)' % scene); p('')
    p('The test wallet borrows most of the live market\'s free USDC, so utilization rises above the agent\'s 95%% screen and the '
      'borrow rate rises; this records what the live agent does with vault %s\'s live position%s. Started at block %d (%s). '
      'Data: `%s.json`; written by `scripts/venue_stress.py`.' % (tag, ' (live is the only market this vault lists, so `leave_market` can only become an exit, a sale of collateral)' if rec.get('expect') == 'exit' else '', rec['started_block'], rec['started_utc'], scene))
    if not rec.get('outcome'): p(''); p('**Incomplete:** the scene has not reached its end (see Notes).')
    p(''); p('## Positions'); p('')
    p('| position | address | role |'); p('|---|---|---|')
    p('| %s | `%s` | vault %s\'s Base address, watched by the live enclave agent; borrower on live |' % (tag, rec['addresses']['v1'], tag))
    p('| wallet | `%s` | the test wallet: lender on live and backup; the scene\'s borrower on live |' % rec['addresses']['wallet'])
    p(''); p('Markets: live `%s`, backup `%s`; executor `%s`.' % (rec['markets']['live'], rec['markets']['backup'], rec['executor']))
    s = rec['sizing']
    p(''); p('## Sizing'); p('')
    p('Borrow = live free liquidity %s USDC - keep %s USDC = %s USDC; collateral = borrow value at the live oracle / %.2f = %s WETH '
      '(rounded up to the wei); wallet LTV %s; live utilization after the borrow %s.' % (
          _usdc(s['free_liquidity_units']), _usdc(s['keep_units']), _usdc(s['borrow_units']), s['collateral_ltv'], _weth(s['collateral_wei']),
          _pct(s['wallet_ltv']), _pct(s['utilization_after'])))
    p(''); p('## Three phases'); p('')
    cols = [k for k, _ in PHASES if k in ph]
    p('| | %s |' % ' | '.join(dict(PHASES)[k] for k in cols)); p('|---|%s' % ('---:|' * len(cols)))
    row = lambda label, f: p('| %s | %s |' % (label, ' | '.join(f(ph[k]) for k in cols)))
    row('block', lambda e: str(e['block'])); row('UTC', lambda e: e['utc'])
    for m in ('live', 'backup'):
        row('%s utilization' % m, lambda e, m=m: _pct(e['markets'][m]['utilization']))
        row('%s borrow APR' % m, lambda e, m=m: _pct(e['markets'][m]['borrow_apr']))
        row('%s free liquidity (USDC)' % m, lambda e, m=m: _usdc(e['markets'][m]['free_liquidity_units']))
    for m in ('live', 'backup'):
        row('%s collateral on %s (WETH)' % (tag, m), lambda e, m=m: _weth(e['positions']['v1'][m]['coll']))
        row('%s debt on %s (USDC)' % (tag, m), lambda e, m=m: _usdc(e['positions']['v1'][m]['debt']))
        row('%s LTV on %s' % (tag, m), lambda e, m=m: _pct(e['positions']['v1'][m]['ltv']))
    row('wallet collateral on live (WETH)', lambda e: _weth(e['positions']['wallet']['live']['coll']))
    row('wallet debt on live (USDC)', lambda e: _usdc(e['positions']['wallet']['live']['debt']))
    row('%s movesInWindow' % tag, lambda e: str(e['moves_in_window']))
    p(''); p('Debt is accrued to each block (Morpho\'s share math and IRM accrual). Borrow APR = `IRM.borrowRateView` (per-second '
             'WAD rate) x 31,536,000 / 1e18, simple. Utilization = borrowed / supplied, both accrued to the block.')
    c = rec.get('comparison') or {}
    p(''); p('## %s collateral and debt across the action' % tag); p('')
    if c:
        p('Summed over live and backup; before at block %s with interest accrued to the time of block %s, after at block %s. %s' % (
            c['block_before'], c['block_after'], c['block_after'], c.get('basis', '')))
        p(''); p('| | before | after | difference |'); p('|---|---:|---:|---:|')
        p('| collateral (WETH) | %s | %s | %s wei |' % (_weth(c['collateral_before_wei']), _weth(c['collateral_after_wei']), c['collateral_diff_wei']))
        p('| debt (USDC) | %s | %s | %s unit%s |' % (_usdc(c['debt_before_units']), _usdc(c['debt_after_units']), c['debt_diff_units'],
                                                    '' if abs(int(c['debt_diff_units'])) == 1 else 's'))
        p(''); p('Collateral identical: %s. Debt equal up to %d unit: %s.' % ('yes' if c['collateral_identical'] else 'no',
                                                                          c['debt_equal_within_units'], 'yes' if c['debt_equal'] else 'no'))
        if c.get('event'): p('The Migrated event: fraction %s, debt moved %s USDC, collateral moved %s WETH.' % (
            c['event']['fraction'], _usdc(c['event']['debt_moved_units']), _weth(c['event']['collateral_moved_wei'])))
    else: p('Not recorded.')
    p(''); p('## Actions'); p('')
    if rec['actions']:
        p('| block | kind | by | market | detail | tx |'); p('|---:|---|---|---|---|---|')
        for a in rec['actions']:
            if a['kind'] == 'migrate': mkt, det = '%s -> %s' % (a['from'], a['to']), 'fraction %s, debt %s USDC, collateral %s WETH' % (
                a['fraction'], _usdc(a['debt_moved_units']), _weth(a['collateral_moved_wei']))
            elif a['kind'] == 'reduce': mkt, det = a['market'], 'repaid %s USDC, sold %s WETH, LTV %s -> %s%s' % (
                _usdc(a['repaid_units']), _weth(a['sold_wei']), _pct(a['ltv_before']), _pct(a['ltv_after']), ', protective' if a['protective'] else '')
            else: mkt, det = a['market'], 'repaid %s USDC (%s from idle), sold %s WETH, collateral out %s WETH' % (
                _usdc(a['repaid_units']), _usdc(a['from_idle_units']), _weth(a['sold_wei']), _weth(a['collateral_out_wei']))
            p('| %d | %s | %s | %s | %s | `%s` |' % (a['block'], a['kind'], a['by'], mkt, det, a['tx']))
    else: p('None recorded.')
    p(''); p('## Agent transcripts'); p('')
    if rec['transcripts']:
        p('| transcript | UTC | trigger | screens | judge | answer (risk, action, market) | chosen | proposal | sha256 |')
        p('|---|---|---|---|---|---|---|---|---|')
        for t in rec['transcripts']:
            j = t['judge'] or {}; a = j.get('answer') or {}; pr = t.get('proposal') or {}
            jd = '' if not j else '%s, accepted %s%s' % (j.get('outcome'), j.get('accepted'), ', ignored' if j.get('ignored') else '')
            ans = '' if not a else '%s, %s, %s' % (a.get('risk'), a.get('action'), a.get('market'))
            prop = '' if not pr else '%s, sent %s%s' % (pr.get('method'), pr.get('sent'), ', Base tx `%s`' % pr['base_tx'] if pr.get('base_tx') else '')
            p('| `%s` | %s | %s | %s | %s | %s | %s | %s | `%s` |' % (t['name'], t['time_utc'], t['trigger']['kind'], ', '.join(t['screens']) or '-',
                                                                 jd or '-', ans or '-', t['chosen'] or '-', prop or '-', t['sha256']))
        for t in rec['transcripts']:
            a = ((t['judge'] or {}).get('answer') or {})
            if a.get('reasons'):
                p(''); p('`%s`, judge reasons:' % t['name'])
                for r in a['reasons']: p('- ' + str(r).replace('\n', ' '))
            for d in t['decisions']:
                if d.get('action') and d['action'] != 'none':
                    p(''); p('`%s`, decision on %s: %s%s, basis: %s' % (t['name'], d['market'], d['action'], ' to %s' % d['to'] if d.get('to') else '', d.get('basis')))
    else: p('None recorded.')
    p(''); p('## Transactions'); p('')
    if rec['transactions']:
        for x in rec['transactions']: p('- %s: `%s` (block %s, gas %s, fee %s wei)' % (x['label'], x['tx'], x['block'], x['gas'], x['fee_wei']))
    else: p('None.')
    for a in rec['actions']: p('- agent %s (block %d): `%s`' % (a['kind'], a['block'], a['tx']))
    if rec['notes']:
        p(''); p('## Notes'); p('')
        for line in rec['notes'].split('\n'): p('- ' + line)
    return '\n'.join(L) + '\n'


# ------------------------------------------------------------------ chain reads (pinned to one block)

_MP = {}


def params(name):
    """Morpho MarketParams of live or backup (read once)."""
    if name not in _MP:
        q = rp.call(MORPHO, 'idToMarketParams(bytes32)(address,address,address,address,uint256)', MARKETS[name])
        _MP[name] = {'loan': q[0], 'coll': q[1], 'oracle': q[2], 'irm': q[3], 'lltv': int(q[4])}
    return _MP[name]


def mp_arg(name):
    m = params(name); return '(%s,%s,%s,%s,%d)' % (m['loan'], m['coll'], m['oracle'], m['irm'], m['lltv'])


def block_ts(block='latest'):
    blk, ts = (int(x) for x in sc.cast_out('block', block, '-f', 'number,timestamp').split()); return blk, ts


def read_market(name, blk, ts):
    """Totals accrued to ts (fee 0 on these markets: supply gains the same interest as borrow), the IRM rate at blk."""
    m = params(name)
    mk = [int(x) for x in sc.cc(blk, MORPHO, 'market(bytes32)%s' % lq.MARKET_T, MARKETS[name])]
    rate = int(sc.cc(blk, m['irm'], 'borrowRateView(%s,%s)(uint256)' % (lq.MP_T, lq.MARKET_T), mp_arg(name), '(%s)' % ','.join(map(str, mk)))[0])
    tba = sc.accrue(mk[2], rate, ts - mk[4]) if int(m['irm'], 16) else mk[2]
    supply = mk[0] + (tba - mk[2])
    price = int(sc.cc(blk, m['oracle'], 'price()(uint256)')[0])
    return {'supply': supply, 'borrow': tba, 'tbs': mk[3], 'stored': mk, 'rate': rate, 'apr': apr(rate), 'price': price,
            'utilization': utilization(tba, supply), 'free': supply - tba, 'lltv': m['lltv']}


def read_position(name, mkt, addr, blk, cash):
    pos = sc.cc(blk, MORPHO, 'position(bytes32,address)(uint256,uint128,uint128)', MARKETS[name], addr)
    x = sc.numbers(int(pos[1]), int(pos[2]), mkt['borrow'], mkt['tbs'], mkt['price'], mkt['lltv'])
    x.update({'coll': int(pos[2]), 'shares': int(pos[1]), 'supply_shares': int(pos[0]), 'cash': cash})
    return x


def observe(v1, wallet, block=None, accrue_ts=None):
    """Both markets, v1 on live and backup, the wallet on live, at one block (latest by default). accrue_ts accrues
    interest to a later time than the block's (the comparison reads the block before an action at the action's time)."""
    blk, ts = block_ts('latest' if block is None else block)
    t = accrue_ts or ts
    mk = {n: read_market(n, blk, t) for n in MARKETS}
    usdc = DEP['usdc']
    cash = {a: int(sc.cc(blk, usdc, 'balanceOf(address)(uint256)', a)[0]) for a in (v1, wallet)}
    o = {'block': blk, 'ts': ts, 'utc': sc.utc(ts), 'markets': mk,
         'positions': {'v1': {n: read_position(n, mk[n], v1, blk, cash[v1]) for n in MARKETS},
                       'wallet': {'live': read_position('live', mk['live'], wallet, blk, cash[wallet])}},
         'moves_in_window': int(sc.cc(blk, EXECUTOR, 'movesInWindow(address)(uint256)', v1)[0]),
         'wallet_weth': int(sc.cc(blk, DEP['weth'], 'balanceOf(address)(uint256)', wallet)[0])}
    return o


def corridor(addr, name, block='latest'):
    c = sc.cc(block, EXECUTOR, sc.CORRIDOR_SIG, addr, MARKETS[name])
    closed = sc.cc(block, EXECUTOR, 'closedForAgent(address,bytes32)(bool)', addr, MARKETS[name])[0] == 'true'
    return {'target': int(c[0]), 'act': int(c[1]), 'floor': int(c[2]), 'sell_budget': int(c[3]), 'migrate_allowed': c[4] == 'true',
            'exit_allowed': c[5] == 'true', 'set': c[6] == 'true', 'closed_for_agent': closed}


def feeds(name, block='latest'):
    """The executor's feed-age gate for a market (config: feeds, maxAges) and each feed's age now: what _requireFresh
    checks on the destination and what the agent's destination filter checks (Feed::fresh)."""
    cfg = json.loads(sc.cast_out('call', EXECUTOR, CONFIG_SIG, MARKETS[name], '--block', block, '--json'))[0]
    _, ts = block_ts(block)
    out = []
    for f, max_age in zip(cfg[2], cfg[3]):
        if int(f, 16) == 0: continue
        r = sc.cc(block, f, FEED_SIG)
        age = ts - int(r[3])
        out.append({'feed': FEED_NAMES.get(f.lower(), f), 'age_s': age, 'max_age_s': int(max_age), 'fresh': int(r[1]) > 0 and age <= int(max_age)})
    return out


def scan(v1, frm):
    """v1's Migrated, Reduced and Exited events on the executor since block frm."""
    to = int(sc.cast_out('block-number').strip())
    t1 = '0x' + v1[2:].lower().rjust(64, '0')
    mig = [decode_migrated(l) for l in sc.logs(EXECUTOR, MIGRATED_T0, frm, to, t1)]
    red = [sc.decode_reduced(l) for l in sc.logs(EXECUTOR, REDUCED_T0, frm, to, t1)]
    ex = [decode_exited(l) for l in sc.logs(EXECUTOR, EXITED_T0, frm, to, t1)]
    return actions_of(v1, mig, red, ex), to


def http_json(url):
    r = urllib.request.Request(url, headers={'User-Agent': USER_AGENT, 'Accept': 'application/json'})
    with urllib.request.urlopen(r, timeout=HTTP_TIMEOUT) as f: return f.read()


def transcripts_index(note):
    try: return json.loads(http_json(TRANSCRIPTS_URL))
    except Exception as e:  # a failed fetch is a note, not a failure
        note('GET %s failed: %s' % (TRANSCRIPTS_URL, e)); return None


# ------------------------------------------------------------------ preconditions and printing

def preconditions(o, corridors, fd_backup, sz, chain_ok=True):
    """Reasons a run refuses before anything is sent."""
    bad = []
    if not chain_ok: bad.append('chain is not Base Sepolia')
    v1l, v1b = o['positions']['v1']['live'], o['positions']['v1']['backup']
    w = o['positions']['wallet']['live']
    b = o['markets']['backup']
    if v1l['shares'] == 0: bad.append('%s has no debt on live' % TAG)
    if EXPECT == 'exit':
        c = corridors['live']
        if not c['set'] or c['closed_for_agent']: bad.append("%s's corridor on live is not in force (set %s, closed for agent %s)" % (TAG, c['set'], c['closed_for_agent']))
        elif not c['exit_allowed']: bad.append("%s's corridor on live does not allow exit" % TAG)
        if corridors['backup']['set']: bad.append("%s's corridor on backup is set: the agent would migrate there instead of exiting (--expect exit needs a vault with live only)" % TAG)
        if sz['borrow'] <= 0: bad.append('the borrow is not positive (free liquidity %s USDC, keep %s USDC)' % (_usdc(sz['free_liquidity']), _usdc(sz['free_liquidity'] - sz['borrow'])))
        if o['wallet_weth'] < sz['collateral']: bad.append('the wallet holds %s WETH, the collateral needs %s' % (_weth(o['wallet_weth']), _weth(sz['collateral'])))
        if w['shares'] or w['coll']: bad.append('the wallet already has a live position (borrow shares %d, collateral %d): run `unwind` first' % (w['shares'], w['coll']))
        return bad
    if b['free'] < v1l['debt'] + v1l['debt'] // 100:
        bad.append('backup free liquidity %s USDC is below v1\'s debt %s USDC plus 1%% (the agent then moves only part: logic.rs migrate_fraction)' % (_usdc(b['free']), _usdc(v1l['debt'])))
    for n in MARKETS:
        c = corridors[n]
        if not c['set'] or c['closed_for_agent']: bad.append("v1's corridor on %s is not in force (set %s, closed for agent %s)" % (n, c['set'], c['closed_for_agent']))
        elif not c['migrate_allowed']: bad.append("v1's corridor on %s does not allow migrate" % n)
    if o['moves_in_window'] >= MAX_MOVES: bad.append('movesInWindow(v1) is %d, the executor allows %d per 24 h' % (o['moves_in_window'], MAX_MOVES))
    if sz['borrow'] <= 0: bad.append('the borrow is not positive (free liquidity %s USDC, keep %s USDC)' % (_usdc(sz['free_liquidity']), _usdc(sz['free_liquidity'] - sz['borrow'])))
    if o['wallet_weth'] < sz['collateral']: bad.append('the wallet holds %s WETH, the collateral needs %s' % (_weth(o['wallet_weth']), _weth(sz['collateral'])))
    if w['shares'] or w['coll']: bad.append('the wallet already has a live position (borrow shares %d, collateral %d): run `unwind` first' % (w['shares'], w['coll']))
    stale = [f for f in fd_backup if not f['fresh']]
    if stale: bad.append('backup feeds not fresh (Executor._requireFresh would revert StaleFeed): %s' % ', '.join('%s age %d s > %d s' % (f['feed'], f['age_s'], f['max_age_s']) for f in stale))
    if b['utilization'] > UTILIZATION_SCREEN: bad.append('backup utilization %.4f is above the screen: the agent skips a destination with a fired screen' % b['utilization'])
    if corridors['backup']['set']:
        after = ltv_after_move(v1l['debt'], v1l['coll'], v1b['debt'], v1b['coll'], b['price'], migrate_fraction(b['free'], v1l['debt']))
        if after is None or after > corridors['backup']['act']:
            bad.append("v1's LTV on backup after the move %s would be above backup's act line %.2f (Executor AboveActLine)" % (_pct(_ltv(after)), corridors['backup']['act'] / 1e18))
    return bad


def print_obs(o):
    print('block %d (%s)' % (o['block'], o['utc']))
    print('  %-7s %14s %14s %11s %14s %16s %10s %10s' % ('market', 'supply', 'borrowed', 'utilization', 'free', 'rate/s (WAD)', 'APR', 'oracle'))
    for n, m in o['markets'].items():
        print('  %-7s %14s %14s %11s %14s %16d %10s %10.2f' % (n, _usdc(m['supply']), _usdc(m['borrow']), '%.4f' % m['utilization'], _usdc(m['free']),
                                                          m['rate'], _pct(m['apr']), m['price'] / 1e24))
    print('  %-14s %-42s %14s %12s %10s %16s %12s' % ('position', 'address', 'collateral', 'debt', 'LTV', 'borrow shares', 'USDC held'))
    for who, per in o['positions'].items():
        for n, x in per.items():
            print('  %-14s %-42s %14s %12s %10s %16d %12s' % ('%s on %s' % (TAG if who == 'v1' else who, n), ADDR[who], _weth(x['coll']), _usdc(x['debt']),
                                                             _pct(_ltv(x['ltv'])), x['shares'], _usdc(x['cash'])))
    print('  wallet free WETH %s, free USDC %s; %s movesInWindow %d of %d' % (_weth(o['wallet_weth']), _usdc(o['positions']['wallet']['live']['cash']),
                                                                         TAG, o['moves_in_window'], MAX_MOVES))


def print_corridors(cs):
    for n, c in cs.items():
        print('  %s corridor on %-6s target %.2f act %.2f floor %.2f sell budget %.2f migrate %s exit %s set %s closed for agent %s' % (
            TAG, n, c['target'] / 1e18, c['act'] / 1e18, c['floor'] / 1e18, c['sell_budget'] / 1e18, c['migrate_allowed'], c['exit_allowed'], c['set'], c['closed_for_agent']))


def print_sizing(sz, keep, ltv):
    print('run sizing: live free liquidity %s USDC - keep %s = borrow %s USDC; collateral %s WETH (borrow value / %.2f, rounded up); '
          'wallet LTV %s; live utilization %.4f -> %.4f; free after %s USDC' % (
              _usdc(sz['free_liquidity']), _usdc(keep), _usdc(max(sz['borrow'], 0)), _weth(sz['collateral']), ltv,
              _pct(_ltv(sz['wallet_ltv'])), sz['utilization_before'], sz['utilization_after'], _usdc(sz['free_after'])))


ADDR = {}


def addresses(vault=None):
    """The watched vault's Base address: --vault, else v1's from the deployments."""
    _, me = rp.key()
    ADDR.update({'v1': (vault or RAW['near']['vault_base_address']).lower(), 'wallet': me})
    return ADDR['v1'], me


def read_all(a):
    """The reading status and run share: observation, corridors, backup feeds, sizing, preconditions."""
    sc.check_chain()
    v1, me = addresses(a.vault)
    o = observe(v1, me)
    cs = {n: corridor(v1, n) for n in MARKETS}
    fb = feeds('backup')
    m = o['markets']['live']
    sz = sizing(m['supply'], m['borrow'], m['price'], a.borrow_keep_units, a.collateral_ltv)
    return v1, me, o, cs, fb, sz, preconditions(o, cs, fb, sz)


def status(a):
    v1, me, o, cs, fb, sz, bad = read_all(a)
    print('venue stress status: rpc %s, executor %s, live %s, backup %s' % (rp.RPC, EXECUTOR, MARKETS['live'], MARKETS['backup']))
    print_obs(o)
    print_corridors(cs)
    print('  backup feeds (executor gate): %s' % ', '.join('%s age %d s of %d s%s' % (f['feed'], f['age_s'], f['max_age_s'], '' if f['fresh'] else ' STALE') for f in fb))
    last = int(sc.cc(o['block'], EXECUTOR, 'lastAgentCall(address)(uint256)', v1)[0])
    auth = sc.cc(o['block'], MORPHO, 'isAuthorized(address,address)(bool)', v1, EXECUTOR)[0]
    held = int(sc.cc(o['block'], DEP['usdc'], 'balanceOf(address)(uint256)', MORPHO)[0])
    print('  %s lastAgentCall %d (%s); %s authorizes the executor on Morpho: %s; USDC held by Morpho (flash-loan source) %s' % (
        TAG, last, sc.utc(last) if last else 'never', TAG, auth, _usdc(held)))
    d = sc.cc(o['block'], MORPHO, 'position(bytes32,address)(uint256,uint128,uint128)', DEP['demoMarketId'], v1)
    print('  %s on demo (not part of this scene): borrow shares %s, collateral %s WETH' % (TAG, d[1], _weth(d[2])))
    print_sizing(sz, a.borrow_keep_units, a.collateral_ltv)
    print('preconditions: ' + ('all hold' if not bad else 'a run refuses:\n  ' + '\n  '.join(bad)))


# ------------------------------------------------------------------ actions (send only when not dry)

def tx(rec, label, to, sig, *args):
    """rebalance_pool.send, recorded: hash, block, gas, fee (L2 + L1)."""
    j = rp.send(label, to, sig, *args)
    gas = int(j['gasUsed'], 16); l2 = gas * int(j.get('effectiveGasPrice', '0x0'), 16); l1 = int(j.get('l1Fee') or '0x0', 16)
    x = {'label': label, 'tx': j['transactionHash'], 'block': int(j['blockNumber'], 16), 'gas': gas, 'fee_wei': str(l2 + l1), 'l1_fee_wei': str(l1)}
    if rec is not None: rec['transactions'].append(x)
    return x


def unwind_plan(o):
    w = o['positions']['wallet']['live']
    return w['shares'], w['coll'], w['debt'], w['debt'] + w['debt'] // 100 + 1


def do_unwind(rec, v1, me, dry):
    """Repay the wallet's live borrow by shares (the debt clears exactly) and withdraw all its live collateral."""
    o = observe(v1, me)
    shares, coll, debt, allowance = unwind_plan(o)
    print('unwind at block %d: wallet on live has borrow shares %d (debt %s USDC), collateral %s WETH; USDC held %s' % (
        o['block'], shares, _usdc(debt), _weth(coll), _usdc(o['positions']['wallet']['live']['cash'])))
    if not shares and not coll: print('nothing to unwind'); return []
    steps = []
    if shares:
        steps += [('unwind: USDC approve Morpho', DEP['usdc'], 'approve(address,uint256)', MORPHO, allowance),
                  ('unwind: Morpho repay by shares', MORPHO, 'repay(%s,uint256,uint256,address,bytes)' % lq.MP_T, mp_arg('live'), 0, shares, me, '0x')]
    if coll:
        steps.append(('unwind: Morpho withdrawCollateral', MORPHO, 'withdrawCollateral(%s,uint256,address,address)' % lq.MP_T, mp_arg('live'), coll, me, me))
    if dry:
        for s in steps: print('  dry-run: would send %s: %s %s %s' % (s[0], s[1], s[2], ' '.join(map(str, s[3:]))))
        return []
    if shares and o['positions']['wallet']['live']['cash'] < allowance:
        raise SystemExit('the wallet holds %s USDC, the repayment needs about %s' % (_usdc(o['positions']['wallet']['live']['cash']), _usdc(allowance)))
    return [tx(rec, *s) for s in steps]


def write(rec, out):
    os.makedirs(out, exist_ok=True)
    j = os.path.join(out, SCENE + '.json')
    with open(j + '.tmp', 'w') as f: json.dump(rec, f, indent=1); f.write('\n')
    os.replace(j + '.tmp', j)
    with open(os.path.join(out, SCENE + '.md'), 'w') as f: f.write(render_md(rec))


UNWIND_CMD = 'python3 -I scripts/venue_stress.py unwind'   # main() appends --vault/--tag/--scene when they are not the defaults


def fail(rec, out, msg):
    add_note(rec, msg + '; the wallet\'s live position is left as it is: run `%s`' % UNWIND_CMD)
    write(rec, out)
    print('FAILED: %s\nrun: %s' % (msg, UNWIND_CMD)); raise SystemExit(1)


def poll(v1, me, start_block, a):
    """Wait for the agent: one line per poll; stop at v1's Migrated, at v1's live borrow shares 0, or at timeout.
    Returns (actions, the last reading, why it stopped)."""
    t_end = time.time() + a.timeout_s; n = 0; acts = []; o = None
    while True:
        n += 1
        try:
            o = observe(v1, me)
            acts, _ = scan(v1, start_block)
            l, b = o['markets']['live'], o['positions']['v1']
            print('poll %d block %d (%s): live utilization %.4f APR %s; v1 live coll %s debt %s shares %d; v1 backup coll %s debt %s; actions %s' % (
                n, o['block'], o['utc'], l['utilization'], _pct(l['apr']), _weth(b['live']['coll']), _usdc(b['live']['debt']), b['live']['shares'],
                _weth(b['backup']['coll']), _usdc(b['backup']['debt']), ', '.join('%s@%d' % (x['kind'], x['block']) for x in acts) or 'none'))
            if any(x['kind'] == 'migrate' for x in acts): return acts, o, 'migrate'
            if any(x['kind'] == 'exit' and x['market'] == 'live' for x in acts): return acts, o, 'exit'
            if b['live']['shares'] == 0: return acts, o, 'v1 live borrow shares 0'
        except (subprocess.CalledProcessError, OSError, ValueError, KeyError, IndexError) as e:
            msg = (e.stderr.strip() if isinstance(e, subprocess.CalledProcessError) and e.stderr else str(e))[-200:]
            print('poll %d failed (read again at the next poll): %s' % (n, msg))
        if time.time() + a.poll_s > t_end: return acts, o, 'timeout'
        time.sleep(a.poll_s)


def run(a):
    v1, me, o, cs, fb, sz, bad = read_all(a)
    path = os.path.join(a.out_dir, SCENE + '.json')
    print('venue stress: rpc %s, executor %s, live %s -> backup %s; v1 %s, wallet %s' % (rp.RPC, EXECUTOR, MARKETS['live'], MARKETS['backup'], v1, me))
    print_obs(o); print_corridors(cs)
    print('  backup feeds (executor gate): %s' % ', '.join('%s age %d s of %d s' % (f['feed'], f['age_s'], f['max_age_s']) for f in fb))
    print_sizing(sz, a.borrow_keep_units, a.collateral_ltv)
    if a.dry_run: return dry_run(a, o, sz, bad, path)
    if bad: print('REFUSED, nothing sent:\n  ' + '\n  '.join(bad)); raise SystemExit(2)
    if os.path.exists(path): print('REFUSED: %s exists (evidence is not overwritten); move it first' % path); raise SystemExit(2)
    fd = rp.lock()                                           # the Mac's rebalancer lock: scene.py and the rebalancer share the nonce
    try:
        rec = new_record(o, v1, me, sz, a)
        note = lambda t: add_note(rec, t)
        idx1 = transcripts_index(note)
        o = observe(v1, me)                                  # `before` at the latest block, after the lock
        rec['phases']['before'] = phase_entry(o); rec['started_block'], rec['started_utc'] = o['block'], o['utc']
        rec['corridors'] = {n: {k: (str(v) if isinstance(v, int) and not isinstance(v, bool) else v) for k, v in c.items()} for n, c in cs.items()}
        rec['backup_feeds'] = fb
        add_note(rec, 'v1 corridors: %s' % '; '.join('%s target %.2f act %.2f floor %.2f migrate %s exit %s' % (
            n, c['target'] / 1e18, c['act'] / 1e18, c['floor'] / 1e18, c['migrate_allowed'], c['exit_allowed']) for n, c in cs.items()))
        add_note(rec, 'transcripts index before the borrow: %s entries' % (len(idx1['transcripts']) if idx1 else 'not read'))
        write(rec, a.out_dir)
        live = 'live'
        try:
            tx(rec, 'WETH approve Morpho', DEP['weth'], 'approve(address,uint256)', MORPHO, sz['collateral'])
            tx(rec, 'Morpho supplyCollateral', MORPHO, 'supplyCollateral(%s,uint256,address,bytes)' % lq.MP_T, mp_arg(live), sz['collateral'], me, '0x')
            m = observe(v1, me)['markets'][live]                  # free liquidity again just before the borrow
            borrow = min(sz['borrow'], m['free'] - a.borrow_keep_units)
            if borrow <= 0: raise SystemExit('live free liquidity fell to %s USDC: no borrow' % _usdc(m['free']))
            if borrow != sz['borrow']: add_note(rec, 'borrow resized from %s to %s USDC (free liquidity changed)' % (_usdc(sz['borrow']), _usdc(borrow)))
            rec['sizing']['borrow_units'] = str(borrow)
            b = tx(rec, 'Morpho borrow', MORPHO, 'borrow(%s,uint256,uint256,address,address)' % lq.MP_T, mp_arg(live), borrow, 0, me, me)
        except KeyboardInterrupt:
            fail(rec, a.out_dir, 'interrupted during the wallet transactions')
        except (SystemExit, subprocess.CalledProcessError) as e:
            fail(rec, a.out_dir, 'a wallet transaction failed: %s' % str(getattr(e, 'code', e)).strip()[-300:])
        try: after_borrow_steps(rec, a, v1, me, b, idx1)
        except KeyboardInterrupt: fail(rec, a.out_dir, 'interrupted after the borrow')
        except (subprocess.CalledProcessError, OSError, ValueError, KeyError, IndexError) as e:
            fail(rec, a.out_dir, 'a step after the borrow failed: %s' % (e.stderr.strip() if isinstance(e, subprocess.CalledProcessError) and e.stderr else str(e))[-300:])
    finally:
        os.close(fd)
    print('scene %s done: %s, %s' % (SCENE, path, path[:-5] + '.md'))
    print('  outcome %s; comparison %s' % (json.dumps(rec['outcome']), json.dumps(rec['comparison'])))


def after_borrow_steps(rec, a, v1, me, b, idx1):
    """Steps 5 to 8 of `run`: after_borrow, the wait, after and the comparison, the transcripts, the unwind."""
    live = 'live'; note = lambda t: add_note(rec, t)
    ob = observe(v1, me, block=b['block'])
    rec['phases']['after_borrow'] = phase_entry(ob); write(rec, a.out_dir)
    print('after the borrow (block %d): live utilization %.4f, APR %s, free %s USDC' % (ob['block'], ob['markets'][live]['utilization'],
                                                                                     _pct(ob['markets'][live]['apr']), _usdc(ob['markets'][live]['free'])))
    print('waiting for the agent: poll every %d s, at most %d s (until %s)' % (a.poll_s, a.timeout_s, sc.utc(time.time() + a.timeout_s)))
    acts, last, why = poll(v1, me, rec['started_block'], a)
    rec['actions'] = acts
    add_note(rec, 'wait ended: %s' % why)
    try: record_after(rec, v1, me, acts, last, ob)
    except (subprocess.CalledProcessError, OSError, ValueError, KeyError, IndexError) as e:
        add_note(rec, 'the after reading failed: %s' % str(e)[-200:])
    idx2 = transcripts_index(note)
    for t in new_transcripts(idx1, idx2):
        try: rec['transcripts'].append(transcript_summary(t, http_json(TRANSCRIPTS_URL + '/' + t['name'])))
        except Exception as e: add_note(rec, 'transcript %s not read: %s' % (t['name'], e))
    if idx1 is None or idx2 is None: add_note(rec, 'the transcript diff is incomplete: an index fetch failed')
    write(rec, a.out_dir)
    try: do_unwind(rec, v1, me, False)
    except (SystemExit, subprocess.CalledProcessError) as e:
        fail(rec, a.out_dir, 'unwind failed: %s' % str(getattr(e, 'code', e)).strip()[-300:])
    rec['outcome'] = {'stopped': why, 'agent_actions': [x['kind'] for x in acts]}
    write(rec, a.out_dir)


def record_after(rec, v1, me, acts, last, ob):
    """`after` at the block of the agent's first action that ends the wait (a migrate, or an action that cleared v1's
    live debt), else at the last reading. The comparison reads the block before that action with interest accrued to
    the action block's time, so the only change is the action itself."""
    stop = next((x for x in acts if x['kind'] == 'migrate'), None) or next((x for x in acts if x['kind'] == 'exit' and x['market'] == 'live'), None)
    if stop:
        n = stop['block']; after = observe(v1, me, block=n); _, ts = block_ts(n)
        pre = observe(v1, me, block=n - 1, accrue_ts=ts)
        rec['phases']['before_action'] = phase_entry(pre)
        basis = 'The agent\'s %s in block %d; the debt before is v1\'s live and backup debt at block %d accrued to block %d.' % (stop['kind'], n, n - 1, n)
    else:
        after = last or observe(v1, me); pre = ob
        basis = 'No migrate or exit: before is the reading after the borrow, so the debt difference includes the interest between the two blocks.'
    rec['phases']['after'] = phase_entry(after)
    pick = lambda o: {n: {'coll': o['positions']['v1'][n]['coll'], 'debt': o['positions']['v1'][n]['debt']} for n in MARKETS}
    c = compare(pick(pre), pick(after))
    c.update({'block_before': pre['block'], 'block_after': after['block'], 'basis': basis})
    if stop and stop['kind'] == 'migrate':
        c['event'] = {k: stop[k] for k in ('fraction', 'debt_moved_units', 'collateral_moved_wei')}
    rec['comparison'] = c
    add_note(rec, 'v1 collateral before %s, after %s WETH (difference %s wei); debt before %s, after %s USDC (difference %s units)' % (
        _weth(c['collateral_before_wei']), _weth(c['collateral_after_wei']), c['collateral_diff_wei'],
        _usdc(c['debt_before_units']), _usdc(c['debt_after_units']), c['debt_diff_units']))


def dry_run(a, o, sz, bad, path):
    for b in bad: print('WARNING (a real run refuses): ' + b)
    if not bad: print('preconditions: all hold')
    v1, me = ADDR['v1'], ADDR['wallet']
    print('-- the plan (a real run, under the rebalancer lock)')
    print('  1. GET %s (the transcripts index), then read `before` at the latest block' % TRANSCRIPTS_URL)
    print('  2. wallet: WETH approve(Morpho, %d); Morpho supplyCollateral(live, %d, wallet, 0x); Morpho borrow(live, %d, 0, wallet, wallet)' % (
        sz['collateral'], sz['collateral'], max(sz['borrow'], 0)))
    print('  3. poll every %d s for at most %d s: v1\'s Migrated, Reduced, Exited events (caller %s) since the start block; stop at a '
          'Migrated or at v1\'s live borrow shares 0' % (a.poll_s, a.timeout_s, v1))
    print('  4. `after` at the block of the agent\'s action (or the latest at timeout); the comparison reads the block before it')
    print('  5. GET the transcripts index again; download each new transcript')
    print('  6. unwind: USDC approve(Morpho, debt x 1.01); Morpho repay(live, 0, all borrow shares, wallet, 0x); withdrawCollateral(live, all, wallet, wallet)')
    print('  7. write %s and %s%s' % (path, path[:-5] + '.md', ' (EXISTS: a real run refuses)' if os.path.exists(path) else ''))
    idx = transcripts_index(print)
    print('transcripts index now: %s entries' % (len(idx['transcripts']) if idx else 'not read'))
    print('-- `before` (this reading, in the record\'s shape)')
    print(json.dumps(phase_entry(o), indent=1))
    print('dry-run: nothing sent, nothing written')


def unwind(a):
    sc.check_chain()
    v1, me = addresses(a.vault)
    path = os.path.join(a.out_dir, SCENE + '.json')
    rec = None
    if not a.dry_run and os.path.exists(path):
        with open(path) as f: rec = json.load(f)
    fd = None if a.dry_run else rp.lock()
    try:
        txs = do_unwind(rec, v1, me, a.dry_run)
    finally:
        if fd is not None: os.close(fd)
    if txs: print('transactions: ' + ' '.join(x['tx'] for x in txs))
    if rec is not None and txs:
        add_note(rec, 'unwind run separately at block %d' % txs[-1]['block']); write(rec, a.out_dir)
        print('recorded in %s' % path)


def main():
    ap = argparse.ArgumentParser(description='Venue stress scene on the live market (Base Sepolia only)')
    sub = ap.add_subparsers(dest='cmd', required=True)
    st = sub.add_parser('status')
    r = sub.add_parser('run'); r.add_argument('--dry-run', action='store_true')
    r.add_argument('--timeout-s', type=int, default=1800); r.add_argument('--poll-s', type=int, default=15)
    u = sub.add_parser('unwind'); u.add_argument('--dry-run', action='store_true')
    for x in (st, r):
        x.add_argument('--borrow-keep-units', type=int, default=100_000, help='USDC units left free on live (default 0.10 USDC)')
        x.add_argument('--collateral-ltv', type=float, default=0.5, help="the wallet's LTV on its borrow (default 0.5)")
    for x in (r, u): x.add_argument('--out-dir', default=os.path.join(ROOT, 'evidence', 'scenes'))
    for x in (st, r, u):
        x.add_argument('--vault', default=None, help="the watched vault's Base address (default: v1's, near.vault_base_address)")
        x.add_argument('--tag', default='v1', help='how that vault is named in the output (default v1)')
        x.add_argument('--scene', default='venue-stress', help='output file name (default venue-stress; the exit variant uses venue-stress-exit)')
        x.add_argument('--expect', choices=('migrate', 'exit'), default='migrate', help='migrate: the vault lists backup, the agent moves there; exit: live is its only market, the agent exits')
    a = ap.parse_args()
    if a.cmd in ('status', 'run'):
        if not 0 < a.collateral_ltv < 0.86: raise SystemExit('--collateral-ltv must be above 0 and below the LLTV 0.86')
        if a.borrow_keep_units < 0: raise SystemExit('--borrow-keep-units must not be negative')
    if a.cmd == 'run' and (a.poll_s <= 0 or a.timeout_s <= 0): raise SystemExit('--poll-s and --timeout-s must be positive')
    global SCENE, TAG, EXPECT, UNWIND_CMD
    SCENE, TAG, EXPECT = a.scene, a.tag, a.expect
    extra = ''.join(' --%s %s' % (k, v) for k, v in (('vault', a.vault), ('tag', a.tag), ('scene', a.scene)) if v and v != {'tag': 'v1', 'scene': 'venue-stress'}.get(k))
    UNWIND_CMD += extra
    {'status': status, 'run': run, 'unwind': unwind}[a.cmd](a)


if __name__ == '__main__':
    main()
