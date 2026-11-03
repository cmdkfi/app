#!/usr/bin/env bash
# reproduce-hash.sh: build the cmdk vault wasm from this checkout and print its size, SHA-256 (hex) and the
# base58 of the SHA-256, which is the code hash NEAR shows for a Global Contract ("Global Contract (by Hash: ...)").
# Read-only towards the network: it builds locally and, with --vault, reads one account over JSON-RPC.
#
#   scripts/reproduce-hash.sh [--reproducible] [--vault <account.testnet>] [--near-rpc URL]
#
# default         cargo near build non-reproducible-wasm   (host toolchain; the hash depends on rustc, cargo-near,
#                                                            OS and paths, so it may differ between machines)
# --reproducible  cargo near build reproducible-wasm       (pinned Docker image from Cargo.toml's
#                                                            [package.metadata.near.reproducible_build], built from a
#                                                            clean clone of the last commit; the only build expected to
#                                                            match bit for bit across machines)
# --vault         also read the account's published hash (view_account: global_contract_hash) and print MATCH or
#                 MISMATCH
# --near-rpc      NEAR testnet JSON-RPC (default $NEAR_RPC or https://rpc.testnet.near.org)
#
# Exit codes: 0 built (and MATCH when --vault); 1 error; 2 usage; 3 MISMATCH.
# Needs: cargo-near (cargo near), rustup, python3; Docker for --reproducible; curl and jq for --vault.
set -euo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$HERE/.." && pwd)
CRATE="$ROOT/vault"
NEAR_RPC=${NEAR_RPC:-https://rpc.testnet.near.org}

die() { echo "reproduce-hash.sh: $*" >&2; exit 1; }
usage() { sed -n '2,17p' "$0" | sed 's/^# \{0,1\}//' >&2; exit 2; }

MODE=non-reproducible-wasm; VAULT=""
while [ $# -gt 0 ]; do
  case "$1" in
    --reproducible) MODE=reproducible-wasm; shift ;;
    --vault) [ $# -ge 2 ] || usage; VAULT=$2; shift 2 ;;
    --near-rpc) [ $# -ge 2 ] || usage; NEAR_RPC=$2; shift 2 ;;
    -h|--help) usage ;;
    *) echo "reproduce-hash.sh: unknown argument $1" >&2; usage ;;
  esac
done
command -v cargo >/dev/null 2>&1 || die "cargo not found (install Rust: https://rustup.rs)"
cargo near --version >/dev/null 2>&1 || die "cargo-near not found (https://github.com/near/cargo-near#installation)"
command -v python3 >/dev/null 2>&1 || die "python3 not found"
if [ -n "$VAULT" ]; then
  for t in curl jq; do command -v "$t" >/dev/null 2>&1 || die "$t not found (needed for --vault)"; done
fi
[ -f "$CRATE/Cargo.toml" ] || die "vault crate not found at $CRATE"

COMMIT=$(git -C "$ROOT" rev-parse --short=12 HEAD 2>/dev/null || echo "not a git checkout")
DIRTY=$(git -C "$ROOT" status --porcelain 2>/dev/null | wc -l | tr -d ' ')

if [ "$MODE" = reproducible-wasm ]; then
  MISSING=""
  docker info >/dev/null 2>&1 || MISSING="$MISSING
  - a running Docker daemon (docker info failed)"
  grep -q '^\[package\.metadata\.near\.reproducible_build\]' "$CRATE/Cargo.toml" || MISSING="$MISSING
  - [package.metadata.near.reproducible_build] in vault/Cargo.toml with image, image_digest and
    container_build_command (cargo near new's template shows the keys)"
  grep -Eq '^repository *= *"https://' "$CRATE/Cargo.toml" || MISSING="$MISSING
  - package.repository in vault/Cargo.toml, an https URL of a cloneable repository"
  [ "$DIRTY" = "0" ] || MISSING="$MISSING
  - a clean git working tree ($DIRTY uncommitted or untracked paths; cargo near builds a clone of the last commit)"
  [ -z "$MISSING" ] || die "the reproducible build cannot run here; missing:$MISSING"
fi

T=${TMPDIR:-/tmp}; LOG=$(mktemp "${T%/}/cmdk-vault-build.XXXXXX")
echo "building: cargo near build $MODE (in vault/; log $LOG)" >&2
if ! (cd "$CRATE" && cargo near build "$MODE") >"$LOG" 2>&1; then
  tail -30 "$LOG" >&2
  die "build failed; full log: $LOG"
fi
# cargo near prints "Binary: <path>" and "SHA-256 checksum bs58: <hash>" (strip colour codes first)
CLEAN=$(sed $'s/\x1b\\[[0-9;]*m//g' "$LOG")
WASM=$(printf '%s\n' "$CLEAN" | sed -n 's/.*Binary: *\(.*\.wasm\) *$/\1/p' | tail -1)
[ -n "$WASM" ] || WASM="$CRATE/target/near/cmdk_vault.wasm"
[ -f "$WASM" ] || die "wasm not found at $WASM; full log: $LOG"
CN_BS58=$(printf '%s\n' "$CLEAN" | sed -n 's/.*checksum bs58: *\([1-9A-HJ-NP-Za-km-z]*\).*/\1/p' | tail -1)

SUMS=$(python3 -I -c '
import hashlib, sys
d = open(sys.argv[1], "rb").read()
h = hashlib.sha256(d).digest()
A = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
n = int.from_bytes(h, "big"); s = ""
while n:
    n, r = divmod(n, 58); s = A[r] + s
s = "1" * (len(h) - len(h.lstrip(b"\0"))) + s
print(len(d), h.hex(), s)
' "$WASM") || die "could not hash $WASM"
read -r SIZE HEX BS58 <<< "$SUMS"

RUSTC=$(cd "$CRATE" && rustc --version 2>/dev/null || echo "?")
echo "build            cargo near build $MODE"
if [ "$MODE" = reproducible-wasm ]; then
  echo "                 (pinned Docker image, clean clone of commit $COMMIT: expected to match bit for bit on any machine)"
else
  echo "                 (host toolchain, NOT reproducible: another machine, rustc or cargo-near version may give a"
  echo "                 different hash; only --reproducible can be expected to match bit for bit)"
fi
echo "commit           $COMMIT ($DIRTY uncommitted or untracked paths in the checkout)"
echo "toolchain        $RUSTC; $(cargo near --version 2>/dev/null)"
echo "wasm             $WASM"
echo "size             $SIZE bytes"
echo "sha256 (hex)     $HEX"
echo "sha256 (base58)  $BS58   <- NEAR's Global Contract hash"
if [ -n "$CN_BS58" ] && [ "$CN_BS58" != "$BS58" ]; then
  echo "warning: cargo near printed bs58 $CN_BS58, which differs from the file's hash above" >&2
fi

[ -n "$VAULT" ] || exit 0
RESP=$(curl -sS --max-time 30 "$NEAR_RPC" -H 'Content-Type: application/json' -d \
  '{"jsonrpc":"2.0","id":"cmdk","method":"query","params":{"request_type":"view_account","finality":"final","account_id":"'"$VAULT"'"}}') \
  || die "NEAR RPC $NEAR_RPC not reachable"
ERR=$(printf '%s' "$RESP" | jq -r '.error | select(.) | (.cause.name // .name // "error") + " " + (.data // .message // "" | tostring)')
case "$ERR" in
  "") ;;
  UNKNOWN_ACCOUNT*) die "account '$VAULT' not found on NEAR testnet ($NEAR_RPC)" ;;
  *) die "view_account '$VAULT' failed: $ERR" ;;
esac
BH=$(printf '%s' "$RESP" | jq -r '.result.block_height')
GHASH=$(printf '%s' "$RESP" | jq -r '.result.global_contract_hash // empty')
GACC=$(printf '%s' "$RESP" | jq -r '.result.global_contract_account_id // empty')
CHASH=$(printf '%s' "$RESP" | jq -r '.result.code_hash // empty')
if [ -n "$GHASH" ]; then
  PUB=$GHASH; HOW="global_contract_hash (Global Contract by hash)"
elif [ -n "$GACC" ]; then
  die "'$VAULT' uses the Global Contract of account $GACC (by account id, not by hash): its code can change; no fixed hash to compare"
elif [ -n "$CHASH" ] && [ "$CHASH" != "11111111111111111111111111111111" ]; then
  PUB=$CHASH; HOW="code_hash (contract deployed directly to the account, not a Global Contract)"
else
  die "'$VAULT' has no contract"
fi
echo "vault            $VAULT (view_account, NEAR testnet block $BH)"
echo "published        $PUB   <- $HOW"
if [ "$PUB" = "$BS58" ]; then
  echo "result           MATCH"
else
  echo "result           MISMATCH (the published code is not this build of this checkout)"
  exit 3
fi
