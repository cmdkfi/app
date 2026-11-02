#!/usr/bin/env bash
# Deploy P1 to Base Sepolia (default) or to a local anvil fork: RPC=http://127.0.0.1:8545 DEPLOY_OUT=deployments.anvil.json
# The test wallet key is read from ~/.config/prime/base_sepolia_eoa.json into PK for the forge process only; never printed.
set -euo pipefail
cd "$(dirname "$0")/.."
export PATH="$HOME/.foundry/bin:$PATH"
RPC="${RPC:-https://base-sepolia-rpc.publicnode.com}"
PK="$(python3 -I -c 'import json,os; d=json.load(open(os.path.expanduser("~/.config/prime/base_sepolia_eoa.json")))["data"]; d=d[0] if isinstance(d,list) else d; print(d["private_key"])')"
export PK
forge script script/Deploy.s.sol:Deploy --rpc-url "$RPC" --broadcast --slow "$@"
