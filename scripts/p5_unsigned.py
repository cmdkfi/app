#!/usr/bin/env python3 -I
"""P5 (throwaway prototype): print the unsigned EIP-1559 signing payload (0x02 || rlp([...]), empty access list,
value 0) that the vault builds, the same layout as prototype/agent/src/chain.rs `unsigned_1559`, for the vault's
read-only `check_transaction` view. No key, no network.

  p5_unsigned.py TO DATA_HEX [--nonce N] [--gas G] [--max-fee F] [--prio P]
"""
import sys

CHAIN_ID = 84532


def rlp_bytes(b):
    if len(b) == 1 and b[0] < 0x80:
        return b
    return rlp_len(len(b), 0x80) + b


def rlp_len(n, off):
    if n < 56:
        return bytes([off + n])
    lb = n.to_bytes((n.bit_length() + 7) // 8, 'big')
    return bytes([off + 55 + len(lb)]) + lb


def rlp_uint(x):
    return rlp_bytes(x.to_bytes((x.bit_length() + 7) // 8, 'big') if x else b'')


def opt(argv, name, default):
    if name in argv:
        i = argv.index(name)
        v = int(argv[i + 1])
        del argv[i:i + 2]
        return v
    return default


def main():
    argv = sys.argv[1:]
    nonce = opt(argv, '--nonce', 11)
    gas = opt(argv, '--gas', 500000)
    fee = opt(argv, '--max-fee', 11000000)
    prio = opt(argv, '--prio', 1000000)
    if len(argv) != 2:
        raise SystemExit(__doc__)
    to, data = bytes.fromhex(argv[0][2:]), bytes.fromhex(argv[1][2:] if argv[1].startswith('0x') else argv[1])
    body = (rlp_uint(CHAIN_ID) + rlp_uint(nonce) + rlp_uint(prio) + rlp_uint(fee) + rlp_uint(gas) + rlp_bytes(to)
            + rlp_uint(0) + rlp_bytes(data) + b'\xc0')
    print('0x' + (b'\x02' + rlp_len(len(body), 0xc0) + body).hex())


if __name__ == '__main__':
    main()
