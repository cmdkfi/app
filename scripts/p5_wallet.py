#!/usr/bin/env python3 -I
"""P5 (throwaway prototype): test-wallet transactions that are expected to revert, landed on chain with a fixed
gas limit so the revert has a hash and gas. Testnet only (Base Sepolia, chain id 84532). The key is read from
~/.config/prime/base_sepolia_eoa.json and handed only to the cast subprocess, as in rebalance_pool.py; it is
never printed or written.

  p5_wallet.py sim  TO SIG [ARGS...]                     eth_call from the wallet; prints the revert, decoded
  p5_wallet.py send TO SIG [ARGS...] [--gas N]           sim, then send with gas limit N (no estimate); receipt
  p5_wallet.py late TO SIG [ARGS...] --deadline T [--gas N]
      sign now (cast mktx), wait until the latest block's timestamp is above T, then publish; receipt
"""
import json, os, subprocess, sys, time

HOME = os.path.expanduser('~')
CAST = os.path.join(HOME, '.foundry', 'bin', 'cast')
RPC = 'https://base-sepolia-rpc.publicnode.com'
CHAIN_ID = 84532
ERRORS = ['Expired()', 'Reentrancy()', 'UnknownMarket()', 'UnapprovedMarket()', 'BadCorridor()', 'Cooldown()',
          'BelowFloor()', 'AtTarget()', 'SellBudget()', 'MoveLimit()', 'MigrateDisabled()', 'ExitDisabled()',
          'MarketMismatch()', 'BadFraction()', 'NoDebt()', 'StaleFeed()', 'Slippage()', 'PartialFill()',
          'AboveTarget()', 'NotMorpho()', 'NotPool()', 'MigrationMismatch()', 'BalanceChanged()', 'BadConfig()']


def run(args):
    p = subprocess.run(args, capture_output=True, text=True)
    return p.returncode, p.stdout.strip(), p.stderr.strip()


def key():
    with open(os.path.join(HOME, '.config', 'prime', 'base_sepolia_eoa.json')) as f:
        d = json.load(f)['data']
        d = d[0] if isinstance(d, list) else d
    return d['private_key'], d['address']


def selectors():
    return {run([CAST, 'sig', e])[1].lower(): e for e in ERRORS}


def decode(text):
    t = text.lower()
    for s, name in selectors().items():
        if s in t:
            return '%s (%s)' % (name, s)
    return text[-300:]


def sim(to, sig, args, me):
    rc, out, err = run([CAST, 'call', to, sig, *args, '--from', me, '--rpc-url', RPC])
    if rc == 0:
        print('sim: no revert, returns', out)
        return None
    print('sim: reverts', decode(err or out))
    return err or out


def receipt(h):
    for _ in range(120):
        rc, out, _ = run([CAST, 'receipt', h, '--json', '--rpc-url', RPC])
        if rc == 0 and out:
            j = json.loads(out)
            row = {'hash': h, 'status': int(j['status'], 16), 'block': int(j['blockNumber'], 16),
                   'gasUsed': int(j['gasUsed'], 16), 'effectiveGasPrice': int(j['effectiveGasPrice'], 16),
                   'l1Fee': int(j.get('l1Fee') or '0x0', 16)}
            ts = run([CAST, 'block', str(row['block']), '-f', 'timestamp', '--rpc-url', RPC])[1]
            row['block_timestamp'] = int(ts)
            print(json.dumps(row))
            return row
        time.sleep(1)
    raise SystemExit('no receipt for %s after 120 s' % h)


def opt(argv, name, default=None):
    if name in argv:
        i = argv.index(name)
        v = argv[i + 1]
        del argv[i:i + 2]
        return v
    return default


def main():
    argv = sys.argv[1:]
    if len(argv) < 3 or argv[0] not in ('sim', 'send', 'late'):
        raise SystemExit(__doc__)
    gas = opt(argv, '--gas', '300000')
    deadline = opt(argv, '--deadline')
    cmd, to, sig, args = argv[0], argv[1], argv[2], argv[3:]
    k, me = key()
    if cmd == 'sim':
        sim(to, sig, args, me)
        return
    if cmd == 'send':
        sim(to, sig, args, me)
        # no check=True anywhere: CalledProcessError would print the command line, key included
        rc, out, err = run([CAST, 'send', to, sig, *args, '--rpc-url', RPC, '--private-key', k, '--json',
                            '--gas-limit', gas, '--async'])
        if rc != 0:
            raise SystemExit('send failed: %s' % (err or out)[-400:])
        h = out.strip().strip('"')
        print('sent', h)
        receipt(h)
        return
    # late: sign now with the next nonce, publish only after the deadline has passed
    if deadline is None:
        raise SystemExit('late needs --deadline')
    nonce = run([CAST, 'nonce', me, '--rpc-url', RPC])[1]
    rc, raw, err = run([CAST, 'mktx', to, sig, *args, '--rpc-url', RPC, '--private-key', k, '--gas-limit', gas,
                        '--nonce', nonce, '--chain', str(CHAIN_ID)])
    if rc != 0:
        raise SystemExit('mktx failed: %s' % err[-400:])
    now = int(run([CAST, 'block', 'latest', '-f', 'timestamp', '--rpc-url', RPC])[1])
    print('signed at block timestamp %d, deadline %d, nonce %s; waiting' % (now, int(deadline), nonce))
    while int(run([CAST, 'block', 'latest', '-f', 'timestamp', '--rpc-url', RPC])[1]) <= int(deadline):
        time.sleep(2)
    sim(to, sig, args, me)
    rc, out, err = run([CAST, 'publish', raw, '--rpc-url', RPC, '--async'])
    if rc != 0:
        raise SystemExit('publish failed: %s' % (err or out)[-400:])
    h = out.strip().strip('"')
    print('published', h)
    receipt(h)


if __name__ == '__main__':
    main()
