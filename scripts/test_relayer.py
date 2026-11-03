#!/usr/bin/env python3 -I
"""Unit tests of relayer.py; no network (every chain call goes to FakeChain).

  python3 -I -m unittest discover -s scripts -p test_relayer.py     (from the repository root)
  python3 -I scripts/test_relayer.py
"""
import base64, json, os, shutil, subprocess, sys, tempfile, threading, time, unittest, urllib.error, urllib.request
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import relayer as R  # noqa: E402  (our own file)

USDC = '0x036cbd53842c5426634e7929541ec2318f3dcf7e'
WETH = '0x4200000000000000000000000000000000000006'
DUSDC = '0xeb68fd5baaafd4e46b43baa7feab3216b87b10d5'
EXEC = '0xb23e0d7e1d0182db7f6f2637ec4ac51c10290f40'
EXEC2 = '0x' + '22' * 20
MORPHO = '0xbbbbbbbbbb9cc5e90e3b3af64bdaf62c37eeffcb'
BASE = '0x' + 'ab' * 20
OWNER = '0x' + 'cd' * 20
LIVE, BACKUP, DEMO = '0x' + 'a2' * 32, '0x' + '99' * 32, '0x' + '55' * 32
VAULT = 'v1.factory.cmdk.testnet'
AGENT = 'ab' * 32
SIG = '0x' + '11' * 65


def market(mid, loan, executor=EXEC, previous=()):
    return {'id': mid, 'loan_token': loan, 'collateral_token': WETH, 'executor': executor,
            'previous_executors': list(previous), 'slice_max': '1000000000', 'corridor_set': False}


def vault_state(markets=None):
    return {'owner': OWNER, 'owner_nonce': 4, 'signer': 'v1.signer-prod.testnet', 'path': 'cmdk-base', 'base_address': BASE,
            'morpho': MORPHO, 'cooldown_secs': 120, 'last_agent_action': 0, 'approved': None, 'agent': None,
            'markets': markets if markets is not None else [market(LIVE, USDC), market(BACKUP, USDC), market(DEMO, DUSDC)]}


CORRIDOR = {'target': '5', 'act': '7', 'floor': '3', 'sell_budget': '2', 'migrate_allowed': True, 'exit_allowed': True}
MEASUREMENT = {'mrtd': 'aa' * 48, 'rtmr0': 'bb' * 48, 'rtmr1': 'cc' * 48, 'rtmr2': 'dd' * 48, 'compose_hash': 'ee' * 32,
               'os_image_hash': 'ff' * 32}


def part(mid, approve=True, authorize=True, corridor=None):
    p = {'market': mid, 'approve': approve, 'authorize': authorize}
    if corridor is not None:
        p['corridor'] = corridor
    return p


def setup_args(*parts, measurement=None):
    a = {'markets': list(parts)}
    if measurement is not None:
        a['measurement'] = measurement
    return a


# The Create vault message: live set up and authorized, backup on the same tokens and executor, both corridors.
CREATE = setup_args(part(LIVE, corridor=CORRIDOR), part(BACKUP, False, False, CORRIDOR), measurement=MEASUREMENT)


def message(method, args, nonce=5, expiry=None, **over):
    m = {'protocol': 'cmdk-vault/1', 'network': 'testnet', 'vault': VAULT, 'method': method, 'args': args,
         'nonce': nonce, 'expiry': expiry or int(time.time()) + 600}
    m.update(over)
    return json.dumps(m, separators=(',', ':'))


def rlp_item(b):
    if len(b) == 1 and b[0] < 0x80:
        return b
    if len(b) <= 55:
        return bytes([0x80 + len(b)]) + b
    n = len(b).to_bytes((len(b).bit_length() + 7) // 8, 'big')
    return bytes([0xb7 + len(n)]) + n + b


def rlp_list(items):
    p = b''.join(items)
    if len(p) <= 55:
        return bytes([0xc0 + len(p)]) + p
    n = len(p).to_bytes((len(p).bit_length() + 7) // 8, 'big')
    return bytes([0xf7 + len(n)]) + n + p


def ib(n):
    return n.to_bytes((n.bit_length() + 7) // 8, 'big') if n else b''


def fake_raw(chain_id, nonce, pad=0):
    """A type-2 envelope with the given chain id and nonce (enough for raw_chain_id)."""
    return '0x02' + rlp_list([rlp_item(ib(chain_id)), rlp_item(ib(nonce)), rlp_item(b'\x01' * pad)]).hex()


def rlp_at(b, i):
    """(value, next index) of the RLP string at b[i]."""
    if b[i] < 0x80:
        return b[i], i + 1
    n = b[i] - 0x80
    return int.from_bytes(b[i + 1:i + 1 + n], 'big'), i + 1 + n


def raw_nonce(raw):
    b = bytes.fromhex(raw[4:])
    _, i = rlp_at(b, 1 if b[0] <= 0xf7 else 1 + b[0] - 0xf7)     # skip the chain id
    return rlp_at(b, i)[0]


def load(path):
    with open(path) as f:
        return json.load(f)


def enc(*xs):
    return '0x' + ''.join(format(x, '064x') for x in xs)


class FakeChain(R.Chain):
    """Canned NEAR and Base answers; records every command and raw transaction it is asked to send."""

    def __init__(self, dry_run=False, allow_reads=True):
        super().__init__('near-rpc', 'base-rpc', dry_run, allow_reads)
        self.state = vault_state()
        self.authorized = set()          # (who, spender)
        self.allowances = {}             # (token, spender) -> amount
        self.balances = {}               # token -> amount
        self.position = (0, 0)           # (borrow_shares, collateral) on every market
        self.totals = (0, 0, 0, 0, 0, 0)
        self.near_balances = {'relayer.testnet': 5 * R.YOCTO}
        self.eth_balance = 10 ** 17
        self.pending_nonce = 7
        self.base_fee, self.priority = 10 ** 6, 10 ** 3
        self.sent_cmds, self.sent_raws = [], []
        self.statuses = []               # receipt status per broadcast, default 1
        self.tx_result = None

    def read(self, url, method, params):
        if not self.allow_reads:
            raise R.Skipped('%s %s' % (url, method))
        return self.near(method, params) if url == 'near-rpc' else self.base(method, params)

    def near(self, method, params):
        if method == 'status':
            return {'chain_id': 'testnet'}
        if method == 'tx':
            return self.tx_result
        if params['request_type'] == 'view_account':
            if params['account_id'] not in self.near_balances:
                raise R.ChainError('query: {"cause": {"name": "UNKNOWN_ACCOUNT"}}')
            return {'amount': str(self.near_balances[params['account_id']])}
        return {'result': list(json.dumps(self.state).encode())}

    def base(self, method, params):
        simple = {'eth_chainId': hex(84532), 'eth_blockNumber': hex(123), 'eth_maxPriorityFeePerGas': hex(self.priority),
                  'eth_getTransactionCount': hex(self.pending_nonce), 'eth_getBalance': hex(self.eth_balance),
                  'eth_getBlockByNumber': {'baseFeePerGas': hex(self.base_fee)}}
        if method in simple:
            return simple[method]
        return self.call(params[0]['to'].lower(), params[0]['data'])

    def call(self, to, data):
        sel, a = data[:10], R.words(data[10:])
        addr = ['0x%040x' % x for x in a]
        if sel == R.SEL['isAuthorized']:
            return enc(int((addr[0], addr[1]) in self.authorized))
        if sel == R.SEL['allowance']:
            return enc(self.allowances.get((to, addr[1]), 0))
        if sel == R.SEL['balanceOf']:
            return enc(self.balances.get(to, 0))
        if sel == R.SEL['corridors']:
            return enc(5 * 10 ** 17, 7 * 10 ** 17, 3 * 10 ** 17, 2 * 10 ** 17, 1, 1, 1)
        if sel == R.SEL['position']:
            return enc(0, self.position[0], self.position[1])
        if sel == R.SEL['market']:
            return enc(*self.totals)
        raise AssertionError('unexpected eth_call ' + data)

    def near_send(self, cmd):
        if self.dry_run:
            raise R.Refused('dry run')
        self.sent_cmds.append(cmd)
        if 'owner_call' in cmd:
            txs = json.loads(cmd[cmd.index('json-args') + 1])['txs']
            signed = [{'raw': fake_raw(84532, t['nonce']), 'hash': '0x%064x' % t['nonce'], 'from': BASE, 'to': EXEC,
                       'value': '0', 'nonce': t['nonce']} for t in txs]
            self.tx_result = {'status': {'SuccessValue': base64.b64encode(json.dumps(signed).encode()).decode()}}
        return '9' * 44

    def send_raw(self, raw):
        R.check_raw_chain_id(raw)
        self.sent_raws.append(raw)
        return '0x%064x' % raw_nonce(raw)

    def receipt(self, h, timeout=0, poll=0):
        i = len(self.sent_raws) - 1
        status = self.statuses[i] if i < len(self.statuses) else 1
        return {'transactionHash': h, 'status': hex(status), 'blockNumber': hex(100 + i), 'gasUsed': hex(50000),
                'effectiveGasPrice': hex(10 ** 6), 'l1Fee': hex(5), 'from': BASE, 'to': EXEC}


class TestMessage(unittest.TestCase):
    def parse(self, msg, sig=SIG, last=4):
        return R.parse_message(msg, sig, VAULT, last, int(time.time()))

    def test_valid(self):
        m = self.parse(message('setup', setup_args(part(LIVE))))
        self.assertEqual(m['method'], 'setup')
        self.parse(message('setup', CREATE))
        self.parse(message('setup', dict(setup_args(dict(part(LIVE), corridor=None)), measurement=None)))   # null = absent
        self.parse(message('setup', setup_args(measurement={'anything': 'the vault validates'})))
        self.parse(message('open_from_stables', {'market': LIVE, 'stables_in': '250000000', 'target_ltv': '5'}))

    def test_expiry_upper_bound(self):
        now = int(time.time())
        msg = lambda expiry: message('cancel', {}, expiry=expiry)
        self.assertEqual(R.parse_message(msg(now + 3600), SIG, VAULT, 4, now)['expiry'], now + 3600)
        with self.assertRaisesRegex(R.Refused, 'more than 3600 s ahead'):
            R.parse_message(msg(now + 3601), SIG, VAULT, 4, now)

    def test_refusals(self):
        ok = setup_args(part(LIVE))
        no_approve = {'market': LIVE, 'authorize': True}
        bad_corridor = dict(CORRIDOR, sell_budget=2)
        bad = [message('setup', ok, protocol='prime-vault/1'),
               message('setup', ok, network='mainnet'),
               message('setup', ok, vault='v2.factory.cmdk.testnet'),
               message('setup', ok, nonce=4),
               message('setup', ok, expiry=int(time.time()) + 5),
               message('nope', {}),
               message('setup', {'market': LIVE, 'authorize': True}),                      # the old shape
               message('setup', {}),
               message('setup', {'markets': part(LIVE)}),
               message('setup', setup_args(no_approve)),
               message('setup', setup_args(dict(part(LIVE), extra=1))),
               message('setup', dict(ok, extra=1)),
               message('setup', setup_args(dict(part(LIVE), authorize='true'))),
               message('setup', setup_args(part('0x1234'))),
               message('setup', setup_args(part(LIVE, corridor=bad_corridor))),
               message('setup', setup_args(part(LIVE, corridor=dict(CORRIDOR, market=LIVE)))),
               message('setup', setup_args(part(LIVE), measurement='aa')),
               message('repay', {'market': LIVE, 'amount': 5}),
               message('close', {'market': '0x1234'}),
               message('withdraw', {'token': USDC, 'amount': '1', 'destination': '0x12'}),
               message('open_from_stables', {'market': LIVE, 'stables_in': 1, 'target_ltv': '5'}),
               message('open_from_stables', {'market': LIVE, 'collateral_in': '1', 'target_ltv': '5'}),
               message('open_from_stables', {'market': LIVE, 'stables_in': '1', 'target_ltv': '5', 'recipient': OWNER}),
               'not json', json.dumps({'protocol': 'cmdk-vault/1'})]
        for msg in bad:
            with self.assertRaises(R.Refused, msg=msg):
                self.parse(msg)

    def test_bad_signature(self):
        for sig in ('0x12', SIG[:-2], '11' * 65, SIG + '00'):
            with self.assertRaises(R.Refused):
                self.parse(message('cancel', {}), sig)

    def test_measurement_args_pass_through(self):
        self.parse(message('approve_measurement', {'anything': 'the vault validates'}))


class TestPlan(unittest.TestCase):
    def count(self, method, args, st=None):
        return [p['kind'] for p in R.plan_txs(method, args, st or vault_state())]

    def test_counts(self):
        self.assertEqual(self.count('setup', setup_args(part(LIVE))), ['approve', 'approve', 'setAuthorization'])
        self.assertEqual(self.count('setup', setup_args(part(LIVE, authorize=False))), ['approve', 'approve'])
        self.assertEqual(self.count('setup', CREATE), ['approve', 'approve', 'setAuthorization', 'setCorridor', 'setCorridor'])
        self.assertEqual(self.count('setup', setup_args(measurement=MEASUREMENT)), [])
        corridor = dict(CORRIDOR, market=LIVE)
        self.assertEqual(self.count('set_corridor', corridor), ['setCorridor'])
        self.assertEqual(self.count('open', {'market': LIVE, 'collateral_in': '1', 'target_ltv': '5'}), ['open'])
        ofs = R.plan_txs('open_from_stables', {'market': LIVE, 'stables_in': '1', 'target_ltv': '5'}, vault_state())
        self.assertEqual(ofs, [{'kind': 'openFromStables', 'to': EXEC, 'value': 0}])
        self.assertEqual(R.gas_limit(ofs[0]['kind']), 800_000)
        self.assertEqual(self.count('repay', {'market': LIVE, 'amount': '1'}), ['repay'])
        self.assertEqual(self.count('close', {'market': DEMO}), ['close'])
        self.assertEqual(self.count('withdraw', {'token': USDC, 'amount': '1', 'destination': OWNER}), ['transfer'])
        self.assertEqual(self.count('cancel', {}), ['cancel'])
        self.assertEqual(self.count('approve_measurement', {}), [])
        self.assertEqual(self.count('revoke_agent', {}), [])
        sweep = R.plan_txs('sweep_eth', {'amount': '7'}, vault_state())
        self.assertEqual((sweep[0]['kind'], sweep[0]['to'], sweep[0]['value']), ('sweep_eth', OWNER, 7))
        self.assertEqual(R.plan_txs('cancel', {}, vault_state())[0]['to'], BASE)

    def test_zero_amounts(self):
        # open_from_stables with stables_in 0 is refused before any read, in the vault's wording ("zero stables_in");
        # open with collateral_in 0 is planned (borrow more against the collateral already supplied).
        with self.assertRaisesRegex(R.Refused, '^open_from_stables: zero stables_in$'):
            R.plan_txs('open_from_stables', {'market': LIVE, 'stables_in': '0', 'target_ltv': '5'}, vault_state())
        self.assertEqual(self.count('open', {'market': LIVE, 'collateral_in': '0', 'target_ltv': '5'}), ['open'])

    def test_targets(self):
        p = R.plan_txs('setup', setup_args(part(DEMO)), vault_state())
        self.assertEqual([x['to'] for x in p], [DUSDC, WETH, MORPHO])
        p = R.plan_txs('setup', CREATE, vault_state())
        self.assertEqual([x['to'] for x in p], [USDC, WETH, MORPHO, EXEC, EXEC])

    def test_setup_deduplicates(self):
        # The vault's rule: a call identical to one already in the message is left out. Live and backup share USDC,
        # WETH and the executor; demo shares WETH and the executor.
        p = R.plan_txs('setup', setup_args(part(LIVE), part(BACKUP), part(DEMO)), vault_state())
        self.assertEqual([(x['kind'], x['to']) for x in p],
                         [('approve', USDC), ('approve', WETH), ('setAuthorization', MORPHO), ('approve', DUSDC)])
        p = R.plan_txs('setup', setup_args(part(LIVE, authorize=False), part(BACKUP, authorize=False)), vault_state())
        self.assertEqual([x['to'] for x in p], [USDC, WETH])
        # Another executor is another spender: its approvals and authorization are not duplicates.
        st = vault_state([market(LIVE, USDC), market(BACKUP, USDC, EXEC2)])
        p = R.plan_txs('setup', setup_args(part(LIVE), part(BACKUP)), st)
        self.assertEqual([x['to'] for x in p], [USDC, WETH, MORPHO, USDC, WETH, MORPHO])

    def test_refusals(self):
        st = vault_state()
        for method, args in (('setup', setup_args(part('0x' + '01' * 32))),
                             ('setup', setup_args()),
                             ('setup', setup_args(part(LIVE, False, False))),
                             ('setup', setup_args(part(LIVE), part(BACKUP, False, False, CORRIDOR), part(LIVE.upper().replace('0X', '0x')))),
                             ('setup', setup_args(part(LIVE, corridor=dict(CORRIDOR, floor=str(2 ** 64))))),
                             ('withdraw', {'token': '0x' + '01' * 20, 'amount': '1', 'destination': OWNER}),
                             ('withdraw', {'token': USDC, 'amount': '0', 'destination': OWNER}),
                             ('withdraw', {'token': USDC, 'amount': '1', 'destination': '0x' + '00' * 20}),
                             ('sweep_eth', {'amount': '0'}),
                             ('set_corridor', {'market': LIVE, 'target': str(2 ** 64), 'act': '1', 'floor': '1',
                                               'sell_budget': '1', 'migrate_allowed': True, 'exit_allowed': True}),
                             ('set_executor', {'market': LIVE, 'executor': EXEC, 'revoke_authorization': True}),
                             ('set_executor', {'market': LIVE, 'executor': '0x' + '00' * 20, 'revoke_authorization': True})):
            with self.assertRaises(R.Refused, msg=method):
                R.plan_txs(method, args, st)

    def test_set_executor_shared_executor_is_zero(self):
        # LIVE moves; BACKUP and DEMO still use the old executor: nothing to revoke yet.
        self.assertEqual(self.count('set_executor', {'market': LIVE, 'executor': EXEC2, 'revoke_authorization': True}), [])

    def test_set_executor_single_market(self):
        st = vault_state([market(LIVE, USDC)])
        args = {'market': LIVE, 'executor': EXEC2, 'revoke_authorization': True}
        self.assertEqual(self.count('set_executor', args, st), ['setAuthorization', 'approve', 'approve'])
        args['revoke_authorization'] = False
        self.assertEqual(self.count('set_executor', args, st), ['approve', 'approve'])

    def test_set_executor_last_of_three(self):
        # LIVE and BACKUP moved earlier (old executor in their previous_executors); DEMO is the last one.
        st = vault_state([market(LIVE, USDC, EXEC2, [EXEC]), market(BACKUP, USDC, EXEC2, [EXEC]), market(DEMO, DUSDC)])
        p = R.plan_txs('set_executor', {'market': DEMO, 'executor': EXEC2, 'revoke_authorization': True}, st)
        self.assertEqual([x['kind'] for x in p], ['setAuthorization', 'approve', 'approve', 'approve'])
        self.assertEqual([x['to'] for x in p], [MORPHO, USDC, WETH, DUSDC])
        self.assertEqual(st['markets'][2]['executor'], EXEC, 'plan must not change the state it was given')


class TestFlags(unittest.TestCase):
    def check(self, method, args, facts, plan=('x',)):
        R.check_flags(method, args, list(plan), facts)

    def test_setup(self):
        def facts(authorized, *allowances, executor=EXEC):
            return {'executor': executor, 'authorized': authorized, 'allowances': dict(zip((USDC, WETH), allowances))}

        def check(args, *parts):
            self.check('setup', args, {'markets': list(parts)})

        fresh, ready = facts(False, 0, 0), facts(True, 5, 5)
        check(setup_args(part(LIVE)), fresh)
        check(setup_args(part(LIVE, authorize=False)), facts(True, 0, 0))
        check(setup_args(part(LIVE, authorize=False)), ready)                       # a repeated approve is allowed
        check(setup_args(part(LIVE, False, False, CORRIDOR)), ready)
        check(CREATE, fresh, fresh)                                                 # backup covered by live
        check(setup_args(part(LIVE, authorize=False), part(BACKUP, False, True)), fresh, fresh)
        with self.assertRaisesRegex(R.Refused, 'would revert'):
            check(setup_args(part(LIVE)), facts(True, 0, 0))
        with self.assertRaisesRegex(R.Refused, 'would revert'):
            check(setup_args(part(LIVE, authorize=False), part(BACKUP)), ready, ready)
        with self.assertRaisesRegex(R.Refused, 'authorize true'):
            check(setup_args(part(LIVE, authorize=False)), fresh)
        with self.assertRaisesRegex(R.Refused, 'authorize true'):
            check(setup_args(part(LIVE, False, False, CORRIDOR)), facts(False, 5, 5))
        with self.assertRaisesRegex(R.Refused, 'approve true'):
            check(setup_args(part(LIVE, approve=False)), fresh)
        with self.assertRaisesRegex(R.Refused, 'approve true'):
            check(setup_args(part(LIVE, False, False, CORRIDOR)), facts(True, 5, 0))
        # Per executor: a second executor needs its own authorization and approvals.
        with self.assertRaisesRegex(R.Refused, 'executor %s' % EXEC2):
            check(setup_args(part(LIVE), part(BACKUP, False, False)), fresh, facts(False, 0, 0, executor=EXEC2))
        check(setup_args(part(LIVE), part(BACKUP)), fresh, facts(False, 0, 0, executor=EXEC2))

    def test_set_executor(self):
        self.check('set_executor', {'revoke_authorization': True}, {'authorized_old': True})
        self.check('set_executor', {'revoke_authorization': False}, {'authorized_old': False})
        self.check('set_executor', {'revoke_authorization': True}, {'authorized_old': False}, plan=())   # nothing signed
        for flag in (True, False):
            with self.assertRaises(R.Refused):
                self.check('set_executor', {'revoke_authorization': flag}, {'authorized_old': not flag})

    def test_close_and_repay(self):
        ok = {'loan_allowance': 2 ** 256 - 1, 'loan_balance': 1010, 'debt': 1000}
        self.check('close', {}, ok)
        self.check('repay', {'amount': '500'}, dict(ok, loan_balance=505))
        self.check('close', {}, dict(ok, debt=0, loan_allowance=0, loan_balance=0))   # nothing to pull
        with self.assertRaisesRegex(R.Refused, 'allowance'):
            self.check('close', {}, dict(ok, loan_allowance=0))
        with self.assertRaisesRegex(R.Refused, 'allowance'):
            self.check('repay', {'amount': '5000'}, dict(ok, loan_allowance=1000))
        with self.assertRaisesRegex(R.Refused, 'holds'):
            self.check('close', {}, dict(ok, loan_balance=1000))
        with self.assertRaisesRegex(R.Refused, 'NoDebt'):
            self.check('repay', {'amount': '1'}, dict(ok, debt=0))

    def test_open(self):
        # Symmetric with open_from_stables: authorization, then allowance and balance of the token pulled.
        ok = {'authorized': True, 'collateral_allowance': 10, 'collateral_balance': 10}
        self.check('open', {'collateral_in': '10'}, ok)
        self.check('open', {'collateral_in': '0'}, dict(ok, collateral_allowance=0, collateral_balance=0))  # borrow more
        with self.assertRaisesRegex(R.Refused, 'no authorization'):
            self.check('open', {'collateral_in': '10'}, dict(ok, authorized=False))
        with self.assertRaisesRegex(R.Refused, 'allowance to the executor is 9, below collateral_in 10'):
            self.check('open', {'collateral_in': '10'}, dict(ok, collateral_allowance=9))
        with self.assertRaisesRegex(R.Refused, 'holds 9 of the collateral token, below collateral_in 10'):
            self.check('open', {'collateral_in': '10'}, dict(ok, collateral_balance=9))

    def test_open_from_stables(self):
        ok = {'authorized': True, 'loan_allowance': 10, 'loan_balance': 10}
        self.check('open_from_stables', {'stables_in': '10'}, ok)
        with self.assertRaisesRegex(R.Refused, 'no authorization'):
            self.check('open_from_stables', {'stables_in': '10'}, dict(ok, authorized=False))
        with self.assertRaisesRegex(R.Refused, 'allowance to the executor is 9, below stables_in 10'):
            self.check('open_from_stables', {'stables_in': '10'}, dict(ok, loan_allowance=9))
        with self.assertRaisesRegex(R.Refused, 'holds 9 of the loan token, below stables_in 10'):
            self.check('open_from_stables', {'stables_in': '10'}, dict(ok, loan_balance=9))


class TestFeesAndGas(unittest.TestCase):
    def test_fees(self):
        self.assertEqual(R.fees(10 ** 6, 10 ** 3), (2 * 10 ** 6 + 10 ** 3, 10 ** 3))
        self.assertEqual(R.fees(9 * 10 ** 8, 5 * 10 ** 8), (2 * 10 ** 9, 5 * 10 ** 8))       # capped at 2 gwei
        self.assertEqual(R.fees(10 ** 9, 3 * 10 ** 9), (2 * 10 ** 9, 2 * 10 ** 9))           # priority <= max fee
        with self.assertRaises(R.Refused):
            R.fees(2 * 10 ** 9, 0)

    def test_gas_table(self):
        self.assertEqual(R.GAS_TABLE, {'approve': 80000, 'setAuthorization': 80000, 'setCorridor': 120000, 'open': 600000,
                                       'openFromStables': 800000, 'repay': 300000, 'close': 500000, 'reduce': 700000,
                                       'migrate': 900000, 'exit': 800000, 'transfer': 80000, 'cancel': 30000,
                                       'sweep_eth': 30000})
        self.assertTrue(all(R.gas_limit(k) <= R.GAS_LIMIT_BOUND for k in R.GAS_TABLE))
        old = R.GAS_TABLE['open']
        try:
            R.GAS_TABLE['open'] = 2_000_000
            self.assertEqual(R.gas_limit('open'), 1_000_000)
        finally:
            R.GAS_TABLE['open'] = old

    def test_compose_params(self):
        c = FakeChain()
        c.base_fee, c.priority, c.pending_nonce = 5 * 10 ** 6, 10 ** 5, 41
        plan = R.plan_txs('setup', setup_args(part(LIVE)), vault_state())
        txs = R.compose_params(c, BASE, plan)
        self.assertEqual([t['nonce'] for t in txs], [41, 42, 43])
        self.assertEqual([t['gas_limit'] for t in txs], [80000, 80000, 80000])
        self.assertTrue(all(t['max_fee_per_gas'] == 10 ** 7 + 10 ** 5 and t['max_priority_fee_per_gas'] == 10 ** 5 for t in txs))
        self.assertEqual(R.compose_params(c, BASE, []), [])

    def test_check_funds(self):
        c = FakeChain()
        plan = [{'kind': 'sweep_eth', 'to': OWNER, 'value': 10 ** 16}]
        txs = [{'nonce': 1, 'gas_limit': 30000, 'max_fee_per_gas': 10 ** 9, 'max_priority_fee_per_gas': 1}]
        c.eth_balance = 10 ** 16 + 30000 * 10 ** 9
        R.check_funds(c, BASE, plan, txs)
        c.eth_balance -= 1
        with self.assertRaises(R.Refused):
            R.check_funds(c, BASE, plan, txs)


class TestPrechecks(unittest.TestCase):
    def policy(self, **over):
        m = {'id': LIVE, 'loan_token': USDC, 'collateral_token': WETH, 'executor': EXEC, 'slice_max': '1000000000'}
        m.update(over)
        return {'cooldown_secs': 120, 'markets': [m]}

    def test_policy_ok(self):
        R.validate_policy(OWNER, self.policy())
        R.validate_policy(OWNER[2:], self.policy(executor=EXEC[2:]))       # 0x is optional, as in the factory

    def test_policy_refusals(self):
        two = self.policy()
        two['markets'].append(dict(two['markets'][0]))
        cases = [(OWNER[:-2], self.policy()), (OWNER, self.policy(executor='0x' + '00' * 20)), (OWNER, two),
                 (OWNER, {'cooldown_secs': 120, 'markets': []}), (OWNER, self.policy(id='0x1234')),
                 (OWNER, self.policy(loan_token=USDC + '00')), (OWNER, self.policy(slice_max=1000)),
                 (OWNER, self.policy(extra=1)), (OWNER, {'cooldown_secs': '120', 'markets': self.policy()['markets']}),
                 (OWNER, self.policy(collateral_token='0xzz' + WETH[4:]))]
        for owner, pol in cases:
            with self.assertRaises(R.Refused, msg=json.dumps(pol)):
                R.validate_policy(owner, pol)

    def test_accounts(self):
        R.check_implicit_account(AGENT)
        for bad in (AGENT.upper(), AGENT[:-1], AGENT + '0', 'relayer.testnet', 'g' * 64, None):
            with self.assertRaises(R.Refused):
                R.check_implicit_account(bad)
        R.check_named_account('relayer.testnet')
        for bad in ('relayer.near', 'testnet', 'Relayer.testnet', AGENT, ''):
            with self.assertRaises(R.Refused):
                R.check_named_account(bad)

    def test_near_amounts(self):
        self.assertEqual(R.parse_near('2'), 2 * R.YOCTO)
        self.assertEqual(R.parse_near('0.000000000000000000000001'), 1)
        self.assertEqual(R.fmt_near(16 * 10 ** 22), '0.16 NEAR')
        for bad in ('x', 0.5, '1e-30'):
            with self.assertRaises(R.Refused):
                R.parse_near(bad)

    def test_raw_chain_id(self):
        self.assertEqual(R.raw_chain_id(fake_raw(84532, 3)), 84532)
        self.assertEqual(R.raw_chain_id(fake_raw(84532, 3, pad=120)), 84532)       # long-list prefix
        R.check_raw_chain_id(fake_raw(84532, 0))
        for raw in (fake_raw(8453, 0), fake_raw(1, 0)):
            with self.assertRaises(R.Refused):
                R.check_raw_chain_id(raw)
        with self.assertRaises(R.Refused):
            R.raw_chain_id('0x01' + fake_raw(84532, 0)[4:])

    def test_near_cmd_is_testnet(self):
        cmd = R.near_cmd(['tokens', 'relayer.testnet', 'send-near', AGENT, '1 yoctonear'])
        self.assertEqual(cmd[-4:], ['network-config', 'testnet', 'sign-with-keychain', 'send'])
        with self.assertRaises(R.Refused):
            R.Chain(dry_run=True).near_send(cmd)                  # dry run never runs the CLI
        with self.assertRaises(R.Refused):
            R.Chain().near_send(['near', 'tokens', 'x', 'network-config', 'mainnet', 'sign-with-keychain', 'send'])
        with self.assertRaises(R.Refused):
            R.Chain(dry_run=True).send_raw(fake_raw(84532, 0))

    def test_near_cmd_access_key_file(self):
        # the container sets NEAR_ACCESS_KEY_FILE (cloud/README.md); R.SIGN is what the module computed from it
        sign = ['sign-with-access-key-file', '/home/cmdk/.config/prime/near_relayer_key.json']
        orig = R.SIGN
        R.SIGN = sign
        try:
            cmd = R.near_cmd(['tokens', 'relayer.testnet', 'send-near', AGENT, '1 yoctonear'])
            self.assertEqual(cmd[-5:], ['network-config', 'testnet'] + sign + ['send'])
            with self.assertRaises(R.Refused):
                R.Chain(dry_run=True).near_send(cmd)
            with self.assertRaises(R.Refused):                   # keychain form refused while a key file is set
                R.Chain().near_send(['near', 'tokens', 'x', 'network-config', 'testnet', 'sign-with-keychain', 'send'])
            with self.assertRaises(R.Refused):
                R.Chain().near_send(['near', 'tokens', 'x', 'network-config', 'mainnet'] + sign + ['send'])
        finally:
            R.SIGN = orig

    def test_start_guard(self):
        c = FakeChain()
        R.check_networks(c)
        c.base = lambda method, params: hex(8453)            # Base mainnet
        with self.assertRaises(SystemExit) as e:
            R.check_networks(c)
        self.assertEqual(e.exception.code, 2)                # relayer.sh does not restart on 2

    def test_debt_math(self):
        self.assertEqual(R.to_assets_up(0, 10 ** 6, 10 ** 12), 0)
        self.assertEqual(R.to_assets_up(10 ** 12, 10 ** 6, 10 ** 12), 10 ** 6)
        self.assertEqual(R.to_assets_up(1, 10, 10 ** 12), 1)       # rounds up

    @unittest.skipUnless(os.path.exists(R.CAST), 'cast not installed')
    def test_selectors(self):
        sigs = {'isAuthorized': 'isAuthorized(address,address)', 'allowance': 'allowance(address,address)',
                'balanceOf': 'balanceOf(address)', 'corridors': 'corridors(address,bytes32)',
                'position': 'position(bytes32,address)', 'market': 'market(bytes32)'}
        for k, s in sigs.items():
            self.assertEqual(subprocess.run([R.CAST, 'sig', s], capture_output=True, text=True).stdout.strip(), R.SEL[k], s)


class TestState(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, 'sub', 'relayer.json')

    def tearDown(self):
        shutil.rmtree(self.dir)

    def test_atomic_write(self):
        R.atomic_write_json(self.path, {'a': 1})
        self.assertEqual(load(self.path), {'a': 1})
        with self.assertRaises(TypeError):
            R.atomic_write_json(self.path, {'a': object()})       # fails mid-write
        self.assertEqual(load(self.path), {'a': 1})   # the old file is intact
        self.assertEqual(os.listdir(os.path.dirname(self.path)), ['relayer.json'])   # no temp file left

    def test_state_round_trip_and_dry_run(self):
        s = R.State(self.path)
        s.bump('owner_call')
        s.record_funding(AGENT, 10 ** 23, 'tx1', 1000.0)
        t = R.State(self.path)
        self.assertEqual(t.data['counters'], {'owner_call': 1, 'fund_agent': 1})
        self.assertEqual(t.funded_last_day(AGENT, 1000.0 + 86399), 10 ** 23)
        self.assertEqual(t.funded_last_day(AGENT, 1000.0 + 86401), 0)
        d = R.State(self.path, write=False)
        d.bump('owner_call')
        self.assertEqual(R.State(self.path).data['counters']['owner_call'], 1)    # a dry run writes nothing


class TestFunding(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.chain = FakeChain()
        self.app = R.App(self.chain, R.State(os.path.join(self.dir, 's.json')), 'relayer.testnet', 'factory.testnet')

    def tearDown(self):
        shutil.rmtree(self.dir)

    def test_new_account(self):
        out = self.app.fund_agent({'implicit_account': AGENT})
        self.assertEqual(out['amount_yocto'], str(16 * 10 ** 22))
        self.assertEqual(self.chain.sent_cmds[0][:5], ['near', 'tokens', 'relayer.testnet', 'send-near', AGENT])
        self.assertEqual(self.chain.sent_cmds[0][5], '%d yoctonear' % (16 * 10 ** 22))

    def test_top_up_and_already_funded(self):
        self.chain.near_balances[AGENT] = 10 ** 23
        self.assertEqual(self.app.fund_agent({'implicit_account': AGENT})['amount_yocto'], str(6 * 10 ** 22))
        self.chain.near_balances[AGENT] = 15 * 10 ** 22
        self.assertEqual(self.app.fund_agent({'implicit_account': AGENT})['status'], 'already funded')
        self.assertEqual(len(self.chain.sent_cmds), 1)

    def test_cap(self):
        self.app.state.record_funding(AGENT, 45 * 10 ** 22, 'earlier', time.time() - 3600)
        with self.assertRaisesRegex(R.Refused, 'cap'):
            self.app.fund_agent({'implicit_account': AGENT})          # 0.45 + 0.16 > 0.5
        self.app.state.data['funded'][AGENT][0]['time'] -= 86400
        self.app.fund_agent({'implicit_account': AGENT})              # a day later it is allowed
        self.assertEqual(len(self.chain.sent_cmds), 1)

    def test_refuses_bad_account(self):
        with self.assertRaises(R.Refused):
            self.app.fund_agent({'implicit_account': 'someone.testnet'})


class TestHttp(unittest.TestCase):
    """The HTTP handler in a thread against FakeChain."""

    def start(self, chain):
        self.dir = tempfile.mkdtemp()
        self.chain = chain
        self.app = R.App(chain, R.State(os.path.join(self.dir, 's.json'), write=not chain.dry_run),
                         'relayer.testnet', 'factory.testnet')
        self.srv = R.make_server(self.app, 0)
        self.addCleanup(self.stop)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = 'http://127.0.0.1:%d' % self.srv.server_address[1]

    def stop(self):
        self.srv.shutdown()
        self.srv.server_close()
        shutil.rmtree(self.dir)

    def get(self, path, body=None):
        data = None if body is None else (body if isinstance(body, bytes) else json.dumps(body).encode())
        req = urllib.request.Request(self.url + path, data, {'Content-Type': 'application/json'})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.load(r)
        except urllib.error.HTTPError as e:
            with e:
                return e.code, json.load(e)

    def setUp(self):
        log, R.log = R.log, lambda line: None      # keep the test output clean
        self.addCleanup(setattr, R, 'log', log)

    def owner_call(self, method, args):
        # "wait": true is the synchronous contract (3 Nov); TestJobs covers the asynchronous default
        return self.get('/owner-call', {'vault': VAULT, 'message': message(method, args), 'signature': SIG, 'wait': True})

    def test_binds_localhost(self):
        self.start(FakeChain())
        self.assertEqual(self.srv.server_address[0], '127.0.0.1')

    def test_health(self):
        self.start(FakeChain())
        code, out = self.get('/health')
        self.assertEqual((code, out['ok'], out['base_block'], out['sender_balance']), (200, True, 123, '5 NEAR'))
        self.assertFalse(out['dry_run'])

    def test_health_dry_run_no_reads(self):
        self.start(FakeChain(dry_run=True, allow_reads=False))
        code, out = self.get('/health')
        self.assertEqual((code, out['ok'], out['dry_run'], len(out['reads_skipped'])), (200, True, True, 2))

    def test_vault_view(self):
        c = FakeChain()
        c.authorized.add((BASE, EXEC))
        c.allowances[(USDC, EXEC)] = 2 ** 256 - 1
        c.position, c.totals = (10 ** 12, 4 * 10 ** 15), (0, 0, 10 ** 6, 10 ** 12, 0, 0)
        self.start(c)
        code, out = self.get('/vault/' + VAULT)
        live = out['base']['markets'][0]
        self.assertEqual((code, live['morpho_authorized'], live['corridor']['set']), (200, True, True))
        self.assertEqual((live['loan_allowance'], live['collateral_allowance']), (str(2 ** 256 - 1), '0'))
        self.assertEqual((live['position']['debt'], live['position']['collateral']), ('1000000', str(4 * 10 ** 15)))
        self.assertEqual(sorted(out['base']['tokens']), sorted([USDC, WETH, DUSDC]))
        self.assertEqual(self.get('/vault/v1.factory.near')[0], 400)

    def test_owner_call_refused_by_base(self):
        c = FakeChain()
        c.authorized.add((BASE, EXEC))
        self.start(c)
        code, out = self.owner_call('setup', setup_args(part(LIVE)))
        self.assertEqual((code, out['ok']), (400, False))
        self.assertIn('would revert', out['error'])
        code, out = self.owner_call('setup', setup_args(part(LIVE, False, False, CORRIDOR)))
        self.assertEqual((code, out['ok']), (400, False))
        self.assertIn('approve true', out['error'])
        self.assertEqual(c.sent_cmds, [])
        code, out = self.owner_call('close', {'market': LIVE})
        self.assertEqual((code, out['ok']), (200, True))      # close with no debt pulls nothing: allowed
        self.assertEqual(c.sent_cmds[0][4:6], [VAULT, 'owner_call'])

    def test_owner_call_close_without_allowance(self):
        c = FakeChain()
        c.position, c.totals = (10 ** 12, 10 ** 15), (0, 0, 10 ** 6, 10 ** 12, 0, 0)
        c.balances[USDC] = 2 * 10 ** 6
        self.start(c)
        code, out = self.owner_call('close', {'market': LIVE})
        self.assertEqual(code, 400)
        self.assertIn('item 14', out['error'])
        self.assertEqual(c.sent_cmds, [])

    def test_owner_call_open_from_stables(self):
        # The loan token (USDC on live) is read at the vault's Base address, for the executor; refused before NEAR
        # until allowance and balance cover stables_in, then one transaction with the openFromStables gas limit.
        c = FakeChain()
        c.authorized.add((BASE, EXEC))
        c.allowances[(USDC, EXEC)] = 250_000_000
        c.allowances[(WETH, EXEC)] = 2 ** 256 - 1
        self.start(c)
        args = {'market': LIVE, 'stables_in': '250000000', 'target_ltv': '400000000000000000'}
        code, out = self.owner_call('open_from_stables', args)
        self.assertEqual(code, 400)
        self.assertIn('holds 0 of the loan token', out['error'])
        c.balances[USDC] = 250_000_000
        code, out = self.owner_call('open_from_stables', dict(args, stables_in='250000001'))
        self.assertEqual(code, 400)
        self.assertIn('allowance to the executor is 250000000', out['error'])
        self.assertEqual(c.sent_cmds, [])
        code, out = self.owner_call('open_from_stables', args)
        self.assertEqual((code, out['ok'], len(out['signed'])), (200, True, 1))
        sent = json.loads(c.sent_cmds[0][c.sent_cmds[0].index('json-args') + 1])
        self.assertEqual(json.loads(sent['message'])['method'], 'open_from_stables')
        self.assertEqual([t['gas_limit'] for t in sent['txs']], [800_000])

    def test_owner_call_open(self):
        # The collateral token (WETH on live) is read at the vault's Base address, for the executor; refused before
        # NEAR until allowance and balance cover collateral_in, then one transaction.
        c = FakeChain()
        c.authorized.add((BASE, EXEC))
        c.allowances[(WETH, EXEC)] = 10 ** 15
        self.start(c)
        args = {'market': LIVE, 'collateral_in': str(10 ** 15), 'target_ltv': '500000000000000000'}
        code, out = self.owner_call('open', args)
        self.assertEqual(code, 400)
        self.assertIn('holds 0 of the collateral token, below collateral_in %d' % 10 ** 15, out['error'])
        c.balances[WETH] = 10 ** 15
        code, out = self.owner_call('open', dict(args, collateral_in=str(10 ** 15 + 1)))
        self.assertEqual(code, 400)
        self.assertIn('allowance to the executor is %d' % 10 ** 15, out['error'])
        self.assertEqual(c.sent_cmds, [])
        code, out = self.owner_call('open', args)
        self.assertEqual((code, out['ok'], len(out['signed'])), (200, True, 1))
        self.assertEqual(json.loads(json.loads(c.sent_cmds[0][c.sent_cmds[0].index('json-args') + 1])['message'])['method'],
                         'open')

    def test_owner_call_open_from_stables_zero(self):
        c = FakeChain()
        c.authorized.add((BASE, EXEC))
        self.start(c)
        code, out = self.owner_call('open_from_stables', {'market': LIVE, 'stables_in': '0', 'target_ltv': '5'})
        self.assertEqual((code, out['ok']), (400, False))
        self.assertIn('open_from_stables: zero stables_in', out['error'])
        self.assertEqual(c.sent_cmds, [])

    def test_owner_call_broadcasts_in_nonce_order(self):
        c = FakeChain()
        self.start(c)
        code, out = self.owner_call('setup', setup_args(part(LIVE)))
        self.assertEqual((code, out['ok'], len(out['signed']), len(out['receipts'])), (200, True, 3, 3))
        args = json.loads(c.sent_cmds[0][c.sent_cmds[0].index('json-args') + 1])
        self.assertEqual([t['nonce'] for t in args['txs']], [7, 8, 9])
        self.assertEqual(c.sent_cmds[0][-8:-4], ['attached-deposit', '0 NEAR', 'sign-as', 'relayer.testnet'])
        self.assertIn('300 Tgas', c.sent_cmds[0])
        self.assertEqual([raw_nonce(r) for r in c.sent_raws], [7, 8, 9])
        self.assertEqual(self.app.state.data['vaults'][VAULT]['held'], [])
        self.assertEqual(load(os.path.join(self.dir, 's.json'))['counters']['broadcast'], 3)

    def test_owner_call_stops_at_revert(self):
        c = FakeChain()
        c.statuses = [1, 0, 1]
        self.start(c)
        code, out = self.owner_call('setup', setup_args(part(LIVE)))
        self.assertEqual((out['ok'], len(out['receipts']), out['receipts'][1]['status']), (False, 2, 0))
        self.assertEqual(len(c.sent_raws), 2)
        self.assertEqual(out['held'], ['0x%064x' % 9])
        self.assertIn('reverted', out['error'])

    def test_owner_call_create_vault(self):
        # One signature: five Base transactions at consecutive nonces, broadcast in order; then the same message
        # once Morpho has the authorization is refused before NEAR.
        c = FakeChain()
        self.start(c)
        code, out = self.owner_call('setup', CREATE)
        self.assertEqual((code, out['ok'], len(out['signed']), len(out['receipts'])), (200, True, 5, 5))
        args = json.loads(c.sent_cmds[0][c.sent_cmds[0].index('json-args') + 1])
        self.assertEqual([t['nonce'] for t in args['txs']], [7, 8, 9, 10, 11])
        self.assertEqual([t['gas_limit'] for t in args['txs']], [80000, 80000, 80000, 120000, 120000])
        self.assertEqual([raw_nonce(r) for r in c.sent_raws], [7, 8, 9, 10, 11])
        c.authorized.add((BASE, EXEC))
        code, out = self.owner_call('setup', CREATE)
        self.assertEqual((code, out['ok']), (400, False))
        self.assertIn('would revert', out['error'])
        self.assertEqual(len(c.sent_cmds), 1)

    def test_owner_call_no_base_transaction(self):
        c = FakeChain()
        self.start(c)
        code, out = self.owner_call('revoke_agent', {})
        self.assertEqual((code, out['ok'], out['signed'], c.sent_raws), (200, True, [], []))

    def test_owner_call_dry_run(self):
        c = FakeChain(dry_run=True, allow_reads=True)
        self.start(c)
        code, out = self.owner_call('set_corridor', {'market': LIVE, 'target': '5', 'act': '7', 'floor': '3',
                                                     'sell_budget': '2', 'migrate_allowed': True, 'exit_allowed': False})
        self.assertEqual((code, out['dry_run'], len(out['txs']), out['txs'][0]['gas_limit']), (200, True, 1, 120000))
        self.assertTrue(out['near_command'].startswith('near contract call-function as-transaction ' + VAULT + ' owner_call'))
        self.assertTrue(out['near_command'].endswith('network-config testnet sign-with-keychain send'))
        self.assertEqual((c.sent_cmds, c.sent_raws), ([], []))

    def test_create_vault(self):
        c = FakeChain()
        c.tx_result = {'status': {'SuccessValue': ''}, 'receipts_outcome': [
            {'outcome': {'logs': []}}, {'outcome': {'logs': ['creating vault: v1.factory.testnet ' + BASE]}}]}
        self.start(c)
        req = {'owner': OWNER, 'deposit_near': '2', 'policy': {'cooldown_secs': 120, 'markets': [
            {'id': LIVE, 'loan_token': USDC, 'collateral_token': WETH, 'executor': EXEC, 'slice_max': '1000000000'}]}}
        code, out = self.get('/create-vault', dict(req, wait=True))
        self.assertEqual((code, out['vault'], out['base_address']), (200, 'v1.factory.testnet', BASE))
        self.assertNotIn('float', out)                       # App's default: no float
        cmd = c.sent_cmds[0]
        self.assertEqual(cmd[4:6], ['factory.testnet', 'create_vault'])
        self.assertEqual(cmd[cmd.index('attached-deposit') + 1], '%d yoctonear' % (2 * R.YOCTO))
        req['policy']['markets'][0]['executor'] = '0x' + '00' * 20
        self.assertEqual(self.get('/create-vault', req)[0], 400)
        req['policy']['markets'][0]['executor'], req['deposit_near'] = EXEC, '11'
        self.assertEqual(self.get('/create-vault', req)[0], 400)
        self.assertEqual(len(c.sent_cmds), 1)

    def test_create_vault_dry_run_and_errors(self):
        self.start(FakeChain(dry_run=True, allow_reads=False))
        req = {'owner': OWNER, 'deposit_near': '2', 'policy': {'cooldown_secs': 120, 'markets': [
            {'id': LIVE, 'loan_token': USDC, 'collateral_token': WETH, 'executor': EXEC, 'slice_max': '1'}]}}
        code, out = self.get('/create-vault', dict(req, wait=True))
        self.assertEqual((code, out['dry_run']), (200, True))
        self.assertIn("create_vault json-args '{", out['near_command'])
        self.assertEqual(self.get('/nope')[0], 404)
        self.assertEqual(self.get('/owner-call', b'not json')[0], 400)
        code, out = self.get('/fund-agent', {'implicit_account': AGENT})
        self.assertEqual((code, out['dry_run'], len(out['reads_skipped'])), (200, True, 1))


class GatedChain(FakeChain):
    """FakeChain whose NEAR send and Base receipts wait for the test, so a job can be seen between stages."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.near_gate, self.receipt_gate = threading.Event(), threading.Event()

    def near_send(self, cmd):
        assert self.near_gate.wait(5), 'near gate not opened'
        return super().near_send(cmd)

    def receipt(self, h, timeout=0, poll=0):
        assert self.receipt_gate.wait(5), 'receipt gate not opened'
        return super().receipt(h)


class FakeWallet:
    """Wallet without a key: records the transfers it signs; a raw transaction FakeChain can decode."""
    address = '0x' + 'ee' * 20

    def __init__(self, fail=None):
        self.signed, self.fail = [], fail

    def sign_transfer(self, to, value, nonce, max_fee, priority):
        if self.fail:
            raise R.ChainError(self.fail)
        self.signed.append({'to': to, 'value': value, 'nonce': nonce, 'max_fee': max_fee, 'priority': priority})
        return {'raw': fake_raw(84532, nonce), 'hash': '0x%064x' % nonce}


CREATED = {'status': {'SuccessValue': ''}, 'receipts_outcome': [
    {'outcome': {'logs': ['creating vault: v2.factory.testnet ' + BASE]}}]}
CREATE_REQ = {'owner': OWNER, 'deposit_near': '2', 'policy': {'cooldown_secs': 120, 'markets': [
    {'id': LIVE, 'loan_token': USDC, 'collateral_token': WETH, 'executor': EXEC, 'slice_max': '1000000000'}]}}
FLOAT = 5 * 10 ** 15


class JobHttp(TestHttp):
    """TestHttp's server and helpers with App options (float, CORS); TestHttp's own tests are not inherited."""

    def start(self, chain, **app):
        self.dir = tempfile.mkdtemp()
        self.chain = chain
        self.app = R.App(chain, R.State(os.path.join(self.dir, 's.json'), write=not chain.dry_run), 'relayer.testnet',
                         'factory.testnet', wallet_lock_dir=os.path.join(self.dir, 'lock'), **app)
        self.srv = R.make_server(self.app, 0)
        self.addCleanup(self.stop)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = 'http://127.0.0.1:%d' % self.srv.server_address[1]

    def post_owner(self, method, args, **extra):
        return self.get('/owner-call', dict({'vault': VAULT, 'message': message(method, args), 'signature': SIG}, **extra))

    def wait_stage(self, jid, *stages, timeout=5):
        t0, out = time.time(), None
        while time.time() - t0 < timeout:
            code, out = self.get('/job/' + jid)
            if out.get('stage') in stages:
                return code, out
            time.sleep(0.02)
        raise AssertionError('job %s never reached %s: %s' % (jid, stages, out))

    def request(self, method, path, headers=None):
        req = urllib.request.Request(self.url + path, None, headers or {}, method=method)
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, r.headers, r.read()


for _name in [n for n in dir(TestHttp) if n.startswith('test_')]:
    setattr(JobHttp, _name, None)        # inherit the helpers, not the tests


class TestJobs(JobHttp):
    def test_owner_call_stages_in_order(self):
        c = GatedChain()
        self.start(c)
        code, out = self.post_owner('setup', setup_args(part(LIVE)))
        self.assertEqual(code, 202)
        self.assertEqual(sorted(out), ['job', 'ok'])
        jid = out['job']
        self.assertRegex(jid, '^[0-9a-f]{16}$')
        code, job = self.get('/job/' + jid)
        self.assertEqual((code, job['ok'], job['stage'], job['kind'], job['vault']), (200, True, 'accepted', 'owner_call', VAULT))
        c.near_gate.set()
        code, job = self.wait_stage(jid, 'base_sent')         # the first receipt is held back
        self.assertEqual((code, job['ok'], job['method'], job['near_tx']), (200, True, 'setup', '9' * 44))
        self.assertEqual([s['nonce'] for s in job['signed']], [7, 8, 9])
        self.assertEqual(job['base_sent'], ['0x%064x' % 7])
        self.assertNotIn('receipts', job)
        c.receipt_gate.set()
        code, job = self.wait_stage(jid, 'done', 'failed')
        self.assertEqual((code, job['ok'], job['stage']), (200, True, 'done'))
        self.assertEqual(list(job['times']), ['accepted', 'vault_checked', 'base_sent', 'done'])
        t = list(job['times'].values())
        self.assertEqual(t, sorted(t))
        self.assertEqual(job['base_sent'], ['0x%064x' % n for n in (7, 8, 9)])
        self.assertEqual([(r['hash'], r['status']) for r in job['receipts']], [('0x%064x' % n, 1) for n in (7, 8, 9)])
        code, health = self.get('/health')
        self.assertEqual(health['jobs'], {'kept': 1, 'max': 1000, 'running': 0, 'stages': {'done': 1}})

    def test_owner_call_without_base_transaction(self):
        self.start(FakeChain())
        code, out = self.post_owner('revoke_agent', {})
        code, job = self.wait_stage(out['job'], 'done', 'failed')
        self.assertEqual((job['stage'], job['signed'], job['receipts']), ('done', [], []))
        self.assertEqual(list(job['times']), ['accepted', 'vault_checked', 'done'])

    def test_failure_paths(self):
        c = FakeChain()
        c.statuses = [1, 0, 1]
        self.start(c)
        code, out = self.post_owner('setup', setup_args(part(LIVE)))
        code, job = self.wait_stage(out['job'], 'done', 'failed')
        self.assertEqual((code, job['ok'], job['stage'], job['http_status']), (200, False, 'failed', 200))
        self.assertIn('reverted; the rest were not broadcast', job['error'])
        self.assertEqual((len(job['receipts']), job['held'], job['near_tx']), (2, ['0x%064x' % 9], '9' * 44))
        self.assertEqual(list(job['times']), ['accepted', 'vault_checked', 'base_sent', 'failed'])
        # a refusal found in the thread (it needs reads): failed, http_status 400, nothing sent
        c.authorized.add((BASE, EXEC))
        code, out = self.post_owner('setup', setup_args(part(LIVE)))
        self.assertEqual(code, 202)
        code, job = self.wait_stage(out['job'], 'done', 'failed')
        self.assertEqual((job['ok'], job['http_status'], list(job['times'])), (False, 400, ['accepted', 'failed']))
        self.assertIn('would revert', job['error'])
        self.assertEqual(len(c.sent_cmds), 1)
        # the vault refuses on NEAR: failed with near_tx, as the synchronous answer
        c.authorized.clear()

        def refused_on_near(cmd):
            c.tx_result = {'status': {'Failure': {'panic': 'bad owner signature'}}}
            return '8' * 44
        c.near_send = refused_on_near
        code, out = self.post_owner('setup', setup_args(part(LIVE)))
        code, job = self.wait_stage(out['job'], 'done', 'failed')
        self.assertEqual((job['ok'], job['http_status'], job['near_tx']), (False, 200, '8' * 44))
        self.assertIn('NEAR transaction failed', job['error'])
        # a bad request is refused at once and starts no job
        code, out = self.get('/owner-call', {'vault': VAULT, 'message': '{}'})
        self.assertEqual((code, out['ok']), (400, False))
        self.assertIn('request: missing', out['error'])
        code, out = self.get('/owner-call', {'vault': 'v1.f.near', 'message': '{}', 'signature': SIG})
        self.assertEqual(code, 400)
        self.assertEqual(self.app.jobs.counts()['kept'], 3)

    def test_chain_error_after_near_send(self):
        # the NEAR transaction was sent, then its RPC read failed: failed with http_status 502 and the NEAR tx named
        c = FakeChain()
        self.start(c)

        def rpc_down(h, sender):
            raise R.ChainError('tx near-rpc: timeout')
        c.near_tx = rpc_down
        code, out = self.get('/create-vault', CREATE_REQ)
        code, job = self.wait_stage(out['job'], 'done', 'failed')
        self.assertEqual((job['ok'], job['http_status'], job['near_tx'], job['error']),
                         (False, 502, '9' * 44, 'chain: tx near-rpc: timeout'))
        self.assertEqual(list(job['times']), ['accepted', 'failed'])
        code, out = self.post_owner('revoke_agent', {})          # owner_call answers this one as 200 ok false
        code, job = self.wait_stage(out['job'], 'done', 'failed')
        self.assertEqual((job['ok'], job['http_status'], job['near_tx'], job['error']),
                         (False, 200, '9' * 44, 'tx near-rpc: timeout'))

    def test_unknown_job(self):
        self.start(FakeChain())
        for path in ('/job/0123456789abcdef', '/job/nope', '/job/', '/job/0123456789ABCDEF'):
            code, out = self.get(path)
            self.assertEqual((code, out), (404, {'ok': False, 'error': 'no such job'}), path)

    def test_bounded_map(self):
        jobs = R.Jobs(limit=3)
        ids = [jobs.new('owner_call', vault=VAULT) for _ in range(5)]
        self.assertEqual([jobs.get(i) is None for i in ids], [True, True, False, False, False])
        jobs.update(ids[0], 'done', ok=True)                 # a dropped job's thread updates nothing
        jobs.update(ids[4], 'failed', ok=False, error='x')
        self.assertEqual(jobs.counts(), {'kept': 3, 'max': 3, 'running': 2, 'stages': {'accepted': 2, 'failed': 1}})
        self.assertEqual(R.JOBS_MAX, 1000)

    def test_wait_true_is_synchronous(self):
        c = FakeChain()
        self.start(c)
        code, out = self.post_owner('setup', setup_args(part(LIVE)), wait=True)
        self.assertEqual((code, out['ok'], len(out['receipts'])), (200, True, 3))
        self.assertNotIn('job', out)
        c.authorized.add((BASE, EXEC))
        code, out = self.post_owner('setup', setup_args(part(LIVE)), wait=True)
        self.assertEqual((code, out['ok']), (400, False))     # the 3 Nov status codes
        code, out = self.post_owner('setup', setup_args(part(LIVE)), wait='yes')
        self.assertEqual((code, out['error']), (400, 'wait: expected true or false'))
        code, out = self.post_owner('revoke_agent', {}, wait=False)
        self.assertEqual(code, 202)
        self.assertEqual(self.get('/fund-agent', {'implicit_account': AGENT, 'wait': True})[0], 400)   # fund-agent stays synchronous
        self.assertEqual(self.app.jobs.counts()['kept'], 1)

    def test_dry_run_job(self):
        self.start(FakeChain(dry_run=True, allow_reads=False))
        code, out = self.post_owner('revoke_agent', {})
        code, job = self.wait_stage(out['job'], 'done', 'failed')
        self.assertEqual((job['stage'], job['dry_run'], len(job['reads_skipped'])), ('done', True, 1))
        code, out = self.get('/create-vault', CREATE_REQ)
        code, job = self.wait_stage(out['job'], 'done', 'failed')
        self.assertEqual((job['kind'], job['owner'], job['dry_run']), ('create_vault', OWNER, True))
        self.assertIn('create_vault json-args', job['near_command'])


class TestFloat(JobHttp):
    def test_float_sent(self):
        c = FakeChain()
        c.tx_result, c.pending_nonce = CREATED, 3
        wal = FakeWallet()
        self.start(c, wallet=wal, eth_float=FLOAT)
        code, out = self.get('/create-vault', CREATE_REQ)
        self.assertEqual(code, 202)
        code, job = self.wait_stage(out['job'], 'done', 'failed')
        self.assertEqual((job['ok'], job['stage'], job['vault'], job['base_address']), (True, 'done', 'v2.factory.testnet', BASE))
        self.assertEqual(list(job['times']), ['accepted', 'vault_created', 'float_sent', 'done'])
        fl = job['float']
        self.assertEqual((fl['from'], fl['to'], fl['value_wei'], fl['tx'], fl['nonce']),
                         (wal.address, BASE, str(FLOAT), '0x%064x' % 3, 3))
        self.assertEqual(fl['receipt']['status'], 1)
        self.assertEqual(wal.signed, [{'to': BASE, 'value': FLOAT, 'nonce': 3, 'max_fee': 2 * 10 ** 6 + 10 ** 3, 'priority': 10 ** 3}])
        self.assertEqual([raw_nonce(r) for r in c.sent_raws], [3])
        self.assertEqual(self.app.state.data['counters']['eth_float'], 1)
        code, out = self.get('/create-vault', dict(CREATE_REQ, wait=True))     # synchronous: the same fields
        self.assertEqual((code, out['ok'], out['float']['receipt']['status']), (200, True, 1))
        code, health = self.get('/health')
        self.assertEqual(health['eth_float_wei'], str(FLOAT))
        self.assertEqual(health['wallet'], {'address': wal.address, 'balance_wei': str(10 ** 17), 'balance': '0.1 ETH',
                                            'floats_left': 20})

    def test_float_disabled(self):
        c = FakeChain()
        c.tx_result = CREATED
        self.start(c)
        code, out = self.get('/create-vault', CREATE_REQ)
        code, job = self.wait_stage(out['job'], 'done', 'failed')
        self.assertEqual((job['stage'], list(job['times'])), ('done', ['accepted', 'vault_created', 'done']))
        self.assertNotIn('float', job)
        self.assertEqual(c.sent_raws, [])
        code, health = self.get('/health')
        self.assertEqual((health['eth_float_wei'], health['wallet']), ('0', None))
        with self.assertRaises(R.Refused):
            R.App(c, self.app.state, 'relayer.testnet', 'factory.testnet', eth_float=FLOAT)    # a float needs a wallet

    def test_float_failure_keeps_the_vault(self):
        c = FakeChain()
        c.tx_result = CREATED
        self.start(c, wallet=FakeWallet(fail='insufficient funds'), eth_float=FLOAT)
        code, out = self.get('/create-vault', CREATE_REQ)
        code, job = self.wait_stage(out['job'], 'done', 'failed')
        self.assertEqual((job['ok'], job['stage'], job['http_status'], job['vault'], job['base_address']),
                         (False, 'failed', 200, 'v2.factory.testnet', BASE))
        self.assertEqual(list(job['times']), ['accepted', 'vault_created', 'failed'])
        self.assertIn('ETH float of 0.005 ETH to %s failed: insufficient funds; the vault exists' % BASE, job['error'])
        c.statuses = [0]                                      # the float transaction reverts
        self.app.wallet = FakeWallet()
        code, out = self.get('/create-vault', dict(CREATE_REQ, wait=True))
        self.assertEqual((code, out['ok'], out['vault'], out['float']['tx']), (200, False, 'v2.factory.testnet', '0x%064x' % 7))
        self.assertIn('reverted; the vault exists', out['error'])

    def test_wallet_lock(self):
        d = os.path.join(tempfile.mkdtemp(), 'lock')
        self.addCleanup(shutil.rmtree, os.path.dirname(d))
        with R.wallet_lock(d):                               # a timer run holds the lock
            with self.assertRaisesRegex(R.ChainError, 'still held'):
                with R.wallet_lock(d, wait=0.2):
                    pass
        with R.wallet_lock(d, wait=0.2):                     # released
            pass

    def test_insufficient_wallet_refuses_to_start(self):
        c = FakeChain()
        wal = FakeWallet()
        c.eth_balance = 20 * FLOAT - 1
        with self.assertRaises(SystemExit) as e:
            R.check_wallet(c, wal, FLOAT)
        self.assertEqual(e.exception.code, 2)                # relayer.sh does not restart on 2
        c.eth_balance = 20 * FLOAT
        R.check_wallet(c, wal, FLOAT)

    def test_flags(self):
        a = R.parser().parse_args(['serve', '--sender', 'r.testnet', '--factory', 'f.testnet'])
        self.assertEqual((a.eth_float, a.eth_key_file, a.cors_origin), (FLOAT, R.EOA_KEY_DEFAULT, []))
        a = R.parser().parse_args(['serve', '--sender', 'r.testnet', '--factory', 'f.testnet', '--eth-float', '0',
                                   '--cors-origin', 'https://app.example.com/', '--cors-origin', 'http://localhost:5173'])
        self.assertEqual((a.eth_float, a.cors_origin), (0, ['https://app.example.com', 'http://localhost:5173']))
        for bad in ('-1', 'x', '0.0000000000000000001'):
            with self.assertRaises(R.argparse.ArgumentTypeError):
                R.parse_eth(bad)
        with self.assertRaises(R.argparse.ArgumentTypeError):
            R.parse_origin('https://app.example.com/path')
        self.assertEqual(R.fmt_eth(FLOAT), '0.005 ETH')

    @unittest.skipUnless(os.path.exists(R.CAST), 'cast not installed')
    def test_wallet_signs_offline(self):
        # a fresh throwaway key from `cast wallet new` (the EOA key file has the same shape); never funded
        path = os.path.join(tempfile.mkdtemp(), 'k.json')
        self.addCleanup(shutil.rmtree, os.path.dirname(path))
        d = json.loads(subprocess.run([R.CAST, 'wallet', 'new', '--json'], capture_output=True, text=True, check=True).stdout)
        key, addr = d['data'][0]['private_key'], d['data'][0]['address']
        with open(path, 'w') as f:
            json.dump(d, f)
        wal = R.Wallet(path)
        self.assertEqual(wal.address, addr.lower())
        self.assertNotIn(key[2:], repr(wal))
        tx = wal.sign_transfer(BASE, FLOAT, 3, 2 * 10 ** 6, 10 ** 3)
        self.assertEqual((R.raw_chain_id(tx['raw']), raw_nonce(tx['raw'])), (84532, 3))
        self.assertEqual(tx['hash'], R.keccak(tx['raw']))
        with self.assertRaises(R.ChainError) as e:            # a cast failure never carries the key
            wal.sign_transfer(BASE, 'not-a-number', 3, 2 * 10 ** 6, 10 ** 3)
        self.assertNotIn(key[2:], str(e.exception))
        with open(path, 'w') as f:
            json.dump({'data': {'private_key': key, 'address': BASE}}, f)
        with self.assertRaisesRegex(R.Refused, 'not the address of its key'):
            R.Wallet(path)


class TestCors(JobHttp):
    ORIGIN = 'https://app.example.com'

    def test_preflight_and_headers(self):
        self.start(FakeChain(), cors_origins=[self.ORIGIN])
        code, h, body = self.request('OPTIONS', '/owner-call', {'Origin': self.ORIGIN, 'Access-Control-Request-Method': 'POST',
                                                               'Access-Control-Request-Headers': 'content-type'})
        self.assertEqual((code, body), (204, b''))
        self.assertEqual((h['Access-Control-Allow-Origin'], h['Access-Control-Allow-Methods'], h['Access-Control-Allow-Headers'],
                          h['Vary']), (self.ORIGIN, 'GET, POST, OPTIONS', 'content-type', 'Origin'))
        code, h, _ = self.request('GET', '/health', {'Origin': self.ORIGIN})
        self.assertEqual((code, h['Access-Control-Allow-Origin']), (200, self.ORIGIN))
        req = urllib.request.Request(self.url + '/owner-call', json.dumps(
            {'vault': VAULT, 'message': message('revoke_agent', {}), 'signature': SIG}).encode(),
            {'Content-Type': 'application/json', 'Origin': self.ORIGIN})
        with urllib.request.urlopen(req, timeout=10) as r:
            self.assertEqual((r.status, r.headers['Access-Control-Allow-Origin']), (202, self.ORIGIN))
        with self.assertRaises(urllib.error.HTTPError) as e:   # an error answer carries it too
            self.request('GET', '/job/0123456789abcdef', {'Origin': self.ORIGIN})
        self.assertEqual((e.exception.code, e.exception.headers['Access-Control-Allow-Origin']), (404, self.ORIGIN))
        e.exception.close()
        code, h, _ = self.request('OPTIONS', '/owner-call', {'Origin': 'https://evil.example.com'})
        self.assertEqual((code, h['Access-Control-Allow-Origin'], h['Vary']), (204, None, 'Origin'))
        code, h, _ = self.request('GET', '/health', {'Origin': 'https://evil.example.com'})
        self.assertIsNone(h['Access-Control-Allow-Origin'])

    def test_off_by_default(self):
        self.start(FakeChain())
        code, h, _ = self.request('OPTIONS', '/owner-call', {'Origin': self.ORIGIN, 'Access-Control-Request-Method': 'POST'})
        self.assertEqual((code, h['Allow'], h['Access-Control-Allow-Origin'], h['Vary']), (204, 'GET, POST, OPTIONS', None, None))
        code, h, _ = self.request('GET', '/health', {'Origin': self.ORIGIN})
        self.assertEqual((code, h['Access-Control-Allow-Origin']), (200, None))


if __name__ == '__main__':
    unittest.main()
