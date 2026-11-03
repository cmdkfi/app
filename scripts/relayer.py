#!/usr/bin/env python3 -I
"""cmdk relayer: carries owner messages to the vault on NEAR and the vault's signed transactions to Base Sepolia.
It holds no authority: the vault decides what is signed; the relayer pays NEAR gas, broadcasts on Base, and,
because the vault cannot read Base, reads Base before it composes a call (design §4.1). It signs NEAR
transactions only through near-cli-rs's keychain (sign-with-keychain) and never handles an EVM key.
Testnet only: Base chain id 84532 and NEAR network testnet are checked; anything else is refused.

  relayer.py serve --sender ACCOUNT --factory ACCOUNT [--port 8787] [--near-rpc URL] [--base-rpc URL]
                   [--state FILE] [--dry-run [--allow-reads]]
      JSON service on 127.0.0.1 only (the app's server-side proxy calls it; it is never exposed directly):
      POST /create-vault, GET /vault/<id>, POST /owner-call, POST /fund-agent, GET /health. See RELAYER.md.
      --dry-run sends nothing: it prints the exact near command and the Base RPC calls that would run. Without
      --allow-reads it also makes no read-only call and stops at the first one, naming it.
  relayer.py derive --account ACCOUNT [--path PATH]
      Base address the signer (v1.signer-prod.testnet) derives for (ACCOUNT, PATH): derived_public_key view,
      then keccak of the uncompressed key (cast keccak).
  relayer.py params --from ADDR --to ADDR --data HEX [--nonce N] [--rpc URL]
      One TxParams set: nonce (pending count unless given), gas_limit = eth_estimateGas x 1.3 (P1 finding),
      max_fee_per_gas = 2 x base fee + priority, capped at the vault's 2 gwei bound.
  relayer.py submit --vault ID --signed FILE --txs JSON --out FILE [--sender ACCOUNT] [--dry-run]
      Calls owner_call(message, signature, txs) on the vault, signed by SENDER's key in the near CLI keychain,
      300 Tgas. Writes the NEAR tx hash, gas summed over every receipt, and the returned signed transactions.
  relayer.py near-gas TXHASH [--sender ACCOUNT]
      Per-receipt gas of a NEAR transaction (as F's txgas.py).
  relayer.py broadcast FILE|0xRAW [--index I] [--rpc URL] [--log FILE] [--dry-run]
      eth_sendRawTransaction; checks the chain id, then that the returned hash equals the vault's hash-to-be;
      waits for the receipt; appends {hash, status, block, gasUsed, effectiveGasPrice, l1Fee, from, to} to the log.
"""
import argparse, base64, json, os, re, shlex, subprocess, sys, tempfile, threading, time, urllib.parse, urllib.request
from decimal import Decimal, InvalidOperation
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
HOME = os.path.expanduser('~')
CAST = os.path.join(HOME, '.foundry', 'bin', 'cast')
NEAR_RPC = 'https://rpc.testnet.near.org'
BASE_RPC = 'https://base-sepolia-rpc.publicnode.com'
SIGNER = 'v1.signer-prod.testnet'
RELAYER = 'prime-measure-1792736012.testnet'
HOST = '127.0.0.1'                   # the service binds here only; there is no flag to change it
STATE_DEFAULT = os.path.join(HERE, 'state', 'relayer.json')
CHAIN_ID = 84532                     # Base Sepolia, the vault's CHAIN_ID
NEAR_NETWORK = 'testnet'
PROTOCOL = 'cmdk-vault/1'            # the vault's owner::PROTOCOL
MAX_FEE_BOUND = 2_000_000_000        # the vault's MAX_FEE_PER_GAS_MAX
GAS_LIMIT_BOUND = 1_000_000          # the vault's GAS_LIMIT_MAX
U64_MAX = 2 ** 64 - 1
YOCTO = 10 ** 24
AGENT_MIN = 15 * 10 ** 22            # 0.15 NEAR: the agent waits until its implicit account holds this (item 32)
AGENT_EXTRA = 10 ** 22               # 0.01 NEAR on top of the difference
AGENT_DAY_CAP = 5 * 10 ** 23         # 0.5 NEAR per implicit account per rolling 24 hours
DEPOSIT_MAX = 10 * YOCTO             # refuse a create-vault deposit above this (a typo guard)
OWNER_CALL_GAS = '300 Tgas'
CREATE_VAULT_GAS = '150 Tgas'        # DEPLOY.md step 6; the sandbox run burnt 20.5 Tgas
EXPIRY_MARGIN = 30                   # seconds a message must still be valid for when it reaches the relayer
EXPIRY_MAX_AHEAD = 3600              # the vault refuses an expiry more than this after its block time (owner.rs)
REPAY_HEADROOM = 100                 # require allowance and balance of debt + 1/100 (interest accrues before repay)
RECEIPT_TIMEOUT = 120
B58 = '123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz'
TX_ID = re.compile(r'Transaction ID:\s*([1-9A-HJ-NP-Za-km-z]{43,44})')

# Gas limit per Base transaction kind. eth_estimateGas on the owner's calldata is not possible before the vault
# signs (the vault sets the deadline and the calldata at signing time, and the relayer must send the gas limit
# with the request), so the limits come from this table. Prototype finding 11: an estimate x 1.3 ran out of gas
# on a swap, so swap paths get more margin. Measured gasUsed (executor/gas_onchain.json, 26-27 Oct): approve
# 46-56k, setAuthorization 47k, setCorridor 84k, open 189-270k, openFromStables 225k, repay 128k, close 146k,
# reduce 220-393k, migrate 330-444k, exit 246-249k, plain transfer 21k. Every value is capped at the vault bound.
GAS_TABLE = {
    'approve': 80_000, 'setAuthorization': 80_000, 'setCorridor': 120_000, 'open': 600_000,
    'openFromStables': 800_000, 'repay': 300_000, 'close': 500_000, 'reduce': 700_000, 'migrate': 900_000,
    'exit': 800_000, 'transfer': 80_000, 'cancel': 30_000, 'sweep_eth': 30_000,
}

# 4-byte selectors of the views the relayer reads (computed with `cast sig`; a unit test re-checks them).
SEL = {
    'isAuthorized': '0x65e4ad9e',   # Morpho isAuthorized(address,address)
    'allowance': '0xdd62ed3e',      # ERC-20 allowance(address,address)
    'balanceOf': '0x70a08231',      # ERC-20 balanceOf(address)
    'corridors': '0x2e18e9e7',      # Executor corridors(address,bytes32)
    'position': '0x93c52062',       # Morpho position(bytes32,address)
    'market': '0x5c60e39a',         # Morpho market(bytes32)
}

# Owner methods: the exact argument fields the vault accepts (deny_unknown_fields) and their kinds.
OWNER_ARGS = {
    'setup': None,                   # {markets: [...], measurement?}; checked by check_setup_args
    'set_corridor': ('market', 'target', 'act', 'floor', 'sell_budget', 'migrate_allowed', 'exit_allowed'),
    'open': ('market', 'collateral_in', 'target_ltv'),
    'open_from_stables': ('market', 'stables_in', 'target_ltv'),
    'repay': ('market', 'amount'),
    'close': ('market',),
    'withdraw': ('token', 'amount', 'destination'),
    'cancel': (),
    'sweep_eth': ('amount',),
    'set_executor': ('market', 'executor', 'revoke_authorization'),
    'approve_measurement': None,     # a Measurement; the vault validates it, no Base transaction
    'revoke_agent': (),
}
SETUP_MARKET = ('market', 'approve', 'authorize')                 # plus `corridor`, optional
CORRIDOR = ('target', 'act', 'floor', 'sell_budget', 'migrate_allowed', 'exit_allowed')
ARG_KIND = {
    'market': 'id', 'token': 'addr', 'destination': 'addr', 'executor': 'addr',
    'approve': 'bool', 'authorize': 'bool', 'migrate_allowed': 'bool', 'exit_allowed': 'bool', 'revoke_authorization': 'bool',
    'target': 'u128', 'act': 'u128', 'floor': 'u128', 'sell_budget': 'u128', 'collateral_in': 'u128',
    'stables_in': 'u128', 'target_ltv': 'u128', 'amount': 'u128',
}


class Refused(ValueError):
    """A request the relayer will not carry; the message says why. Nothing was sent."""


class ChainError(RuntimeError):
    """An RPC or CLI call failed, or the chain returned a failure."""


class Skipped(Exception):
    """Dry run without reads: the read-only call named in `call` was not made."""

    def __init__(self, call):
        super().__init__(call)
        self.call = call


def log(line):
    print('%s %s' % (time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), line), file=sys.stderr, flush=True)


def rpc(url, method, params):
    body = json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params}).encode()
    req = urllib.request.Request(url, body, {'Content-Type': 'application/json', 'User-Agent': 'curl/8'})
    r = json.load(urllib.request.urlopen(req, timeout=120))
    if 'error' in r:
        raise ChainError('%s %s: %s' % (method, url, json.dumps(r['error'])[:600]))
    return r['result']


def near_view(account, method, args):
    r = rpc(NEAR_RPC, 'query', {'request_type': 'call_function', 'finality': 'final', 'account_id': account,
                                'method_name': method, 'args_base64': base64.b64encode(json.dumps(args).encode()).decode()})
    return json.loads(bytes(r['result']))


def near_tx(tx_hash, sender):
    return rpc(NEAR_RPC, 'tx', {'tx_hash': tx_hash, 'sender_account_id': sender, 'wait_until': 'FINAL'})


def b58decode(s):
    n = 0
    for c in s:
        n = n * 58 + B58.index(c)
    raw = n.to_bytes((n.bit_length() + 7) // 8, 'big')
    return b'\x00' * (len(s) - len(s.lstrip('1'))) + raw


def keccak(hex_data):
    return subprocess.run([CAST, 'keccak', hex_data], capture_output=True, text=True, check=True).stdout.strip()


def derive(account, path):
    pk = near_view(SIGNER, 'derived_public_key', {'path': path, 'predecessor': account, 'domain_id': 0})
    curve, b = pk.split(':')
    raw = b58decode(b)
    assert curve == 'secp256k1' and len(raw) == 64, pk
    return '0x' + keccak('0x' + raw.hex())[-40:], pk


# ---------------------------------------------------------------- guards and parsing (no network)

def check_named_account(acct, what='account'):
    """A NEAR testnet named account (ends in .testnet); a mainnet id is refused."""
    if not isinstance(acct, str) or not re.fullmatch(r'([a-z0-9]+[-_])*[a-z0-9]+(\.([a-z0-9]+[-_])*[a-z0-9]+)*', acct) \
            or not acct.endswith('.' + NEAR_NETWORK):
        raise Refused('%s %r is not a NEAR %s account id' % (what, acct, NEAR_NETWORK))
    return acct


def check_implicit_account(acct):
    """A NEAR implicit account: 64 lower-case hex characters (the agent's ed25519 key)."""
    if not isinstance(acct, str) or not re.fullmatch(r'[0-9a-f]{64}', acct):
        raise Refused('implicit_account must be 64 lower-case hex characters')
    return acct


def hex_len(v, n, what):
    """`v` is 0x-optional hex of exactly n bytes (the factory's and vault's hex_bytes)."""
    s = v[2:] if isinstance(v, str) and v.startswith('0x') else v
    if not isinstance(s, str) or len(s) != 2 * n or not re.fullmatch(r'[0-9a-fA-F]*', s):
        raise Refused('%s: expected %d bytes of hex' % (what, n))
    return '0x' + s.lower()


def check_u128(v, what):
    """A U128 in near-sdk JSON: a decimal string."""
    if not isinstance(v, str) or not re.fullmatch(r'[0-9]+', v) or int(v) >= 2 ** 128:
        raise Refused('%s: expected a decimal string (U128)' % what)
    return int(v)


def check_keys(d, keys, what, optional=()):
    if not isinstance(d, dict):
        raise Refused('%s: expected an object' % what)
    missing, extra = set(keys) - set(d), set(d) - set(keys) - set(optional)
    if missing or extra:
        raise Refused('%s: missing %s, unknown %s' % (what, sorted(missing), sorted(extra)))


def validate_policy(owner, policy):
    """The factory's `validate` (hex lengths, non-zero executor, at least one market, no duplicate ids), plus
    types and unknown keys (the factory would silently drop an unknown key)."""
    hex_len(owner, 20, 'owner')
    check_keys(policy, ('cooldown_secs', 'markets'), 'policy')
    c = policy['cooldown_secs']
    if type(c) is not int or not 0 <= c <= U64_MAX:
        raise Refused('policy.cooldown_secs: expected an integer')
    if not isinstance(policy['markets'], list) or not policy['markets']:
        raise Refused('no markets')
    ids = []
    for i, m in enumerate(policy['markets']):
        w = 'markets[%d]' % i
        check_keys(m, ('id', 'loan_token', 'collateral_token', 'executor', 'slice_max'), w)
        mid = hex_len(m['id'], 32, w + '.id (market id)')
        hex_len(m['loan_token'], 20, w + '.loan_token')
        hex_len(m['collateral_token'], 20, w + '.collateral_token')
        if int(hex_len(m['executor'], 20, w + '.executor'), 16) == 0:
            raise Refused('zero executor')
        check_u128(m['slice_max'], w + '.slice_max')
        if mid in ids:
            raise Refused('duplicate market')
        ids.append(mid)


def parse_near(v):
    """A NEAR amount ("2", "0.5", or an integer) in yoctoNEAR, exactly."""
    try:
        d = Decimal(str(v)) * YOCTO
    except InvalidOperation:
        raise Refused('deposit_near: not a number')
    if isinstance(v, (bool, float)) or d != d.to_integral_value():
        raise Refused('deposit_near: give a decimal string with at most 24 decimals')
    return int(d)


def fmt_near(yocto):
    s = format(Decimal(yocto) / YOCTO, 'f')
    return (s.rstrip('0').rstrip('.') if '.' in s else s) + ' NEAR'


def check_arg(name, v):
    kind = ARG_KIND[name]
    if kind == 'id':
        return hex_len(v, 32, name)
    if kind == 'addr':
        return hex_len(v, 20, name)
    if kind == 'bool':
        if type(v) is not bool:
            raise Refused('%s: expected true or false' % name)
        return v
    return check_u128(v, name)


def check_setup_args(a):
    """setup's args as the vault parses them: {markets: [{market, approve, authorize, corridor?}], measurement?},
    unknown fields refused, `corridor` and `measurement` optional (absent or null). The vault validates the
    measurement itself (as approve_measurement's args)."""
    check_keys(a, ('markets',), 'args of setup', optional=('measurement',))
    if not isinstance(a['markets'], list):
        raise Refused('args of setup: markets: expected a list')
    for i, p in enumerate(a['markets']):
        w = 'args of setup: markets[%d]' % i
        check_keys(p, SETUP_MARKET, w, optional=('corridor',))
        for k in SETUP_MARKET:
            check_arg(k, p[k])
        if p.get('corridor') is not None:
            check_keys(p['corridor'], CORRIDOR, w + '.corridor')
            for k in CORRIDOR:
                check_arg(k, p['corridor'][k])
    if a.get('measurement') is not None and not isinstance(a['measurement'], dict):
        raise Refused('args of setup: measurement: expected an object')


def parse_message(message, signature, vault, last_nonce, now):
    """The owner message's own fields, checked as the vault will check them (except the signature recovery),
    so a message the vault would refuse never costs NEAR gas."""
    try:
        m = json.loads(message)
    except (TypeError, ValueError):
        raise Refused('message is not JSON')
    check_keys(m, ('protocol', 'network', 'vault', 'method', 'args', 'nonce', 'expiry'), 'message')
    if m['protocol'] != PROTOCOL:
        raise Refused('message protocol %r is not %r' % (m['protocol'], PROTOCOL))
    if m['network'] != NEAR_NETWORK:
        raise Refused('message network %r is not %r' % (m['network'], NEAR_NETWORK))
    if m['vault'] != vault:
        raise Refused('message is for vault %r, not %r' % (m['vault'], vault))
    if type(m['nonce']) is not int or m['nonce'] <= last_nonce:
        raise Refused('message nonce %r is not above the vault owner_nonce %d' % (m['nonce'], last_nonce))
    if type(m['expiry']) is not int or m['expiry'] < now + EXPIRY_MARGIN:
        raise Refused('message expired or expires within %d s' % EXPIRY_MARGIN)
    if m['expiry'] > now + EXPIRY_MAX_AHEAD:
        raise Refused('message expiry is more than %d s ahead; the vault refuses it' % EXPIRY_MAX_AHEAD)
    if m['method'] not in OWNER_ARGS:
        raise Refused('unknown method %r' % m['method'])
    fields = OWNER_ARGS[m['method']]
    if m['method'] == 'setup':
        check_setup_args(m['args'])
    elif fields is not None:
        check_keys(m['args'], fields, 'args of ' + m['method'])
        for k in fields:
            check_arg(k, m['args'][k])
    if not isinstance(signature, str) or not re.fullmatch(r'0x[0-9a-fA-F]{130}', signature):
        raise Refused('signature: expected 0x and 65 bytes of hex')
    return m


def find_market(st, market_id):
    mid = hex_len(market_id, 32, 'market').lower()
    for m in st['markets']:
        if m['id'].lower() == mid:
            return m
    raise Refused('market %s is not in the vault allowlist' % mid)


def tokens_of(markets):
    out = []
    for m in markets:
        for t in (m['loan_token'].lower(), m['collateral_token'].lower()):
            if t not in out:
                out.append(t)
    return out


def set_executor_plan(args, st):
    """The vault's set_executor: nothing while another market still uses the old executor; otherwise
    setAuthorization(old, false) if the flag is set, then approve(old, 0) once per distinct token of every
    market that used the old executor."""
    markets = [dict(m, previous_executors=[p.lower() for p in m['previous_executors']]) for m in st['markets']]
    mk = find_market({'markets': markets}, args['market'])
    old, new = mk['executor'].lower(), hex_len(args['executor'], 20, 'executor')
    if new == old or int(new, 16) == 0:
        raise Refused('set_executor: executor unchanged or zero')
    if old not in mk['previous_executors']:
        mk['previous_executors'].append(old)
    mk['executor'] = new
    if any(m['executor'].lower() == old for m in markets):
        return []
    plan = [{'kind': 'setAuthorization', 'to': st['morpho'], 'value': 0}] if args['revoke_authorization'] else []
    used = [m for m in markets if old in m['previous_executors']]
    return plan + [{'kind': 'approve', 'to': t, 'value': 0} for t in tokens_of(used)]


def setup_plan(args, st):
    """The vault's setup: per market part, in order, approve(executor) on the loan and collateral token if
    `approve`, setAuthorization(executor, true) if `authorize`, setCorridor if `corridor`; a call identical to
    one already planned (same token and executor, same executor) is left out. Refused when it produces nothing
    and carries no measurement, or lists a market twice."""
    plan, seen, emitted = [], [], set()
    for p in args['markets']:
        mk = find_market(st, p['market'])
        if mk['id'].lower() in seen:
            raise Refused('setup: market listed twice')
        seen.append(mk['id'].lower())
        ex = mk['executor'].lower()
        calls = [(('approve', t, ex), {'kind': 'approve', 'to': t, 'value': 0}) for t in tokens_of([mk])] if p['approve'] else []
        if p['authorize']:
            calls.append((('setAuthorization', ex), {'kind': 'setAuthorization', 'to': st['morpho'], 'value': 0}))
        for key, tx in calls:
            if key not in emitted:
                emitted.add(key)
                plan.append(tx)
        c = p.get('corridor')
        if c is not None:
            if any(int(c[k]) > U64_MAX for k in ('target', 'act', 'floor', 'sell_budget')):
                raise Refused('setup: corridor value above uint64')
            plan.append({'kind': 'setCorridor', 'to': mk['executor'], 'value': 0})
    if not plan and args.get('measurement') is None:
        raise Refused('setup: empty (no Base transaction and no measurement)')
    return plan


def plan_txs(method, args, st):
    """One entry {kind, to, value} per Base transaction the vault will build for this owner method, in the
    vault's order; `kind` keys the gas table. Mirrors owner_call in vault/src/lib.rs."""
    if method in ('approve_measurement', 'revoke_agent'):
        return []
    if method == 'set_executor':
        return set_executor_plan(args, st)
    if method == 'setup':
        return setup_plan(args, st)
    if method == 'cancel':
        return [{'kind': 'cancel', 'to': st['base_address'], 'value': 0}]
    if method == 'sweep_eth':
        if int(args['amount']) == 0:
            raise Refused('sweep_eth: zero amount')
        return [{'kind': 'sweep_eth', 'to': st['owner'], 'value': int(args['amount'])}]
    if method == 'withdraw':
        token = hex_len(args['token'], 20, 'token')
        if token not in tokens_of(st['markets']):
            raise Refused('withdraw: token not in the allowlist')
        if int(hex_len(args['destination'], 20, 'destination'), 16) == 0 or int(args['amount']) == 0:
            raise Refused('withdraw: zero destination or zero amount')
        return [{'kind': 'transfer', 'to': token, 'value': 0}]
    mk = find_market(st, args['market'])
    if method == 'set_corridor':
        if any(int(args[k]) > U64_MAX for k in ('target', 'act', 'floor', 'sell_budget')):
            raise Refused('set_corridor: corridor value above uint64')
        return [{'kind': 'setCorridor', 'to': mk['executor'], 'value': 0}]
    if method == 'open_from_stables':
        if int(args['stables_in']) == 0:
            raise Refused('open_from_stables: zero stables_in')     # the vault's wording; open with collateral_in 0 is allowed
        return [{'kind': 'openFromStables', 'to': mk['executor'], 'value': 0}]
    return [{'kind': method, 'to': mk['executor'], 'value': 0}]       # open, repay, close


def check_setup_flags(args, f):
    """setup's flags against Base, per executor: an authorization in the message while Morpho already has it
    reverts; none while Morpho lacks it leaves the markets unusable. Per (token, executor) of every listed market:
    an allowance of 0 that no part of the message approves leaves the market unusable. A repeated approve is
    allowed (it does not revert; the vault emits it once per message)."""
    parts = list(zip(args['markets'], f['markets']))
    for ex in dict.fromkeys(x['executor'] for _, x in parts):
        auth = any(p['authorize'] for p, x in parts if x['executor'] == ex)
        on = next(x['authorized'] for _, x in parts if x['executor'] == ex)
        if auth and on:
            raise Refused('setup: authorize is true for executor %s but Morpho already has isAuthorized(vault, '
                          'executor) = true, so setAuthorization would revert; sign again with authorize false on '
                          'every market of that executor' % ex)
        if not auth and not on:
            raise Refused('setup: Morpho has no authorization for executor %s and no market in the message '
                          'authorizes it; sign again with authorize true on one of its markets' % ex)
    approved = {(t, x['executor']) for p, x in parts if p['approve'] for t in x['allowances']}
    for p, x in parts:
        for t, a in x['allowances'].items():
            if a == 0 and (t, x['executor']) not in approved:
                raise Refused('setup: market %s: the allowance of token %s to the executor is 0 and no market in the '
                              'message approves it; sign again with approve true' % (p['market'], t))


def check_flags(method, args, plan, f):
    """Refuse when the message disagrees with Base (`f`: the facts read by base_facts)."""
    if method == 'setup':
        check_setup_flags(args, f)
    if method == 'set_executor' and plan and args['revoke_authorization'] != f['authorized_old']:
        if f['authorized_old']:
            raise Refused('set_executor: revoke_authorization is false but Morpho still authorizes the old executor; '
                          'sign again with revoke_authorization true')
        raise Refused('set_executor: revoke_authorization is true but Morpho has no authorization for the old executor, '
                      'so setAuthorization(old, false) would revert; sign again with revoke_authorization false')
    if method == 'open':
        if not f['authorized']:
            raise Refused('open: Morpho has no authorization for the executor; sign setup for this market first')
        need = int(args['collateral_in'])
        if f['collateral_allowance'] < need:
            raise Refused('open: collateral-token allowance to the executor is %d, below collateral_in %d; sign setup '
                          'for this market first' % (f['collateral_allowance'], need))
        if f['collateral_balance'] < need:
            raise Refused('open: the vault holds %d of the collateral token, below collateral_in %d'
                          % (f['collateral_balance'], need))
    if method == 'open_from_stables':
        need = int(args['stables_in'])
        if not f['authorized']:
            raise Refused('open_from_stables: Morpho has no authorization for the executor; sign setup for this market first')
        if f['loan_allowance'] < need:
            raise Refused('open_from_stables: loan-token allowance to the executor is %d, below stables_in %d; sign setup '
                          'for this market first' % (f['loan_allowance'], need))
        if f['loan_balance'] < need:
            raise Refused('open_from_stables: the vault holds %d of the loan token, below stables_in %d'
                          % (f['loan_balance'], need))
    if method in ('close', 'repay'):
        if method == 'repay' and f['debt'] == 0:
            raise Refused('repay: no debt on this market (the executor reverts with NoDebt)')
        need = f['debt'] if method == 'close' else min(int(args['amount']), f['debt'])
        need += need // REPAY_HEADROOM
        if f['loan_allowance'] < need:
            raise Refused('%s: loan-token allowance to the executor is %d, below the %d it pulls (debt plus 1%%); '
                          'sign setup for this market first (item 14)' % (method, f['loan_allowance'], need))
        if f['loan_balance'] < need:
            raise Refused('%s: the vault holds %d of the loan token, below the %d it pulls (debt plus 1%%)'
                          % (method, f['loan_balance'], need))


def fees(base_fee, priority):
    """(max_fee_per_gas, max_priority_fee_per_gas): 2 x base fee + priority, capped at the vault's 2 gwei."""
    if base_fee >= MAX_FEE_BOUND:
        raise Refused('Base fee %d wei is at or above the vault bound %d; a transaction would not be included'
                      % (base_fee, MAX_FEE_BOUND))
    max_fee = min(2 * base_fee + priority, MAX_FEE_BOUND)
    return max_fee, min(priority, max_fee)


def gas_limit(kind):
    return min(GAS_TABLE[kind], GAS_LIMIT_BOUND)


def raw_chain_id(raw):
    """The chain id of a signed type-2 transaction (0x02 || rlp([chain_id, ...]))."""
    b = bytes.fromhex(raw[2:] if raw.startswith('0x') else raw)
    if len(b) < 4 or b[0] != 2 or b[1] < 0xc0:
        raise Refused('not a signed EIP-1559 (type 2) transaction')
    i = 2 if b[1] <= 0xf7 else 2 + (b[1] - 0xf7)
    if b[i] < 0x80:
        return b[i]
    n = b[i] - 0x80
    return int.from_bytes(b[i + 1:i + 1 + n], 'big')


def check_raw_chain_id(raw):
    cid = raw_chain_id(raw)
    if cid != CHAIN_ID:
        raise Refused('transaction chain id %d is not %d (Base Sepolia); not broadcast' % (cid, CHAIN_ID))


def near_cmd(parts):
    """A near-cli-rs command that signs with the keychain on testnet; nothing else is ever run."""
    return ['near'] + parts + ['network-config', NEAR_NETWORK, 'sign-with-keychain', 'send']


def w(x):
    """One ABI word from an address or bytes32 hex."""
    return x[2:].lower().rjust(64, '0') if x.startswith('0x') else x.lower().rjust(64, '0')


def words(hexdata):
    h = hexdata[2:] if hexdata.startswith('0x') else hexdata
    return [int(h[i:i + 64], 16) for i in range(0, len(h), 64)]


def to_assets_up(shares, total_assets, total_shares):
    """Morpho SharesMathLib.toAssetsUp (virtual assets 1, virtual shares 1e6), as the executor computes debt."""
    num, den = shares * (total_assets + 1), total_shares + 10 ** 6
    return (num + den - 1) // den


# ---------------------------------------------------------------- network access

class Chain:
    """Every network access of the relayer. Dry run: nothing is sent; reads go out only with allow_reads."""

    def __init__(self, near_rpc=NEAR_RPC, base_rpc=BASE_RPC, dry_run=False, allow_reads=True):
        self.near_rpc, self.base_rpc = near_rpc, base_rpc
        self.dry_run, self.allow_reads = dry_run, allow_reads or not dry_run

    def read(self, url, method, params):
        if not self.allow_reads:
            call = '%s %s %s' % (url, method, json.dumps(params)[:300])
            log('dry run, read not made: ' + call)
            raise Skipped(call)
        return rpc(url, method, params)

    def near_view(self, account, method, args):
        r = self.read(self.near_rpc, 'query', {'request_type': 'call_function', 'finality': 'final', 'account_id': account,
                      'method_name': method, 'args_base64': base64.b64encode(json.dumps(args).encode()).decode()})
        if 'error' in r:
            raise ChainError('%s.%s: %s' % (account, method, r['error'][:600]))
        return json.loads(bytes(r['result']))

    def near_balance(self, account):
        """Balance in yoctoNEAR; 0 for an account that does not exist yet (an unfunded implicit account)."""
        try:
            r = self.read(self.near_rpc, 'query', {'request_type': 'view_account', 'finality': 'final', 'account_id': account})
        except ChainError as e:
            if 'UNKNOWN_ACCOUNT' in str(e) or 'does not exist' in str(e):
                return 0
            raise
        return int(r['amount'])

    def near_tx(self, h, sender):
        return self.read(self.near_rpc, 'tx', {'tx_hash': h, 'sender_account_id': sender, 'wait_until': 'FINAL'})

    def eth(self, method, params):
        return self.read(self.base_rpc, method, params)

    def eth_call(self, to, data):
        return self.eth('eth_call', [{'to': to, 'data': data}, 'latest'])

    def near_send(self, cmd):
        """Runs a near-cli-rs command that sends a transaction; returns its transaction id."""
        if self.dry_run or cmd[-4:] != ['network-config', NEAR_NETWORK, 'sign-with-keychain', 'send']:
            raise Refused('refusing to run: %s' % shlex.join(cmd)[:300])
        log('run: ' + shlex.join(cmd)[:2000])
        try:
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        except subprocess.TimeoutExpired:
            raise ChainError('the near CLI did not finish within 600 s; check the sender account for the transaction')
        out = p.stdout + p.stderr
        m = TX_ID.search(out)
        if not m:
            raise ChainError('no transaction id in the near CLI output: ' + out[-600:])
        return m.group(1)

    def send_raw(self, raw):
        check_raw_chain_id(raw)
        if self.dry_run:
            raise Refused('dry run: eth_sendRawTransaction not sent')
        return rpc(self.base_rpc, 'eth_sendRawTransaction', [raw])

    def receipt(self, h, timeout=RECEIPT_TIMEOUT, poll=1.0):
        t0 = time.time()
        while time.time() - t0 < timeout:
            rc = rpc(self.base_rpc, 'eth_getTransactionReceipt', [h])
            if rc:
                return rc
            time.sleep(poll)
        raise ChainError('no receipt for %s after %d s' % (h, timeout))


def check_networks(chain):
    """Testnet guard at start-up: Base chain id 84532 and NEAR chain id testnet, or exit 2."""
    cid = int(chain.eth('eth_chainId', []), 16)
    near = chain.read(chain.near_rpc, 'status', [])['chain_id']
    if cid != CHAIN_ID or near != NEAR_NETWORK:
        log('refusing to start: Base chain id %d (need %d), NEAR chain id %r (need %r)' % (cid, CHAIN_ID, near, NEAR_NETWORK))
        raise SystemExit(2)
    log('networks: Base chain id %d, NEAR %s' % (cid, near))


# ---------------------------------------------------------------- Base reads (eth_call, read-only)

def read_uint(chain, to, data):
    return words(chain.eth_call(to, data))[0]


def read_authorized(chain, morpho, who, spender):
    return read_uint(chain, morpho, SEL['isAuthorized'] + w(who) + w(spender)) == 1


def read_allowance(chain, token, owner, spender):
    return read_uint(chain, token, SEL['allowance'] + w(owner) + w(spender))


def read_balance(chain, token, who):
    return read_uint(chain, token, SEL['balanceOf'] + w(who))


def read_corridor(chain, executor, who, market_id):
    v = words(chain.eth_call(executor, SEL['corridors'] + w(who) + w(market_id)))
    keys = ('target', 'act', 'floor', 'sell_budget', 'migrate_allowed', 'exit_allowed', 'set')
    return {k: (bool(x) if i >= 4 else str(x)) for i, (k, x) in enumerate(zip(keys, v))}


def read_position(chain, morpho, market_id, who):
    """Collateral and debt at Morpho's stored totals (interest since the last accrual is not included)."""
    _, borrow_shares, collateral = words(chain.eth_call(morpho, SEL['position'] + w(market_id) + w(who)))[:3]
    mk = words(chain.eth_call(morpho, SEL['market'] + w(market_id)))
    return {'collateral': collateral, 'borrow_shares': borrow_shares, 'debt': to_assets_up(borrow_shares, mk[2], mk[3])}


def base_facts(chain, method, args, st):
    """The Base reads check_flags needs for this owner method."""
    base, morpho = st['base_address'], st['morpho']
    if method == 'set_executor':
        return {'authorized_old': read_authorized(chain, morpho, base, find_market(st, args['market'])['executor'])}
    if method == 'setup':
        parts = []
        for p in args['markets']:
            mk = find_market(st, p['market'])
            ex = mk['executor'].lower()
            parts.append({'executor': ex, 'authorized': read_authorized(chain, morpho, base, ex),
                          'allowances': {t: read_allowance(chain, t, base, ex) for t in tokens_of([mk])}})
        return {'markets': parts}
    if method not in ('open', 'open_from_stables', 'close', 'repay'):
        return {}
    mk = find_market(st, args['market'])
    if method == 'open':
        return {'authorized': read_authorized(chain, morpho, base, mk['executor']),
                'collateral_allowance': read_allowance(chain, mk['collateral_token'], base, mk['executor']),
                'collateral_balance': read_balance(chain, mk['collateral_token'], base)}
    if method == 'open_from_stables':
        return {'authorized': read_authorized(chain, morpho, base, mk['executor']),
                'loan_allowance': read_allowance(chain, mk['loan_token'], base, mk['executor']),
                'loan_balance': read_balance(chain, mk['loan_token'], base)}
    return {'loan_allowance': read_allowance(chain, mk['loan_token'], base, mk['executor']),
            'loan_balance': read_balance(chain, mk['loan_token'], base),
            'debt': read_position(chain, morpho, mk['id'], base)['debt']}


def compose_params(chain, base_address, plan):
    """One TxParams per planned transaction: consecutive nonces from the pending count, fees, table gas."""
    if not plan:
        return []
    nonce = int(chain.eth('eth_getTransactionCount', [base_address, 'pending']), 16)
    base_fee = int(chain.eth('eth_getBlockByNumber', ['latest', False])['baseFeePerGas'], 16)
    max_fee, prio = fees(base_fee, int(chain.eth('eth_maxPriorityFeePerGas', []), 16))
    return [{'nonce': nonce + i, 'gas_limit': gas_limit(p['kind']), 'max_fee_per_gas': max_fee,
             'max_priority_fee_per_gas': prio} for i, p in enumerate(plan)]


def check_funds(chain, base_address, plan, txs):
    """The vault's Base address must hold gas_limit x max_fee + value for every transaction (L1 fee not included)."""
    need = sum(t['gas_limit'] * t['max_fee_per_gas'] + p['value'] for p, t in zip(plan, txs))
    have = int(chain.eth('eth_getBalance', [base_address, 'latest']), 16)
    if have < need:
        raise Refused('the vault Base address holds %d wei, below the %d wei the transactions may cost' % (have, need))


def near_result(r):
    """The decoded SuccessValue of a final NEAR transaction, or ChainError with its failure."""
    st = r['status']
    if 'SuccessValue' not in st:
        raise ChainError('NEAR transaction failed: %s' % json.dumps(st)[:600])
    v = base64.b64decode(st['SuccessValue'])
    return json.loads(v) if v else None


def check_signed(signed, txs, st):
    """The vault returned one signed transaction per TxParams, from its Base address, at the nonces given."""
    if not isinstance(signed, list) or len(signed) != len(txs):
        raise ChainError('vault returned %r signed transactions, expected %d' % (None if signed is None else len(signed), len(txs)))
    for s, t in zip(signed, txs):
        if s['from'].lower() != st['base_address'].lower() or s['nonce'] != t['nonce']:
            raise ChainError('signed transaction %s: from %s nonce %s, expected %s nonce %d'
                             % (s['hash'], s['from'], s['nonce'], st['base_address'], t['nonce']))
        check_raw_chain_id(s['raw'])
    return sorted(signed, key=lambda s: s['nonce'])


def receipt_row(rc):
    return {'hash': rc['transactionHash'], 'status': int(rc['status'], 16), 'block': int(rc['blockNumber'], 16),
            'gasUsed': int(rc['gasUsed'], 16), 'effectiveGasPrice': int(rc.get('effectiveGasPrice', '0x0'), 16),
            'l1Fee': int(rc.get('l1Fee') or '0x0', 16), 'from': rc['from'], 'to': rc['to']}


# ---------------------------------------------------------------- state file

def atomic_write_json(path, data):
    """Write JSON to a temp file in the same directory, fsync, then rename over `path`."""
    d = os.path.dirname(os.path.abspath(path))
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix='.relayer-', suffix='.tmp', dir=d)
    try:
        with os.fdopen(fd, 'w') as f:
            json.dump(data, f, indent=1, sort_keys=True)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


class State:
    """Funded agents (time, amount, tx), the last broadcast per vault, counters. Not written in a dry run."""

    def __init__(self, path, write=True):
        self.path, self.write, self.lock = path, write, threading.Lock()
        self.data = {'counters': {}, 'funded': {}, 'vaults': {}}
        if os.path.exists(path):
            with open(path) as f:
                self.data.update(json.load(f))

    def _save(self):
        if self.write:
            atomic_write_json(self.path, self.data)

    def bump(self, name):
        with self.lock:
            self.data['counters'][name] = self.data['counters'].get(name, 0) + 1
            self._save()

    def funded_last_day(self, acct, now):
        return sum(int(e['yocto']) for e in self.data['funded'].get(acct, []) if e['time'] > now - 86400)

    def record_funding(self, acct, yocto, tx, now):
        with self.lock:
            self.data['funded'].setdefault(acct, []).append({'time': int(now), 'yocto': str(yocto), 'near_tx': tx})
            self.data['counters']['fund_agent'] = self.data['counters'].get('fund_agent', 0) + 1
            self._save()

    def record_broadcast(self, vault, rec):
        with self.lock:
            self.data['vaults'][vault] = rec
            self._save()


# ---------------------------------------------------------------- the service

class App:
    """The endpoints' logic; `chain` does every network access, `state` keeps the counters."""

    def __init__(self, chain, state, sender, factory):
        self.chain, self.state = chain, state
        self.sender, self.factory = check_named_account(sender, 'sender'), check_named_account(factory, 'factory')
        self.started = time.time()
        self.send_lock = threading.Lock()     # one sending request at a time (Base nonces, the funding cap)

    def dry(self, cmd, **extra):
        log('dry run, would run: ' + shlex.join(cmd)[:2000])
        return dict({'ok': True, 'dry_run': True, 'near_command': shlex.join(cmd)}, **extra)

    def health(self):
        out = {'ok': True, 'dry_run': self.chain.dry_run, 'sender': self.sender, 'factory': self.factory,
               'near_rpc': self.chain.near_rpc, 'base_rpc': self.chain.base_rpc, 'chain_id': CHAIN_ID,
               'near_network': NEAR_NETWORK, 'uptime_s': int(time.time() - self.started),
               'counters': dict(self.state.data['counters']), 'agents_funded': len(self.state.data['funded'])}
        for key, f in (('sender_balance_yocto', lambda: str(self.chain.near_balance(self.sender))),
                       ('base_block', lambda: int(self.chain.eth('eth_blockNumber', []), 16))):
            try:
                out[key] = f()
            except Skipped as s:
                out.setdefault('reads_skipped', []).append(s.call)
            except Exception as e:
                out['ok'] = False
                out.setdefault('errors', []).append('%s: %s' % (key, e))
        if 'sender_balance_yocto' in out:
            out['sender_balance'] = fmt_near(int(out['sender_balance_yocto']))
        return out

    def create_vault(self, req):
        check_keys(req, ('owner', 'policy', 'deposit_near'), 'request')
        validate_policy(req['owner'], req['policy'])
        deposit = parse_near(req['deposit_near'])
        if not 0 < deposit <= DEPOSIT_MAX:
            raise Refused('deposit_near must be above 0 and at most %s' % fmt_near(DEPOSIT_MAX))
        args = json.dumps({'owner': req['owner'], 'policy': req['policy']}, separators=(',', ':'))
        cmd = near_cmd(['contract', 'call-function', 'as-transaction', self.factory, 'create_vault', 'json-args', args,
                        'prepaid-gas', CREATE_VAULT_GAS, 'attached-deposit', '%d yoctonear' % deposit, 'sign-as', self.sender])
        if self.chain.dry_run:
            return self.dry(cmd)
        with self.send_lock:
            h = self.chain.near_send(cmd)
            r = self.chain.near_tx(h, self.sender)
        self.state.bump('create_vault')
        try:
            vault, base_address = creating_log(r)
            near_result(r)     # raises if the vault's init failed
        except ChainError as e:
            return {'ok': False, 'error': str(e), 'near_tx': h}
        return {'ok': True, 'near_tx': h, 'vault': vault, 'base_address': base_address}

    def vault_view(self, vault):
        check_named_account(vault, 'vault')
        st = self.chain.near_view(vault, 'get_state', {})
        base = st['base_address']
        markets = [self.market_view(st, m) for m in st['markets']]
        tokens = {t: str(read_balance(self.chain, t, base)) for t in tokens_of(st['markets'])}
        return {'ok': True, 'vault': vault, 'state': st, 'base': {
            'address': base, 'eth_wei': str(int(self.chain.eth('eth_getBalance', [base, 'latest']), 16)),
            'pending_nonce': int(self.chain.eth('eth_getTransactionCount', [base, 'pending']), 16),
            'tokens': tokens, 'markets': markets},
            'notes': ['corridor.set on Base is the source of truth; state.markets[].corridor_set is the vault\'s hint (item 10)',
                      'position debt is at Morpho\'s stored totals: interest since the last accrual is not included']}

    def market_view(self, st, m):
        base, ex, morpho = st['base_address'], m['executor'], st['morpho']
        pos = read_position(self.chain, morpho, m['id'], base)
        return {'id': m['id'], 'executor': ex, 'morpho_authorized': read_authorized(self.chain, morpho, base, ex),
                'loan_allowance': str(read_allowance(self.chain, m['loan_token'], base, ex)),
                'collateral_allowance': str(read_allowance(self.chain, m['collateral_token'], base, ex)),
                'corridor': read_corridor(self.chain, ex, base, m['id']),
                'position': {k: str(v) for k, v in pos.items()}}

    def owner_call(self, req):
        check_keys(req, ('vault', 'message', 'signature'), 'request')
        vault = check_named_account(req['vault'], 'vault')
        with self.send_lock:
            st = self.chain.near_view(vault, 'get_state', {})
            m = parse_message(req['message'], req['signature'], vault, st['owner_nonce'], int(time.time()))
            plan = plan_txs(m['method'], m['args'], st)
            check_flags(m['method'], m['args'], plan, base_facts(self.chain, m['method'], m['args'], st))
            txs = compose_params(self.chain, st['base_address'], plan)
            if plan:
                check_funds(self.chain, st['base_address'], plan, txs)
            args = json.dumps({'message': req['message'], 'signature': req['signature'], 'txs': txs}, separators=(',', ':'))
            cmd = near_cmd(['contract', 'call-function', 'as-transaction', vault, 'owner_call', 'json-args', args,
                            'prepaid-gas', OWNER_CALL_GAS, 'attached-deposit', '0 NEAR', 'sign-as', self.sender])
            if self.chain.dry_run:
                return self.dry(cmd, method=m['method'], plan=plan, txs=txs, then=(
                    'eth_sendRawTransaction on %s for each of the %d signed transactions in nonce order, waiting for '
                    'each receipt' % (self.chain.base_rpc, len(txs))))
            return self.submit_and_broadcast(vault, st, m['method'], cmd, txs)

    def submit_and_broadcast(self, vault, st, method, cmd, txs):
        h = self.chain.near_send(cmd)
        self.state.bump('owner_call')
        try:
            signed = check_signed(near_result(self.chain.near_tx(h, self.sender)), txs, st)
        except ChainError as e:
            return {'ok': False, 'error': str(e), 'near_tx': h}
        receipts, error = self.broadcast_all(signed)
        held = signed[len(receipts):]
        self.state.record_broadcast(vault, {'time': int(time.time()), 'method': method, 'near_tx': h, 'receipts': receipts,
                                            'held': [{'nonce': s['nonce'], 'hash': s['hash'], 'raw': s['raw']} for s in held]})
        out = {'ok': error is None, 'near_tx': h, 'signed': signed, 'receipts': receipts}
        if error:
            out.update(error=error, held=[s['hash'] for s in held], note=(
                'held transactions stay valid until their nonce passes; broadcast them with relayer.py broadcast, '
                'or ask the owner to sign cancel'))
        return out

    def broadcast_all(self, signed):
        """Broadcast in nonce order, waiting for each receipt; stop at the first failure or revert."""
        receipts = []
        for s in signed:
            try:
                h = self.chain.send_raw(s['raw'])
                self.state.bump('broadcast')
                if h.lower() != s['hash'].lower():
                    return receipts, 'RPC hash %s differs from the vault hash-to-be %s' % (h, s['hash'])
                row = receipt_row(self.chain.receipt(h))
            except (ChainError, Refused, OSError) as e:
                return receipts, 'broadcast of nonce %d failed: %s' % (s['nonce'], e)
            receipts.append(row)
            log('broadcast %s status %d block %d' % (row['hash'], row['status'], row['block']))
            if row['status'] != 1:
                self.state.bump('broadcast_reverted')
                return receipts, 'Base transaction %s (nonce %d) reverted; the rest were not broadcast' % (h, s['nonce'])
        return receipts, None

    def fund_agent(self, req):
        check_keys(req, ('implicit_account',), 'request')
        acct = check_implicit_account(req['implicit_account'])
        with self.send_lock:
            bal = self.chain.near_balance(acct)
            if bal >= AGENT_MIN:
                self.state.bump('fund_agent_already')
                return {'ok': True, 'status': 'already funded', 'balance_yocto': str(bal)}
            amount = AGENT_MIN - bal + AGENT_EXTRA
            now = time.time()
            if self.state.funded_last_day(acct, now) + amount > AGENT_DAY_CAP:
                raise Refused('funding cap: %s per implicit account per 24 hours' % fmt_near(AGENT_DAY_CAP))
            cmd = near_cmd(['tokens', self.sender, 'send-near', acct, '%d yoctonear' % amount])
            if self.chain.dry_run:
                return self.dry(cmd, amount_yocto=str(amount), balance_yocto=str(bal))
            h = self.chain.near_send(cmd)
            self.state.record_funding(acct, amount, h, now)
        return {'ok': True, 'status': 'funded', 'near_tx': h, 'amount_yocto': str(amount), 'amount': fmt_near(amount),
                'balance_before_yocto': str(bal)}


def creating_log(r):
    """(vault id, Base address) from the factory's `creating vault: <id> <0x address>` log."""
    for ro in r.get('receipts_outcome', []):
        for line in ro['outcome'].get('logs', []):
            m = re.match(r'creating vault: (\S+) (0x[0-9a-f]{40})$', line)
            if m:
                return m.group(1), m.group(2)
    raise ChainError('no "creating vault:" log; status %s' % json.dumps(r.get('status'))[:600])


# ---------------------------------------------------------------- HTTP

class Handler(BaseHTTPRequestHandler):
    """JSON endpoints; every response is {ok, ...} or {ok: false, error}."""
    server_version = 'cmdk-relayer/1'
    MAX_BODY = 1 << 20

    def do_GET(self):
        app = self.server.app
        if self.path == '/health':
            self.respond(app.health)
        elif self.path.startswith('/vault/'):
            self.respond(lambda: app.vault_view(urllib.parse.unquote(self.path[len('/vault/'):])))
        else:
            self.send_json(404, {'ok': False, 'error': 'no such endpoint'})

    def do_POST(self):
        app = self.server.app
        routes = {'/create-vault': app.create_vault, '/owner-call': app.owner_call, '/fund-agent': app.fund_agent}
        if self.path not in routes:
            return self.send_json(404, {'ok': False, 'error': 'no such endpoint'})
        n = int(self.headers.get('Content-Length') or 0)
        if not 0 < n <= self.MAX_BODY:
            return self.send_json(400, {'ok': False, 'error': 'a JSON body of at most 1 MB is required'})
        try:
            body = json.loads(self.rfile.read(n))
        except ValueError:
            return self.send_json(400, {'ok': False, 'error': 'body is not JSON'})
        self.respond(lambda: routes[self.path](body))

    def respond(self, fn):
        try:
            code, out = 200, fn()
        except Refused as e:
            code, out = 400, {'ok': False, 'error': str(e)}
        except Skipped as e:
            code, out = 200, {'ok': True, 'dry_run': True, 'reads_skipped': [e.call],
                              'note': 'stopped at the first read-only call; serve --dry-run --allow-reads goes further'}
        except (ChainError, OSError) as e:
            code, out = 502, {'ok': False, 'error': 'chain: %s' % e}
        except Exception as e:
            code, out = 500, {'ok': False, 'error': '%s: %s' % (type(e).__name__, e)}
            self.server.app.state.bump('errors')
        self.send_json(code, out)

    def send_json(self, code, out):
        b = json.dumps(out).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def log_message(self, fmt, *args):
        log('%s %s' % (self.address_string(), fmt % args))


def make_server(app, port):
    srv = ThreadingHTTPServer((HOST, port), Handler)
    srv.daemon_threads = True
    srv.app = app
    return srv


def cmd_serve(a):
    chain = Chain(a.near_rpc, a.base_rpc, dry_run=a.dry_run, allow_reads=a.allow_reads)
    if chain.allow_reads:
        check_networks(chain)
    else:
        log('dry run without reads: chain ids not checked, nothing read or sent')
    try:
        app = App(chain, State(a.state, write=not a.dry_run), a.sender, a.factory)
    except Refused as e:
        log('refusing to start: %s' % e)
        raise SystemExit(2)
    srv = make_server(app, a.port)
    log('listening on http://%s:%d dry_run=%s state=%s' % (HOST, a.port, a.dry_run, a.state))
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        log('stopped')


# ---------------------------------------------------------------- the prototype subcommands

def gas_summary(r):
    t = r['transaction_outcome']['outcome']
    rows = [('tx', t['executor_id'], t['gas_burnt'], int(t['tokens_burnt']), 'conversion')]
    for ro in r['receipts_outcome']:
        o = ro['outcome']
        st = o['status']
        kind = next(iter(st))
        desc = kind if kind != 'Failure' else 'Failure:' + json.dumps(st[kind])[:300]
        rows.append((ro['id'][:8], o['executor_id'], o['gas_burnt'], int(o['tokens_burnt']), desc))
    return rows


def print_gas(h, r):
    rows = gas_summary(r)
    print('== %s  final status %s' % (h, next(iter(r['status']))))
    for rid, ex, g, tok, d in rows:
        print('   %-9s %-45s %8.3f Tgas  %.6f NEAR  %s' % (rid, ex[:45], g / 1e12, tok / 1e24, d))
    print('   TOTAL %.3f Tgas, %.6f NEAR burnt' % (sum(x[2] for x in rows) / 1e12, sum(x[3] for x in rows) / 1e24))
    return rows


def cmd_derive(a):
    addr, pk = derive(a.account, a.path)
    print(json.dumps({'account': a.account, 'path': a.path, 'derived_public_key': pk, 'address': addr}, indent=1))


def cmd_params(a):
    nonce = a.nonce if a.nonce is not None else int(rpc(a.rpc, 'eth_getTransactionCount', [a.__dict__['from'], 'pending']), 16)
    est = int(rpc(a.rpc, 'eth_estimateGas', [{'from': a.__dict__['from'], 'to': a.to, 'data': a.data}]), 16)
    base = int(rpc(a.rpc, 'eth_getBlockByNumber', ['latest', False])['baseFeePerGas'], 16)
    prio = int(rpc(a.rpc, 'eth_maxPriorityFeePerGas', []), 16)
    gas = int(est * 1.3)
    p = {'nonce': nonce, 'gas_limit': gas, 'max_fee_per_gas': min(2 * base + prio, MAX_FEE_BOUND), 'max_priority_fee_per_gas': prio}
    if gas > GAS_LIMIT_BOUND:
        raise SystemExit('estimate x 1.3 = %d is above the vault bound %d' % (gas, GAS_LIMIT_BOUND))
    print(json.dumps({'estimate': est, 'base_fee': base, 'params': p}))


def cmd_submit(a):
    signed = json.load(open(a.signed))
    args = {'message': signed['message'], 'signature': signed['signature'], 'txs': json.loads(a.txs)}
    tmp = a.out + '.args.json'
    json.dump(args, open(tmp, 'w'))
    cmd = near_cmd(['contract', 'call-function', 'as-transaction', a.vault, 'owner_call', 'file-args', tmp,
                    'prepaid-gas', OWNER_CALL_GAS, 'attached-deposit', '0 NEAR', 'sign-as', a.sender])
    if a.dry_run:
        print('dry run, would run (args file kept):', shlex.join(cmd))
        return
    t0 = time.time()
    p = subprocess.run(cmd, capture_output=True, text=True)
    wall = time.time() - t0
    out = p.stdout + p.stderr
    m = TX_ID.search(out)
    if not m:
        print(out[-3000:])
        raise SystemExit('no transaction id in the CLI output')
    h = m.group(1)
    r = near_tx(h, a.sender)
    rows = print_gas(h, r)
    st = r['status']
    result = json.loads(base64.b64decode(st['SuccessValue'])) if 'SuccessValue' in st else None
    rec = {'near_tx': h, 'sender': a.sender, 'vault': a.vault, 'message': signed['message'], 'txs': args['txs'],
           'status': st, 'wall_seconds_cli': round(wall, 2),
           'gas_total': sum(x[2] for x in rows), 'near_burnt': sum(x[3] for x in rows) / 1e24,
           'receipts': [{'id': x[0], 'executor': x[1], 'gas': x[2], 'tokens_burnt': x[3], 'status': x[4]} for x in rows],
           'signed': result}
    json.dump(rec, open(a.out, 'w'), indent=1)
    os.remove(tmp)
    print('signed transactions:', json.dumps(result, indent=1))


def cmd_near_gas(a):
    print_gas(a.tx, near_tx(a.tx, a.sender))


def broadcast_items(a):
    if a.target.startswith('0x'):
        return [{'raw': a.target, 'hash': None}]
    rec = json.load(open(a.target))
    items = rec['signed'] if isinstance(rec, dict) else rec
    return [items[a.index]] if a.index is not None else items


def cmd_broadcast(a):
    items = broadcast_items(a)
    for it in items:
        check_raw_chain_id(it['raw'])
    if a.dry_run:
        for it in items:
            print('dry run, would call: %s eth_sendRawTransaction [%s...] (hash-to-be %s), then eth_getTransactionReceipt'
                  % (a.rpc, it['raw'][:18], it.get('hash')))
        return
    log_rows = json.load(open(a.log)) if a.log and os.path.exists(a.log) else []
    for it in items:
        h = rpc(a.rpc, 'eth_sendRawTransaction', [it['raw']])
        if it.get('hash') and h.lower() != it['hash'].lower():
            raise SystemExit('RPC hash %s differs from the vault hash-to-be %s' % (h, it['hash']))
        print('sent', h)
        row = dict(receipt_row(Chain(base_rpc=a.rpc).receipt(h)), rpc=a.rpc)
        print(json.dumps(row))
        log_rows.append(row)
        if a.log:
            json.dump(log_rows, open(a.log, 'w'), indent=1)
        if row['status'] != 1:
            raise SystemExit('reverted')


def parser():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest='cmd', required=True)
    p = sp.add_parser('serve'); p.add_argument('--port', type=int, default=8787); p.add_argument('--sender', required=True)
    p.add_argument('--factory', required=True); p.add_argument('--near-rpc', default=NEAR_RPC); p.add_argument('--base-rpc', default=BASE_RPC)
    p.add_argument('--state', default=STATE_DEFAULT); p.add_argument('--dry-run', action='store_true')
    p.add_argument('--allow-reads', action='store_true', help='with --dry-run: make read-only calls (still sends nothing)')
    p.set_defaults(f=cmd_serve)
    p = sp.add_parser('derive'); p.add_argument('--account', required=True); p.add_argument('--path', default='cmdk-base'); p.set_defaults(f=cmd_derive)
    p = sp.add_parser('params'); p.add_argument('--from', required=True); p.add_argument('--to', required=True)
    p.add_argument('--data', required=True); p.add_argument('--nonce', type=int); p.add_argument('--rpc', default=BASE_RPC); p.set_defaults(f=cmd_params)
    p = sp.add_parser('submit'); p.add_argument('--vault', required=True); p.add_argument('--signed', required=True)
    p.add_argument('--txs', required=True); p.add_argument('--out', required=True); p.add_argument('--sender', default=RELAYER)
    p.add_argument('--dry-run', action='store_true'); p.set_defaults(f=cmd_submit)
    p = sp.add_parser('near-gas'); p.add_argument('tx'); p.add_argument('--sender', default=RELAYER); p.set_defaults(f=cmd_near_gas)
    p = sp.add_parser('broadcast'); p.add_argument('target'); p.add_argument('--index', type=int)
    p.add_argument('--rpc', default=BASE_RPC); p.add_argument('--log'); p.add_argument('--dry-run', action='store_true')
    p.set_defaults(f=cmd_broadcast)
    return ap


def main():
    a = parser().parse_args()
    a.f(a)


if __name__ == '__main__':
    main()
