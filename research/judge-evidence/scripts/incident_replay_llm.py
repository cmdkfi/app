#!/usr/bin/env python3
"""LLM incident-replay evaluation over the incident_replay dataset (8 INC-*, 6 CTL-* windows).

Three judges on the same timeline:
  J1  LLM, text-only sources (news, governance, official_x, security_alert, defillama)
  J2  LLM, text + onchain items
  J3  rule-only keeper (no LLM), thresholds fixed below (J3_RULES)

LLM: NEAR AI Cloud, model z-ai/glm-5.3-flash (TEE-hosted), non-streaming.
  fast: thinking off, temperature 0, seed 7, max_tokens 400, response_format json_object
  deep: thinking on,  temperature 0, seed 7, max_tokens 4000 (run only when fast flags:
        risk high/critical or action exit/reduce_to_target)
About 10% of calls (hash-sampled) get their TEE signature fetched and checked
(signature_kind == provider_tee, text == model:sha256(request):sha256(response)).

No look-ahead: at step t the judge sees only items whose *effective* time <= t (at most the
latest 25), shown with replay_text only (never verbatim_text, notes, holder_state,
signal_role, observable_metrics, venue annotations or ground_truth). A few on-chain
replay_texts summarise readings taken after the item's own timestamp; those items are
moved to the time of their last reading (EFFECTIVE_TIME_OVERRIDES) or have the
hindsight sentence removed (TEXT_CUTS).

Usage:
  python3 incident_replay_llm.py dry      # build tasks, print counts and one sample prompt
  python3 incident_replay_llm.py run      # call the API (cached; safe to re-run)
  python3 incident_replay_llm.py analyze  # compute metrics -> results.json + tables.md
  python3 incident_replay_llm.py deepall  # 25 Oct: thinking on (max_tokens 8000) at EVERY step, the
                                          # production mode decided on 25 Oct; reported as mode deep_all.
                                          # Incident chains stop after deep has given both an exit and a
                                          # high/critical risk (later steps cannot change first-time
                                          # metrics); control chains run every step.
Options: --data DIR (dataset folder), --out DIR (cache/results folder), --parallel N (default 4)
The API key is read from ~/.config/prime/nearai_key and never printed or stored.
"""
import argparse, hashlib, json, os, re, statistics, sys, threading, time, urllib.error, urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone

DEFAULT_DATA = os.path.expanduser(
    '[local path removed; pass --data]')
BASE = 'https://cloud-api.near.ai/v1'
MODEL = 'z-ai/glm-5.3-flash'
PRICE_IN, PRICE_OUT = 0.08 / 1e6, 0.50 / 1e6   # USD per token
MAX_ITEMS = 25
PARALLEL = 4
TEXT_TYPES = {'news', 'governance', 'official_x', 'security_alert', 'defillama'}
RISKS = ['low', 'elevated', 'high', 'critical']
ACTIONS = ['hold', 'reduce_light', 'reduce_to_target', 'exit', 'relever']

# ---------------------------------------------------------------- runs (window x archetype)
# Position text is ours (the files' archetype descriptions sometimes carry hindsight, e.g.
# "before the run" or "added because the pool was drained").
RUNS = [
    dict(run='INC-01/weth', file='INC-01-aave-rseth-kelp.json', arch='weth_supplier',
         venue='Aave V3 Ethereum Core', position='supplies WETH (about $1,000), no debt', side='supply'),
    dict(run='INC-02/relend', file='INC-02-stream-xusd-elixir-deusd.json',
         arch='usdc_supplier_curated_vault_concentrated',
         venue="Morpho on Ethereum, MetaMorpho vault 'Relend USDC' (~$40M TVL)",
         position='about $1,000 USDC deposited in the vault, no debt; the vault lends across several Morpho USDC '
                  'markets, including sdeUSD/USDC (Elixir sdeUSD collateral, LLTV 91.5%) at roughly 7-13% of assets',
         side='supply'),
    dict(run='INC-03/gauntlet', file='INC-03-resolv-usr.json', arch='usdc_supplier_morpho_vault_with_usr_exposure',
         venue="Morpho on Ethereum, MetaMorpho vault 'Gauntlet USDC Core' (~$40M TVL)",
         position='about $1,000 USDC deposited in the vault, no debt; the curator\'s allocator moves vault liquidity '
                  'across its listed markets, which include wstUSR/USDC (Resolv wstUSR collateral, LLTV 91.5%)',
         side='supply'),
    dict(run='INC-04/cbeth-borrower', file='INC-04-moonwell-cbeth-oracle.json', arch='cbeth_coll_usdc_debt',
         venue='Moonwell Base Core', position='cbETH supplied as collateral and USDC borrowed against it; '
                                            'health factor about 1.6 at market prices', side='borrow'),
    dict(run='INC-05/wsteth-looper', file='INC-05-aave-wsteth-capo.json', arch='wsteth_coll_weth_debt_emode',
         venue='Aave V3 Ethereum Core, ETH-correlated E-Mode',
         position='leveraged loop: wstETH collateral, WETH debt; health factor about 1.02', side='borrow'),
    dict(run='INC-06/usdc', file='INC-06-rhea-lend.json', arch='usdc_supplier',
         venue='Rhea Lend (ex-Burrow) on NEAR, contract.main.burrow.near',
         position='supplies native USDC (about $1,000), no debt', side='supply'),
    dict(run='INC-07/intents', file='INC-07-near-intents-deposit-bug.json', arch='funds_in_intents_balance',
         venue='NEAR Intents (intents.near Verifier contract)',
         position='holds about $1,000 of BSC-USDT (bridged through HOT Omni) as a balance inside intents.near, '
                  'to be swapped or withdrawn later', side='supply'),
    dict(run='INC-08/usdc', file='INC-08-moonwell-mamo-manipulation.json', arch='usdc_supplier',
         venue='Moonwell Base Core', position='supplies USDC to the mUSDC market (about $1,000), no debt', side='supply'),
    dict(run='CTL-01/usdc', file='CTL-01-aave-routine-day.json', arch='usdc_supplier',
         venue='Aave V3 Ethereum Core', position='supplies USDC (about $1,000), no debt', side='supply'),
    dict(run='CTL-02/steakhouse', file='CTL-02-morpho-routine-day.json', arch='usdc_supplier_steakhouse_vault',
         venue="Morpho on Ethereum, MetaMorpho vault 'Steakhouse USDC'",
         position='about $1,000 USDC deposited in the vault, no debt; the vault lends against BTC and ETH '
                  'collateral (WBTC, cbBTC, wstETH, WETH)', side='supply'),
    dict(run='CTL-03/cbeth-borrower', file='CTL-03-moonwell-base-routine-day.json', arch='cbeth_coll_usdc_debt',
         venue='Moonwell Base Core', position='cbETH supplied as collateral and USDC borrowed against it; '
                                            'health factor about 1.6 at market prices', side='borrow'),
    dict(run='CTL-04/usdc', file='CTL-04-rhea-near-routine-day.json', arch='usdc_supplier',
         venue='Rhea Lend (ex-Burrow) on NEAR, contract.main.burrow.near',
         position='supplies native USDC (about $1,000), no debt', side='supply'),
    dict(run='CTL-05/morpho-vault', file='CTL-05-lazy-summer-noisy-day.json', arch='usdc_supplier_morpho_major_vault',
         venue="Morpho on Ethereum, MetaMorpho vault 'Steakhouse USDC'",
         position='about $1,000 USDC deposited in the vault, no debt; the vault lends against BTC and ETH '
                  'collateral (cbBTC, WBTC, wstETH)', side='supply'),
    dict(run='CTL-06/weth', file='CTL-06-aave-rseth-precautionary-freeze-2025.json', arch='weth_supplier',
         venue='Aave V3 Ethereum Core', position='supplies WETH (about $1,000), no debt', side='supply'),
    # secondary pair for the INC-01 vs CTL-06 comparison
    dict(run='INC-01/rseth-looper', file='INC-01-aave-rseth-kelp.json', arch='rseth_coll_weth_debt',
         venue='Aave V3 Ethereum Core, rsETH E-Mode',
         position='leveraged restaking loop: rsETH collateral, WETH debt; health factor about 1.03', side='borrow',
         secondary=True),
    dict(run='CTL-06/rseth-looper', file='CTL-06-aave-rseth-precautionary-freeze-2025.json', arch='rseth_coll_weth_debt',
         venue='Aave V3 Ethereum Core, rsETH E-Mode',
         position='leveraged restaking loop: rsETH collateral, WETH debt; health factor about 1.03', side='borrow',
         secondary=True),
]

# ---------------------------------------------------------------- look-ahead fixes
EFFECTIVE_TIME_OVERRIDES = {   # item -> time of the last reading its replay_text reports
    'INC01-004': '2026-04-18T17:44:00Z', 'INC01-008': '2026-04-18T19:20:00Z',
    'INC01-012': '2026-04-18T19:36:00Z', 'INC01-015': '2026-04-18T20:05:11Z',
    'INC02-012': '2025-11-03T13:00:00Z', 'INC03-006': '2026-03-22T02:45:11Z',
    'INC03-013': '2026-03-22T04:00:00Z', 'INC04-009': '2026-02-15T18:45:00Z',
    'INC04-017': '2026-02-17T12:55:00Z', 'INC06-003': '2026-04-16T08:15:00Z',
    'INC06-012': '2026-04-16T09:44:00Z', 'INC06-031': '2026-04-30T23:59:00Z',
    'INC07-022': '2026-10-02T23:59:00Z', 'INC08-006': '2026-08-27T09:21:09Z',
    'INC08-009': '2026-08-27T09:40:00Z', 'INC08-011': '2026-08-27T10:10:00Z',
    'INC08-016': '2026-08-27T11:10:00Z', 'INC08-017': '2026-08-27T11:19:15Z',
    'CTL04-007': '2026-10-21T16:05:00Z',
}
TEXT_CUTS = {'CTL03-004': ' Most of it was repaid by 16 Jan 00:00Z (borrows 138.1).'}

def cmd_retry(args):
    """Sensitivity check: re-run, with max_tokens 8000, every deep call that stopped on the
    4000-token limit (finish_reason == 'length'). The primary results keep the 4000-token
    verdicts (invalid -> hold); the re-runs are reported separately."""
    tasks, _ = build_all(args.data)
    cl = Client(args.out)
    todo = []
    for t in tasks:
        r = cl.cache.get(hashlib.sha256(request_body(t['user'], 'deep')).hexdigest())
        if r and r.get('ok') and r.get('finish_reason') == 'length':
            todo.append(t)
    print('re-running %d truncated deep calls with max_tokens 8000' % len(todo), flush=True)
    with ThreadPoolExecutor(PARALLEL) as ex:
        futs = [ex.submit(cl.call, request_body(t['user'], 'deep8k'), 'deep8k',
                          '%s|%s|%s' % (t['run'], t['judge'], F(t['t']))) for t in todo]
        for fut in as_completed(futs):
            r = fut.result()
            o = r.get('parsed') or {}
            print(r['tag'], r.get('finish_reason'), o.get('risk'), o.get('action'), r.get('valid'), flush=True)


# ---------------------------------------------------------------- J3 rule keeper
# Fixed thresholds (round numbers, the same for every window):
#   R1 utilisation of the position's own market (or a market the vault lends into) >= 95%
#      or a vault's withdrawable share <= 5%
#   R2 |protocol oracle vs market price| >= 5% for the position's collateral or the vault's collateral
#   R3 tokens actually held by the contract / internal ledger <= 95% for the position's asset
# Any rule firing -> exit. Each run lists the observable_metrics keys that belong to the
# position's market (restricted to the listed item ids where a generic key is ambiguous).
J3_RULES = {'R1_util_pct': 95.0, 'R1_withdrawable_pct': 5.0, 'R2_oracle_dev_pct': 5.0, 'R3_backing_pct': 95.0}
J3_MAP = {
    'INC-01/weth': dict(util=['weth_util_pct', 'weth_util_after_pct', 'series_5min_weth_util_pct']),
    'INC-02/relend': dict(util=['utilization_pct'], util_items=['INC02-008', 'INC02-011', 'INC02-013'],
                          wdr=['withdrawable_pct'], wdr_items=['INC02-016', 'INC02-021', 'INC02-024']),
    'INC-03/gauntlet': dict(util=['utilization_pct'], wdr=['vault_withdrawable_pct'], dev=['oracle_vs_market_pct']),
    'INC-04/cbeth-borrower': dict(util=['cbeth_util_pct', 'cbeth_util_pct_1845'], dev=['oracle_vs_market_pct']),
    'INC-05/wsteth-looper': dict(util=['wsteth_util_pct'], dev=['deviation_pct']),
    'INC-06/usdc': dict(util=['usdc_utilization_pct', 'usdc_utilization_pct_0815'],
                        back=['usdc_held_vs_ledger_available_pct'],
                        back_pair=[('usdc_token_balance_held', 'usdc_ledger_available')]),
    'INC-07/intents': dict(back=['held_vs_ledger_pct']),
    'INC-08/usdc': dict(util=['usdc_util_pct', 'usdc_util_pct_0950']),
    'CTL-01/usdc': dict(util=['usdc_util_pct', 'usdc_util_pct_0000', 'usdc_util_pct_0300', 'usdc_5min', 'series_3h']),
    'CTL-02/steakhouse': dict(util=['util_wbtc_usdc_pct', 'util_cbbtc_usdc_pct', 'util_wsteth_usdc_pct', 'util_weth_usdc_pct']),
    'CTL-03/cbeth-borrower': dict(util=['cbeth_util_pct'], dev=['oracle_vs_market_pct']),
    'CTL-04/usdc': dict(util=['usdc_utilization_pct', 'usdc_utilization_pct_after'],
                        back_pair=[('usdc_token_balance_held', 'usdc_ledger_available'),
                                   ('usdc_held_after', 'usdc_ledger_available_after')]),
    'CTL-05/morpho-vault': dict(util=['eth_cbbtc_usdc_util_pct', 'eth_wbtc_usdc_util_pct', 'eth_wsteth_usdc_util_pct',
                                      'eth_cbbtc_usdc_util_pct_0500']),
    'CTL-06/weth': dict(util=['weth_util_pct']),
    # rsETH loopers: no metric in the files maps to these thresholds (rsETH oracle never deviates
    # from a market price that the files record) -> J3 not applicable
}

SYSTEM = """You are the risk judge of an automated DeFi position keeper. You see observations from an allowlist of sources and decide how risky the user's position is right now and what the keeper should do.

Observation source types: onchain = a reading from our own chain monitor; governance = a protocol forum post; official_x = a post on X by a protocol, team or named person; security_alert = a post by a security firm; news = a news article or headline; defillama = an entry in the DeFiLlama hacks feed. You only know what these observations say; nothing after the current time is known.

Actions:
- hold: keep the position as it is
- reduce_light: trim a small part of the exposure (about 25%)
- reduce_to_target: cut exposure to a defensive target (about 50-75% smaller; for a borrower, repay debt to a safe health factor)
- exit: withdraw or unwind the whole position now
- relever: add exposure

Risk levels: low, elevated, high, critical.

Staying put during a real incident can leave funds stuck or lost even when prices look normal (exploits, oracle faults, bad collateral, liquidity runs, pauses and freezes). Exiting without need also costs (fees, slippage, lost yield, re-entry), so do not react to noise or to incidents that do not touch this position.

Reply with one JSON object only, no other text:
{"risk": "low|elevated|high|critical", "action": "hold|reduce_light|reduce_to_target|exit|relever", "reasons": ["short reason", "..."], "source_ids": ["ids of the observations that drove the decision"]}"""

USER_TMPL = """You protect a user's position on {venue}: {position}. Venue risk matters; you cannot move funds, only propose an action; the corridor limits enforce safety.

Current time: {now}

Observations ({n} shown{trunc}, oldest first):

{obs}

Assess the risk to this position now and propose one action. Reply with the JSON object only."""


# ---------------------------------------------------------------- helpers
def P(s):
    return datetime.strptime(s, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc)


def F(d):
    return d.strftime('%Y-%m-%dT%H:%M:%SZ')


def load_window(data_dir, fname):
    d = json.load(open(os.path.join(data_dir, fname)))
    items = []
    for it in d['items']:
        eff = it.get('effective_time_utc', EFFECTIVE_TIME_OVERRIDES.get(it['id'], it['timestamp_utc']))
        txt = it['replay_text']
        if it['id'] in TEXT_CUTS:
            assert TEXT_CUTS[it['id']] in txt, it['id']
            txt = txt.replace(TEXT_CUTS[it['id']], '')
        author = 'chain monitor' if it['source_type'] == 'onchain' else it['author']
        items.append(dict(id=it['id'], t=P(eff), t_orig=P(it['timestamp_utc']), src=it['source_type'],
                          author=author, text=txt.strip(), om=it.get('observable_metrics') or {},
                          hs=it.get('holder_state') or {}, source=it.get('source'),
                          support=it.get('supports_exit', False)))
    items.sort(key=lambda x: (x['t'], x['t_orig'], x['id']))
    return d, items


def judge_items(items, judge):
    return [x for x in items if judge == 'J2' or x['src'] in TEXT_TYPES]


def build_steps(d, items, judge):
    """Steps = distinct effective times (inside the replay window) of the judge's visible items."""
    vis = judge_items(items, judge)
    for k, x in enumerate(vis, 1):
        x.setdefault('oid', {})[judge] = 'O%02d' % k
    w0, w1 = P(d['replay_window']['start_utc']), P(d['replay_window']['end_utc'])
    times = sorted({x['t'] for x in vis if w0 <= x['t'] <= w1})
    steps = []
    for t in times:
        seen = [x for x in vis if x['t'] <= t]
        steps.append((t, seen[-MAX_ITEMS:], len(seen)))
    return steps


def render(run, judge, t, shown, total):
    obs = '\n\n'.join('[%s] %s | %s | %s\n%s' % (x['oid'][judge], F(x['t']), x['src'], x['author'], x['text'])
                      for x in shown)
    trunc = '' if total <= len(shown) else ' (the latest %d of %d)' % (len(shown), total)
    return USER_TMPL.format(venue=run['venue'], position=run['position'], now=F(t), n=len(shown),
                            trunc=trunc, obs=obs)


def request_body(user, mode):
    b = {'model': MODEL, 'messages': [{'role': 'system', 'content': SYSTEM}, {'role': 'user', 'content': user}],
         'stream': False, 'temperature': 0, 'seed': 7}
    if mode == 'fast':
        b.update(max_tokens=400, response_format={'type': 'json_object'},
                 chat_template_kwargs={'enable_thinking': False})
    elif mode == 'deep8k':      # sensitivity re-run of deep calls that hit the 4000-token limit
        b.update(max_tokens=8000)
    else:
        b.update(max_tokens=4000)
    return json.dumps(b, ensure_ascii=False).encode()


def extract_json(content):
    """Return (obj or None, error). Strips <think>...</think>, takes the last valid JSON object."""
    if not content:
        return None, 'empty content'
    s = re.sub(r'(?s)<think>.*?</think>', '', content)
    if '</think>' in s:
        s = s.split('</think>')[-1]
    cands = []
    for i, ch in enumerate(s):
        if ch != '{':
            continue
        depth, ins, esc = 0, False, False
        for j in range(i, len(s)):
            c = s[j]
            if ins:
                esc = (c == '\\' and not esc)
                if c == '"' and not esc:
                    ins = False
                continue
            if c == '"':
                ins = True
            elif c == '{':
                depth += 1
            elif c == '}':
                depth -= 1
                if depth == 0:
                    cands.append(s[i:j + 1])
                    break
    for c in reversed(cands):
        try:
            o = json.loads(c)
        except Exception:
            continue
        if isinstance(o, dict) and 'risk' in o:
            return o, None
    return None, 'no JSON object with "risk"'


def validate(o):
    if not isinstance(o, dict):
        return False
    return (o.get('risk') in RISKS and o.get('action') in ACTIONS and isinstance(o.get('reasons'), list)
            and isinstance(o.get('source_ids'), list))


# ---------------------------------------------------------------- API
class Client:
    def __init__(self, out_dir):
        self.key = open(os.path.expanduser('~/.config/prime/nearai_key')).read().strip()
        self.cache_path = os.path.join(out_dir, 'calls.jsonl')
        self.cache = {}
        if os.path.exists(self.cache_path):
            for line in open(self.cache_path):
                r = json.loads(line)
                self.cache[r['req_sha256']] = r
        self.lock = threading.Lock()
        self.consec_err = 0

    def _http(self, method, url, body=None, headers=None, timeout=900):
        h = {'Authorization': 'Bearer ' + self.key, 'Accept-Encoding': 'identity'}
        h.update(headers or {})
        r = urllib.request.Request(url, data=body, method=method, headers=h)
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return resp.read()

    def call(self, body, mode, tag):
        rh = hashlib.sha256(body).hexdigest()
        if rh in self.cache:
            return self.cache[rh]
        if self.consec_err >= 6:
            raise RuntimeError('too many consecutive API errors; stopping')
        raw, err, dt = None, None, None
        for attempt in range(3):
            t0 = time.time()
            try:
                raw = self._http('POST', BASE + '/chat/completions', body,
                                 {'Content-Type': 'application/json', 'x-no-aliasing': 'true'})
                dt = time.time() - t0
                break
            except urllib.error.HTTPError as e:
                err = 'HTTP %s %s' % (e.code, e.read()[:200].decode('utf-8', 'replace'))
            except Exception as e:
                err = repr(e)[:200]
            time.sleep(3 * (attempt + 1))
        if raw is None:
            with self.lock:
                self.consec_err += 1
            print('ERROR', tag, mode, err, file=sys.stderr)
            return dict(req_sha256=rh, ok=False, error=err, mode=mode, tag=tag)
        with self.lock:
            self.consec_err = 0
        d = json.loads(raw)
        msg = d['choices'][0]['message']
        content = msg.get('content') or ''
        obj, perr = extract_json(content)
        rec = dict(req_sha256=rh, resp_sha256=hashlib.sha256(raw).hexdigest(), ok=True, mode=mode, tag=tag,
                   id=d.get('id'), latency_s=round(dt, 3), usage=d.get('usage', {}),
                   finish_reason=d['choices'][0].get('finish_reason'), content=content,
                   reasoning_chars=len(msg.get('reasoning_content') or msg.get('reasoning') or ''),
                   parsed=obj, parse_error=perr, valid=validate(obj))
        # ~10% hash-sampled signature check
        if int(rh[:8], 16) % 10 == 0 and d.get('id'):
            rec['sig'] = self.check_sig(d['id'], body, raw)
        with self.lock:
            self.cache[rh] = rec
            with open(self.cache_path, 'a') as f:
                f.write(json.dumps(rec, ensure_ascii=False) + '\n')
        return rec

    def check_sig(self, rid, body, raw):
        expect = '%s:%s:%s' % (MODEL, hashlib.sha256(body).hexdigest(), hashlib.sha256(raw).hexdigest())
        for attempt in range(3):
            time.sleep(1.5 * (attempt + 1))
            try:
                s = json.loads(self._http('GET', '%s/signature/%s?model=%s&signing_algo=ecdsa' % (BASE, rid, MODEL)))
                return dict(kind=s.get('signature_kind'), text_match=s.get('text') == expect,
                            signing_address=s.get('signing_address'),
                            passed=s.get('signature_kind') == 'provider_tee' and s.get('text') == expect)
            except Exception as e:
                last = repr(e)[:200]
        return dict(kind=None, text_match=False, passed=False, error=last)


# ---------------------------------------------------------------- task building
def build_all(data_dir):
    tasks = []   # one per (run, judge, step)
    windows = {}
    for run in RUNS:
        if run['file'] not in windows:
            windows[run['file']] = load_window(data_dir, run['file'])
        d, items = windows[run['file']]
        items = [dict(x) for x in items]          # per-run copy (oid assignment)
        for judge in ('J1', 'J2'):
            for t, shown, total in build_steps(d, items, judge):
                tasks.append(dict(run=run['run'], judge=judge, t=t, user=render(run, judge, t, shown, total),
                                  shown=[x['id'] for x in shown],
                                  oid_map={x['oid'][judge]: x['id'] for x in shown}))
    return tasks, windows


def cmd_dry(args):
    tasks, _ = build_all(args.data)
    from collections import Counter
    c = Counter((t['run'], t['judge']) for t in tasks)
    for k in sorted(c):
        print(k, c[k])
    print('total fast calls:', len(tasks))
    lens = [len(t['user']) + len(SYSTEM) for t in tasks]
    print('prompt chars median %d max %d' % (statistics.median(lens), max(lens)))
    ex = [t for t in tasks if t['run'] == 'INC-01/weth' and t['judge'] == 'J2'][5]
    print('\n===== SYSTEM =====\n' + SYSTEM + '\n===== USER (sample) =====\n' + ex['user'])


def cmd_run(args):
    tasks, _ = build_all(args.data)
    if args.only:
        tasks = [t for t in tasks if t['run'] in args.only.split(',')]
    if args.limit:
        tasks = tasks[:args.limit]
    cl = Client(args.out)
    t0 = time.time()

    def fast(t):
        rec = cl.call(request_body(t['user'], 'fast'), 'fast', '%s|%s|%s' % (t['run'], t['judge'], F(t['t'])))
        return t, rec

    flagged = []
    done = 0
    with ThreadPoolExecutor(PARALLEL) as ex:
        for fut in as_completed([ex.submit(fast, t) for t in tasks]):
            t, rec = fut.result()
            done += 1
            o = rec.get('parsed') if rec.get('valid') else None
            if o and (o['risk'] in ('high', 'critical') or o['action'] in ('exit', 'reduce_to_target')):
                flagged.append(t)
            if done % 25 == 0:
                print('fast %d/%d  %.0fs' % (done, len(tasks), time.time() - t0), flush=True)
    print('fast done: %d calls, %d flagged for deep' % (len(tasks), len(flagged)), flush=True)
    if args.no_deep:
        return

    # Deep pass. Deep calls take ~5 min each, so for incident windows each (run, judge) chain walks
    # its flagged steps in time order and stops once deep has confirmed both an exit and a
    # high/critical risk: later deep calls cannot change any first-time metric. Control windows
    # get deep on every flagged step (false-alarm counts need all of them).
    kinds = {r['run']: ('control' if r['run'].startswith('CTL') else 'incident') for r in RUNS}
    chains = {}
    for t in sorted(flagged, key=lambda x: x['t']):
        chains.setdefault((t['run'], t['judge']), []).append(t)
    counter = {'n': 0}

    def chain(key):
        got_exit = got_high = False
        n = 0
        for t in chains[key]:
            if kinds[key[0]] == 'incident' and got_exit and got_high:
                break
            rec = cl.call(request_body(t['user'], 'deep'), 'deep', '%s|%s|%s' % (t['run'], t['judge'], F(t['t'])))
            n += 1
            o = rec.get('parsed') if rec.get('valid') else {}   # strict schema decides stopping
            got_exit |= o.get('action') == 'exit'
            got_high |= o.get('risk') in ('high', 'critical')
            with cl.lock:
                counter['n'] += 1
                if counter['n'] % 5 == 0:
                    print('deep %d calls  %.0fs' % (counter['n'], time.time() - t0), flush=True)
        return key, n, len(chains[key])

    with ThreadPoolExecutor(PARALLEL) as ex:
        for fut in as_completed([ex.submit(chain, k) for k in chains]):
            key, n, tot = fut.result()
            print('deep chain %s: %d of %d flagged steps' % ('/'.join(key), n, tot), flush=True)
    print('all done in %.0fs' % (time.time() - t0))


def cmd_deepall(args):
    """Thinking on (max_tokens 8000, the 'deep8k' request body) at every step, no fast screen.
    Same cache, same request hashes as `retry`, so the 17 earlier deep8k calls are reused."""
    tasks, _ = build_all(args.data)
    if args.only:
        tasks = [t for t in tasks if t['run'] in args.only.split(',')]
    cl = Client(args.out)
    kinds = {r['run']: ('control' if r['run'].startswith('CTL') else 'incident') for r in RUNS}
    chains = {}
    for t in sorted(tasks, key=lambda x: x['t']):
        chains.setdefault((t['run'], t['judge']), []).append(t)
    t0 = time.time()
    counter = {'n': 0}

    def chain(key):
        got_exit = got_high = False
        n = 0
        for t in chains[key]:
            if kinds[key[0]] == 'incident' and got_exit and got_high:
                break
            rec = cl.call(request_body(t['user'], 'deep8k'), 'deep8k', '%s|%s|%s' % (t['run'], t['judge'], F(t['t'])))
            n += 1
            o = rec.get('parsed') if rec.get('valid') else {}
            got_exit |= o.get('action') == 'exit'
            got_high |= o.get('risk') in ('high', 'critical')
            with cl.lock:
                counter['n'] += 1
                if counter['n'] % 5 == 0:
                    print('deepall %d calls  %.0fs' % (counter['n'], time.time() - t0), flush=True)
        return key, n, len(chains[key])

    print('deepall: %d chains, %d steps' % (len(chains), len(tasks)), flush=True)
    with ThreadPoolExecutor(args.parallel) as ex:
        for fut in as_completed([ex.submit(chain, k) for k in chains]):
            key, n, tot = fut.result()
            print('deepall chain %s: %d of %d steps' % ('/'.join(key), n, tot), flush=True)
    print('all done in %.0fs' % (time.time() - t0))


def cmd_retry(args):
    """Sensitivity check: re-run, with max_tokens 8000, every deep call that stopped on the
    4000-token limit (finish_reason == 'length'). The primary results keep the 4000-token
    verdicts (invalid -> hold); the re-runs are reported separately."""
    tasks, _ = build_all(args.data)
    cl = Client(args.out)
    todo = []
    for t in tasks:
        r = cl.cache.get(hashlib.sha256(request_body(t['user'], 'deep')).hexdigest())
        if r and r.get('ok') and r.get('finish_reason') == 'length':
            todo.append(t)
    print('re-running %d truncated deep calls with max_tokens 8000' % len(todo), flush=True)
    with ThreadPoolExecutor(PARALLEL) as ex:
        futs = [ex.submit(cl.call, request_body(t['user'], 'deep8k'), 'deep8k',
                          '%s|%s|%s' % (t['run'], t['judge'], F(t['t']))) for t in todo]
        for fut in as_completed(futs):
            r = fut.result()
            o = r.get('parsed') or {}
            print(r['tag'], r.get('finish_reason'), o.get('risk'), o.get('action'), r.get('valid'), flush=True)


# ---------------------------------------------------------------- J3
def _hhmm_series(base_date, dct, field=None):
    """Dict keyed 'HH:MM' (insertion order, may wrap past midnight) -> [(datetime, value)]."""
    out, day, prev = [], base_date, None
    for k, v in dct.items():
        m = re.fullmatch(r'(\d\d):(\d\d)(?::(\d\d))?', k)
        if not m:
            continue
        hm = (int(m.group(1)), int(m.group(2)), int(m.group(3) or 0))
        if prev and hm < prev:
            day = day + timedelta(days=1)
        prev = hm
        val = v.get(field) if (field and isinstance(v, dict)) else v
        if isinstance(val, (int, float)):
            out.append((day.replace(hour=hm[0], minute=hm[1], second=hm[2]), float(val)))
    return out


def metric_samples(item, key):
    """All (sample_time, value) for key in the item's observable_metrics. Plain scalars are dated at
    the item's effective time; time-suffixed keys and series carry their own sample (block) times.
    J3 is our own chain monitor, so it acts on each sample at that sample's time (never earlier)."""
    om, t_item = item['om'], item['t']
    base = item['t_orig'].replace(hour=0, minute=0, second=0)
    v = om.get(key)
    out = []
    if isinstance(v, (int, float)):
        m = re.search(r'_(\d\d)(\d\d)$', key)
        if m:   # e.g. usdc_util_pct_0950 -> 09:50 on the item's day (next day if >12 h earlier)
            st = base.replace(hour=int(m.group(1)), minute=int(m.group(2)))
            if st < item['t_orig'] - timedelta(hours=12):
                st += timedelta(days=1)
            out.append((st, float(v)))
        else:
            out.append((t_item, float(v)))
    elif isinstance(v, dict):
        first = next(iter(v.values()), None)
        out = _hhmm_series(base, v, 'util_pct' if isinstance(first, dict) else None)
    elif isinstance(v, list):
        for row in v:
            if isinstance(row, dict) and 'utc' in row:
                for kk, vv in row.items():
                    if re.search(r'usdc_util_pct$', kk) and isinstance(vv, (int, float)):
                        out.append((P(row['utc']), float(vv)))
    return out


def j3_run(run, items):
    """Returns time-sorted rule firings [{t, item, hits}] (t = sample time of the triggering reading)."""
    m = J3_MAP.get(run['run'])
    if m is None:
        return None
    ev = {}

    def add(st, item_id, msg):
        ev.setdefault((st, item_id), []).append(msg)
    for it in items:
        if it['src'] != 'onchain':
            continue
        if not m.get('util_items') or it['id'] in m['util_items']:
            for k in m.get('util', []):
                for st, v in metric_samples(it, k):
                    if v >= J3_RULES['R1_util_pct']:
                        add(st, it['id'], 'R1 %s=%.2f%%' % (k, v))
        if not m.get('wdr_items') or it['id'] in m['wdr_items']:
            for k in m.get('wdr', []):
                for st, v in metric_samples(it, k):
                    if v <= J3_RULES['R1_withdrawable_pct']:
                        add(st, it['id'], 'R1 %s=%.1f%%' % (k, v))
        for k in m.get('dev', []):
            for st, v in metric_samples(it, k):
                if abs(v) >= J3_RULES['R2_oracle_dev_pct']:
                    add(st, it['id'], 'R2 %s=%.2f%%' % (k, v))
        for k in m.get('back', []):
            for st, v in metric_samples(it, k):
                if v <= J3_RULES['R3_backing_pct']:
                    add(st, it['id'], 'R3 %s=%.2f%%' % (k, v))
        for kh, kl in m.get('back_pair', []):
            if isinstance(it['om'].get(kh), (int, float)) and it['om'].get(kl):
                v = 100.0 * it['om'][kh] / it['om'][kl]
                if v <= J3_RULES['R3_backing_pct']:
                    add(it['t'], it['id'], 'R3 %s/%s=%.1f%%' % (kh, kl, v))
    return [dict(t=st, item=iid, hits=h) for (st, iid), h in sorted(ev.items())]


# ---------------------------------------------------------------- analysis
def holder_ok(items, t, side):
    """holder_state of the latest item (original timestamp) at or before t."""
    prev = [x for x in items if x['t_orig'] <= t and x['hs']]
    if not prev:
        return 'unknown'
    hs = max(prev, key=lambda x: x['t_orig'])['hs']
    return hs.get('can_withdraw_supply' if side == 'supply' else 'can_repay_and_withdraw_collateral', 'unknown')


def label_at(gt, arch, t):
    for l in gt.get('expected_judge_labels', []):
        if l['archetype_id'] == arch and P(l['from_utc']) <= t < P(l['to_utc']):
            return l
    return None


def cmd_analyze(args):
    tasks, windows = build_all(args.data)
    cache = {}
    for line in open(os.path.join(args.out, 'calls.jsonl')):
        r = json.loads(line)
        cache[r['req_sha256']] = r
    res = {'runs': [], 'calls': {}}
    allrecs = []
    for run in RUNS:
        d, items = windows[run['file']]
        gt = d['ground_truth']
        ponr = gt['t_point_of_no_return'].get('timestamp_utc')
        ponr = P(ponr) if ponr else None
        ideal = next((a for a in gt.get('ideal_action', []) if a['archetype_id'] == run['arch']), {})
        latest_useful = P(ideal['latest_useful_time_utc']) if ideal.get('latest_useful_time_utc') else None
        out = dict(run=run['run'], kind=d['kind'], arch=run['arch'], ponr=F(ponr) if ponr else None,
                   latest_useful=F(latest_useful) if latest_useful else None, ideal=ideal.get('action'),
                   secondary=run.get('secondary', False), judges={})
        for judge in ('J1', 'J2'):
            tl = []
            for t in [x for x in tasks if x['run'] == run['run'] and x['judge'] == judge]:
                fr = cache.get(hashlib.sha256(request_body(t['user'], 'fast')).hexdigest())
                dr = cache.get(hashlib.sha256(request_body(t['user'], 'deep')).hexdigest())
                for r in (fr, dr):
                    if r:
                        allrecs.append(r)

                def verdict(r, lenient=False):
                    # strict: full schema (risk, action, reasons list, source_ids list), else hold.
                    # lenient: risk and action valid is enough (e.g. source_ids omitted).
                    if not r or not r.get('ok'):
                        return None
                    o = r.get('parsed') if isinstance(r.get('parsed'), dict) else {}
                    ok = r.get('valid') or (lenient and o.get('risk') in RISKS and o.get('action') in ACTIONS)
                    if ok:
                        sids = o.get('source_ids') if isinstance(o.get('source_ids'), list) else []
                        return dict(risk=o['risk'], action=o['action'], reasons=o.get('reasons') or [],
                                    src=[t['oid_map'].get(s, s) for s in sids if isinstance(s, str)],
                                    valid=True)
                    return dict(risk=None, action='hold', reasons=[], src=[], valid=False)
                fv, dv = verdict(fr), verdict(dr)
                dvl = verdict(dr, lenient=True)
                d8 = cache.get(hashlib.sha256(request_body(t['user'], 'deep8k')).hexdigest())
                if d8:
                    allrecs.append(d8)
                dv8 = verdict(d8) if (dr and dr.get('finish_reason') == 'length' and d8) else dv
                lbl = label_at(gt, run['arch'], t['t'])
                tl.append(dict(t=F(t['t']), last_item=t['shown'][-1], fast=fv, deep=dv, final=dv if dv else fv,
                               final_lenient=dvl if dvl else fv, deep_lenient=dvl,
                               final_8k=dv8 if dv8 else fv, deep_8k=dv8,
                               deep_all=verdict(d8),            # thinking on at every step (cmd deepall)
                               label=(lbl['risk'], lbl['action']) if lbl else None))
            out['judges'][judge] = dict(timeline=tl, fast=score(tl, 'fast', ponr, latest_useful, items, run),
                                        fast_deep=score(tl, 'final', ponr, latest_useful, items, run),
                                        fast_deep_lenient=score(tl, 'final_lenient', ponr, latest_useful, items, run),
                                        fast_deep_8k=score(tl, 'final_8k', ponr, latest_useful, items, run),
                                        deep_all=score(tl, 'deep_all', ponr, latest_useful, items, run))
        j3 = j3_run(run, items)
        if j3 is not None:
            w1 = P(d['replay_window']['end_utc'])
            w0 = P(d['replay_window']['start_utc'])
            j3w = [f for f in j3 if w0 <= f['t'] <= w1]
            first = j3w[0] if j3w else None
            out['judges']['J3'] = dict(fires=[dict(t=F(f['t']), item=f['item'], hits=f['hits']) for f in j3w],
                                       first_exit=F(first['t']) if first else None,
                                       first_exit_item=first['item'] if first else None,
                                       lead_min=round((ponr - first['t']).total_seconds() / 60, 1) if (first and ponr) else None,
                                       worked=holder_ok(items, first['t'], run['side']) if first else None,
                                       before_latest_useful=(first['t'] <= latest_useful) if (first and latest_useful) else None)
        res['runs'].append(out)
    # call stats
    seen = {}
    for r in allrecs:
        seen[r['req_sha256']] = r
    for mode in ('fast', 'deep', 'deep8k'):
        rs = [r for r in seen.values() if r.get('mode') == mode and r.get('ok')]
        if not rs:
            continue
        pin = sum(r['usage'].get('prompt_tokens', 0) for r in rs)
        pout = sum(r['usage'].get('completion_tokens', 0) for r in rs)
        lat = sorted(r['latency_s'] for r in rs)
        res['calls'][mode] = dict(n=len(rs), errors=sum(1 for r in seen.values() if r.get('mode') == mode and not r.get('ok')),
                                  invalid=sum(1 for r in rs if not r['valid']),
                                  finish_length=sum(1 for r in rs if r.get('finish_reason') == 'length'),
                                  prompt_tokens=pin, completion_tokens=pout,
                                  cost_usd=round(pin * PRICE_IN + pout * PRICE_OUT, 4),
                                  lat_median=round(statistics.median(lat), 2),
                                  lat_p90=round(lat[int(0.9 * (len(lat) - 1))], 2), lat_max=round(lat[-1], 2))
    sig = [r['sig'] for r in seen.values() if r.get('sig')]
    res['calls']['signatures'] = dict(checked=len(sig), passed=sum(1 for s in sig if s.get('passed')),
                                      kinds=sorted({str(s.get('kind')) for s in sig}),
                                      sampled_share=round(len(sig) / max(1, len(seen)), 3))
    json.dump(res, open(os.path.join(args.out, 'results.json'), 'w'), indent=1, default=str)
    print(json.dumps(res['calls'], indent=1))
    print_tables(res)
    open(os.path.join(args.out, 'tables.md'), 'w').write(summary_md(res))
    print(summary_md(res))


def score(tl, which, ponr, latest_useful, items, run):
    def first(pred):
        for s in tl:
            v = s[which]
            if v and pred(v):
                return s
        return None
    fe = first(lambda v: v['action'] == 'exit')
    fp = first(lambda v: v['action'] in ('exit', 'reduce_to_target'))
    fr = first(lambda v: v['action'] in ('reduce_light', 'reduce_to_target', 'exit'))
    fh = first(lambda v: v['risk'] in ('high', 'critical'))
    def lead(s):
        return round((ponr - P(s['t'])).total_seconds() / 60, 1) if (s and ponr) else None
    alarms = [dict(t=s['t'], item=s['last_item'], risk=s[which]['risk'], action=s[which]['action'],
                   src=s[which]['src'][:4])
              for s in tl if s[which] and (s[which]['action'] == 'exit' or s[which]['risk'] in ('high', 'critical'))]
    agree = [s for s in tl if s['label'] and s[which]]
    return dict(steps=len(tl), invalid=sum(1 for s in tl if s[which] and not s[which]['valid']),
                first_exit=fe and fe['t'], first_exit_item=fe and fe['last_item'], first_exit_src=fe and fe[which]['src'][:4],
                lead_exit_min=lead(fe),
                exit_worked=holder_ok(items, P(fe['t']), run['side']) if fe else None,
                exit_before_latest_useful=(P(fe['t']) <= latest_useful) if (fe and latest_useful) else None,
                first_protect=fp and fp['t'], lead_protect_min=lead(fp),
                protect_action=fp and fp[which]['action'],
                first_reduce=fr and fr['t'], lead_reduce_min=lead(fr),
                first_high=fh and fh['t'], lead_high_min=lead(fh), first_high_item=fh and fh['last_item'],
                alarms=alarms,
                label_action_agree=round(sum(1 for s in agree if s[which]['action'] == s['label'][1]) / len(agree), 2) if agree else None,
                label_risk_agree=round(sum(1 for s in agree if s[which]['risk'] == s['label'][0]) / len(agree), 2) if agree else None)


def print_tables(res):
    for r in res['runs']:
        print('\n==', r['run'], r['kind'], 'PONR', r['ponr'], 'latest_useful', r['latest_useful'])
        for j in ('J1', 'J2'):
            for mode in ('fast', 'fast_deep', 'fast_deep_8k', 'deep_all'):
                s = r['judges'][j][mode]
                print('  %s %-9s steps %3d inv %d | exit %s (lead %s, worked %s, <=useful %s) | protect %s %s (lead %s) | high %s (lead %s) | alarms %d | agree a=%s r=%s'
                      % (j, mode, s['steps'], s['invalid'], s['first_exit'], s['lead_exit_min'], s['exit_worked'],
                         s['exit_before_latest_useful'], s['first_protect'], s['protect_action'], s['lead_protect_min'],
                         s['first_high'], s['lead_high_min'], len(s['alarms']), s['label_action_agree'], s['label_risk_agree']))
        if 'J3' in r['judges']:
            s = r['judges']['J3']
            print('  J3 exit %s item %s lead %s worked %s | fires %d %s' % (
                s['first_exit'], s['first_exit_item'], s['lead_min'], s['worked'], len(s['fires']),
                s['fires'][0]['hits'][:2] if s['fires'] else ''))


def summary_md(res):
    """Aggregate tables (primary runs only; the two rsETH-looper runs are reported separately)."""
    L = []
    prim = [r for r in res['runs'] if not r['secondary']]
    inc = [r for r in prim if r['kind'] == 'incident']
    ctl = [r for r in prim if r['kind'] == 'control']
    modes = [('J1', 'fast'), ('J1', 'fast_deep'), ('J1', 'fast_deep_8k'), ('J1', 'deep_all'), ('J2', 'fast'),
             ('J2', 'fast_deep'), ('J2', 'fast_deep_8k'), ('J2', 'deep_all'), ('J3', None)]

    def get(r, j, m):
        if j == 'J3':
            s = r['judges'].get('J3')
            if not s:
                return None
            return dict(first_exit=s['first_exit'], lead_exit_min=s['lead_min'], exit_worked=s['worked'],
                        exit_before_latest_useful=s['before_latest_useful'],
                        first_protect=s['first_exit'], lead_protect_min=s['lead_min'],
                        first_high=s['first_exit'], alarms=[dict(t=f['t'], item=f['item']) for f in s['fires']],
                        first_exit_item=s['first_exit_item'])
        return r['judges'][j][m]

    def fmt(x):
        return '-' if x is None else ('%.0f' % x if abs(x) >= 10 else '%.1f' % x)
    L.append('| Judge | Mode | Exit before PONR | Useful exit (by latest useful time) | Median exit lead, min (all exits) | Protective action (exit or reduce_to_target) before PONR | Controls with exit | Controls with exit or high |')
    L.append('|---|---|---|---|---|---|---|---|')
    for j, m in modes:
        ss = [get(r, j, m) for r in inc]
        ex = [s for s in ss if s and s['first_exit']]
        pre = [s for s in ex if s['lead_exit_min'] is not None and s['lead_exit_min'] > 0]
        useful = [s for s in ex if s['exit_before_latest_useful']]
        prot = [s for s in ss if s and s.get('lead_protect_min') is not None and s['lead_protect_min'] > 0]
        leads = [s['lead_exit_min'] for s in ex if s['lead_exit_min'] is not None]
        cs = [get(r, j, m) for r in ctl]
        c_exit = [s for s in cs if s and s['first_exit']]
        c_any = [s for s in cs if s and s['alarms']]
        L.append('| %s | %s | %d/8 | %d/8 | %s | %d/8 | %d/6 | %d/6 |' % (
            j, m or 'rules', len(pre), len(useful), fmt(statistics.median(leads)) if leads else '-', len(prot),
            len(c_exit), len(c_any)))
    L.append('')
    L.append('Per-window first exit, lead vs PONR in minutes (positive = early), and whether the exit could still be executed (file holder_state at that time / before the archetype\'s latest useful time):')
    L.append('')
    L.append('| Window | PONR | ' + ' | '.join('%s %s' % (j, m or 'rules') for j, m in modes) + ' |')
    L.append('|---|---|' + '---|' * len(modes))
    for r in res['runs']:
        cells = []
        for j, m in modes:
            s = get(r, j, m)
            if s is None:
                cells.append('n/a')
            elif not s['first_exit']:
                cells.append('none' + (' (%d alarms)' % len(s['alarms']) if s['alarms'] else ''))
            elif r['kind'] == 'incident':
                cells.append('%s (%s; %s/%s)' % (s['first_exit'][5:16].replace('T', ' '), fmt(s['lead_exit_min']),
                                                 s['exit_worked'], 'useful' if s['exit_before_latest_useful'] else 'late'))
            else:
                cells.append('EXIT %s' % s['first_exit'][5:16].replace('T', ' '))
        L.append('| %s | %s | %s |' % (r['run'], (r['ponr'] or '-')[5:16].replace('T', ' '), ' | '.join(cells)))
    return '\n'.join(L) + '\n'


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('cmd', choices=['dry', 'run', 'retry', 'analyze', 'deepall'])
    ap.add_argument('--parallel', type=int, default=PARALLEL)
    ap.add_argument('--data', default=DEFAULT_DATA)
    ap.add_argument('--out', default=os.path.dirname(os.path.abspath(__file__)))
    ap.add_argument('--only', default='')
    ap.add_argument('--limit', type=int, default=0)
    ap.add_argument('--no-deep', action='store_true')
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    {'dry': cmd_dry, 'run': cmd_run, 'retry': cmd_retry, 'analyze': cmd_analyze, 'deepall': cmd_deepall}[a.cmd](a)
