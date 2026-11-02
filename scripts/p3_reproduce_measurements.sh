#!/bin/bash
# P3 (throwaway): measurement H's reproduction, parameterised for the production OS image the P3 CVM runs
# (dstack-0.5.12, not dstack-dev-0.5.12). Rebuilds MRTD and RTMR0-2 with dstack-mr from the public release tarball,
# replays RTMR3 from the event log, and checks os_image_hash = sha256(sha256sum.txt) against the event log.
# Reuses H's Dockerfile.dstack-mr and verify_measurements.py (research_notes/Measurements/H_artifacts/, read only).
#
#   p3_reproduce_measurements.sh <work-dir> <evidence-dir> [image-name] [vcpu] [memory]
# <evidence-dir> holds tdx_quote.hex, event_log.json, vm_config.json (as saved by the agent).
set -euo pipefail
WORK=$(mkdir -p "$1" && cd "$1" && pwd); EV=$(cd "$2" && pwd)
IMAGE=${3:-dstack-0.5.12}; VCPU=${4:-1}; MEM=${5:-2G}
H=$(cd "$(dirname "$0")/../../research_notes/Measurements/H_artifacts" && pwd)
URL=https://github.com/Dstack-TEE/meta-dstack/releases/download/v0.5.12/${IMAGE}.tar.gz
EXPECTED=$(python3 -I -c "import json,sys; print(next(e['event_payload'] for e in json.load(open(sys.argv[1])) if e['imr']==3 and e['event']=='os-image-hash'))" "$EV/event_log.json")
echo "vm_config: $(cat "$EV/vm_config.json")"
echo "os-image-hash in the event log: $EXPECTED"

mkdir -p "$WORK/image"
[ -f "$WORK/image/${IMAGE}.tar.gz" ] || curl -sL -o "$WORK/image/${IMAGE}.tar.gz" "$URL"
echo "tarball $(wc -c < "$WORK/image/${IMAGE}.tar.gz") bytes sha256 $(shasum -a 256 "$WORK/image/${IMAGE}.tar.gz" | cut -d' ' -f1)"
tar xzf "$WORK/image/${IMAGE}.tar.gz" -C "$WORK/image"
OSH=$(shasum -a 256 "$WORK/image/$IMAGE/sha256sum.txt" | cut -d' ' -f1)
[ "$OSH" = "$EXPECTED" ] && echo "os_image_hash MATCH ($OSH = sha256(sha256sum.txt))" || { echo "os_image_hash MISMATCH ($OSH)"; exit 1; }
python3 -I -c "import json,sys; m=json.load(open(sys.argv[1])); print('metadata: version', m.get('version'), 'is_dev', m.get('is_dev'), 'ovmf_variant', m.get('ovmf_variant'), 'git_revision', m.get('git_revision'))" "$WORK/image/$IMAGE/metadata.json"

if ! docker image inspect prime-dstack-mr:0.5.12 >/dev/null 2>&1; then
  [ -d "$WORK/dstack-src" ] || git clone --quiet --depth 1 --branch v0.5.12 https://github.com/Dstack-TEE/dstack.git "$WORK/dstack-src"
  printf 'target\n.git\n' > "$WORK/dstack-src/.dockerignore"
  docker build --quiet --platform linux/amd64 -f "$H/Dockerfile.dstack-mr" -t prime-dstack-mr:0.5.12 "$WORK/dstack-src" >/dev/null
fi
# H's flags (QEMU 8.x profile on Phala's tdx.small host)
docker run --rm --platform linux/amd64 -v "$WORK/image/$IMAGE:/img:ro" prime-dstack-mr:0.5.12 \
  measure -c "$VCPU" -m "$MEM" --two-pass-add-pages true --pic true --json /img/metadata.json > "$WORK/dstack-mr.json"
python3 -I "$H/verify_measurements.py" "$EV/tdx_quote.hex" "$WORK/dstack-mr.json" "$EV/event_log.json"
