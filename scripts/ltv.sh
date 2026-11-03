#!/usr/bin/env bash
# ltv.sh: read one cmdk vault's Morpho Blue position on Base Sepolia and compute collateral value, debt and LTV
# with Morpho's own share math. Read-only: no keys, no transactions. Testnet only (NEAR testnet, Base Sepolia).
#
#   scripts/ltv.sh <vault.testnet> [--market live|backup|demo|0x<id>] [--rpc URL] [--near-rpc URL]
#   scripts/ltv.sh --address 0x<base address> [--market ...] [--rpc URL]
#
# <vault>: NEAR testnet account of the vault; its `get_state` view gives the Base address, the Morpho address and
#          the markets the vault allows (each with its executor).
# --address: skip the NEAR lookup and read this Base address (Morpho and executor from deployments/testnet.json).
# --market:  name from deployments/testnet.json or a 0x market id (default: live).
# --rpc:     Base Sepolia JSON-RPC (default $BASE_RPC or https://base-sepolia-rpc.publicnode.com).
# --near-rpc: NEAR testnet JSON-RPC (default $NEAR_RPC or https://rpc.testnet.near.org).
#
# Math (Morpho Blue SharesMathLib, MathLib, ConstantsLib; the executor's CorridorMath):
#   debt             = ceil(borrowShares * (totalBorrowAssets + 1) / (totalBorrowShares + 1e6))
#   collateral value = floor(collateral * oracle.price() / 1e36)            (loan-token units)
#   LTV (WAD)        = ceil(debt * 1e18 / collateral value)                  (0 if no debt)
# Morpho's market() totals are as of its lastUpdate. The "accrued" column adds the interest since then the way
# Morpho's _accrueInterest does: totalBorrowAssets += totalBorrowAssets * wTaylorCompounded(irm.borrowRateView, dt).
# The executor's ltvOf reads the stored totals, so it is compared with the "at lastUpdate" column.
#
# Exit codes: 0 read and printed; 1 error (vault, market or RPC); 2 usage.
# Needs: bash (3.2 or later), cast (Foundry), curl, jq, python3.
set -euo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$HERE/.." && pwd)
DEP="$ROOT/deployments/testnet.json"
BASE_RPC=${BASE_RPC:-https://base-sepolia-rpc.publicnode.com}
NEAR_RPC=${NEAR_RPC:-https://rpc.testnet.near.org}
CAST=${CAST:-$(command -v cast || echo "$HOME/.foundry/bin/cast")}

die() { echo "ltv.sh: $*" >&2; exit 1; }
usage() { sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//' >&2; exit 2; }
lower() { printf '%s' "$1" | tr 'A-F' 'a-f'; }

VAULT=""; ADDRESS=""; MARKET="live"
while [ $# -gt 0 ]; do
  case "$1" in
    --address) [ $# -ge 2 ] || usage; ADDRESS=$2; shift 2 ;;
    --market) [ $# -ge 2 ] || usage; MARKET=$2; shift 2 ;;
    --rpc) [ $# -ge 2 ] || usage; BASE_RPC=$2; shift 2 ;;
    --near-rpc) [ $# -ge 2 ] || usage; NEAR_RPC=$2; shift 2 ;;
    -h|--help) usage ;;
    -*) echo "ltv.sh: unknown option $1" >&2; usage ;;
    *) [ -z "$VAULT" ] || usage; VAULT=$1; shift ;;
  esac
done
[ -n "$VAULT" ] || [ -n "$ADDRESS" ] || usage
for t in curl jq python3; do command -v "$t" >/dev/null 2>&1 || die "$t not found"; done
[ -x "$CAST" ] || die "cast not found (install Foundry: https://getfoundry.sh); or set CAST=/path/to/cast"
[ -f "$DEP" ] || die "deployments file not found: $DEP"

MORPHO=$(jq -r '.base.morpho' "$DEP")
EXECUTOR=$(jq -r '.base.executor' "$DEP")

# ---- market id
case "$MARKET" in
  0x*) printf '%s' "$MARKET" | grep -Eq '^0x[0-9a-fA-F]{64}$' || die "market id must be 0x + 64 hex characters: $MARKET"
       MARKET_ID=$(lower "$MARKET"); MARKET_NAME=$(jq -r --arg id "$MARKET_ID" \
         '[.base.markets | to_entries[] | select((.value.id? // "") | ascii_downcase == $id) | .key] | first // "-"' "$DEP") ;;
  *) MARKET_ID=$(jq -r --arg m "$MARKET" '.base.markets[$m].id // empty' "$DEP")
     [ -n "$MARKET_ID" ] || die "unknown market name '$MARKET' (deployments/testnet.json has: $(jq -r '[.base.markets | to_entries[] | select(.value|type=="object") | .key] | join(", ")' "$DEP"))"
     MARKET_ID=$(lower "$MARKET_ID"); MARKET_NAME=$MARKET ;;
esac

# ---- NEAR: vault get_state
SOURCE="--address (no vault lookup; Morpho and executor from deployments/testnet.json)"
if [ -n "$ADDRESS" ]; then
  printf '%s' "$ADDRESS" | grep -Eq '^0x[0-9a-fA-F]{40}$' || die "--address must be 0x + 40 hex characters: $ADDRESS"
fi
if [ -n "$VAULT" ]; then
  RESP=$(curl -sS --max-time 30 "$NEAR_RPC" -H 'Content-Type: application/json' -d \
    '{"jsonrpc":"2.0","id":"cmdk","method":"query","params":{"request_type":"call_function","finality":"final","account_id":"'"$VAULT"'","method_name":"get_state","args_base64":"e30="}}') \
    || die "NEAR RPC $NEAR_RPC not reachable"
  ERR=$(printf '%s' "$RESP" | jq -r '(.error | select(.) | (.cause.name // .name // "") + ": " + ((.cause.info|tostring?) // .data // .message // "")) // (.result.error // empty)')
  if [ -n "$ERR" ]; then
    case "$ERR" in
      UNKNOWN_ACCOUNT*) die "vault account '$VAULT' not found on NEAR testnet ($NEAR_RPC)" ;;
      NO_CONTRACT_CODE*|*CodeDoesNotExist*) die "account '$VAULT' exists but has no contract (not a vault)" ;;
      *MethodNotFound*) die "account '$VAULT' has no get_state view (not a cmdk vault)" ;;
      *) die "get_state on '$VAULT' failed: $ERR" ;;
    esac
  fi
  STATE=$(printf '%s' "$RESP" | jq -c '.result.result' | python3 -I -c 'import sys,json; print(bytes(json.load(sys.stdin)).decode())') \
    || die "could not decode get_state of '$VAULT'"
  NEAR_BLOCK=$(printf '%s' "$RESP" | jq -r '.result.block_height')
  VAULT_ADDR=$(printf '%s' "$STATE" | jq -r '.base_address // empty')
  [ -n "$VAULT_ADDR" ] || die "get_state of '$VAULT' has no base_address"
  if [ -n "$ADDRESS" ] && [ "$(lower "$ADDRESS")" != "$(lower "$VAULT_ADDR")" ]; then
    die "--address $ADDRESS differs from the vault's base_address $VAULT_ADDR; pass one or the other"
  fi
  ADDRESS=$VAULT_ADDR
  VM=$(printf '%s' "$STATE" | jq -r '.morpho // empty')
  if [ -n "$VM" ] && [ "$(lower "$VM")" != "$(lower "$MORPHO")" ]; then
    echo "note: the vault's morpho $VM differs from deployments/testnet.json $MORPHO; using the vault's" >&2
  fi
  [ -n "$VM" ] && MORPHO=$VM
  EX=$(printf '%s' "$STATE" | jq -r --arg id "$MARKET_ID" '[.markets[] | select((.id|ascii_downcase) == $id) | .executor] | first // empty')
  [ -n "$EX" ] || die "market $MARKET_ID is not in the vault's allowlist; get_state.markets: $(printf '%s' "$STATE" | jq -r '[.markets[].id] | join(" ")')"
  EXECUTOR=$EX
  SOURCE="$VAULT (get_state, NEAR testnet block $NEAR_BLOCK)"
fi

# ---- Base Sepolia reads, all at one block
CHAIN=$("$CAST" chain-id --rpc-url "$BASE_RPC" 2>/dev/null) || die "Base RPC $BASE_RPC not reachable"
[ "$CHAIN" = "84532" ] || die "RPC $BASE_RPC is chain $CHAIN, not Base Sepolia (84532); testnet only"
BT=$("$CAST" block latest -f number,timestamp --rpc-url "$BASE_RPC") || die "could not read the latest block"
BLOCK=$(printf '%s\n' "$BT" | sed -n 1p); NOW=$(printf '%s\n' "$BT" | sed -n 2p)

# cast call at $BLOCK; prints the first word of each output line (drops cast's "[3e12]" annotations)
cc() { "$CAST" call --rpc-url "$BASE_RPC" --block "$BLOCK" "$@" | awk '{print $1}' | tr -d '"'; }

MP=$(cc "$MORPHO" 'idToMarketParams(bytes32)(address,address,address,address,uint256)' "$MARKET_ID") \
  || die "Morpho idToMarketParams failed at $MORPHO"
LOAN=$(echo "$MP" | sed -n 1p); COLL=$(echo "$MP" | sed -n 2p); ORACLE=$(echo "$MP" | sed -n 3p)
IRM=$(echo "$MP" | sed -n 4p); LLTV=$(echo "$MP" | sed -n 5p)
[ "$(lower "$LOAN")" != "0x0000000000000000000000000000000000000000" ] \
  || die "market $MARKET_ID not found on Morpho $MORPHO (Base Sepolia)"
MK=$(cc "$MORPHO" 'market(bytes32)(uint128,uint128,uint128,uint128,uint128,uint128)' "$MARKET_ID") || die "Morpho market() failed"
POS=$(cc "$MORPHO" 'position(bytes32,address)(uint256,uint128,uint128)' "$MARKET_ID" "$ADDRESS") || die "Morpho position() failed"
PRICE=$(cc "$ORACLE" 'price()(uint256)') || die "oracle $ORACLE price() failed"
LDEC=$(cc "$LOAN" 'decimals()(uint8)') || die "decimals() failed on loan token $LOAN"
CDEC=$(cc "$COLL" 'decimals()(uint8)') || die "decimals() failed on collateral token $COLL"
LSYM=$(cc "$LOAN" 'symbol()(string)' 2>/dev/null || echo "?"); CSYM=$(cc "$COLL" 'symbol()(string)' 2>/dev/null || echo "?")
MK_TUPLE="($(echo "$MK" | paste -sd, -))"
TBA=$(echo "$MK" | sed -n 3p); LAST=$(echo "$MK" | sed -n 5p)
RATE=0
if [ "$(lower "$IRM")" != "0x0000000000000000000000000000000000000000" ] && [ "$TBA" != "0" ] && [ "$NOW" -gt "$LAST" ]; then
  RATE=$(cc "$IRM" 'borrowRateView((address,address,address,address,uint256),(uint128,uint128,uint128,uint128,uint128,uint128))(uint256)' \
    "($LOAN,$COLL,$ORACLE,$IRM,$LLTV)" "$MK_TUPLE") || die "IRM borrowRateView failed"
fi
if XLTV=$("$CAST" call --rpc-url "$BASE_RPC" --block "$BLOCK" "$EXECUTOR" 'ltvOf(address,bytes32)(uint256)' "$ADDRESS" "$MARKET_ID" 2>&1); then
  XLTV=$(echo "$XLTV" | awk 'NR==1{print $1}')
else
  XLTV="revert:$(echo "$XLTV" | sed -n 's/.*execution reverted: *\([A-Za-z0-9_]*\).*/\1/p' | head -1)"
fi

python3 -I - "$SOURCE" "$ADDRESS" "$MARKET_NAME" "$MARKET_ID" "$MORPHO" "$EXECUTOR" "$BLOCK" "$NOW" \
  "$LOAN" "$LSYM" "$LDEC" "$COLL" "$CSYM" "$CDEC" "$ORACLE" "$IRM" "$LLTV" "$PRICE" "$RATE" "$XLTV" \
  $(echo "$MK") $(echo "$POS") <<'PY'
import sys, datetime
a = sys.argv[1:]
src, addr, mname, mid, morpho, executor = a[0:6]
block, now = int(a[6]), int(a[7])
loan, lsym, ldec, coll, csym, cdec = a[8], a[9], int(a[10]), a[11], a[12], int(a[13])
oracle, irm, lltv, price, rate, xltv = a[14], a[15], int(a[16]), int(a[17]), int(a[18]), a[19]
tsa, tss, tba, tbs, last, fee = (int(x) for x in a[20:26])
sshares, bshares, collateral = (int(x) for x in a[26:29])

WAD = 10**18; ORACLE_SCALE = 10**36; VIRTUAL_SHARES = 10**6; VIRTUAL_ASSETS = 1
def up(x, y, d): return (x * y + d - 1) // d
def debt_of(ta): return up(bshares, ta + VIRTUAL_ASSETS, tbs + VIRTUAL_SHARES)       # SharesMathLib.toAssetsUp
def ltv_of(debt, value):                                                              # CorridorMath.ltv
    if debt == 0: return 0
    if value == 0: return None
    return (debt * WAD + value - 1) // value
def w_taylor(x, n):                                                                   # MathLib.wTaylorCompounded
    first = x * n; second = first * first // (2 * WAD); third = second * first // (3 * WAD)
    return first + second + third
def fmt(v, dec, places=None):
    places = dec if places is None else places
    s = '-' if v < 0 else ''; v = abs(v)
    q = v * 10**places // 10**dec
    i, f = divmod(q, 10**places)
    return s + '{:,}'.format(i) + ('.' + str(f).rjust(places, '0') if places else '')
def pct(w): return 'n/a (debt with zero collateral)' if w is None else fmt(w * 100, 18, 4) + ' %'

dt = max(now - last, 0)
tba_now = tba + (tba * w_taylor(rate, dt) // WAD if rate and dt and tba else 0)
value = collateral * price // ORACLE_SCALE                                            # CorridorMath.value
max_borrow = value * lltv // WAD                                                      # Morpho _isHealthy
rows = []
for ta in (tba, tba_now):
    d = debt_of(ta); w = ltv_of(d, value)
    rows.append((d, w, 'yes' if d > max_borrow else 'no'))
utc = lambda t: datetime.datetime.fromtimestamp(t, datetime.timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')
# oracle price as loan units per 1 whole collateral token
px = price * 10**cdec // ORACLE_SCALE

L = []
p = L.append
p('cmdk ltv.sh (read-only) - Base Sepolia (84532) block %d, %s' % (block, utc(now)))
p('source           %s' % src)
p('base address     %s' % addr)
p('market           %s %s' % (mname, mid))
p('  loan token     %s %s (%d decimals)' % (lsym, loan, ldec))
p('  collateral     %s %s (%d decimals)' % (csym, coll, cdec))
p('  oracle         %s price %d (1e36 scale; %s %s per %s)' % (oracle, price, fmt(px, ldec, 2), lsym, csym))
p('  irm / lltv     %s / %s %%' % (irm, fmt(lltv * 100, 18, 2)))
p('  morpho         %s' % morpho)
p('  totals         totalBorrowAssets %d, totalBorrowShares %d, lastUpdate %d (%s, %d s before this block)' % (tba, tbs, last, utc(last), dt))
p('position         supplyShares %d, borrowShares %d, collateral %d' % (sshares, bshares, collateral))
if sshares == 0 and bshares == 0 and collateral == 0:
    p('                 (no position on this market)')
p('collateral       %s %s' % (fmt(collateral, cdec), csym))
p('collateral value %s %s  (= collateral x price / 1e36, rounded down; raw %d)' % (fmt(value, ldec), lsym, value))
p('')
p('                 %-28s %s' % ('at lastUpdate', 'accrued to block %d (+%d s, rate %d/s)' % (block, dt, rate)))
p('borrow assets    %-28d %d  (market totalBorrowAssets)' % (tba, tba_now))
p('debt (raw)       %-28d %d' % (rows[0][0], rows[1][0]))
p('debt             %-28s %s' % ('%s %s' % (fmt(rows[0][0], ldec), lsym), '%s %s' % (fmt(rows[1][0], ldec), lsym)))
p('LTV              %-28s %s' % (pct(rows[0][1]), pct(rows[1][1])))
p('LTV (WAD, up)    %-28s %s' % (rows[0][1] if rows[0][1] is not None else 'n/a', rows[1][1] if rows[1][1] is not None else 'n/a'))
p('liquidatable     %-28s %s' % (rows[0][2], rows[1][2]))
p('  (Morpho: liquidatable when debt > collateral value x LLTV = %s %s)' % (fmt(max_borrow, ldec), lsym))
p('')
if xltv.startswith('revert:'):
    p('executor ltvOf   %s reverted%s (market not configured on that executor?)' % (executor, (': ' + xltv[7:]) if xltv[7:] else ''))
else:
    x = int(xltv); ours = rows[0][1] if rows[0][1] is not None else 2**256 - 1
    p('executor ltvOf   %d (%s) from %s' % (x, pct(None if x == 2**256 - 1 else x), executor))
    p('  cross-check    %s with the "at lastUpdate" LTV (WAD)' % ('MATCH' if x == ours else 'MISMATCH'))
print('\n'.join(L))
PY
