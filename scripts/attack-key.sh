#!/usr/bin/env bash
# attack-key.sh: try the agent's function-call key outside its permission and outside the vault's policy, and
# print the exact error each attempt gets. NEAR testnet (or a local sandbox) only; never mainnet.
#
#   scripts/attack-key.sh <vault.testnet> [--key-file path] [--rpc URL] [--market live|backup|demo|0x<id>]
#                         [--other-receiver account]
#
# The key is a function-call key on the vault's own account with the permission `register_agent` adds
# (vault/src/lib.rs: receiver the vault, methods reduce, migrate, exit, allowance 2 NEAR). Three attempts, each
# signed with that key by the near CLI offline and sent with the RPC's send_tx, so the RPC's own error is printed:
#   (a) MethodNameMismatch   `withdraw` on the vault (an owner method name, not in the key's list): refused by the
#                            protocol at submission; nothing lands, no gas.
#   (b) ReceiverMismatch     `reduce` on another account (--other-receiver, default the Chain Signatures contract
#                            v1.signer-prod.testnet): refused by the protocol at submission; nothing lands.
#   (c) slice maximum        `reduce` on the vault with amount = the market's slice_max + 1: lands, and the vault
#                            panics "amount above the slice maximum" (gas from the key's allowance, about 0.0003
#                            NEAR). It reaches that check only with the registered agent key, after the cooldown and
#                            on a market whose corridor is set; otherwise the vault's earlier panic is printed.
# Output: one line per attempt, `attack <name>: <error as returned> PASS|FAIL`; exit 1 if any FAIL.
#
# --key-file        near-credentials JSON {"account_id", "public_key", "private_key"} of the key (default
#                   ~/.near-credentials/testnet/<vault>.json). Only its path is handed to the near CLI; the
#                   script reads the public key and never prints the private key.
# --rpc             NEAR JSON-RPC (default $NEAR_RPC or https://rpc.testnet.near.org); a sandbox RPC works too.
# --market          market for (c), a name from deployments/testnet.json or a 0x id (default live).
# --other-receiver  receiver for (b) (default v1.signer-prod.testnet).
#
# Exit codes: 0 every attempt PASS; 1 a FAIL or an error; 2 usage.
# Needs: bash (3.2 or later), near (near-cli-rs 0.30), curl, jq, python3.
set -euo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$HERE/.." && pwd)
DEP="$ROOT/deployments/testnet.json"
RPC=${NEAR_RPC:-https://rpc.testnet.near.org}
KEYFILE=""
MARKET=live
OTHER=v1.signer-prod.testnet
VAULT=""

usage() { sed -n '2,30p' "$0" | sed 's/^# \{0,1\}//'; exit 2; }
while [ $# -gt 0 ]; do
  case "$1" in
    --key-file) KEYFILE="$2"; shift 2 ;;
    --rpc) RPC="$2"; shift 2 ;;
    --market) MARKET="$2"; shift 2 ;;
    --other-receiver) OTHER="$2"; shift 2 ;;
    -h|--help) usage ;;
    -*) echo "unknown option: $1" >&2; usage ;;
    *) [ -z "$VAULT" ] && VAULT="$1" && shift || { echo "unexpected argument: $1" >&2; usage; } ;;
  esac
done
[ -n "$VAULT" ] || usage
[ -n "$KEYFILE" ] || KEYFILE="$HOME/.near-credentials/testnet/$VAULT.json"
die() { echo "error: $*" >&2; exit 1; }
info() { echo "  $*"; }
[ -f "$KEYFILE" ] || die "key file not found: $KEYFILE"
PUB=$(jq -r .public_key "$KEYFILE")
case "$PUB" in ed25519:*) ;; *) die "no ed25519 public_key in $KEYFILE" ;; esac

rpc() { curl -s -m 60 -X POST -H 'content-type: application/json' --data "{\"jsonrpc\":\"2.0\",\"id\":\"attack-key\",\"method\":\"$1\",\"params\":$2}" "$RPC"; }

CHAIN=$(rpc status '[]' | jq -r .result.chain_id)
case "$CHAIN" in
  mainnet) die "chain mainnet: testnet only" ;;
  testnet|test-chain-*|localnet|sandbox) ;;
  null|"") die "RPC not reachable: $RPC" ;;
  *) die "unexpected chain id $CHAIN" ;;
esac

case "$MARKET" in
  0x*) MID="$MARKET" ;;
  *) MID=$(jq -r ".base.markets.$MARKET.id" "$DEP"); [ "$MID" != null ] || die "market $MARKET not in $DEP" ;;
esac
MID=$(echo "$MID" | tr 'A-F' 'a-f')

# ------------------------------------------------------------------ the key and the vault
AK=$(rpc query "{\"request_type\":\"view_access_key\",\"finality\":\"final\",\"account_id\":\"$VAULT\",\"public_key\":\"$PUB\"}")
echo "$AK" | jq -e .result.nonce >/dev/null || die "key $PUB is not on $VAULT: $(echo "$AK" | jq -c '.error.cause // .result.error // .error')"
echo "attack-key: vault $VAULT, key $PUB, chain $CHAIN, RPC $RPC"
info "key permission: $(echo "$AK" | jq -c .result.permission)"
STATE=$(rpc query "{\"request_type\":\"call_function\",\"finality\":\"final\",\"account_id\":\"$VAULT\",\"method_name\":\"get_state\",\"args_base64\":\"e30=\"}" \
  | jq -r '.result.result | implode' 2>/dev/null || true)
if [ -n "$STATE" ] && echo "$STATE" | jq -e . >/dev/null 2>&1; then
  AGENT=$(echo "$STATE" | jq -r '.agent.public_key // "none"')
  SLICE=$(echo "$STATE" | jq -r --arg m "$MID" '[.markets[] | select((.id | ascii_downcase) == $m)][0].slice_max // empty')
  CSET=$(echo "$STATE" | jq -r --arg m "$MID" '[.markets[] | select((.id | ascii_downcase) == $m)][0].corridor_set // empty')
  info "vault get_state: registered agent $AGENT (this key: $([ "$AGENT" = "$PUB" ] && echo yes || echo no)); market $MID slice_max ${SLICE:-?}, corridor_set ${CSET:-?}"
else
  die "get_state on $VAULT failed"
fi
[ -n "$SLICE" ] || die "market $MID not in the vault's policy"

# ------------------------------------------------------------------ sign with the key (near CLI, offline) and send
send() { # receiver method json-args gas -> prints the RPC response
  local receiver=$1 method=$2 args=$3 gas=$4 nonce b bh bht out b64
  nonce=$(rpc query "{\"request_type\":\"view_access_key\",\"finality\":\"final\",\"account_id\":\"$VAULT\",\"public_key\":\"$PUB\"}" | jq -r .result.nonce)
  b=$(rpc block '{"finality":"final"}'); bh=$(echo "$b" | jq -r .result.header.hash); bht=$(echo "$b" | jq -r .result.header.height)
  # --offline: the CLI only signs (network-config names no RPC here); the RPC above gives nonce and block hash
  out=$(near --offline contract call-function as-transaction "$receiver" "$method" json-args "$args" \
    prepaid-gas "$gas" attached-deposit '0 NEAR' sign-as "$VAULT" network-config testnet \
    sign-with-access-key-file "$KEYFILE" --nonce $((nonce + 1)) --block-hash "$bh" --block-height "$bht" display < /dev/null 2>&1) || true
  b64=$(echo "$out" | sed 's/\x1b\[[0-9;]*m//g' | grep -A1 'serialized as base64' | tail -1 | tr -d ' ')
  [ -n "$b64" ] || die "near CLI did not sign: $(echo "$out" | sed 's/\x1b\[[0-9;]*m//g' | grep -i error | head -3)"
  rpc send_tx "{\"signed_tx_base64\":\"$b64\",\"wait_until\":\"EXECUTED_OPTIMISTIC\"}"
}
# The error as the RPC returns it: a refused transaction's error data, or a landed call's failure and hash.
error_of() {
  jq -c 'if .error then (.error.data // .error.cause // .error)
         elif (.result.status.Failure) then {Failure: .result.status.Failure, tx: .result.transaction.hash}
         else {ok: .result.status, tx: .result.transaction.hash} end'
}
PASSES=0; FAILS=0
report() { # name needle error
  local v=FAIL; case "$3" in *"$2"*) v=PASS ;; esac
  [ $v = PASS ] && PASSES=$((PASSES + 1)) || FAILS=$((FAILS + 1))
  echo "attack $1: $3 $v"
}

TX='{"nonce":0,"gas_limit":500000,"max_fee_per_gas":11000000,"max_priority_fee_per_gas":1000000}'

echo "-- (a) withdraw on the vault, signed with the key"
E=$(send "$VAULT" withdraw '{"token":"0x4200000000000000000000000000000000000006","amount":"1","destination":"0x3333333333333333333333333333333333333333"}' '30 Tgas' | error_of)
report MethodNameMismatch MethodNameMismatch "$E"

echo "-- (b) reduce on $OTHER (another receiver), signed with the key"
E=$(send "$OTHER" reduce "{\"market\":\"$MID\",\"amount\":\"1\",\"tx\":$TX}" '30 Tgas' | error_of)
report ReceiverMismatch ReceiverMismatch "$E"

echo "-- (c) reduce on the vault, market $MID, amount slice_max + 1 = $(python3 -I -c "print($SLICE + 1)")"
E=$(send "$VAULT" reduce "{\"market\":\"$MID\",\"amount\":\"$(python3 -I -c "print($SLICE + 1)")\",\"tx\":$TX}" '50 Tgas' | error_of)
report SliceMax "amount above the slice maximum" "$E"

echo "result: $PASSES PASS, $FAILS FAIL (vault $VAULT, key $PUB)"
[ $FAILS = 0 ]
