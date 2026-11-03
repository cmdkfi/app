#!/usr/bin/env bash
# attack-corridor.sh: provoke the executor's corridor reverts from a wallet's own executor position and print
# each revert decoded. Testnet only (Base Sepolia, chain id 84532), or a local anvil fork of it.
#
#   scripts/attack-corridor.sh [--rpc URL] [--executor 0x...] [--deployments path] [--key-file path]
#                              [--land] [--close-existing] [--no-restore]
#
# The executor's checks key on msg.sender, so the wallet's position takes the place of the vault's Base address
# (the same code path; prototype P5 B1-B5). Five attacks, each an eth_call whose revert data is matched against
# the 4-byte selectors of the executor's custom errors (`cast sig "BelowFloor()"` ...):
#   Expired           a reduce signed with a deadline, broadcast after it          (modifier before(deadline))
#   MoveLimit         a migrate after two moves within 24 hours                    (_recordMove, MAX_MOVES = 2)
#   UnapprovedMarket  a migrate to a market id with no corridor of the wallet      (_agentCorridor)
#   BelowFloor        a discretionary reduce at LTV under the floor                (reduce, repayToLtv(floor) = 0)
#   SellBudget        a discretionary reduce once the 24 h sell budget is used up  (reduce, remainingBudget = 0)
# Output: one line per attack, `attack <name>: revert <ErrorName> <selector> PASS|FAIL`; exit 1 if any FAIL.
#
# Corridor set by the script on live, backup and demo: target 50%, act 70%, floor 30%, sell budget 5%, migrate
# and exit allowed. Positions: 0.002 WETH on live (USDC/WETH) for the moves, 0.002 WETH on demo (dUSDC/WETH) for
# the reduces; both closed at the end (owner `close`), and corridors that existed before are restored.
#
# --rpc           Base Sepolia JSON-RPC (default $BASE_RPC or https://base-sepolia-rpc.publicnode.com). An anvil
#                 fork is detected by web3_clientVersion; there the script skips real waits with evm_increaseTime
#                 and tops up gas ETH with anvil_setBalance. On testnet it waits in real time: the 60 s cooldown
#                 before each agent call (about 6-8 minutes in all) and the Expired deadline (about 10 s).
# --executor      executor address (default: base.executor of the deployments file).
# --deployments   deployments file (default deployments/testnet.json): market ids, tokens, Morpho.
# --key-file      the wallet's key file, JSON {"data": {"private_key", "address"}} (default
#                 ~/.config/prime/base_sepolia_eoa.json, the team's test wallet). The key is handed only to cast
#                 and never printed.
# --land          also send each attack as a transaction with a fixed gas limit, so the revert lands on chain
#                 with a hash (the evidence form of P5). Without it the attacks are eth_calls; the Expired
#                 transaction is signed before its deadline either way and published only with --land.
# --close-existing  the wallet already holds collateral or debt on live, backup or demo: close those positions
#                 first with the owner path `close` (repays from the wallet's loan token, returns its collateral).
#                 Without it the script stops, so it never mixes its test position into an existing one.
# --no-restore    leave the attack corridors in place at the end.
#
# Spend: gas only (Base Sepolia), plus Morpho interest of a few loan-token units. WETH is wrapped from ETH if
# the wallet has less than 0.0025 WETH.
# Exit codes: 0 every attack PASS; 1 a FAIL or an error; 2 usage.
# Needs: bash (3.2 or later), cast (Foundry), jq, python3.
set -euo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$HERE/.." && pwd)
export PATH="$HOME/.foundry/bin:$PATH"

RPC=${BASE_RPC:-https://base-sepolia-rpc.publicnode.com}
DEP="$ROOT/deployments/testnet.json"
EXEC=""
KEYFILE="$HOME/.config/prime/base_sepolia_eoa.json"
LAND=0
CLOSE_EXISTING=0
RESTORE=1

usage() { sed -n '2,40p' "$0" | sed 's/^# \{0,1\}//'; exit 2; }
while [ $# -gt 0 ]; do
  case "$1" in
    --rpc) RPC="$2"; shift 2 ;;
    --executor) EXEC="$2"; shift 2 ;;
    --deployments) DEP="$2"; shift 2 ;;
    --key-file) KEYFILE="$2"; shift 2 ;;
    --land) LAND=1; shift ;;
    --close-existing) CLOSE_EXISTING=1; shift ;;
    --no-restore) RESTORE=0; shift ;;
    -h|--help) usage ;;
    *) echo "unknown argument: $1" >&2; usage ;;
  esac
done

die() { echo "error: $*" >&2; exit 1; }
info() { echo "  $*"; }
calc() { python3 -I -c "print($1)"; }

# ------------------------------------------------------------------ addresses
[ -f "$DEP" ] || die "deployments file not found: $DEP"
j() { jq -r "$1" "$DEP"; }
[ -n "$EXEC" ] || EXEC=$(j .base.executor)
MORPHO=$(j .base.morpho)
WETH=$(j .base.tokens.weth)
USDC=$(j .base.tokens.usdc)
DUSDC=$(j .base.tokens.dusdc)
LIVE=$(j .base.markets.live.id)
BACKUP=$(j .base.markets.backup.id)
DEMO=$(j .base.markets.demo.id)
for v in EXEC MORPHO WETH USDC DUSDC LIVE BACKUP DEMO; do
  eval "x=\$$v"; [ -n "$x" ] && [ "$x" != null ] || die "$v missing in $DEP"
done
# A market id with no corridor of the wallet: the live market's parameters with LLTV 94.5% (P5 B1b). It is not
# configured on the executor; the corridor check comes first, so the call reverts UnapprovedMarket.
NOAPPROVAL=$(cast keccak "$(cast abi-encode 'f(address,address,address,address,uint256)' \
  "$(j .base.markets.live.loanToken)" "$(j .base.markets.live.collateralToken)" "$(j .base.markets.live.oracle)" \
  "$(j .base.markets.live.irm)" 945000000000000000)")

# ------------------------------------------------------------------ network
CHAIN=$(cast chain-id --rpc-url "$RPC") || die "RPC not reachable: $RPC"
[ "$CHAIN" = 84532 ] || die "chain id $CHAIN is not Base Sepolia (84532); testnet only"
CLIENT=$(cast rpc web3_clientVersion --rpc-url "$RPC" 2>/dev/null | tr -d '"' || true)
FORK=0; case "$CLIENT" in anvil*) FORK=1 ;; esac
[ "$(cast code "$EXEC" --rpc-url "$RPC")" != 0x ] || die "no code at executor $EXEC"

# ------------------------------------------------------------------ wallet (key never printed)
[ -f "$KEYFILE" ] || die "key file not found: $KEYFILE"
PK=$(python3 -I -c 'import json,sys; d=json.load(open(sys.argv[1]))["data"]; d=d[0] if isinstance(d,list) else d; print(d["private_key"])' "$KEYFILE")
ME=$(python3 -I -c 'import json,sys; d=json.load(open(sys.argv[1]))["data"]; d=d[0] if isinstance(d,list) else d; print(d["address"])' "$KEYFILE")
[ -n "$PK" ] && [ -n "$ME" ] || die "key file has no data.private_key / data.address"

# ------------------------------------------------------------------ chain helpers
call() { cast call "$@" --rpc-url "$RPC" | head -1 | awk '{print $1}'; }
ts() { cast block latest -f timestamp --rpc-url "$RPC"; }
dl() { echo $(( $(ts) + 600 )); }
COOLDOWN=$(call "$EXEC" "COOLDOWN()(uint256)")

send() { # to sig args...: a transaction that must succeed. The estimate is raised by half: a migrate's nested
  # flash loan, repay and borrow ran out of gas at the plain estimate (63/64 rule), on a fork with 2 s blocks
  local out st
  out=$(cast send "$@" --private-key "$PK" --rpc-url "$RPC" --gas-estimate-multiplier 150 --json 2>&1) || die "transaction failed: $1 $2: $(echo "$out" | tail -c 400)"
  st=$(echo "$out" | jq -r .status 2>/dev/null || true)
  [ "$st" = 0x1 ] || die "transaction reverted: $1 $2 (status $st)"
  echo "$out" | jq -r .transactionHash
}

ERRS="Expired Reentrancy UnknownMarket UnapprovedMarket BadCorridor Cooldown BelowFloor SellBudget MoveLimit
MigrateDisabled ExitDisabled MarketMismatch BadFraction NoDebt StaleFeed Slippage PartialFill AboveTarget NotMorpho
NotPool MigrationMismatch AboveActLine BalanceChanged BadConfig AtTarget"
SELS=""
for e in $ERRS; do SELS="$SELS $(cast sig "$e()"):$e"; done
name_of() { # selector -> error name
  local p; for p in $SELS; do [ "${p%%:*}" = "$1" ] && { echo "${p#*:}"; return; }; done; echo unknown
}
revert_of() { # eth_call from the wallet; prints the 4-byte revert selector, or "none"
  local out sel
  if out=$(cast call "$@" --from "$ME" --rpc-url "$RPC" 2>&1); then echo none; return; fi
  sel=$(echo "$out" | grep -oE 'data: "0x[0-9a-fA-F]{8}' | head -1 | grep -oE '0x[0-9a-fA-F]{8}' || true)
  [ -n "$sel" ] || sel=$(echo "$out" | grep -oE '0x[0-9a-fA-F]{8}' | head -1 || true)
  [ -n "$sel" ] || { echo "?: $(echo "$out" | tail -c 200)" >&2; sel=0x00000000; }
  echo "$sel" | tr 'A-F' 'a-f'
}

land() { # to sig args...: send with a fixed gas limit (no estimate) so the revert lands; prints hash, status, gas
  local gas=$1; shift
  local h r
  h=$(cast send "$@" --private-key "$PK" --rpc-url "$RPC" --gas-limit "$gas" --async 2>&1 | tail -1 | tr -d '"')
  case "$h" in 0x*) ;; *) info "landing failed: $h"; return ;; esac
  for _ in $(seq 1 60); do
    r=$(cast receipt "$h" --json --rpc-url "$RPC" 2>/dev/null || true)
    [ -n "$r" ] && break; sleep 1
  done
  info "landed: tx $h block $(echo "$r" | jq -r .blockNumber | xargs cast to-dec) status $(echo "$r" | jq -r .status) gasUsed $(echo "$r" | jq -r .gasUsed | xargs cast to-dec) / $gas"
}

wait_until() { # block timestamp >= $1
  local now; now=$(ts)
  [ "$now" -ge "$1" ] && return
  if [ $FORK = 1 ]; then
    cast rpc evm_increaseTime $(( $1 - now )) --rpc-url "$RPC" >/dev/null
    cast rpc evm_mine --rpc-url "$RPC" >/dev/null
    info "fork: time advanced $(( $1 - now )) s (evm_increaseTime)"
  else
    info "waiting $(( $1 - now )) s in real time"
    while [ "$(ts)" -lt "$1" ]; do sleep 3; done
  fi
}
wait_cooldown() { wait_until $(( $(call "$EXEC" "lastAgentCall(address)(uint256)" "$ME") + COOLDOWN )); }

PASSES=0; FAILS=0; RESULTS=""
report() { # attack-name expected selector
  local got; got=$(name_of "$3")
  local verdict=FAIL; [ "$got" = "$2" ] && verdict=PASS
  [ $verdict = PASS ] && PASSES=$((PASSES + 1)) || FAILS=$((FAILS + 1))
  [ "$3" = none ] && got="(no revert)"
  local line="attack $1: revert $got $3 $verdict"
  [ $verdict = FAIL ] && line="$line (expected $2)"
  echo "$line"; RESULTS="$RESULTS$line"$'\n'
}

pos() { cast call "$MORPHO" "position(bytes32,address)(uint256,uint128,uint128)" "$1" "$ME" --rpc-url "$RPC" | awk '{print $1}' | tr '\n' ' '; }
pp() { pos "$1" | awk '{printf "borrowShares %s collateral %s", $2, $3}'; }
collateral() { pos "$1" | awk '{print $3}'; }
liquidity() { cast call "$MORPHO" "market(bytes32)(uint128,uint128,uint128,uint128,uint128,uint128)" "$1" --rpc-url "$RPC" | awk '{print $1}' | tr '\n' ' ' | awk '{print $1 - $3}'; }
ltv() { call "$EXEC" "ltvOf(address,bytes32)(uint256)" "$ME" "$1"; }
pct() { calc "'%.4f' % ($1 / 1e18)"; }
sold() { # soldInWindow: (address,address) in the build, (address) in the prototype executor
  cast call "$EXEC" "soldInWindow(address,address)(uint256)" "$ME" "$WETH" --rpc-url "$RPC" 2>/dev/null | awk '{print $1}' \
    || cast call "$EXEC" "soldInWindow(address)(uint256)" "$ME" --rpc-url "$RPC" | awk '{print $1}'
}
CORR_SIG="setCorridor(bytes32,(uint64,uint64,uint64,uint64,bool,bool,bool),uint256)"
CORRIDOR="(500000000000000000,700000000000000000,300000000000000000,50000000000000000,true,true,false)"
corridor_of() { cast call "$EXEC" "corridors(address,bytes32)(uint64,uint64,uint64,uint64,bool,bool,bool)" "$ME" "$1" --rpc-url "$RPC" | awk '{print $1}' | tr '\n' ',' | sed 's/,$//'; }

# ------------------------------------------------------------------ start
STARTED=0
trap 'rc=$?; [ $rc != 0 ] && [ $STARTED = 1 ] && echo "stopped (exit $rc); the wallet may still hold the test positions: rerun with --close-existing" >&2' EXIT
echo "attack-corridor: executor $EXEC, wallet $ME, chain $CHAIN ($( [ $FORK = 1 ] && echo "anvil fork: $CLIENT" || echo testnet)), block $(cast block-number --rpc-url "$RPC"), time $(ts)"
[ $FORK = 1 ] || echo "  testnet: cooldown waits run in real time (MoveLimit, BelowFloor and SellBudget each wait ${COOLDOWN} s before an agent call)"

# gas
BAL=$(cast balance "$ME" --rpc-url "$RPC")
if [ "$(calc "int($BAL < 10**16)")" = 1 ]; then
  [ $FORK = 1 ] || [ "$(calc "int($BAL < 2*10**15)")" = 0 ] || die "wallet has $(cast from-wei "$BAL") ETH; needs at least 0.002 for gas"
  if [ $FORK = 1 ]; then cast rpc anvil_setBalance "$ME" 0x16345785D8A0000 --rpc-url "$RPC" >/dev/null; info "fork: wallet ETH set to 0.1 (anvil_setBalance)"; fi
fi

# existing positions
EXISTING=""
for m in live:$LIVE backup:$BACKUP demo:$DEMO; do
  p=$(pos "${m#*:}"); bs=$(echo "$p" | awk '{print $2}'); co=$(echo "$p" | awk '{print $3}')
  [ "$bs" = 0 ] && [ "$co" = 0 ] || EXISTING="$EXISTING ${m%%:*}(borrowShares $bs, collateral $co)"
done
if [ -n "$EXISTING" ]; then
  [ $CLOSE_EXISTING = 1 ] || die "the wallet already has positions:$EXISTING. Close them first, or pass --close-existing to have the script close them"
  info "closing existing positions:$EXISTING"
fi

STARTED=1
# approvals and Morpho authorization (owner side of the wallet)
MAX=115792089237316195423570985008687907853269984665640564039457584007913129639935
for t in $WETH $USDC $DUSDC; do
  [ "$(call "$t" "allowance(address,address)(uint256)" "$ME" "$EXEC")" = $MAX ] || { send "$t" "approve(address,uint256)" "$EXEC" $MAX >/dev/null; info "approved $t to the executor"; }
done
[ "$(call "$MORPHO" "isAuthorized(address,address)(bool)" "$ME" "$EXEC")" = true ] || { send "$MORPHO" "setAuthorization(address,bool)" "$EXEC" true >/dev/null; info "Morpho authorization for the executor set"; }

if [ -n "$EXISTING" ]; then
  for m in $LIVE $BACKUP $DEMO; do
    p=$(pos "$m"); [ "$(echo "$p" | awk '{print $2 + $3}')" = 0 ] && continue
    send "$EXEC" "close(bytes32,uint256)" "$m" "$(dl)" >/dev/null; info "closed the wallet's position on $m"
  done
fi

WETHBAL=$(call "$WETH" "balanceOf(address)(uint256)" "$ME")
if [ "$(calc "int($WETHBAL < 2500000000000000)")" = 1 ]; then
  send "$WETH" "deposit()" --value 2500000000000000 >/dev/null; info "wrapped 0.0025 ETH to WETH"
fi

# corridors (owner path setCorridor; no cooldown); remember the old ones to restore at the end
OLD_LIVE=$(corridor_of "$LIVE"); OLD_BACKUP=$(corridor_of "$BACKUP"); OLD_DEMO=$(corridor_of "$DEMO")
for m in $LIVE $BACKUP $DEMO; do send "$EXEC" "$CORR_SIG" "$m" "$CORRIDOR" "$(dl)" >/dev/null; done
info "corridor on live, backup, demo: target 0.50, act 0.70, floor 0.30, sell budget 0.05, migrate and exit allowed"

# ------------------------------------------------------------------ 1. Expired
echo "-- Expired: reduce(demo) signed with deadline = block time + 6 s, broadcast after the deadline"
T0=$(ts); DEADLINE=$((T0 + 6))
NONCE=$(cast nonce "$ME" --rpc-url "$RPC")
RAW=$(cast mktx "$EXEC" "reduce(bytes32,uint256,uint256)" "$DEMO" 1000000 $DEADLINE --private-key "$PK" --rpc-url "$RPC" \
  --gas-limit 300000 --nonce "$NONCE" --chain 84532 2>&1) || die "mktx failed: $(echo "$RAW" | tail -c 300)"
info "signed at block time $T0, nonce $NONCE, deadline $DEADLINE"
wait_until $((DEADLINE + 1))
SEL=$(revert_of "$EXEC" "reduce(bytes32,uint256,uint256)" "$DEMO" 1000000 $DEADLINE)
if [ $LAND = 1 ]; then
  H=$(cast publish "$RAW" --rpc-url "$RPC" --async 2>&1 | tail -1 | tr -d '"')
  for _ in $(seq 1 60); do R=$(cast receipt "$H" --json --rpc-url "$RPC" 2>/dev/null || true); [ -n "$R" ] && break; sleep 1; done
  info "published after the deadline: tx $H block $(echo "$R" | jq -r .blockNumber | xargs cast to-dec) (time $(cast block "$(echo "$R" | jq -r .blockNumber)" -f timestamp --rpc-url "$RPC")) status $(echo "$R" | jq -r .status) gasUsed $(echo "$R" | jq -r .gasUsed | xargs cast to-dec)"
else
  info "signed transaction not published (pass --land to publish it)"
fi
report Expired Expired "$SEL"

# ------------------------------------------------------------------ 2. MoveLimit
echo "-- MoveLimit: two moves between live and backup within 24 h, then a third"
feed_age() { local f=$1; local u; u=$(cast call "$f" "latestRoundData()(uint80,int256,uint256,uint256,uint80)" --rpc-url "$RPC" | sed -n 4p | awk '{print $1}'); echo $(( $(ts) - u )); }
EA=$(feed_age "$(j .base.chainlink.ethUsd.address)"); UA=$(feed_age "$(j .base.chainlink.usdcUsd.address)")
info "feed ages: ETH/USD ${EA} s (max $(j .base.chainlink.ethUsd.maxAgeSeconds)), USDC/USD ${UA} s (max $(j .base.chainlink.usdcUsd.maxAgeSeconds))"
[ "$EA" -le "$(j .base.chainlink.ethUsd.maxAgeSeconds)" ] && [ "$UA" -le "$(j .base.chainlink.usdcUsd.maxAgeSeconds)" ] \
  || info "a feed is past its max age: open and migrate on live/backup revert StaleFeed (on a fork: restart anvil)"
PRICE=$(call "$(j .base.markets.live.oracle)" "price()(uint256)")
VALUE=$(calc "2000000000000000 * $PRICE // 10**36")
LIQ=$(calc "min($(liquidity "$LIVE"), $(liquidity "$BACKUP"))")
TLTV=$(calc "min(200000000000000000, $LIQ * 10**18 // 2 // $VALUE)")
info "0.002 WETH is worth $VALUE USDC units; free liquidity min(live, backup) $LIQ; open at LTV $(pct "$TLTV")"
[ "$(calc "int($TLTV >= 10**16)")" = 1 ] || die "not enough USDC liquidity on live/backup for a 1% LTV move"
send "$EXEC" "open(bytes32,uint256,uint256,uint256)" "$LIVE" 2000000000000000 "$TLTV" "$(dl)" >/dev/null
info "open(live, 0.002 WETH, LTV $(pct "$TLTV")): $(pp "$LIVE")"
MOVES=$(call "$EXEC" "movesInWindow(address)(uint256)" "$ME")
info "moves in the last 24 h before: $MOVES"
AT=$LIVE; OTHER=$BACKUP
k=$MOVES
while [ "$k" -lt 2 ]; do
  wait_cooldown
  send "$EXEC" "migrate(bytes32,bytes32,uint256,uint256)" "$AT" "$OTHER" 1000000000000000000 "$(dl)" >/dev/null
  t=$AT; AT=$OTHER; OTHER=$t; k=$((k + 1))
  info "move $k: migrate(100%) done at time $(ts); movesInWindow = $(call "$EXEC" "movesInWindow(address)(uint256)" "$ME")"
done
wait_cooldown
SEL=$(revert_of "$EXEC" "migrate(bytes32,bytes32,uint256,uint256)" "$AT" "$OTHER" 1000000000000000000 "$(dl)")
[ $LAND = 1 ] && land 600000 "$EXEC" "migrate(bytes32,bytes32,uint256,uint256)" "$AT" "$OTHER" 1000000000000000000 "$(dl)"
report MoveLimit MoveLimit "$SEL"

# ------------------------------------------------------------------ 3. UnapprovedMarket
echo "-- UnapprovedMarket: migrate from live to $NOAPPROVAL (live's parameters, LLTV 94.5%; no corridor)"
info "corridors(wallet, that id) = ($(corridor_of "$NOAPPROVAL"))"
SEL=$(revert_of "$EXEC" "migrate(bytes32,bytes32,uint256,uint256)" "$LIVE" "$NOAPPROVAL" 1000000000000000000 "$(dl)")
[ $LAND = 1 ] && land 300000 "$EXEC" "migrate(bytes32,bytes32,uint256,uint256)" "$LIVE" "$NOAPPROVAL" 1000000000000000000 "$(dl)"
report UnapprovedMarket UnapprovedMarket "$SEL"

send "$EXEC" "close(bytes32,uint256)" "$AT" "$(dl)" >/dev/null
info "closed the move position (owner close); live: $(pp "$LIVE"); backup: $(pp "$BACKUP")"

# ------------------------------------------------------------------ 4. BelowFloor
echo "-- BelowFloor: discretionary reduce(demo) at LTV 0.10, floor 0.30"
send "$EXEC" "open(bytes32,uint256,uint256,uint256)" "$DEMO" 2000000000000000 100000000000000000 "$(dl)" >/dev/null
info "open(demo, 0.002 WETH, LTV 0.10): LTV $(pct "$(ltv "$DEMO")")"
wait_cooldown
SEL=$(revert_of "$EXEC" "reduce(bytes32,uint256,uint256)" "$DEMO" 1000000 "$(dl)")
[ $LAND = 1 ] && land 400000 "$EXEC" "reduce(bytes32,uint256,uint256)" "$DEMO" 1000000 "$(dl)"
report BelowFloor BelowFloor "$SEL"

# ------------------------------------------------------------------ 5. SellBudget
echo "-- SellBudget: discretionary reduces on demo at LTV 0.45 until the 5% budget is used, then one more"
send "$EXEC" "open(bytes32,uint256,uint256,uint256)" "$DEMO" 0 450000000000000000 "$(dl)" >/dev/null
info "open(demo, +0, LTV 0.45): LTV $(pct "$(ltv "$DEMO")"), collateral $(collateral "$DEMO"), sold in window $(sold)"
ASK=100000000 # 100 dUSDC: more than the debt; each reduce is clamped to the remaining budget
SEL=none
for i in 1 2 3 4 5 6 7 8; do
  wait_cooldown
  SEL=$(revert_of "$EXEC" "reduce(bytes32,uint256,uint256)" "$DEMO" $ASK "$(dl)")
  [ "$SEL" = none ] || break
  send "$EXEC" "reduce(bytes32,uint256,uint256)" "$DEMO" $ASK "$(dl)" >/dev/null
  info "sale $i: reduce(demo, ask 100 dUSDC) clamped; sold in window $(sold) wei WETH, collateral $(collateral "$DEMO"), LTV $(pct "$(ltv "$DEMO")")"
done
[ $LAND = 1 ] && [ "$SEL" != none ] && land 400000 "$EXEC" "reduce(bytes32,uint256,uint256)" "$DEMO" $ASK "$(dl)"
report SellBudget SellBudget "$SEL"

# ------------------------------------------------------------------ close-out
send "$EXEC" "close(bytes32,uint256)" "$DEMO" "$(dl)" >/dev/null
STARTED=0
info "closed demo; positions live: $(pp "$LIVE"); backup: $(pp "$BACKUP"); demo: $(pp "$DEMO")"
if [ $RESTORE = 1 ]; then
  n=0
  for pair in "$LIVE:$OLD_LIVE" "$BACKUP:$OLD_BACKUP" "$DEMO:$OLD_DEMO"; do
    id=${pair%%:*}; c=${pair#*:}
    case "$c" in *,true) ;; *) continue ;; esac # restore only a corridor that was set before
    send "$EXEC" "$CORR_SIG" "$id" "($c)" "$(dl)" >/dev/null; n=$((n + 1))
  done
  info "corridors restored to their values before the run: $n (a market without one before keeps the attack corridor)"
fi

echo "result: $PASSES PASS, $FAILS FAIL (executor $EXEC, wallet $ME, block $(cast block-number --rpc-url "$RPC"))"
[ $FAILS = 0 ]
