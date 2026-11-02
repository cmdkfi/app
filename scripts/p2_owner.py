#!/usr/bin/env python3 -I
"""P2 owner side (throwaway prototype): build an owner message for the vault and sign it ERC-191 with the test
wallet (`cast wallet sign`). The key is read from ~/.config/prime/base_sepolia_eoa.json and handed only to the
cast subprocess (as P1's scripts do); it is never printed or written. Testnet only.

  p2_owner.py --vault ID --method METHOD --args JSON --out FILE [--nonce N] [--ttl SECONDS]

The nonce defaults to the vault's owner_nonce + 1 (get_state view); expiry = now + TTL (default 600 s).
Writes {"message": ..., "signature": ...}; submit it with relayer.py submit.
"""
import argparse, json, os, subprocess, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import relayer  # noqa: E402  (our own file)


def key():
    with open(os.path.expanduser('~/.config/prime/base_sepolia_eoa.json')) as f:
        d = json.load(f)['data']
        d = d[0] if isinstance(d, list) else d
    return d['private_key'], d['address']


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--vault', required=True); ap.add_argument('--method', required=True)
    ap.add_argument('--args', required=True); ap.add_argument('--out', required=True)
    ap.add_argument('--nonce', type=int); ap.add_argument('--ttl', type=int, default=600)
    a = ap.parse_args()
    st = relayer.near_view(a.vault, 'get_state', {})
    k, addr = key()
    if addr.lower() != st['owner'].lower():
        raise SystemExit('the test wallet %s is not the vault owner %s' % (addr, st['owner']))
    nonce = a.nonce if a.nonce is not None else st['owner_nonce'] + 1
    msg = json.dumps({'protocol': 'prime-vault/1', 'network': 'testnet', 'vault': a.vault, 'method': a.method,
                      'args': json.loads(a.args), 'nonce': nonce, 'expiry': int(time.time()) + a.ttl}, separators=(',', ':'))
    # no check=True: CalledProcessError would print the command line, key included
    p = subprocess.run([relayer.CAST, 'wallet', 'sign', '--private-key', k, msg], capture_output=True, text=True)
    if p.returncode != 0:
        raise SystemExit('cast wallet sign failed: %s' % p.stderr.strip()[-300:])
    sig = p.stdout.strip()
    json.dump({'message': msg, 'signature': sig}, open(a.out, 'w'), indent=1)
    print(msg)
    print('signature', sig)


if __name__ == '__main__':
    main()
