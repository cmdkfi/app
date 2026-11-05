#!/usr/bin/env python3 -I
"""Unit tests for the pure parts of scripts/venue_stress.py (no network, no key): sizing, utilization and APR, the
agent's migrate fraction and LTV after a move, the before/after comparison, event decoding, the transcript diff and
summary, the Markdown.

  python3 -I scripts/test_venue_stress.py
"""
import hashlib, json, os, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import venue_stress as V   # noqa: E402

WAD = 10 ** 18
PRICE = 2270372012677815781656536631          # live oracle on 5 Nov (2270.37 USD per WETH, 1e36 scale)
V1 = '0xb3ccd0ad7e5fca9d4c18cf59d19fab4703eee6fb'
WALLET = '0x0B7Ed66f46fe83252a208563A88201BA60490d25'


def topic_addr(a): return '0x' + a[2:].lower().rjust(64, '0')
def word(x): return '%064x' % x


class Sizing(unittest.TestCase):
    def test_default_keep(self):
        s = V.sizing(13_000_148, 7_729_864, PRICE, 100_000, 0.5)
        self.assertEqual(s['free_liquidity'], 5_270_284)
        self.assertEqual(s['borrow'], 5_170_284)
        self.assertEqual(s['free_after'], 100_000)
        self.assertAlmostEqual(s['utilization_after'], (7_729_864 + 5_170_284) / 13_000_148)
        self.assertGreater(s['utilization_after'], 0.99)
        # collateral = borrow value / LTV, rounded up to the wei
        need = s['borrow'] * V.ORACLE_SCALE * WAD
        self.assertGreaterEqual(s['collateral'] * PRICE * s['collateral_ltv_wad'], need)
        self.assertLess((s['collateral'] - 1) * PRICE * s['collateral_ltv_wad'], need)
        self.assertEqual(s['collateral'], 4_554_569_886_458_256)      # the dry-run's figure at this price
        self.assertEqual(s['wallet_ltv'], 5 * 10 ** 17)              # CorridorMath ltv rounds up: exactly 50 % here

    def test_keep_edges(self):
        s = V.sizing(13_000_000, 7_000_000, PRICE, 6_000_000, 0.5)       # keep = all the free liquidity
        self.assertEqual((s['borrow'], s['collateral'], s['wallet_ltv']), (0, 0, 0))
        self.assertEqual(s['utilization_after'], s['utilization_before'])
        s = V.sizing(13_000_000, 7_000_000, PRICE, 6_000_001, 0.5)       # keep above it: negative, the run refuses
        self.assertEqual(s['borrow'], -1); self.assertEqual(s['collateral'], 0)
        s = V.sizing(13_000_000, 7_000_000, PRICE, 0, 0.5)               # keep 0: utilization 100 %
        self.assertEqual(s['utilization_after'], 1.0); self.assertEqual(s['free_after'], 0)

    def test_collateral_ltv(self):
        a = V.sizing(13_000_000, 7_000_000, PRICE, 0, 0.5)['collateral']
        b = V.sizing(13_000_000, 7_000_000, PRICE, 0, 0.25)['collateral']
        self.assertTrue(abs(b - 2 * a) <= 1)
        self.assertEqual(V.ltv_w(0.3), 3 * 10 ** 17)                   # no float noise in the WAD


class Rates(unittest.TestCase):
    def test_apr(self):
        self.assertAlmostEqual(V.apr(176_327_413), 176_327_413 * 31_536_000 / 1e18)
        self.assertAlmostEqual(V.apr(176_327_413), 0.005561, places=6)
        self.assertAlmostEqual(V.apr(WAD // 31_536_000), 1.0, places=6)
        self.assertEqual(V.apr(0), 0)

    def test_utilization(self):
        self.assertEqual(V.utilization(0, 0), 0.0)
        self.assertAlmostEqual(V.utilization(7_729_864, 13_000_148), 0.594598, places=6)
        self.assertEqual(V.utilization(5, 5), 1.0)


class AgentMirror(unittest.TestCase):
    def test_migrate_fraction(self):
        self.assertEqual(V.migrate_fraction(13_000_001, 7_729_864), WAD)
        self.assertEqual(V.migrate_fraction(7_807_162, 7_729_864), WAD)        # exactly debt + 1 %
        f = V.migrate_fraction(7_807_161, 7_729_864)
        self.assertLess(f, WAD); self.assertEqual(f, 7_807_161 * WAD // 7_729_864 * 99 // 100)
        self.assertEqual(V.migrate_fraction(1, 0), 0)

    def test_ltv_after_move(self):
        ltv = V.ltv_after_move(7_729_864, 8_335_601_888_990_126, 0, 0, PRICE)
        self.assertAlmostEqual(ltv / 1e18, 0.408449, places=6)
        self.assertLess(ltv, 7 * 10 ** 17)
        self.assertIsNone(V.ltv_after_move(1, 0, 0, 0, PRICE))                 # debt with no collateral


class Compare(unittest.TestCase):
    def pos(self, lc, ld, bc, bd): return {'live': {'coll': lc, 'debt': ld}, 'backup': {'coll': bc, 'debt': bd}}

    def test_identical_move(self):
        c = V.compare(self.pos(8_335_601_888_990_126, 7_729_900, 0, 0), self.pos(0, 0, 8_335_601_888_990_126, 7_729_900))
        self.assertTrue(c['collateral_identical']); self.assertTrue(c['debt_equal'])
        self.assertEqual((c['collateral_diff_wei'], c['debt_diff_units']), ('0', '0'))

    def test_one_unit_rounding(self):
        c = V.compare(self.pos(10, 7_729_900, 0, 0), self.pos(0, 0, 10, 7_729_901))
        self.assertTrue(c['debt_equal']); self.assertEqual(c['debt_diff_units'], '1')

    def test_difference_reported(self):
        c = V.compare(self.pos(10, 7_729_900, 0, 0), self.pos(0, 0, 9, 7_729_902))
        self.assertFalse(c['debt_equal']); self.assertFalse(c['collateral_identical'])
        self.assertEqual((c['collateral_diff_wei'], c['debt_diff_units']), ('-1', '2'))
        self.assertEqual((c['collateral_before_wei'], c['collateral_after_wei'], c['debt_before_units'], c['debt_after_units']),
                         ('10', '9', '7729900', '7729902'))

    def test_partial_move_sums_both_markets(self):
        c = V.compare(self.pos(100, 50, 0, 0), self.pos(1, 1, 99, 49))
        self.assertTrue(c['collateral_identical']); self.assertTrue(c['debt_equal'])


class Events(unittest.TestCase):
    def migrated_log(self):
        return {'address': V.EXECUTOR, 'blockNumber': hex(54_880_000), 'logIndex': '0x3', 'transactionHash': '0x' + 'ab' * 32,
                'topics': [V.MIGRATED_T0, topic_addr(V1), V.MARKETS['live'], V.MARKETS['backup']],
                'data': '0x' + word(WAD) + word(7_729_950) + word(8_335_601_888_990_126)}

    def test_decode_migrated(self):
        e = V.decode_migrated(self.migrated_log())
        self.assertEqual(e['caller'], V1)
        self.assertEqual((e['from'], e['to']), (V.MARKETS['live'], V.MARKETS['backup']))
        self.assertEqual((e['block'], e['log_index'], e['tx']), (54_880_000, 3, '0x' + 'ab' * 32))
        self.assertEqual((e['fraction'], e['debt_moved'], e['collateral_moved']), (WAD, 7_729_950, 8_335_601_888_990_126))

    def test_decode_exited(self):
        log = {'blockNumber': '0x10', 'logIndex': '0x0', 'transactionHash': '0x' + 'cd' * 32, 'topics': [V.EXITED_T0, topic_addr(V1), V.MARKETS['live']],
               'data': '0x' + word(7) + word(2) + word(3) + word(4)}
        e = V.decode_exited(log)
        self.assertEqual((e['market'], e['repaid'], e['from_idle'], e['sold'], e['collateral_out']), (V.MARKETS['live'], 7, 2, 3, 4))

    def test_actions_in_chain_order(self):
        mig = [V.decode_migrated(self.migrated_log())]
        red = [{'caller': V1, 'market': V.MARKETS['live'], 'block': 54_879_990, 'log_index': 1, 'tx': '0x01', 'repaid': 5, 'sold': 6,
                'ltv_before': 8 * 10 ** 17, 'ltv_after': 5 * 10 ** 17, 'protective': True},
               {'caller': '0x' + '11' * 20, 'market': V.MARKETS['live'], 'block': 1, 'log_index': 0, 'tx': '0x02', 'repaid': 1, 'sold': 1,
                'ltv_before': 0, 'ltv_after': 0, 'protective': False}]
        acts = V.actions_of(V1.upper().replace('0X', '0x'), mig, red, [])
        self.assertEqual([a['kind'] for a in acts], ['reduce', 'migrate'])
        self.assertTrue(all(a['by'] == 'agent' for a in acts))
        m = acts[1]
        self.assertEqual((m['from'], m['to'], m['fraction'], m['debt_moved_units'], m['collateral_moved_wei']),
                         ('live', 'backup', str(WAD), '7729950', '8335601888990126'))
        self.assertEqual((acts[0]['market'], acts[0]['ltv_before'], acts[0]['ltv_after']), ('live', 0.8, 0.5))


def transcript(loop, action='leave_market', market='live'):
    return {'vault': {'tag': 'v1'}, 'loop': loop, 'time_utc': '2026-11-06T10:00:%02dZ' % loop,
            'trigger': {'kind': 'judge_result', 'new_screens': [], 'judge_in_flight': False},
            'inputs': {'snapshot': {'block': 54_880_000 + loop}},
            'screens': [{'market': 'live', 'screen': 'utilization_above_95pct', 'detail': 'utilization 0.9923'}],
            'judge': {'outcome': 'answer', 'accepted': True, 'ignored': None, 'trigger': ['live:utilization_above_95pct'], 'judgment_market': market,
                      'call': {'parsed': {'risk': 'high', 'action': action, 'market': market, 'reasons': ['utilization 0.9923 above 0.95'], 'source_ids': []}}},
            'decisions': [{'action': 'migrate', 'protective': False, 'market': 'live', 'to': 'backup', 'amount': '0', 'fraction': str(WAD),
                           'basis': 'judge leave_market + on-chain screen: migrate to the approved market that can take it', 'detail': {}}],
            'chosen': 'live',
            'proposal': {'method': 'migrate', 'args': {'from': V.MARKETS['live'], 'to': V.MARKETS['backup'], 'fraction': str(WAD)}, 'sent': True,
                         'near_tx': 'Abc', 'base': {'hash': '0x' + 'ab' * 32, 'status': 1, 'block': 54_880_002}}}


class Transcripts(unittest.TestCase):
    def idx(self, *names): return {'transcripts': [{'name': n, 'sha256': 'x', 'vault': 'v1', 'loop': i} for i, n in enumerate(names)]}

    def test_diff(self):
        a, b = self.idx('t1', 't2'), self.idx('t1', 't2', 't3', 't4')
        self.assertEqual([t['name'] for t in V.new_transcripts(a, b)], ['t3', 't4'])
        self.assertEqual(V.new_transcripts(b, b), [])
        self.assertEqual([t['name'] for t in V.new_transcripts(None, a)], ['t1', 't2'])   # first fetch failed
        self.assertEqual(V.new_transcripts(a, None), [])                                  # second fetch failed

    def test_summary(self):
        raw = json.dumps(transcript(7)).encode()
        s = V.transcript_summary({'name': 'boot-x-v1-0007.json', 'sha256': hashlib.sha256(raw).hexdigest()}, raw)
        self.assertEqual(s['sha256'], s['sha256_index'])
        self.assertEqual((s['vault'], s['loop'], s['block'], s['chosen']), ('v1', 7, 54_880_007, 'live'))
        self.assertEqual(s['screens'], ['live:utilization_above_95pct'])
        self.assertEqual(s['judge']['answer'], {'risk': 'high', 'action': 'leave_market', 'market': 'live', 'reasons': ['utilization 0.9923 above 0.95']})
        self.assertEqual((s['judge']['outcome'], s['judge']['accepted']), ('answer', True))
        self.assertEqual(s['decisions'][0]['to'], 'backup'); self.assertNotIn('detail', s['decisions'][0])
        self.assertEqual((s['proposal']['method'], s['proposal']['base_tx'], s['proposal']['base_status']), ('migrate', '0x' + 'ab' * 32, 1))

    def test_summary_without_judge(self):
        t = transcript(8); t['judge'] = None; del t['proposal']
        s = V.transcript_summary({'name': 'n'}, json.dumps(t).encode())
        self.assertIsNone(s['judge']); self.assertIsNone(s['proposal'])


def obs(block, live_coll, live_debt, backup_coll, backup_debt, util):
    mk = lambda u: {'supply': 13_000_148, 'borrow': int(13_000_148 * u), 'free': 13_000_148 - int(13_000_148 * u), 'rate': 176_327_413,
                    'apr': V.apr(176_327_413), 'utilization': u, 'price': PRICE}
    pos = lambda c, d: {'coll': c, 'debt': d, 'ltv': None if not d else d * WAD // max(1, c * PRICE // V.ORACLE_SCALE), 'shares': d * 10 ** 6, 'cash': 11_579_501}
    return {'block': block, 'ts': 1_793_896_796 + block, 'utc': '2026-11-06 10:00:00 UTC', 'markets': {'live': mk(util), 'backup': mk(0.0)},
            'positions': {'v1': {'live': pos(live_coll, live_debt), 'backup': pos(backup_coll, backup_debt)}, 'wallet': {'live': pos(0, 0)}},
            'moves_in_window': 0, 'wallet_weth': 33_996_839_373_083_644}


class Markdown(unittest.TestCase):
    def record(self):
        a = type('A', (), {'borrow_keep_units': 100_000, 'collateral_ltv': 0.5})()
        o0 = obs(100, 8_335_601_888_990_126, 7_729_864, 0, 0, 0.5946)
        sz = V.sizing(13_000_148, 7_729_864, PRICE, 100_000, 0.5)
        rec = V.new_record(o0, V1, WALLET, sz, a)
        rec['phases']['before'] = V.phase_entry(o0)
        rec['phases']['after_borrow'] = V.phase_entry(obs(103, 8_335_601_888_990_126, 7_729_864, 0, 0, 0.9923))
        return rec

    def test_incomplete(self):
        md = V.render_md(self.record())
        self.assertIn('# Scene `venue-stress` on the live market (Base Sepolia)', md)
        self.assertIn('Started at block 100', md)
        self.assertIn('**Incomplete:**', md)
        self.assertIn('| | before the borrow | after the borrow |', md)
        self.assertIn('| live utilization | 59.46 % | 99.23 % |', md)
        self.assertIn('Not recorded.', md)

    def test_complete(self):
        rec = self.record()
        rec['phases']['after'] = V.phase_entry(obs(140, 0, 0, 8_335_601_888_990_126, 7_729_901, 0.3977))
        rec['actions'] = [{'block': 140, 'kind': 'migrate', 'tx': '0x' + 'ab' * 32, 'by': 'agent', 'from': 'live', 'to': 'backup', 'fraction': str(WAD),
                           'debt_moved_units': '7729901', 'collateral_moved_wei': '8335601888990126'}]
        c = V.compare({'live': {'coll': 8_335_601_888_990_126, 'debt': 7_729_901}, 'backup': {'coll': 0, 'debt': 0}},
                      {'live': {'coll': 0, 'debt': 0}, 'backup': {'coll': 8_335_601_888_990_126, 'debt': 7_729_901}})
        c.update({'block_before': 139, 'block_after': 140, 'basis': 'test', 'event': {'fraction': str(WAD), 'debt_moved_units': '7729901',
                                                                                       'collateral_moved_wei': '8335601888990126'}})
        rec['comparison'] = c
        raw = json.dumps(transcript(9)).encode()
        rec['transcripts'] = [V.transcript_summary({'name': 'boot-x-v1-0009.json'}, raw)]
        rec['transactions'] = [{'label': 'Morpho borrow', 'tx': '0x' + '01' * 32, 'block': 103, 'gas': 120000, 'fee_wei': '5', 'l1_fee_wei': '1'}]
        rec['outcome'] = {'stopped': 'migrate', 'agent_actions': ['migrate']}
        rec['notes'] = 'one\ntwo'
        md = V.render_md(rec)
        self.assertNotIn('**Incomplete:**', md)
        self.assertIn("| | before the borrow | after the borrow | after the agent's action |", md)
        self.assertIn('| v1 collateral on backup (WETH) | 0.000000000 | 0.000000000 | 0.008335602 |', md)
        self.assertIn('| collateral (WETH) | 0.008335602 | 0.008335602 | 0 wei |', md)
        self.assertIn('Collateral identical: yes. Debt equal up to 1 unit: yes.', md)
        self.assertIn('| 140 | migrate | agent | live -> backup | fraction %d, debt 7.729901 USDC, collateral 0.008335602 WETH |' % WAD, md)
        self.assertIn('| `boot-x-v1-0009.json` | 2026-11-06T10:00:09Z | judge_result | live:utilization_above_95pct | answer, accepted True | high, leave_market, live | live |', md)
        self.assertIn('- utilization 0.9923 above 0.95', md)
        self.assertIn('decision on live: migrate to backup', md)
        self.assertIn('- Morpho borrow: `0x%s` (block 103, gas 120000, fee 5 wei)' % ('01' * 32), md)
        self.assertIn('## Notes', md); self.assertIn('- two', md)
        json.dumps(rec)                                                       # the record serialises


if __name__ == '__main__':
    unittest.main(verbosity=2)
