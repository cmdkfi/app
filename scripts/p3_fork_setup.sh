#!/usr/bin/env bash
# P3 (throwaway): give the vault's Base address a position on an anvil fork of Base Sepolia, so the agent's tick loop
# can be run against a mock-price move without any Base Sepolia transaction. FORK ONLY: it impersonates the vault's
# address (anvil_impersonateAccount), which stands in for the transactions the vault would sign through Chain
# Signatures (setup, set_corridor, open). The executor, backup market and demo pool must already be deployed on the
# fork (prototype/executor/script/deploy.sh with RPC=<fork>, run from a scratch copy).
#
#   p3_fork_setup.sh [RPC] [COLLATERAL_WEI]
set -euo pipefail
export PATH="$HOME/.foundry/bin:$PATH"
R=${1:-http://127.0.0.1:8547}
COLL=${2:-20000000000000000}   # 0.02 WETH
V=0xf1aa3f639ded66d2e4b635c7a727d509f0c9f755
X=0xB23e0d7e1D0182Db7f6f2637EC4Ac51c10290f40
MORPHO=0xBBBBBbbBBb9cC5e90e3b3Af64bdAF62C37EEFFCb
WETH=0x4200000000000000000000000000000000000006
USDC=0x036CbD53842c5426634e7929541eC2318f3dCF7e
DUSDC=0xeb68Fd5bAAAfD4e46b43bAa7fEAb3216B87b10d5
LIVE=0xa25ac5171b7e500b82653d3750b91bb44e2409c32ca3c9e80b0db077e8a4535c
BACKUP=0x99d43c0f2a0eb6172774598a4e34e7eee09caa502692c2b2df0f40e1e3c5ea75
DEMO=0x55872b53dba6147b6badf9d4277bc166dfb7d5f8ac8d491da093960dbef6d585
CORRIDOR="(500000000000000000,700000000000000000,300000000000000000,200000000000000000,true,true,false)"

[ "$(cast chain-id --rpc-url "$R")" = 84532 ] || { echo "not a Base Sepolia fork"; exit 1; }
[ "$(cast code $X --rpc-url "$R")" != 0x ] || { echo "executor not deployed on this fork"; exit 1; }
case "$R" in http://127.0.0.1:*|http://localhost:*) ;; *) echo "refusing: $R is not a local fork"; exit 1;; esac

cast rpc anvil_setBalance $V 0x16345785D8A0000 --rpc-url "$R" >/dev/null        # 0.1 ETH, fork only
cast rpc anvil_impersonateAccount $V --rpc-url "$R" >/dev/null
send() { cast send --unlocked --from $V --rpc-url "$R" "$@" --json | python3 -I -c 'import json,sys; r=json.load(sys.stdin); print(r["transactionHash"], "status", int(r["status"],16), "gas", int(r["gasUsed"],16))'; }
DL=$(( $(date +%s) + 600 ))
echo "setup (as the vault's setup() would sign):"
send $USDC 'approve(address,uint256)' $X "$(cast max-uint)"
send $WETH 'approve(address,uint256)' $X "$(cast max-uint)"
send $DUSDC 'approve(address,uint256)' $X "$(cast max-uint)"
send $MORPHO 'setAuthorization(address,bool)' $X true
echo "corridors (target 0.5, act 0.7, floor 0.3, budget 0.2, migrate on, exit on):"
for M in $LIVE $BACKUP $DEMO; do send $X 'setCorridor(bytes32,(uint64,uint64,uint64,uint64,bool,bool,bool),uint256)' $M "$CORRIDOR" $DL; done
echo "open on the demo market at 50% LTV:"
send --value $COLL $WETH 'deposit()'
send $X 'open(bytes32,uint256,uint256,uint256)' $DEMO $COLL 500000000000000000 $DL
cast rpc anvil_stopImpersonatingAccount $V --rpc-url "$R" >/dev/null
echo "ltvOf(vault, demo) = $(cast call $X 'ltvOf(address,bytes32)(uint256)' $V $DEMO --rpc-url "$R")"
cast call $MORPHO 'position(bytes32,address)(uint256,uint128,uint128)' $DEMO $V --rpc-url "$R"
