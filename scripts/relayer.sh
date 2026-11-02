#!/bin/sh
# Restarts relayer.py after a crash: runs `python3 -I scripts/relayer.py "$@"` in a loop.
# Exit 0 (stopped with Ctrl-C) or 2 (refused to start: wrong chain id or network, bad argument) ends the loop;
# any other exit restarts it after 5 s, with a log line on stderr. Ctrl-C or SIGTERM to this script stops both.
#
#   scripts/relayer.sh serve --port 8787 --sender RELAYER.testnet --factory FACTORY.testnet
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
child=
trap '[ -n "$child" ] && kill "$child" 2>/dev/null; exit 0' INT TERM
while :; do
  python3 -I "$HERE/relayer.py" "$@" &
  child=$!
  wait "$child"
  code=$?
  child=
  case $code in
    0|2) echo "$(date -u +%FT%TZ) relayer.sh: relayer.py exited $code; not restarting" >&2; exit "$code" ;;
  esac
  echo "$(date -u +%FT%TZ) relayer.sh: relayer.py exited $code; restarting in 5 s" >&2
  sleep 5
done
