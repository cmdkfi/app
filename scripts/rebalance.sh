#!/bin/sh
# Rebalancer timer (testnet only). Every INTERVAL_MIN minutes (default 10) runs
#   python3 -I scripts/rebalance_pool.py --market live "$@"   then   ... --market demo "$@"
# and prints one line per run on stdout: the script's RUN line (tick, oracle, pool, gap, action, repair quote, swap
# input, transactions, ETH fee, L1 part) plus its exit code. Exit 3 is the budget guard's refusal, 4 another run
# holding the lock; any other non-zero exit is a crash, logged as such, and the next round runs as usual.
# The full output of every run is appended to $LOG_DIR/rebalance-detail.log (default ~/.local/state/cmdk).
# Ctrl-C or SIGTERM: a run in progress is allowed to finish (a burn without its re-mint would leave the pool bare),
# then the loop exits 0; a pending sleep is cut short.
#
#   scripts/rebalance.sh                          # defaults: --max-cost-usdc 6 --reserve-usdc 8
#   INTERVAL_MIN=5 scripts/rebalance.sh --max-cost-usdc 4
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
INTERVAL_MIN=${INTERVAL_MIN:-10}
INTERVAL_SEC=${INTERVAL_SEC:-$((INTERVAL_MIN * 60))}     # seconds override, for a test on a fork
LOG_DIR=${LOG_DIR:-$HOME/.local/state/cmdk}
mkdir -p "$LOG_DIR"
stop=0; child=; sleeper=
trap 'stop=1; echo "$(date -u +%FT%TZ) rebalance.sh: stop requested; finishing the current step" >&2; [ -n "$sleeper" ] && kill "$sleeper" 2>/dev/null' INT TERM

run_market() {
  out="$LOG_DIR/rebalance-last-$1.out"
  python3 -I "$HERE/rebalance_pool.py" --market "$@" > "$out" 2>&1 &
  child=$!
  wait "$child"; code=$?
  while kill -0 "$child" 2>/dev/null; do wait "$child"; code=$?; done   # a signal interrupts wait, not the run
  child=
  { echo "==== $(date -u +%FT%TZ) market $1 exit $code"; cat "$out"; } >> "$LOG_DIR/rebalance-detail.log"
  line=$(grep '^RUN ' "$out" | tail -n 1)
  case $code in
    0) note= ;;
    3) note=" (refused by the budget guard)" ;;
    4) note=" (another run holds the lock)" ;;
    *) note=" (crashed: $(grep -v '^RUN ' "$out" | tail -n 1))" ;;
  esac
  echo "${line:-RUN $(date -u +%FT%TZ) market=$1 action=crash} exit=$code$note"
}

echo "$(date -u +%FT%TZ) rebalance.sh: every $INTERVAL_SEC s, live then demo, args: $*; detail log $LOG_DIR/rebalance-detail.log" >&2
while [ "$stop" = 0 ]; do
  for market in live demo; do
    [ "$stop" = 0 ] && run_market "$market" "$@"
  done
  [ "$stop" = 0 ] || break
  sleep "$INTERVAL_SEC" &
  sleeper=$!
  wait "$sleeper" 2>/dev/null
  sleeper=
done
echo "$(date -u +%FT%TZ) rebalance.sh: stopped" >&2
exit 0
