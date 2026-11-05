#!/usr/bin/env python3 -I
"""Unit tests for the pure parts of scripts/scene.py (no network, no key): step plans, the keeper amount, the dry-run
projection, the outcome row and net value, event and liquidator-output decoding, the Markdown tables.

  python3 -I scripts/test_scene.py
"""
import argparse, json, os, subprocess, sys, tempfile, unittest
from unittest import mock
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import scene as S   # noqa: E402

WAD = 10 ** 18
START = 2335.0
DEMO_LLTV = 86 * 10 ** 16


def w(x): return int(round(x * 1e6)) * 10 ** 12


def corridor(target=0.50, act=0.70, floor=0.30, budget=0.20):
    return {'target': w(target), 'act': w(act), 'floor': w(floor), 'sell_budget': w(budget), 'migrate_allowed': True, 'exit_allowed': True, 'set': True}


def moves(rows): return [r for r in rows if r['kind'] != 'hold']


class Plan(unittest.TestCase):
    def check_steps(self, rows):
        prev = START
        for r in rows:
            self.assertGreater(r['price'], 0)
            if r['kind'] == 'move': self.assertTrue(S.fits(prev, r['price'], S.rp.DEMO_STEP), r)
            prev = r['price']

    def test_gradual_crash(self):
        rows = S.plan_scene('gradual-crash', START)
        self.assertEqual(len(moves(rows)), 32)                       # the plan's 32 steps of about -1.85 %
        self.assertEqual([r['kind'] for r in rows][-1], 'hold')
        self.assertEqual(rows[31]['price'], round(START * 0.55, 2))  # -45 %
        self.assertTrue(all(b['price'] < a['price'] for a, b in zip(rows[:31], rows[1:32])))
        self.assertAlmostEqual(rows[0]['price'] / START - 1, -0.0185, places=3)
        self.check_steps(rows)

    def test_gap_has_one_large_move(self):
        rows = S.plan_scene('gap', START)
        self.assertEqual([r['kind'] for r in rows], ['move'] * 15 + ['hold', 'gap', 'hold'])
        self.assertEqual(rows[14]['price'], round(START * 0.75, 2))
        g = rows[16]
        self.assertEqual((g['price'], g['max_step']), (round(START * 0.55, 2), 1.0))
        big = [r for p, r in zip([START] + [x['price'] for x in rows], rows) if abs(r['price'] / p - 1) > 0.019]
        self.assertEqual(big, [g])                                    # the only move above 1.9 %
        self.assertAlmostEqual(g['price'] / rows[15]['price'] - 1, 0.55 / 0.75 - 1, places=4)
        self.assertFalse(S.fits(rows[15]['price'], g['price'], S.rp.DEMO_STEP))
        self.assertTrue(S.fits(rows[15]['price'], g['price'], g['max_step']))
        self.check_steps(rows)

    def test_whipsaw_down_and_back(self):
        rows = S.plan_scene('whipsaw', START)
        kinds = [r['kind'] for r in rows]
        self.assertEqual(kinds, ['move'] * 21 + ['hold'] + ['move'] * 21 + ['hold'])
        self.assertEqual(min(r['price'] for r in rows), round(START * 0.68, 2))
        self.assertEqual(rows[-1]['price'], START)
        self.check_steps(rows)

    def test_waits_and_numbering(self):
        rows = S.plan_scene('gap', START, step_wait=180, hold=600)
        self.assertEqual([r['step'] for r in rows], list(range(1, 19)))
        self.assertEqual({r['wait'] for r in rows if r['kind'] == 'hold'}, {600})
        self.assertEqual({r['wait'] for r in rows if r['kind'] != 'hold'}, {180})

    def test_low_start_price_stays_positive(self):
        for scene in S.SCENES:
            rows = S.plan_scene(scene, 50.0)
            self.assertTrue(all(r['price'] > 0 for r in rows))
        self.assertEqual(S.steps_to(START, START, 0.019), [])

    def test_crossings_match_the_plan(self):
        # act line crossed at -28.6 %, LLTV at -41.9 % (tasks/scenes-plan.md, gradual crash)
        rows = S.plan_scene('gradual-crash', START)
        c = S.crossings(rows, START, 0.5, (0.70, 0.86))
        self.assertLessEqual(rows[c[0.70] - 1]['price'], START * 0.5 / 0.70)
        self.assertGreater(rows[c[0.70] - 2]['price'], START * 0.5 / 0.70)
        self.assertEqual((c[0.70], c[0.86]), (19, 30))
        self.assertEqual(S.crossings(S.plan_scene('whipsaw', START), START, 0.5, (0.86,))[0.86], None)


class Keeper(unittest.TestCase):
    """keeper_decision mirrors agent/src/logic.rs `protective` and the protective branch of `decide_market`
    (`reduce(amount_to(m, target))` clamped to the slice maximum and the debt); amount_to = CorridorMath.repayToLtv."""

    def test_logic_rs_case_b(self):
        # logic.rs every_market_with_debt_is_evaluated...: b = 1,200 USDC on 1 WETH at 2,000, corridor 40/55/30
        amt, why = S.keeper_decision(1200 * 10 ** 6, 2000 * 10 ** 6, corridor(0.40, 0.55), 0, 1)
        self.assertEqual(amt, (1200 * 10 ** 6 * WAD - w(0.40) * 2000 * 10 ** 6) // (WAD - w(0.40)))
        self.assertEqual(amt, 666_666_666)
        self.assertIn('above act', why)

    def test_logic_rs_case_a_below_act(self):
        self.assertEqual(S.keeper_decision(1300 * 10 ** 6, 2000 * 10 ** 6, corridor(), 0, 1)[0], 0)   # LTV 0.65 < act 0.70

    def test_exactly_at_act_is_not_above(self):
        self.assertEqual(S.keeper_decision(700 * 10 ** 6, 1000 * 10 ** 6, corridor(), 0, 1)[0], 0)

    def test_session_continues_below_act_until_target(self):
        # logic.rs protective_session_continues_below_act_until_target: LTV 0.60, session 100 s old; lapsed after 24 h
        T = 1_800_000_000
        amt, why = S.keeper_decision(1200 * 10 ** 6, 2000 * 10 ** 6, corridor(), T - 100, T)
        self.assertEqual(amt, (1200 * 10 ** 6 * WAD - w(0.5) * 2000 * 10 ** 6) // (WAD - w(0.5)))
        self.assertIn('session', why)
        self.assertEqual(S.keeper_decision(1200 * 10 ** 6, 2000 * 10 ** 6, corridor(), T - S.SESSION_SECS - 1, T)[0], 0)
        # within TARGET_TOLERANCE of target: the session does not sell
        self.assertEqual(S.keeper_decision(5005 * 10 ** 5, 1000 * 10 ** 6, corridor(), T - 100, T)[0], 0)

    def test_gap_step_amount_and_ltv_after(self):
        # the scene's twin after the gap: 2.334927 dUSDC on 0.002 WETH at 1,284.25 (LTV 90.9 %)
        debt, value = 2_334_927, S.lq.mul_div_down(2 * 10 ** 15, S.oracle_int(1284.25), S.ORACLE_SCALE)
        amt, _ = S.keeper_decision(debt, value, corridor(), 0, 1)
        self.assertEqual(amt, 2 * debt - value)                     # (D - V/2) / (1/2)
        self.assertGreaterEqual(S.ltv_wad(debt - amt, value - amt), w(0.5))
        self.assertLess(S.ltv_wad(debt - amt, value - amt), w(0.5) + S.TARGET_TOLERANCE)

    def test_clamps(self):
        self.assertEqual(S.keeper_decision(10 ** 15, 10 ** 15, corridor(), 0, 1)[0], S.SLICE_MAX)   # slice maximum
        self.assertEqual(S.keeper_decision(5, 0, corridor(), 0, 1)[0], 0)                            # collateral worth 0
        self.assertEqual(S.keeper_decision(0, 10, corridor(), 0, 1)[0], 0)                           # no debt
        self.assertEqual(S.keeper_decision(900, 1000, dict(corridor(), set=False), 0, 1)[0], 0)      # no corridor
        self.assertEqual(S.repay_to_ltv(1000, 1000, w(0.5)), 1000)                                   # capped at the debt


class Math(unittest.TestCase):
    def test_numbers_and_liquidation_line(self):
        x = S.numbers(2_334_927_000_000, 2 * 10 ** 15, 10 ** 9, 10 ** 15, S.oracle_int(2334.93), DEMO_LLTV)
        self.assertEqual(x['value'], 4_669_860)
        self.assertFalse(x['liquidatable'])
        # LTV 0.5 / (1 - x) past 0.86 below -41.86 %
        self.assertTrue(S.numbers(10 ** 15, 2 * 10 ** 15, 2_334_927, 10 ** 15 - 10 ** 6, S.oracle_int(1357.0), DEMO_LLTV)['liquidatable'])
        self.assertFalse(S.numbers(10 ** 15, 2 * 10 ** 15, 2_334_927, 10 ** 15 - 10 ** 6, S.oracle_int(1360.0), DEMO_LLTV)['liquidatable'])

    def test_ltv_rounding(self):
        self.assertEqual(S.ltv_wad(0, 0), 0)
        self.assertIsNone(S.ltv_wad(1, 0))
        self.assertEqual(S.ltv_wad(1, 3), (WAD + 2) // 3)

    def test_lif(self):
        self.assertEqual(round(S.lif_of(DEMO_LLTV) / 1e18, 4), 1.0438)

    def test_accrue(self):
        self.assertEqual(S.accrue(10 ** 6, 0, 100), 10 ** 6)
        self.assertGreater(S.accrue(10 ** 12, 10 ** 9, 3600), 10 ** 12)


class Projection(unittest.TestCase):
    def state(self):
        return {n: {'coll': 2 * 10 ** 15, 'debt': 2_334_927} for n in ('keeper', 'unprotected')}

    def events(self, pr, name, kind): return [r['step'] for r in pr if any(e[0] == kind for e in r['twins'][name][1])]

    def test_gradual_crash(self):
        rows = S.plan_scene('gradual-crash', 2334.93)
        pr = S.project(self.state(), rows, corridor(), DEMO_LLTV)
        self.assertEqual(self.events(pr, 'unprotected', 'liquidation'), [30])
        self.assertEqual(self.events(pr, 'keeper', 'liquidation'), [])
        self.assertEqual(self.events(pr, 'keeper', 'reduce')[0], 19)
        self.assertEqual(pr[-1]['twins']['unprotected'][0], 0)

    def test_gap(self):
        rows = S.plan_scene('gap', 2334.93)
        pr = S.project(self.state(), rows, corridor(), DEMO_LLTV)
        self.assertEqual(self.events(pr, 'unprotected', 'liquidation'), [17])
        # on the gap row the liquidator runs first, in the block after setPrice: the keeper is liquidated, never reduces
        self.assertEqual(self.events(pr, 'keeper', 'liquidation'), [17])
        self.assertEqual(self.events(pr, 'keeper', 'reduce'), [])
        # gradual steps keep the order keeper, then liquidator (the keeper sells first and is not liquidated)
        self.assertEqual(self.events(S.project(self.state(), S.plan_scene('gradual-crash', 2334.93), corridor(), DEMO_LLTV), 'keeper', 'liquidation'), [])

    def test_whipsaw_no_liquidation(self):
        pr = S.project(self.state(), S.plan_scene('whipsaw', 2334.93), corridor(), DEMO_LLTV)
        self.assertEqual(self.events(pr, 'unprotected', 'liquidation'), [])
        self.assertEqual(self.events(pr, 'keeper', 'liquidation'), [])


class Outcome(unittest.TestCase):
    def test_kept_position(self):
        o = S.outcome_row(2 * 10 ** 15, 2_334_927, 2_334_927, S.oracle_int(1284.25), False)
        self.assertEqual(o, {'collateral_kept_wei': '2000000000000000', 'debt_units': '2334927', 'cash_units': '2334927',
                             'net_value_usd_at_end': 2.5685, 'liquidated': False})

    def test_liquidated_position(self):
        # 0.002 WETH; liquidator seized 0.001897893 WETH and repaid all debt; the borrowed cash stays
        coll = 2 * 10 ** 15 - 1_897_893_000_000_000
        o = S.outcome_row(coll, 0, 2_334_927, S.oracle_int(1284.25), True)
        self.assertEqual(o['collateral_kept_wei'], str(coll))
        self.assertAlmostEqual(o['net_value_usd_at_end'], (coll * 1284.25 / 1e18) + 2.334927, places=5)
        self.assertTrue(o['liquidated'])

    def test_debt_above_value(self):
        o = S.outcome_row(10 ** 15, 3_000_000, 0, S.oracle_int(2000.0), False)
        self.assertEqual(o['net_value_usd_at_end'], -1.0)


REDUCED = {'address': '0xa9bbabe6d75c5ba96ca095586284774c9d09d18b',
           'topics': ['0x04fa3a3c6cc1ec1dc4efb8381c2d7e0d0c847d25575a7a0028650d21897d72bf',
                      '0x0000000000000000000000000b7ed66f46fe83252a208563a88201ba60490d25',
                      '0x55872b53dba6147b6badf9d4277bc166dfb7d5f8ac8d491da093960dbef6d585'],
           'data': '0x' + ''.join(format(x, '064x') for x in (231158, 0x5a0d105eb8a3, 0x063eb834f12a6323, 0x05d8f81b5b4db6d6, 0)),
           'blockNumber': '0x3437644', 'transactionHash': '0xf4e862d481805ff374f9bc4904bae636027510b6c6c82013654415d3c2041292', 'logIndex': '0x26'}
LIQ = {'topics': ['0xa4946ede45d0c6f06a0f5ce92c9ad3b4751452d2fe0e25010783bcab57a67e41',
                  '0x55872b53dba6147b6badf9d4277bc166dfb7d5f8ac8d491da093960dbef6d585',
                  '0x0000000000000000000000000b7ed66f46fe83252a208563a88201ba60490d25',
                  '0x000000000000000000000000fc85641356fe56b8851f715c3c81ad2b511a67cf'],
       'data': '0x' + ''.join(format(x, '064x') for x in (2_334_927, 2_334_927_000_000, 1_897_893_000_000_000, 0, 0)),
       'blockNumber': '0x3437700', 'transactionHash': '0x' + 'ab' * 32, 'logIndex': '0x3'}
W = '0x0B7Ed66f46fe83252a208563A88201BA60490d25'


class Events(unittest.TestCase):
    def test_decode_and_actions(self):
        r = S.decode_reduced(REDUCED); l = S.decode_liquidate(LIQ)
        self.assertEqual((r['repaid'], r['sold'], r['protective'], r['block']), (231158, 0x5a0d105eb8a3, False, 0x3437644))
        self.assertEqual((l['borrower'], l['seized']), ('0xfc85641356fe56b8851f715c3c81ad2b511a67cf', 1_897_893_000_000_000))
        k = S.actions_of('keeper', W, [r], [l], W)
        self.assertEqual(k, [{'block': 0x3437644, 'kind': 'reduce', 'tx': REDUCED['transactionHash'], 'repaid_units': '231158',
                              'sold_wei': str(0x5a0d105eb8a3), 'by': 'keeper', 'protective': False}])
        u = S.actions_of('unprotected', '0xfc85641356fe56b8851f715c3c81ad2b511a67cf', [r], [l], W)
        self.assertEqual([(a['kind'], a['by'], a['repaid_units']) for a in u], [('liquidation', 'liquidator', '2334927')])
        self.assertEqual(S.actions_of('ai-agent', '0xb3ccd0ad7e5fca9d4c18cf59d19fab4703eee6fb', [r], [l], W), [])

    def test_parse_liquidator(self):
        out = ('liquidatable: ...\n  approve Morpho: tx 0x%s status 1 gas 1\n  Morpho.liquidate: tx 0x%s status 1 gas 2\n'
               'done: ...\ntransactions: 0x%s 0x%s 0x%s\n' % ('1' * 64, '2' * 64, '1' * 64, '2' * 64, '3' * 64))
        self.assertEqual(S.parse_liquidator(out), ('0x' + '2' * 64, ['0x' + '1' * 64, '0x' + '2' * 64, '0x' + '3' * 64]))
        self.assertEqual(S.parse_liquidator('healthy (max borrow ...): Morpho would refuse; nothing sent\n'), (None, []))


class Markdown(unittest.TestCase):
    def rec(self, complete=True):
        tw = {'ai-agent': '0xb3ccd0ad7e5fca9d4c18cf59d19fab4703eee6fb', 'keeper': W, 'unprotected': '0xfc85641356fe56b8851f715c3c81ad2b511a67cf'}
        o = {'block': 100, 'utc': '2026-11-06 10:00:00 UTC', 'price': S.oracle_int(2334.93)}
        rec = S.new_record('gap', tw, o, W)
        for b, price in ((100, 2334.93), (110, 1284.21)):
            for n in S.TWINS:
                rec['twins'][n]['ltv'].append({'block': b, 'ltv': 0.5 if b == 100 else 0.909091, 'collateral_wei': '2000000000000000',
                                               'debt_units': '2334927', 'price_usd': price})
        rec['price_path'] = [{'step': 0, 'block': 100, 'utc': 'a', 'price_usd': 2334.93, 'tx': []},
                             {'step': 17, 'block': 110, 'utc': 'b', 'price_usd': 1284.21, 'tx': ['0x' + 'cd' * 32]}]
        rec['twins']['unprotected']['actions'] = S.actions_of('unprotected', tw['unprotected'], [], [S.decode_liquidate(LIQ)], W)
        if complete:
            for n in S.TWINS: rec['twins'][n]['outcome'] = S.outcome_row(2 * 10 ** 15, 2_334_927, 2_334_927, S.oracle_int(1284.21), n == 'unprotected')
        rec['notes'] = 'first note\nsecond note'
        return rec

    def test_tables(self):
        md = S.render_md(self.rec())
        lines = md.split('\n')
        for h in ('## Price path', '## LTV (block x twin)', '## Outcome', '## Transactions', '## Notes'): self.assertIn(h, lines)
        self.assertIn('| block | oracle price (USD) | ai-agent | keeper | unprotected |', lines)
        self.assertIn('| 110 | 1284.21 | 90.91 % | 90.91 % | 90.91 % |', lines)
        self.assertIn('| 17 | 110 | b | 1284.21 | -45.00 % | 1 |', lines)
        self.assertIn('| unprotected | 0.002000000 | 2.334927 | 2.334927 | 2.568420 | yes |', lines)
        self.assertIn('- step 17 price move: `0x%s`' % ('cd' * 32), lines)
        self.assertIn('- second note', lines)
        self.assertNotIn('Incomplete', md)
        self.assertNotRegex(md.lower(), r'\bbetter\b|\bworse\b|\bwins?\b')     # app-screens A2: no "better" wording
        # every row of a table has the header's column count
        for i, l in enumerate(lines):
            if l.startswith('|---') or l.startswith('|--'):
                n = lines[i - 1].count('|')
                j = i + 1
                while j < len(lines) and lines[j].startswith('|'):
                    self.assertEqual(lines[j].count('|'), n, lines[j]); j += 1

    def test_incomplete(self):
        md = S.render_md(self.rec(complete=False))
        self.assertIn('**Incomplete:**', md)
        self.assertIn('| keeper | | | | | |', md.split('\n'))


class FakeChain:
    """Stands in for every chain call of scene.run: the oracle, three positions, the keeper's reduce, the liquidator."""
    def __init__(self):
        self.price = S.oracle_int(2334.93); self.block = 100; self.fail_at = None; self.calls = 0; self.acts = {n: [] for n in S.TWINS}
        self.log = []                                                   # the order of chain calls
        self.pos = {n: {'coll': 2 * 10 ** 15, 'debt': 2_334_927, 'cash': 2_334_927} for n in S.TWINS}

    def observe(self, tw):
        self.calls += 1; self.log.append(('observe',))
        if self.fail_at and self.fail_at[0] <= self.calls < self.fail_at[0] + self.fail_at[1]:
            raise subprocess.CalledProcessError(1, 'cast', stderr='rpc down')
        self.block += 1
        o = {'block': self.block, 'ts': 1_800_000_000 + self.block, 'utc': 'u%d' % self.block, 'price': self.price, 'twins': {}, 'session': 0}
        for n in tw:
            p = self.pos[n]; v = S.lq.mul_div_down(p['coll'], self.price, S.ORACLE_SCALE)
            o['twins'][n] = {'debt': p['debt'], 'value': v, 'ltv': S.ltv_wad(p['debt'], v), 'coll': p['coll'], 'shares': p['debt'], 'cash': p['cash'],
                             'liquidatable': p['debt'] > S.lq.w_mul_down(v, DEMO_LLTV)}
        return o

    def move(self, usd, max_step, dry):
        self.log.append(('move', usd)); self.price = S.oracle_int(usd); h = '0x%064x' % (len(S.rp.COSTS['txs']) + 1); S.rp.COSTS['txs'].append(h); return [h]

    def keeper(self, tw, corr, dry, note):
        p = self.pos['keeper']; v = S.lq.mul_div_down(p['coll'], self.price, S.ORACLE_SCALE)
        self.log.append(('keeper',))
        amt, _ = S.keeper_decision(p['debt'], v, corr, 0, 1)
        if not amt: return None
        sold = S.lq.mul_div_up(amt, S.ORACLE_SCALE, self.price); p['coll'] -= sold; p['debt'] -= amt
        self.acts['keeper'].append({'block': self.block, 'kind': 'reduce', 'tx': '0xk%d' % self.block, 'repaid_units': str(amt), 'sold_wei': str(sold), 'by': 'keeper'})
        return {'transactionHash': '0xk'}

    def liquidate(self, addr, dry, note):
        n = [k for k, a in TW.items() if a == addr][0]; p = self.pos[n]; self.log.append(('liquidate', n))
        if p['debt'] <= S.lq.w_mul_down(S.lq.mul_div_down(p['coll'], self.price, S.ORACLE_SCALE), DEMO_LLTV): return (None, [])   # healthy, as liquidate.py
        seize = min(p['coll'], S.lq.mul_div_down(S.lq.w_mul_down(p['debt'], S.lif_of(DEMO_LLTV)), S.ORACLE_SCALE, self.price))
        h = '0x%064x' % (0x1000 + len(self.log))
        self.acts[n].append({'block': self.block, 'kind': 'liquidation', 'tx': h, 'repaid_units': str(p['debt']), 'sold_wei': str(seize), 'by': 'liquidator'})
        p['coll'] -= seize; p['debt'] = 0
        return (h, [h])


def load(path):
    with open(path) as f: return json.load(f)


TW = {'ai-agent': '0xb3ccd0ad7e5fca9d4c18cf59d19fab4703eee6fb', 'keeper': W, 'unprotected': '0xfc85641356fe56b8851f715c3c81ad2b511a67cf'}


class RunLoop(unittest.TestCase):
    """scene.run with every chain call replaced: a complete run, a failure after three retries, and --resume."""
    def run_scene(self, fake, out, resume=False):
        a = argparse.Namespace(cmd='run', scene='gap', dry_run=False, resume=resume, step_wait_secs=0, hold_secs=0, out=out,
                               ai=None, unprotected=TW['unprotected'], keeper=None)
        with mock.patch.multiple(S, check_chain=lambda: None, addresses=lambda a: (dict(TW), W), corridor_of=lambda addr, block='latest': corridor(),
                                 observe=fake.observe, move_price=fake.move, keeper_act=fake.keeper, liquidate=fake.liquidate,
                                 scan=lambda tw, frm, liq: {n: list(fake.acts[n]) for n in S.TWINS}, RETRY_PAUSE=0,
                                 market_params=lambda: {'loan': S.DEP['dusdc'], 'coll': S.DEP['weth'], 'oracle': S.DEP['mockOracle'],
                                                        'irm': S.DEP['irm'], 'lltv': DEMO_LLTV}), \
             mock.patch.object(S.rp, 'call', lambda to, sig, *x: [str(fake.price)]), \
             mock.patch.object(S.rp, 'lock', lambda: os.open(os.devnull, os.O_RDONLY)), \
             mock.patch.object(S.time, 'sleep', lambda s: None), mock.patch('builtins.print'):
            S.run(a)
        return load(os.path.join(out, 'gap.json'))

    def test_complete_run(self):
        with tempfile.TemporaryDirectory() as d:
            rec = self.run_scene(FakeChain(), d)
            self.assertEqual([s['step'] for s in rec['price_path']], list(range(19)))
            self.assertEqual(rec['price_path'][17]['price_usd'], 1284.21)
            self.assertEqual(len(rec['price_path'][17]['tx']), 1)          # the move's hash only, not the keeper's
            self.assertEqual(rec['price_path'][16]['tx'], [])              # a hold sends no price transaction
            out = {n: rec['twins'][n]['outcome'] for n in S.TWINS}
            self.assertTrue(out['unprotected']['liquidated'])
            self.assertTrue(out['keeper']['liquidated'])                    # the liquidator runs before the keeper on the gap row
            self.assertEqual(out['keeper']['cash_units'], '2334927')        # the keeper's cash: what its open borrowed
            self.assertEqual(out['keeper']['debt_units'], '0')
            self.assertTrue(out['ai-agent']['liquidated'])                    # the fake agent did nothing: liquidated at the gap
            self.assertEqual(rec['price_path'][17]['block'] - rec['price_path'][16]['block'], 2)   # observed again after the actions
            self.assertTrue(os.path.exists(os.path.join(d, 'gap.md')))
            with self.assertRaises(SystemExit) as e: self.run_scene(FakeChain(), d)   # evidence is not overwritten
            self.assertEqual(e.exception.code, 2)

    def test_gap_order(self):
        with tempfile.TemporaryDirectory() as d:
            f = FakeChain(); rec = self.run_scene(f, d)
            i = f.log.index(('move', 1284.21))
            # move, then at once a reading and the liquidator for every twin past the line (all three at 90.9 %),
            # then the normal sequence: reading, keeper (nothing left to sell), no second liquidation, reading
            self.assertEqual(f.log[i + 1:i + 6], [('observe',), ('liquidate', 'ai-agent'), ('liquidate', 'keeper'),
                                                  ('liquidate', 'unprotected'), ('observe',)])
            self.assertEqual(f.log[i + 6], ('keeper',))
            self.assertNotIn('liquidate', [x[0] for x in f.log[i + 6:]])
            for n in S.TWINS:
                liq = [x for x in rec['twins'][n]['actions'] if x['kind'] == 'liquidation']
                self.assertEqual(len(liq), 1)
                self.assertEqual((liq[0]['by'], liq[0]['note']), ('liquidator', S.IMMEDIATE))
                self.assertIn('%s liquidated immediately after the gap move, tx %s' % (n, liq[0]['tx']), rec['notes'])
            self.assertEqual([x['kind'] for x in rec['twins']['keeper']['actions']], ['liquidation'])
            # a move row keeps the order: move, reading after the wait, keeper, then the liquidator
            j = [x[0] for x in f.log].index('move')
            self.assertEqual(f.log[j + 1:j + 3], [('observe',), ('keeper',)])

    def test_failure_then_resume(self):
        with tempfile.TemporaryDirectory() as d:
            f = FakeChain(); f.fail_at = (6, 4)                             # the 6th read and its 3 retries fail
            with self.assertRaises(SystemExit) as e: self.run_scene(f, d)
            self.assertEqual(e.exception.code, 1)
            rec = load(os.path.join(d, 'gap.json'))
            done = rec['price_path'][-1]['step']
            self.assertIn('step %d failed' % (done + 1), rec['notes'])
            self.assertFalse(any(rec['twins'][n]['outcome'] for n in S.TWINS))
            with open(os.path.join(d, 'gap.md')) as fh: self.assertIn('**Incomplete:**', fh.read())
            f.fail_at = None
            rec = self.run_scene(f, d, resume=True)
            self.assertEqual([s['step'] for s in rec['price_path']], list(range(19)))
            self.assertIn('resumed at block', rec['notes'])
            self.assertTrue(rec['twins']['unprotected']['outcome']['liquidated'])
            with self.assertRaises(SystemExit) as e: self.run_scene(f, d, resume=True)   # complete: refused
            self.assertEqual(e.exception.code, 2)


if __name__ == '__main__':
    unittest.main(verbosity=2)
