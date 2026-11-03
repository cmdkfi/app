#!/usr/bin/env python3 -I
"""P1 deliverable 2: call every executor method once on Base Sepolia and record gas from the receipts.

The test wallet 0x0B7E...0d25 is the caller (the executor is permissionless and caller-scoped; in P5 the vault's
derived address is the caller). Testnet only. Key from ~/.config/prime/base_sepolia_eoa.json, never printed.
RPC from $RPC (an anvil fork for a rehearsal); addresses through rebalance_pool.load_deployments: $DEPLOYMENTS,
default ../deployments/testnet.json, or the old flat ../executor/deployments.json (either shape is accepted).
Output: $OUT (default ../executor/gas_onchain.json) and a table on stdout.

  p1_onchain_run.py [--anvil] [--live-sale | --live-sale-only | --venue-open | --venue-migrate]
    --venue-open    venue row step 2 (3 Nov): corridors on live, backup and demo (target 50%, act 70%, floor 30%,
                 sell budget 100%, migrate and exit allowed), then open 0.003 WETH at 50% on each. Before that, USDC lender
                 supply tops up live and backup when their free liquidity is short of what the opens and the later
                 migrate borrow (live: one open; backup: its open plus live's debt), plus 5%. Prints each position.
                 Output: $OUT (default ../evidence/venue/2026-11-03-venue-step2.json).
    --venue-migrate  venue row step 2: migrate(live, backup, 1e18) on the caller's live position; prints both
                 positions before and after. Same default output file (rows are appended).
    --anvil      skip cooldowns with evm_increaseTime instead of waiting (rehearsal on a local fork)
    --live-sale-only  the live sale and nothing else (27 Oct): check that the live pool is within the rebalancer's
                 tolerance of the oracle (otherwise stop: the repair needs AFTER_FUNDING.md step 2b's quoted stop rule),
                 then open live 0.002 WETH at 50%, sell 0.2 USDC with reduce, exit. No lender supply, no corridors, no
                 demo flow, no MockOracle move. Output: $OUT (default ../executor/gas_onchain_live_sale.json).
    --live-sale  also rebalance the live pool to the oracle and sell on it. Off by default: on 26 Oct the live pool
                 sat 10.6% below the oracle and pulling it back costs about 24.5 USDC, more than the wallet's 24.23
                 (Circle's faucet gives 20 per 2 hours); reduce's gas is measured on the demo market through the same code.

Order: setup (approvals, Morpho authorization, wrap, 3 USDC lender supply to live and backup) -> corridors -> live
flow (open, migrate 40%, migrate 100% back, [discretionary reduce], exit from idle USDC) -> demo flow (open, discretionary reduce, mock price
-33% so LTV > act, protective reduce slice, reduce clamped at target, exit selling collateral, open, openFromStables,
repay, close) -> demo price restored.
"""
import argparse, json, os, subprocess, sys, time
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import rebalance_pool as rp   # noqa: E402  (shared cast helpers; our own file)

CAST = rp.CAST
DEP = rp.load_deployments()           # $DEPLOYMENTS, deployments/testnet.json by default; the old flat file also works
OUT = os.environ.get('OUT', os.path.join(HERE, '..', 'executor', 'gas_onchain.json'))
EXEC = DEP['executor']; MORPHO = DEP['morpho']
LIVE, BACKUP, DEMO = DEP['liveMarketId'], DEP['backupMarketId'], DEP['demoMarketId']
USDC, WETH, DUSDC = DEP['usdc'], DEP['weth'], DEP['dusdc']
MAXU = 2 ** 256 - 1
CORRIDOR = '(500000000000000000,700000000000000000,300000000000000000,200000000000000000,true,true,false)'
CORRIDOR_T = '(uint64,uint64,uint64,uint64,bool,bool,bool)'
COOLDOWN = int(DEP['cooldownSeconds'])
TOLERANCE = 0.0025                       # rebalance_pool.py's default
rows = []
positions = []                           # venue modes: position readings, written next to the rows
VENUE_OUT = os.path.join(HERE, '..', 'evidence', 'venue', '2026-11-03-venue-step2.json')
VENUE_CORRIDOR = (5 * 10 ** 17, 7 * 10 ** 17, 3 * 10 ** 17, 10 ** 18, True, True)   # target, act, floor, budget, migrate, exit
VENUE_COLL = 3 * 10 ** 15                # 0.003 WETH per test position
VENUE_TARGET = 5 * 10 ** 17
MARKET_T = '(uint128,uint128,uint128,uint128,uint128,uint128)'


def now():
    return int(subprocess.run([CAST, 'block', 'latest', '-f', 'timestamp', '--rpc-url', rp.RPC], capture_output=True, text=True, check=True).stdout.strip())


def dl(): return now() + 600


def tx(step, to, sig, *args, value=None):
    j = rp.send(step, to, sig, *args, value=value)
    r = {'step': step, 'method': sig.split('(')[0], 'tx': j['transactionHash'], 'block': int(j['blockNumber'], 16),
         'status': int(j['status'], 16), 'gasUsed': int(j['gasUsed'], 16),
         'effectiveGasPrice': int(j.get('effectiveGasPrice', '0x0'), 16), 'l1FeeWei': int(j.get('l1Fee', '0x0') or '0x0', 16)}
    rows.append(r)
    dump()
    return j


def dump():
    d = {'rpc': rp.RPC, 'deployments': DEP, 'rows': rows}
    if positions: d['positions'] = positions
    json.dump(d, open(OUT, 'w'), indent=1)


def wait_cooldown(anvil):
    if anvil:
        subprocess.run([CAST, 'rpc', 'evm_increaseTime', str(COOLDOWN + 1), '--rpc-url', rp.RPC], capture_output=True, check=True)
        subprocess.run([CAST, 'rpc', 'evm_mine', '--rpc-url', rp.RPC], capture_output=True, check=True)
    else:
        print('  waiting %d s (executor cooldown)' % (COOLDOWN + 2)); time.sleep(COOLDOWN + 2)


def ltv(mid): return int(rp.call(EXEC, 'ltvOf(address,bytes32)(uint256)', ME, mid)[0]) / 1e18


def rebalance(market, set_price=None):
    cmd = [sys.executable, '-I', os.path.join(HERE, 'rebalance_pool.py'), '--market', market]
    if set_price: cmd += ['--set-price', str(set_price)]
    r = subprocess.run(cmd, capture_output=True, text=True, env=os.environ)
    print('\n'.join('    ' + l for l in r.stdout.strip().split('\n')))
    if r.returncode != 0: raise SystemExit(r.stderr[-500:])


def live_sale_only(a):
    m = rp.Market('live')
    o, po, sq, tick, liq = m.read()
    dev = po / o - 1
    usdc = rp.bal(USDC, ME)
    print('live pool vs oracle %+.3f%% (tolerance %.2f%%), tick %d; wallet USDC %.6f' % (100 * dev, 100 * TOLERANCE, tick, usdc / 1e6))
    if abs(dev) > TOLERANCE:
        raise SystemExit('stop: the live pool needs a repair first (AFTER_FUNDING.md step 2b, stop rule on its quoted cost); no sale')
    print('repair cost 0 (within tolerance): stop rule passes; the sale itself needs no USDC')
    ts = int(rp.call(MORPHO, 'market(bytes32)(uint128,uint128,uint128,uint128,uint128,uint128)', LIVE)[0])
    tb = int(rp.call(MORPHO, 'market(bytes32)(uint128,uint128,uint128,uint128,uint128,uint128)', LIVE)[2])
    if ts - tb < 2_500_000:
        raise SystemExit('stop: live has %.6f USDC to lend, under the 2.5 the open needs' % ((ts - tb) / 1e6))
    rp.wrap_if_short(ME, 2 * 10 ** 15, False)
    tx('open live 0.002 WETH at 50%', EXEC, 'open(bytes32,uint256,uint256,uint256)', LIVE, 2 * 10 ** 15, 5 * 10 ** 17, dl())
    print('  LTV live %.4f' % ltv(LIVE))
    wait_cooldown(a.anvil)
    tx('reduce live 0.2 USDC (discretionary)', EXEC, 'reduce(bytes32,uint256,uint256)', LIVE, 200000, dl())
    print('  LTV live %.4f, sold in window %d wei' % (ltv(LIVE), int(rp.call(EXEC, 'soldInWindow(address,address)(uint256)', ME, WETH)[0])))
    o, po, sq, tick, liq = m.read()
    print('  live pool after the sale %+.3f%% vs oracle, tick %d' % (100 * (po / o - 1), tick))
    wait_cooldown(a.anvil)
    tx('exit live (idle USDC, no sale)', EXEC, 'exit(bytes32,uint256)', LIVE, dl())


def position(name, mid, label):
    """Morpho position of the caller with debt (shares rounded up, at the stored totals) and the executor's ltvOf."""
    p = rp.call(MORPHO, 'position(bytes32,address)(uint256,uint128,uint128)', mid, ME)
    mk = [int(x) for x in rp.call(MORPHO, 'market(bytes32)%s' % MARKET_T, mid)]
    shares, coll = int(p[1]), int(p[2])
    debt = (shares * (mk[2] + 1) + mk[3] + 10 ** 6 - 1) // (mk[3] + 10 ** 6)
    r = {'when': label, 'market': name, 'id': mid, 'block': int(subprocess.run([CAST, 'block-number', '--rpc-url', rp.RPC],
         capture_output=True, text=True, check=True).stdout.strip()), 'supplyShares': int(p[0]), 'borrowShares': shares,
         'collateral': coll, 'debt': debt, 'ltvOf': int(rp.call(EXEC, 'ltvOf(address,bytes32)(uint256)', ME, mid)[0]),
         'marketSupply': mk[0], 'marketBorrow': mk[2]}
    positions.append(r); dump()
    print('  %s %s (block %d): collateral %.18f WETH, borrow shares %d, debt %.6f, ltvOf %.6f; market supply %.6f, borrow %.6f' % (
        label, name, r['block'], coll / 1e18, shares, debt / 1e6, r['ltvOf'] / 1e18, mk[0] / 1e6, mk[2] / 1e6))
    return r


def corridor_valid(target, act, floor, lltv, budget):
    """CorridorMath.corridorValid: floor < target, target + 0.001e18 < act < lltv, budget <= 1e18."""
    return floor < target and target + 10 ** 15 < act and act < lltv and budget <= 10 ** 18


def venue_open(a):
    names = (('live', LIVE, DEP['liveOracle'], USDC), ('backup', BACKUP, DEP['backupOracle'], USDC), ('demo', DEMO, DEP['mockOracle'], DUSDC))
    for name, mid, _, _ in names: position(name, mid, 'before')
    print('lender liquidity (USDC markets): free supply against what the opens and the migrate will borrow')
    live_borrow = VENUE_COLL * int(rp.call(DEP['liveOracle'], 'price()(uint256)')[0]) // 10 ** 36 * VENUE_TARGET // 10 ** 18
    for name, mid, oracle, _ in names[:2]:
        borrow = VENUE_COLL * int(rp.call(oracle, 'price()(uint256)')[0]) // 10 ** 36 * VENUE_TARGET // 10 ** 18
        need = borrow + (live_borrow if name == 'backup' else 0)
        mk = [int(x) for x in rp.call(MORPHO, 'market(bytes32)%s' % MARKET_T, mid)]
        free = mk[0] - mk[2]
        top = max(0, (need * 105 // 100 - free + 9999) // 10000 * 10000)
        print('  %s: free %.6f USDC, needs %.6f; supply %.2f' % (name, free / 1e6, need / 1e6, top / 1e6))
        if top:
            if rp.bal(USDC, ME) - top < 8 * 10 ** 6: raise SystemExit('stop: the supply would leave the wallet under 8 USDC')
            tx('approve Morpho USDC', USDC, 'approve(address,uint256)', MORPHO, top)
            tx('supply %.2f USDC to %s' % (top / 1e6, name), MORPHO, 'supply((address,address,address,address,uint256),uint256,uint256,address,bytes)',
               '(%s,%s,%s,%s,%d)' % (USDC, WETH, oracle, DEP['irm'], DEP['lltv']), top, 0, ME, '0x')
    t, act, fl, bud, mig, ex = VENUE_CORRIDOR
    ok = corridor_valid(t, act, fl, DEP['lltv'], bud)
    print('corridor target %.2f act %.2f floor %.2f budget %.2f migrate %s exit %s: corridorValid %s' % (t / 1e18, act / 1e18, fl / 1e18, bud / 1e18, mig, ex, ok))
    if not ok: raise SystemExit('stop: corridor not valid')
    c = '(%d,%d,%d,%d,%s,%s,false)' % (t, act, fl, bud, str(mig).lower(), str(ex).lower())
    for name, mid, _, _ in names:
        tx('setCorridor %s' % name, EXEC, 'setCorridor(bytes32,%s,uint256)' % CORRIDOR_T, mid, c, dl())
        print('  on chain:', ' '.join(rp.call(EXEC, 'corridors(address,bytes32)%s' % CORRIDOR_T, ME, mid)))
    for name, mid, _, _ in names:
        rp.wrap_if_short(ME, VENUE_COLL, False)
        tx('open %s 0.003 WETH at 50%%' % name, EXEC, 'open(bytes32,uint256,uint256,uint256)', mid, VENUE_COLL, VENUE_TARGET, dl())
        position(name, mid, 'after open')


def venue_migrate(a):
    for k, v in (('movesInWindow', rp.call(EXEC, 'movesInWindow(address)(uint256)', ME)[0]),
                 ('lastAgentCall', rp.call(EXEC, 'lastAgentCall(address)(uint256)', ME)[0]), ('chain time', now())):
        print('  %s %s' % (k, v))
    for name, mid in (('live', LIVE), ('backup', BACKUP)): position(name, mid, 'before migrate')
    tx('migrate live->backup 100%', EXEC, 'migrate(bytes32,bytes32,uint256,uint256)', LIVE, BACKUP, 10 ** 18, dl())
    for name, mid in (('live', LIVE), ('backup', BACKUP)): position(name, mid, 'after migrate')


def main():
    global ME, OUT
    ap = argparse.ArgumentParser(); ap.add_argument('--anvil', action='store_true'); ap.add_argument('--live-sale', action='store_true')
    ap.add_argument('--live-sale-only', action='store_true')
    ap.add_argument('--venue-open', action='store_true'); ap.add_argument('--venue-migrate', action='store_true')
    a = ap.parse_args()
    _, ME = rp.key()
    print('executor %s on %s, caller %s' % (EXEC, rp.RPC, ME))
    if a.venue_open or a.venue_migrate:
        chain = int(subprocess.run([CAST, 'chain-id', '--rpc-url', rp.RPC], capture_output=True, text=True, check=True).stdout.strip())
        if chain != rp.CHAIN_ID: raise SystemExit('chain id %d is not Base Sepolia (%d): testnet only' % (chain, rp.CHAIN_ID))
        OUT = os.environ.get('OUT', VENUE_OUT)
        os.makedirs(os.path.dirname(os.path.abspath(OUT)), exist_ok=True)
        if os.path.exists(OUT):                          # the second mode appends to the first one's file
            old = json.load(open(OUT)); rows.extend(old.get('rows', [])); positions.extend(old.get('positions', []))
        venue_open(a) if a.venue_open else venue_migrate(a)
        print('\n| Step | Gas used | L1 fee (wei) | Tx |\n|---|---|---|---|')
        for r in rows:
            print('| %s | %s | %s | %s |' % (r['step'], format(r['gasUsed'], ','), format(r['l1FeeWei'], ','), r['tx']))
        print('\nwritten', OUT)
        return
    if a.live_sale_only:
        OUT = os.environ.get('OUT', os.path.join(HERE, '..', 'executor', 'gas_onchain_live_sale.json'))
        live_sale_only(a)
        print('\n| Step | Gas used | L1 fee (wei) | Tx |\n|---|---|---|---|')
        for r in rows:
            print('| %s | %s | %s | %s |' % (r['step'], format(r['gasUsed'], ','), format(r['l1FeeWei'], ','), r['tx']))
        print('\nwritten', OUT)
        return

    print('setup')
    for t in (WETH, USDC, DUSDC):
        if int(rp.call(t, 'allowance(address,address)(uint256)', ME, EXEC)[0]) < 2 ** 200:
            tx('approve executor', t, 'approve(address,uint256)', EXEC, MAXU)
    if rp.call(MORPHO, 'isAuthorized(address,address)(bool)', ME, EXEC)[0] != 'true':
        tx('Morpho.setAuthorization(executor)', MORPHO, 'setAuthorization(address,bool)', EXEC, 'true')
    rp.wrap_if_short(ME, 12 * 10 ** 15, False)
    for name, mid, oracle in (('live', LIVE, DEP['liveOracle']), ('backup', BACKUP, DEP['backupOracle'])):
        ts = int(rp.call(MORPHO, 'market(bytes32)(uint128,uint128,uint128,uint128,uint128,uint128)', mid)[0])
        if ts < 3 * 10 ** 6:
            amt = 3 * 10 ** 6
            tx('approve Morpho USDC', USDC, 'approve(address,uint256)', MORPHO, amt)
            tx('supply 3 USDC to %s' % name, MORPHO, 'supply((address,address,address,address,uint256),uint256,uint256,address,bytes)',
               '(%s,%s,%s,%s,860000000000000000)' % (USDC, WETH, oracle, '0x46415998764C29aB2a25CbeA6254146D50D22687'), amt, 0, ME, '0x')

    print('corridors (target 50%, act 70%, floor 30%, budget 20%, migrate and exit allowed)')
    for name, mid in (('live', LIVE), ('backup', BACKUP), ('demo', DEMO)):
        tx('setCorridor %s' % name, EXEC, 'setCorridor(bytes32,%s,uint256)' % CORRIDOR_T, mid, CORRIDOR, dl())

    print('live flow')
    if a.live_sale: rebalance('live')
    rp.wrap_if_short(ME, 2 * 10 ** 15, False)
    tx('open live 0.002 WETH at 50%', EXEC, 'open(bytes32,uint256,uint256,uint256)', LIVE, 2 * 10 ** 15, 5 * 10 ** 17, dl())
    print('  LTV live %.4f' % ltv(LIVE))
    wait_cooldown(a.anvil)
    tx('migrate live->backup 40%', EXEC, 'migrate(bytes32,bytes32,uint256,uint256)', LIVE, BACKUP, 4 * 10 ** 17, dl())
    wait_cooldown(a.anvil)
    tx('migrate backup->live 100%', EXEC, 'migrate(bytes32,bytes32,uint256,uint256)', BACKUP, LIVE, 10 ** 18, dl())
    if a.live_sale:
        wait_cooldown(a.anvil)
        tx('reduce live 0.2 USDC (discretionary)', EXEC, 'reduce(bytes32,uint256,uint256)', LIVE, 200000, dl())
        print('  LTV live %.4f, sold in window %d wei' % (ltv(LIVE), int(rp.call(EXEC, 'soldInWindow(address,address)(uint256)', ME, WETH)[0])))
    wait_cooldown(a.anvil)
    tx('exit live (idle USDC, no sale)', EXEC, 'exit(bytes32,uint256)', LIVE, dl())

    print('demo flow')
    live_usd = int(rp.call(DEP['liveOracle'], 'price()(uint256)')[0]) / 1e24
    rebalance('demo', set_price=round(live_usd, 2))
    rp.wrap_if_short(ME, 4 * 10 ** 15, False)
    tx('open demo 0.004 WETH at 50%', EXEC, 'open(bytes32,uint256,uint256,uint256)', DEMO, 4 * 10 ** 15, 5 * 10 ** 17, dl())
    wait_cooldown(a.anvil)
    tx('reduce demo 0.3 dUSDC (discretionary)', EXEC, 'reduce(bytes32,uint256,uint256)', DEMO, 300000, dl())
    print('  LTV demo %.4f' % ltv(DEMO))
    rebalance('demo', set_price=round(live_usd * 0.67, 2))
    print('  LTV demo after the mock price drop %.4f' % ltv(DEMO))
    wait_cooldown(a.anvil)
    tx('reduce demo 0.3 dUSDC (protective slice)', EXEC, 'reduce(bytes32,uint256,uint256)', DEMO, 300000, dl())
    print('  LTV demo %.4f' % ltv(DEMO))
    wait_cooldown(a.anvil)
    tx('reduce demo to target (clamped)', EXEC, 'reduce(bytes32,uint256,uint256)', DEMO, 10 ** 12, dl())
    print('  LTV demo %.4f' % ltv(DEMO))
    wait_cooldown(a.anvil)
    # allowance 0 makes the idle dUSDC unavailable to exit, so it repays everything by selling collateral
    tx('dUSDC allowance to executor 0', DUSDC, 'approve(address,uint256)', EXEC, 0)
    tx('exit demo (all by sale)', EXEC, 'exit(bytes32,uint256)', DEMO, dl())
    tx('dUSDC allowance to executor max', DUSDC, 'approve(address,uint256)', EXEC, MAXU)
    rp.wrap_if_short(ME, 4 * 10 ** 15, False)
    tx('open demo 0.004 WETH at 50%', EXEC, 'open(bytes32,uint256,uint256,uint256)', DEMO, 4 * 10 ** 15, 5 * 10 ** 17, dl())
    tx('openFromStables demo 2 dUSDC at 50%', EXEC, 'openFromStables(bytes32,uint256,uint256,uint256)', DEMO, 2 * 10 ** 6, 5 * 10 ** 17, dl())
    tx('repay demo 0.5 dUSDC', EXEC, 'repay(bytes32,uint256,uint256)', DEMO, 500000, dl())
    tx('close demo', EXEC, 'close(bytes32,uint256)', DEMO, dl())
    rebalance('demo', set_price=round(live_usd, 2))

    print('\n| Step | Gas used | L1 fee (wei) | Tx |\n|---|---|---|---|')
    for r in rows:
        print('| %s | %s | %s | %s |' % (r['step'], format(r['gasUsed'], ','), format(r['l1FeeWei'], ','), r['tx']))
    print('\nwritten', OUT)


if __name__ == '__main__':
    main()
