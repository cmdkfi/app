#!/usr/bin/env python3 -I
"""Unit tests of relayer.py; no network (every chain call goes to FakeChain).

  cd /Users/vf/cmdk/app && python3 -I -m unittest scripts/test_relayer.py
  (or from scripts/: python3 -I -m unittest test_relayer.py)
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
        m = self.parse(message('setup', {'market': LIVE, 'authorize': True}))
        self.assertEqual(m['method'], 'setup')

    def test_refusals(self):
        bad = [message('setup', {'market': LIVE, 'authorize': True}, protocol='prime-vault/1'),
               message('setup', {'market': LIVE, 'authorize': True}, network='mainnet'),
               message('setup', {'market': LIVE, 'authorize': True}, vault='v2.factory.cmdk.testnet'),
               message('setup', {'market': LIVE, 'authorize': True}, nonce=4),
               message('setup', {'market': LIVE, 'authorize': True}, expiry=int(time.time()) + 5),
               message('nope', {}),
               message('setup', {'market': LIVE}),
               message('setup', {'market': LIVE, 'authorize': True, 'extra': 1}),
               message('setup', {'market': LIVE, 'authorize': 'true'}),
               message('repay', {'market': LIVE, 'amount': 5}),
               message('close', {'market': '0x1234'}),
               message('withdraw', {'token': USDC, 'amount': '1', 'destination': '0x12'}),
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
        self.assertEqual(self.count('setup', {'market': LIVE, 'authorize': True}), ['approve', 'approve', 'setAuthorization'])
        self.assertEqual(self.count('setup', {'market': LIVE, 'authorize': False}), ['approve', 'approve'])
        corridor = {'market': LIVE, 'target': '5', 'act': '7', 'floor': '3', 'sell_budget': '2',
                    'migrate_allowed': True, 'exit_allowed': True}
        self.assertEqual(self.count('set_corridor', corridor), ['setCorridor'])
        self.assertEqual(self.count('open', {'market': LIVE, 'collateral_in': '1', 'target_ltv': '5'}), ['open'])
        self.assertEqual(self.count('repay', {'market': LIVE, 'amount': '1'}), ['repay'])
        self.assertEqual(self.count('close', {'market': DEMO}), ['close'])
        self.assertEqual(self.count('withdraw', {'token': USDC, 'amount': '1', 'destination': OWNER}), ['transfer'])
        self.assertEqual(self.count('cancel', {}), ['cancel'])
        self.assertEqual(self.count('approve_measurement', {}), [])
        self.assertEqual(self.count('revoke_agent', {}), [])
        sweep = R.plan_txs('sweep_eth', {'amount': '7'}, vault_state())
        self.assertEqual((sweep[0]['kind'], sweep[0]['to'], sweep[0]['value']), ('sweep_eth', OWNER, 7))
        self.assertEqual(R.plan_txs('cancel', {}, vault_state())[0]['to'], BASE)

    def test_targets(self):
        p = R.plan_txs('setup', {'market': DEMO, 'authorize': True}, vault_state())
        self.assertEqual([x['to'] for x in p], [DUSDC, WETH, MORPHO])

    def test_refusals(self):
        st = vault_state()
        for method, args in (('setup', {'market': '0x' + '01' * 32, 'authorize': True}),
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
        self.check('setup', {'authorize': True}, {'authorized': False})
        self.check('setup', {'authorize': False}, {'authorized': True})
        with self.assertRaisesRegex(R.Refused, 'would revert'):
            self.check('setup', {'authorize': True}, {'authorized': True})
        with self.assertRaisesRegex(R.Refused, 'authorize true'):
            self.check('setup', {'authorize': False}, {'authorized': False})

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
        self.check('open', {'collateral_in': '10'}, {'authorized': True, 'collateral_allowance': 10})
        with self.assertRaises(R.Refused):
            self.check('open', {'collateral_in': '10'}, {'authorized': False, 'collateral_allowance': 10})
        with self.assertRaises(R.Refused):
            self.check('open', {'collateral_in': '10'}, {'authorized': True, 'collateral_allowance': 9})


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
        plan = R.plan_txs('setup', {'market': LIVE, 'authorize': True}, vault_state())
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
        return self.get('/owner-call', {'vault': VAULT, 'message': message(method, args), 'signature': SIG})

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
        code, out = self.owner_call('setup', {'market': LIVE, 'authorize': True})
        self.assertEqual((code, out['ok']), (400, False))
        self.assertIn('would revert', out['error'])
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

    def test_owner_call_broadcasts_in_nonce_order(self):
        c = FakeChain()
        self.start(c)
        code, out = self.owner_call('setup', {'market': LIVE, 'authorize': True})
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
        code, out = self.owner_call('setup', {'market': LIVE, 'authorize': True})
        self.assertEqual((out['ok'], len(out['receipts']), out['receipts'][1]['status']), (False, 2, 0))
        self.assertEqual(len(c.sent_raws), 2)
        self.assertEqual(out['held'], ['0x%064x' % 9])
        self.assertIn('reverted', out['error'])

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
        code, out = self.get('/create-vault', req)
        self.assertEqual((code, out['vault'], out['base_address']), (200, 'v1.factory.testnet', BASE))
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
        code, out = self.get('/create-vault', req)
        self.assertEqual((code, out['dry_run']), (200, True))
        self.assertIn("create_vault json-args '{", out['near_command'])
        self.assertEqual(self.get('/nope')[0], 404)
        self.assertEqual(self.get('/owner-call', b'not json')[0], 400)
        code, out = self.get('/fund-agent', {'implicit_account': AGENT})
        self.assertEqual((code, out['dry_run'], len(out['reads_skipped'])), (200, True, 1))


if __name__ == '__main__':
    unittest.main()
