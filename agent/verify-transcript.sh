#!/usr/bin/env bash
# Re-checks the judge call recorded in one agent transcript, offline, from the transcript alone:
#   1. sha256(request bytes)  = judge.call.request_sha256
#   2. sha256(response bytes) = judge.call.response_sha256
#   3. signature_kind is provider_tee (the only kind that binds the response to the model-serving enclave)
#   4. the signed text = "<model>:<sha256(request)>:<sha256(response)>", recomputed from the bytes here
#   5. the EIP-191 signature over that text recovers to the attested signing address (`cast wallet verify`)
#   6. the attested address is the one the model's attestation bound to our nonce (judge.attestation.signing_address)
#      and the attestation passed (TCB status recorded when the transcript carries it)
# Usage: ./verify-transcript.sh <transcript.json>
# Needs: jq, base64, sha256sum or shasum, and Foundry's cast (CAST=... or ~/.foundry/bin/cast or cast on PATH).
# Exit 0 when every check passes, 1 when one fails, 3 when the transcript holds no judge call (nothing to verify).
set -euo pipefail

f="${1:?usage: $0 <transcript.json>}"
[ -f "$f" ] || { echo "no such file: $f" >&2; exit 2; }
CAST="${CAST:-}"
if [ -z "$CAST" ]; then
  if [ -x "$HOME/.foundry/bin/cast" ]; then CAST="$HOME/.foundry/bin/cast"; else CAST="$(command -v cast || true)"; fi
fi
[ -n "$CAST" ] || { echo "cast not found (install Foundry or set CAST=)" >&2; exit 2; }
command -v jq >/dev/null || { echo "jq not found" >&2; exit 2; }
if command -v sha256sum >/dev/null; then sha() { sha256sum | cut -d' ' -f1; }; else sha() { shasum -a 256 | cut -d' ' -f1; }; fi

j() { jq -r "$1 // empty" "$f"; }
if [ -z "$(j .judge.call.request_b64)" ]; then
  echo "NO-CALL judge outcome: $(j .judge.outcome)"
  exit 3
fi

fails=0
check() { # name ok detail
  if [ "$2" = 1 ]; then echo "PASS $1${3:+  $3}"; else echo "FAIL $1${3:+  $3}"; fails=$((fails + 1)); fi
}

model="$(j .judge.call.model)"
req_rec="$(j .judge.call.request_sha256)"
resp_rec="$(j .judge.call.response_sha256)"
req="$(jq -r .judge.call.request_b64 "$f" | base64 --decode | sha)"
resp="$(jq -r .judge.call.response_b64 "$f" | base64 --decode | sha)"
check request_sha256 "$([ "$req" = "$req_rec" ] && echo 1 || echo 0)" "$req"
check response_sha256 "$([ "$resp" = "$resp_rec" ] && echo 1 || echo 0)" "$resp"

kind="$(j .judge.call.signature.signature_kind)"
check signature_kind_provider_tee "$([ "$kind" = provider_tee ] && echo 1 || echo 0)" "$kind"

text="$(j .judge.call.signature.text)"
expect="$model:$req:$resp"
check signed_text_is_model_and_both_hashes "$([ "$text" = "$expect" ] && echo 1 || echo 0)" "$expect"

attested="$(j .judge.call.signature.attested | tr 'A-F' 'a-f')"
sig="$(j .judge.call.signature.signature)"
if out="$("$CAST" wallet verify --address "$attested" "$expect" "$sig" 2>&1)"; then ok=1; else ok=0; fi
check eip191_signer_is_attested_address "$ok" "$attested ($(echo "$out" | tail -1))"

att_addr="$(j .judge.attestation.signing_address | tr 'A-F' 'a-f')"
att_ok="$(j .judge.attestation.ok)"
tcb="$(j .judge.attestation.dcap.status)"
check attestation_bound_this_address "$([ -n "$att_addr" ] && [ "$att_addr" = "$attested" ] && [ "$att_ok" = true ] && echo 1 || echo 0)" \
  "attestation ok=$att_ok address=$att_addr tcb_status=${tcb:-missing}"
check attestation_tcb_up_to_date "$([ "$tcb" = UpToDate ] && echo 1 || echo 0)" "${tcb:-missing}"

if [ "$fails" = 0 ]; then echo "ALL PASS $f"; exit 0; else echo "$fails FAILED $f"; exit 1; fi
