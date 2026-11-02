#!/usr/bin/env python3 -I
"""P3 (throwaway): rebuild the agent's evidence files from its CVM log.

The agent prints every evidence file (app-compose.json, vm_config.json, tdx_quote.hex, event_log.json, info.json,
registration.json, transcripts) as lines `EVIDENCE <name> <sha256> <i>/<n> <base64 chunk>`. This script reassembles
them under OUT and checks each file's SHA-256 against the one the agent printed. The log comes from
`phala logs --cvm-id <id> --stderr -t` (public logs are on; the agent never prints the NEAR AI key).

  p3_evidence.py LOGFILE OUT
"""
import base64, hashlib, os, re, sys

PAT = re.compile(r'EVIDENCE (\S+) ([0-9a-f]{64}) (\d+)/(\d+) (\S+)\s*$')


def main():
    log, out = sys.argv[1], sys.argv[2]
    parts = {}
    for line in open(log, encoding='utf-8', errors='replace'):
        m = PAT.search(line)
        if m:
            name, h, i, n, chunk = m.group(1), m.group(2), int(m.group(3)), int(m.group(4)), m.group(5)
            parts.setdefault((name, h, n), {})[i] = chunk
    bad = 0
    for (name, h, n), chunks in sorted(parts.items()):
        if '..' in name.split('/') or name.startswith('/'):
            print('SKIP unsafe name', name); bad += 1; continue
        if sorted(chunks) != list(range(1, n + 1)):
            print('INCOMPLETE', name, 'chunks', len(chunks), 'of', n); bad += 1; continue
        data = base64.b64decode(''.join(chunks[i] for i in range(1, n + 1)))
        ok = hashlib.sha256(data).hexdigest() == h
        path = os.path.join(out, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        open(path, 'wb').write(data)
        print('%-8s %s %d bytes sha256 %s' % ('OK' if ok else 'MISMATCH', name, len(data), h))
        bad += not ok
    sys.exit(1 if bad else 0)


if __name__ == '__main__':
    main()
