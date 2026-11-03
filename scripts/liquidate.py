#!/usr/bin/env python3 -I
"""Scripted liquidator for the cmdk TESTNET venue (Base Sepolia only). Testnet tooling, not a production keeper.

Reads one Morpho Blue position and liquidates it from the team's test wallet when Morpho's own health rule says it
may be liquidated: borrowed > collateral * oracle price / 1e36 * LLTV, with the borrow assets accrued to the
latest block through the market's IRM (borrowRateView), as Morpho accrues before it checks.
Key from ~/.config/prime/base_sepolia_eoa.json (never printed); RPC from $RPC; addresses from $DEPLOYMENTS
(deployments/testnet.json by default, or the old flat executor/deployments.json; see rebalance_pool.load_deployments).

  liquidate.py --market demo|live|backup|0x<market id> --borrower 0x... [--dry-run] [--reserve-usdc 8]

What it repays:
  - the whole debt (repaidShares = all borrow shares) when the collateral covers the seizure:
    seized = debt * LIF / price, with LIF = min(1.15, 1 / (1 - 0.3 * (1 - LLTV))) (1.0438 at LLTV 86%);
  - otherwise it seizes all the collateral (seizedAssets = collateral); Morpho books the rest as bad debt;
  - at most what the wallet's loan token covers: on the demo market dUSDC is minted for any shortfall (anyone may
    mint it); on live and backup the wallet keeps --reserve-usdc for the rebalancer, and a partial liquidation
    repays what is left above it.
--dry-run prints the reading and the computed amounts and sends nothing.
Exit codes: 0 liquidated or nothing to do (healthy, no debt), 1 error, 3 the wallet cannot fund any repayment.
"""
import argparse, os, subprocess, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import rebalance_pool as rp   # noqa: E402  (shared cast helpers and deployments loader; our own file)

DEP = rp.DEP
MORPHO = DEP['morpho']
WAD = 10 ** 18; ORACLE_SCALE = 10 ** 36; VIRTUAL_SHARES = 10 ** 6; VIRTUAL_ASSETS = 1
MAX_LIF = 115 * 10 ** 16; LCF = 3 * 10 ** 17                 # Morpho Blue ConstantsLib
MP_T = '(address,address,address,address,uint256)'
MARKET_T = '(uint128,uint128,uint128,uint128,uint128,uint128)'


def mul_div_down(x, y, d): return x * y // d
def mul_div_up(x, y, d): return (x * y + d - 1) // d
def w_mul_down(x, y): return x * y // WAD
def w_div_down(x, y): return x * WAD // y
def to_assets_up(s, ta, ts): return mul_div_up(s, ta + VIRTUAL_ASSETS, ts + VIRTUAL_SHARES)
def to_assets_down(s, ta, ts): return mul_div_down(s, ta + VIRTUAL_ASSETS, ts + VIRTUAL_SHARES)
def to_shares_down(a, ta, ts): return mul_div_down(a, ts + VIRTUAL_SHARES, ta + VIRTUAL_ASSETS)
def to_shares_up(a, ta, ts): return mul_div_up(a, ts + VIRTUAL_SHARES, ta + VIRTUAL_ASSETS)


def w_taylor(x, n):
    """MathLib.wTaylorCompounded: e^(x*n) - 1, three terms."""
    first = x * n; second = mul_div_down(first, first, 2 * WAD); third = mul_div_down(second, first, 3 * WAD)
    return first + second + third


def market_id(name):
    if name.startswith('0x') and len(name) == 66: return name
    return DEP[{'live': 'liveMarketId', 'backup': 'backupMarketId', 'demo': 'demoMarketId'}[name]]


def read(mid, borrower):
    p = rp.call(MORPHO, 'idToMarketParams(bytes32)(address,address,address,address,uint256)', mid)
    mp = {'loan': p[0], 'coll': p[1], 'oracle': p[2], 'irm': p[3], 'lltv': int(p[4])}
    if int(mp['loan'], 16) == 0: raise SystemExit('market %s does not exist on Morpho' % mid)
    mk = [int(x) for x in rp.call(MORPHO, 'market(bytes32)%s' % MARKET_T, mid)]
    pos = rp.call(MORPHO, 'position(bytes32,address)(uint256,uint128,uint128)', mid, borrower)
    now = int(subprocess.run([rp.CAST, 'block', 'latest', '-f', 'timestamp', '--rpc-url', rp.RPC], capture_output=True, text=True, check=True).stdout.strip())
    tba, tbs, last = mk[2], mk[3], mk[4]
    # interest to the latest block, as _accrueInterest does before liquidate checks health
    if int(mp['irm'], 16) and now > last and tba:
        rate = int(rp.call(mp['irm'], 'borrowRateView(%s,%s)(uint256)' % (MP_T, MARKET_T),
                           '(%s,%s,%s,%s,%d)' % (mp['loan'], mp['coll'], mp['oracle'], mp['irm'], mp['lltv']),
                           '(%s)' % ','.join(str(x) for x in mk))[0])
        tba += w_mul_down(tba, w_taylor(rate, now - last))
    return mp, {'tba': tba, 'tbs': tbs, 'stored_tba': mk[2], 'elapsed': now - last}, int(pos[1]), int(pos[2]), int(rp.call(mp['oracle'], 'price()(uint256)')[0])


def main():
    ap = argparse.ArgumentParser(description='Testnet liquidator (Base Sepolia only)')
    ap.add_argument('--market', required=True, help='live, backup, demo or a 0x market id')
    ap.add_argument('--borrower', required=True)
    ap.add_argument('--reserve-usdc', type=float, default=8.0, help='USDC the wallet keeps on live and backup (the rebalancer reserve)')
    ap.add_argument('--dry-run', action='store_true')
    a = ap.parse_args()
    chain = int(subprocess.run([rp.CAST, 'chain-id', '--rpc-url', rp.RPC], capture_output=True, text=True, check=True).stdout.strip())
    if chain != rp.CHAIN_ID: raise SystemExit('chain id %d is not Base Sepolia (%d): testnet only' % (chain, rp.CHAIN_ID))
    _, me = rp.key()
    mid = market_id(a.market)
    mp, mk, shares, coll, price = read(mid, a.borrower)
    dusdc = mp['loan'].lower() == rp.DUSDC.lower()
    dec = 'dUSDC' if dusdc else 'USDC'
    debt = to_assets_up(shares, mk['tba'], mk['tbs'])
    max_borrow = w_mul_down(mul_div_down(coll, price, ORACLE_SCALE), mp['lltv'])
    value = mul_div_down(coll, price, ORACLE_SCALE)
    print('TESTNET liquidator; rpc %s; market %s (%s), borrower %s, liquidator %s' % (rp.RPC, a.market, mid, a.borrower, me))
    print('position: collateral %.18f WETH, borrow shares %d, debt %.6f %s (accrued %d s since the market\'s last update)' % (
        coll / 1e18, shares, debt / 1e6, dec, mk['elapsed']))
    print('oracle %s price $%.2f; collateral value %.6f; LLTV %.4f; max borrow %.6f; LTV %s' % (
        mp['oracle'], price / 1e24, value / 1e6, mp['lltv'] / 1e18, max_borrow / 1e6, '%.4f' % (debt / value) if value else 'n/a'))
    if shares == 0: print('no debt: nothing to liquidate'); return
    if max_borrow >= debt: print('healthy (max borrow %.6f >= debt %.6f): Morpho would refuse; nothing sent' % (max_borrow / 1e6, debt / 1e6)); return
    lif = min(MAX_LIF, w_div_down(WAD, WAD - w_mul_down(LCF, WAD - mp['lltv'])))
    # seized collateral for repaying all shares, as Morpho computes it
    seize_all_shares = mul_div_down(w_mul_down(to_assets_down(shares, mk['tba'], mk['tbs']), lif), ORACLE_SCALE, price)
    if seize_all_shares <= coll:
        mode, seized, rep_shares = 'repay all shares', 0, shares
        repaid = to_assets_up(shares, mk['tba'], mk['tbs'])
        seized_est = seize_all_shares
    else:  # under water: seize everything; repaid = seized * price / LIF, the remaining shares become bad debt
        mode, seized, rep_shares = 'seize all collateral (bad debt remains)', coll, 0
        rs = to_shares_up(mul_div_up(mul_div_up(coll, price, ORACLE_SCALE), WAD, lif), mk['tba'], mk['tbs'])
        repaid = to_assets_up(rs, mk['tba'], mk['tbs']); seized_est = coll
    need = repaid + repaid // 100 + 1                           # 1% for interest between this read and the block
    have = rp.bal(mp['loan'], me)
    if not dusdc:
        budget = max(have - int(a.reserve_usdc * 1e6), 0)
        if budget < need:                                        # partial: the shares the budget covers
            if budget <= 0: print('wallet %.6f USDC, reserve %.2f: nothing to repay with' % (have / 1e6, a.reserve_usdc)); raise SystemExit(3)
            rep_shares = to_shares_down(budget * 100 // 101, mk['tba'], mk['tbs']); seized = 0
            repaid = to_assets_up(rep_shares, mk['tba'], mk['tbs']); need = budget
            seized_est = mul_div_down(w_mul_down(to_assets_down(rep_shares, mk['tba'], mk['tbs']), lif), ORACLE_SCALE, price)
            mode = 'partial: %d of %d shares (wallet USDC above the %.2f reserve)' % (rep_shares, shares, a.reserve_usdc)
    print('liquidatable: debt %.6f > max borrow %.6f. LIF %.4f. Plan: %s; repay about %.6f %s, seize about %.18f WETH '
          '(worth %.6f at the oracle)' % (debt / 1e6, max_borrow / 1e6, lif / 1e18, mode, repaid / 1e6, dec, seized_est / 1e18,
                                          seized_est * price / ORACLE_SCALE / 1e6))
    print('liquidate(%s, %s, seizedAssets %d, repaidShares %d, 0x); approve %d to Morpho; wallet holds %.6f %s' % (
        '(%s,%s,%s,%s,%d)' % (mp['loan'], mp['coll'], mp['oracle'], mp['irm'], mp['lltv']), a.borrower, seized, rep_shares, need, have / 1e6, dec))
    if a.dry_run: print('dry-run: nothing sent'); return
    if dusdc and have < need: rp.send('mint dUSDC for the repayment', mp['loan'], 'mint(address,uint256)', me, need - have)
    rp.send('approve Morpho', mp['loan'], 'approve(address,uint256)', MORPHO, need)
    c0, l0 = rp.bal(mp['coll'], me), rp.bal(mp['loan'], me)
    rp.send('Morpho.liquidate', MORPHO, 'liquidate(%s,address,uint256,uint256,bytes)' % MP_T,
            '(%s,%s,%s,%s,%d)' % (mp['loan'], mp['coll'], mp['oracle'], mp['irm'], mp['lltv']), a.borrower, seized, rep_shares, '0x')
    rp.send('approve Morpho 0', mp['loan'], 'approve(address,uint256)', MORPHO, 0)
    _, _, shares2, coll2, _ = read(mid, a.borrower)
    print('done: repaid %.6f %s, seized %.18f WETH; borrower now %d shares, %.18f WETH collateral; fees %d wei (L1 %d)' % (
        (l0 - rp.bal(mp['loan'], me)) / 1e6, dec, (rp.bal(mp['coll'], me) - c0) / 1e18, shares2, coll2 / 1e18,
        rp.COSTS['l2_wei'] + rp.COSTS['l1_wei'], rp.COSTS['l1_wei']))
    print('transactions:', ' '.join(rp.COSTS['txs']))


if __name__ == '__main__':
    main()
