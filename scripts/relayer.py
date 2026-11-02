#!/usr/bin/env python3 -I
"""P2 relayer (throwaway prototype): carries owner messages to the vault on NEAR and the vault's signed
transactions to Base Sepolia. It holds no authority: the vault decides what is signed, the relayer only pays
NEAR gas and broadcasts. Testnet only.

  relayer.py derive --account ACCOUNT [--path PATH]
      Base address the signer (v1.signer-prod.testnet) derives for (ACCOUNT, PATH): derived_public_key view,
      then keccak of the uncompressed key (cast keccak).
  relayer.py params --from ADDR --to ADDR --data HEX [--nonce N] [--rpc URL]
      One TxParams set: nonce (pending count unless given), gas_limit = eth_estimateGas x 1.3 (P1 finding),
      max_fee_per_gas = 2 x base fee + priority, capped at the vault's 2 gwei bound.
  relayer.py submit --vault ID --signed FILE --txs JSON --out FILE [--sender ACCOUNT]
      Calls owner_call(message, signature, txs) on the vault, signed by SENDER's key in the near CLI keychain,
      300 Tgas. Writes the NEAR tx hash, gas summed over every receipt, and the returned signed transactions.
  relayer.py near-gas TXHASH [--sender ACCOUNT]
      Per-receipt gas of a NEAR transaction (as F's txgas.py).
  relayer.py broadcast FILE|0xRAW [--index I] [--rpc URL] [--log FILE]
      eth_sendRawTransaction; checks the returned hash equals the vault's hash-to-be; waits for the receipt;
      appends {hash, status, block, gasUsed, effectiveGasPrice, l1Fee, from, to} to the log.
"""
import argparse, base64, json, os, re, subprocess, sys, time, urllib.request

HOME = os.path.expanduser('~')
CAST = os.path.join(HOME, '.foundry', 'bin', 'cast')
NEAR_RPC = 'https://rpc.testnet.near.org'
BASE_RPC = 'https://base-sepolia-rpc.publicnode.com'
SIGNER = 'v1.signer-prod.testnet'
RELAYER = 'prime-measure-1792736012.testnet'
MAX_FEE_BOUND = 2_000_000_000        # the vault's MAX_FEE_PER_GAS_MAX
GAS_LIMIT_BOUND = 1_000_000          # the vault's GAS_LIMIT_MAX
B58 = '123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz'


def rpc(url, method, params):
    body = json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params}).encode()
    req = urllib.request.Request(url, body, {'Content-Type': 'application/json', 'User-Agent': 'curl/8'})
    r = json.load(urllib.request.urlopen(req, timeout=120))
    if 'error' in r:
        raise RuntimeError('%s %s: %s' % (method, url, json.dumps(r['error'])[:600]))
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
    cmd = ['near', 'contract', 'call-function', 'as-transaction', a.vault, 'owner_call', 'file-args', tmp,
           'prepaid-gas', '300 Tgas', 'attached-deposit', '0 NEAR', 'sign-as', a.sender,
           'network-config', 'testnet', 'sign-with-keychain', 'send']
    t0 = time.time()
    p = subprocess.run(cmd, capture_output=True, text=True)
    wall = time.time() - t0
    out = p.stdout + p.stderr
    m = re.search(r'Transaction ID:\s*([1-9A-HJ-NP-Za-km-z]{43,44})', out)
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


def cmd_broadcast(a):
    if a.target.startswith('0x'):
        items = [{'raw': a.target, 'hash': None}]
    else:
        rec = json.load(open(a.target))
        items = rec['signed'] if isinstance(rec, dict) else rec
        if a.index is not None:
            items = [items[a.index]]
    log = json.load(open(a.log)) if a.log and os.path.exists(a.log) else []
    for it in items:
        h = rpc(a.rpc, 'eth_sendRawTransaction', [it['raw']])
        if it.get('hash') and h.lower() != it['hash'].lower():
            raise SystemExit('RPC hash %s differs from the vault hash-to-be %s' % (h, it['hash']))
        print('sent', h)
        for _ in range(120):
            rc = rpc(a.rpc, 'eth_getTransactionReceipt', [h])
            if rc:
                break
            time.sleep(1)
        else:
            raise SystemExit('no receipt for %s after 120 s' % h)
        row = {'hash': h, 'status': int(rc['status'], 16), 'block': int(rc['blockNumber'], 16), 'gasUsed': int(rc['gasUsed'], 16),
               'effectiveGasPrice': int(rc.get('effectiveGasPrice', '0x0'), 16), 'l1Fee': int(rc.get('l1Fee') or '0x0', 16),
               'from': rc['from'], 'to': rc['to'], 'rpc': a.rpc}
        print(json.dumps(row))
        log.append(row)
        if a.log:
            json.dump(log, open(a.log, 'w'), indent=1)
        if row['status'] != 1:
            raise SystemExit('reverted')


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest='cmd', required=True)
    p = sp.add_parser('derive'); p.add_argument('--account', required=True); p.add_argument('--path', default='prime-base'); p.set_defaults(f=cmd_derive)
    p = sp.add_parser('params'); p.add_argument('--from', required=True); p.add_argument('--to', required=True)
    p.add_argument('--data', required=True); p.add_argument('--nonce', type=int); p.add_argument('--rpc', default=BASE_RPC); p.set_defaults(f=cmd_params)
    p = sp.add_parser('submit'); p.add_argument('--vault', required=True); p.add_argument('--signed', required=True)
    p.add_argument('--txs', required=True); p.add_argument('--out', required=True); p.add_argument('--sender', default=RELAYER); p.set_defaults(f=cmd_submit)
    p = sp.add_parser('near-gas'); p.add_argument('tx'); p.add_argument('--sender', default=RELAYER); p.set_defaults(f=cmd_near_gas)
    p = sp.add_parser('broadcast'); p.add_argument('target'); p.add_argument('--index', type=int)
    p.add_argument('--rpc', default=BASE_RPC); p.add_argument('--log'); p.set_defaults(f=cmd_broadcast)
    a = ap.parse_args()
    a.f(a)


if __name__ == '__main__':
    main()
