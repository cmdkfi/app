# Attack scripts: `attack-corridor.sh` and `attack-key.sh`

Two scripts that try what a compromised agent could try and print the refusal it gets, decoded. Testnet only:
Base Sepolia (or a local anvil fork of it) and NEAR testnet (or a local NEAR sandbox). In the judges' README
"Verify it", `attack-corridor.sh` is step 7; `attack-key.sh` is covered by the sentence between steps 6 and 7 (the
agent key's limits). `attack-key.sh` has been run only against a local NEAR sandbox: a vault's only key is the
agent's function-call key, made and sealed inside the enclave, so outside the enclave nothing can sign as the
agent; the sandbox run gives the vault a test key in its place (below). The prototype ran the same attacks by
hand on 28 Oct (planning repo, `research_notes/Prototype/results.md`, "P5 Attacks and close-out", parts A and B);
the scripts repeat them.

## `attack-corridor.sh`: the executor's corridor reverts

```sh
scripts/attack-corridor.sh [--rpc URL] [--executor 0x...] [--deployments path] [--key-file path] \
                           [--land] [--close-existing] [--no-restore]
```

The executor is permissionless and keys every check on `msg.sender`. The script therefore uses a wallet's own
executor position, the same code path the vault's Base address takes (P5 part B). By default the wallet is the
team's test wallet, whose key is read from `~/.config/prime/base_sepolia_eoa.json` and handed only to `cast`.
Each attack is an `eth_call` from the wallet. Its revert data is matched against the 4-byte selectors of the
executor's custom errors, computed with `cast sig "Name()"`.

What it does, in order:

1. Checks: the chain id is 84532, the executor has code, and the wallet has no position on live, backup or demo.
   If it has one, the script stops; `--close-existing` closes it first with the owner path `close`. The script
   then approves WETH, USDC and dUSDC to the executor and sets the wallet's Morpho authorization if either is
   missing. It wraps 0.0025 ETH to WETH if the wallet holds less.
2. Corridor on live, backup and demo (owner path `setCorridor`): target 50%, act 70%, floor 30%, sell budget 5%,
   migrate and exit allowed.
3. **Expired:** `reduce(demo)` is signed (`cast mktx`) with deadline = block time + 6 s. The script waits until
   the block time passes the deadline, then makes the call: `Expired`. With `--land` it also publishes the signed
   transaction, which lands as a revert with a hash.
4. **MoveLimit:** `open(live, 0.002 WETH)` at LTV 20%, or lower if the free USDC on live or backup is short. Then
   `migrate` 100% between live and backup until the wallet has two moves in the last 24 hours (`movesInWindow`;
   moves already in the window count), each after the 60 s cooldown. Then a third `migrate`: `MoveLimit`.
5. **UnapprovedMarket:** `migrate` from live to market id
   `0xcdffa34acd76819c6cc63553cd99aaa981848aa90134b63c7be4e502b6cc2b11`: the live market's parameters with LLTV
   94.5%, where the wallet has no corridor (the script prints `corridors(wallet, id)`, all zero). The corridor
   check comes first, so the call reverts `UnapprovedMarket` (P5 B1b). Then the move position is closed.
6. **BelowFloor:** `open(demo, 0.002 WETH)` at LTV 10%, then after the cooldown a discretionary `reduce`:
   `BelowFloor`.
7. **SellBudget:** `open(demo, +0)` to LTV 45%. Then `reduce` asks for 100 dUSDC, more than the debt, after each
   cooldown. Each call is clamped to the remaining budget and sells. When nothing is left, the next call reverts
   `SellBudget`; the sales so far are printed (`soldInWindow`).
8. Close-out: `close(demo)`. The wallet ends with no collateral and no debt on the three markets. Corridors that
   existed before the run are set back (`--no-restore` keeps the attack corridor).

Expected output: five lines, in this order, then the result line. The script exits 0, or 1 if any line is FAIL.

```
attack Expired: revert Expired 0x203d82d8 PASS
attack MoveLimit: revert MoveLimit 0x013da4fb PASS
attack UnapprovedMarket: revert UnapprovedMarket 0x99b2f06d PASS
attack BelowFloor: revert BelowFloor 0x4e7e2916 PASS
attack SellBudget: revert SellBudget 0x397b9774 PASS
result: 5 PASS, 0 FAIL (executor 0x…, wallet 0x…, block …)
```

A FAIL line names the error that came instead, for example
`attack BelowFloor: revert SellBudget 0x397b9774 FAIL (expected BelowFloor)`, or `(no revert)`.

### Run on a local fork (no real transactions)

```sh
anvil --fork-url https://base-sepolia-rpc.publicnode.com          # in another terminal; start it just before
cd <repo>
scripts/attack-corridor.sh --rpc http://127.0.0.1:8545 --close-existing --land
```

The script detects anvil by `web3_clientVersion`. It then replaces each wait with `evm_increaseTime` and tops up
gas ETH with `anvil_setBalance` if the wallet holds less than 0.01 ETH. It takes about 20 s. Start the fork just
before the run: the fork's Chainlink ETH/USD answer never updates, and `open` and `migrate` on live and backup
revert `StaleFeed` once it is older than 1,200 s (the script prints both feed ages first).

Without the team's key file, use a fresh key. Do not use anvil's account #0: on Base Sepolia that address carries
EIP-7702 delegation code. The wallet also needs a few units of each loan token, because the closes repay the
interest that accrues across the fork's time jumps; on the fork they come from the team wallet, impersonated
(checked on 10 Nov: `result: 5 PASS, 0 FAIL`):

```sh
cast wallet new --json | jq '{data: .data[0]}' > /tmp/k.json
A=$(jq -r .data.address /tmp/k.json); T=0x0B7Ed66f46fe83252a208563A88201BA60490d25; R=http://127.0.0.1:8545
cast rpc anvil_impersonateAccount "$T" --rpc-url "$R"
for t in $(jq -r '.base.tokens.usdc, .base.tokens.dusdc' deployments/testnet.json); do
  cast send "$t" "transfer(address,uint256)" "$A" 2000000 --from "$T" --unlocked --rpc-url "$R"
done
scripts/attack-corridor.sh --rpc "$R" --key-file /tmp/k.json --land
```

To test the build's executor rather than the prototype deployment, deploy the current source to the fork. Use a
copy of `executor/` outside the repository, because `Deploy.s.sol` writes `deployments.json`. Then point
`--deployments` at a copy of `deployments/testnet.json` with the new `base.executor`:

```sh
cp -R executor/src executor/script executor/foundry.toml /tmp/exec/ && ln -s "$PWD/executor/lib" /tmp/exec/lib
(cd /tmp/exec && RPC=http://127.0.0.1:8545 BACKUP_ORACLE=0x20Ff664b99676a45A13bD77ACB2AFECeA10Cd538 ./script/deploy.sh)
jq --arg e "$(jq -r .executor /tmp/exec/deployments.json)" '.base.executor=$e' deployments/testnet.json > /tmp/dep.json
scripts/attack-corridor.sh --rpc http://127.0.0.1:8545 --deployments /tmp/dep.json --close-existing --land
```

### Run on Base Sepolia

```sh
scripts/attack-corridor.sh --land 2>&1 | tee evidence/attacks.txt      # executor from deployments/testnet.json
```

Real time: about 10 s for Expired and a 60 s cooldown before each agent call that follows another. MoveLimit
(the moves) and SellBudget (the sales) need that real time, and the script prints each wait. Measured: 364 s with
one move already in the window (6 waits); about a minute more with none. Spend: the recorded fork run made 24
transactions using 3,515,658 gas, including closing the wallet's two old positions. At the 0.006 gwei gas price
read on Base Sepolia on 3 Nov, that is about 0.000021 ETH, plus Base's L1 data fee, which the fork does not
charge. The rest is Morpho interest of a few units on 0.9 USDC and 2 dUSDC for minutes, and the 1% sale bound on
about 0.22 dUSDC of demo collateral. Preconditions: at least 0.002 ETH; 0.0025 WETH, or the ETH to wrap it; and loan token for the
`close` calls (about 0.9 USDC and 2 dUSDC, both borrowed by the script itself). Also free USDC on live and
backup for the move (the script lowers the LTV to half the smaller free amount), and a demo pool within 1% of
the mock oracle for the sales; otherwise the first sale reverts `Slippage` and SellBudget is a FAIL. Stop the
rebalancer timer first (`DEPLOY.md` section 0): it sends from the same wallet, and two senders clash on the
nonce. The Expired step signs at the current nonce and publishes later.

Differences between executors: the prototype deployment (`0xB23e…0f40`) has `soldInWindow(address)`; the build
source has `soldInWindow(address,address)`. The script reads either, and calls nothing else that differs. Both
gave the five lines above on the fork (below).

### Recorded fork run

3 Nov 2026, anvil 1.8.6 fork of Base Sepolia at block 53,828,509 (block time 1,793,687,570), prototype
executor `0xB23e0d7e1D0182Db7f6f2637EC4Ac51c10290f40`, test wallet `0x0B7Ed66f46fe83252a208563A88201BA60490d25`,
`--close-existing --land`. At the fork block the wallet held a backup position (6.744896 USDC borrowed against
0.006 WETH) and 0.000265 WETH of collateral on demo, which the script closed first. Trimmed (approval and close
lines dropped):

```
attack-corridor: executor 0xB23e0d7e1D0182Db7f6f2637EC4Ac51c10290f40, wallet 0x0B7Ed66f46fe83252a208563A88201BA60490d25, chain 84532 (anvil fork: anvil/v1.8.6), block 53828509, time 1793687570
-- Expired: reduce(demo) signed with deadline = block time + 6 s, broadcast after the deadline
  signed at block time 1793687578, nonce 448, deadline 1793687584
  published after the deadline: tx 0x65764134d0d739883b1d74a1a62df5bb23831bd25bc548b1e52a7b9c1f3a2ad1 block 53828517 (time 1793687585) status 0x0 gasUsed 23290
attack Expired: revert Expired 0x203d82d8 PASS
-- MoveLimit: two moves between live and backup within 24 h, then a third
  feed ages: ETH/USD 170 s (max 1200), USDC/USD 77130 s (max 86400)
  open(live, 0.002 WETH, LTV 0.2000): borrowShares 899669000000 collateral 2000000000000000
  moves in the last 24 h before: 1
  move 2: migrate(100%) done at time 1793687588; movesInWindow = 2
  landed: tx 0x80f4f6891516aa31b35e00f267377668e77dc8a2616b229222ae31e29c29f030 block 53828521 status 0x0 gasUsed 67025 / 600000
attack MoveLimit: revert MoveLimit 0x013da4fb PASS
-- UnapprovedMarket: migrate from live to 0xcdffa34acd76819c6cc63553cd99aaa981848aa90134b63c7be4e502b6cc2b11 (live's parameters, LLTV 94.5%; no corridor)
  corridors(wallet, that id) = (0,0,0,0,false,false,false)
  landed: tx 0x1c5e03c9be9c139fe0a6dda7361363d62e1d7dfd15b9b77adee25a83e7e9b2aa block 53828522 status 0x0 gasUsed 35376 / 300000
attack UnapprovedMarket: revert UnapprovedMarket 0x99b2f06d PASS
-- BelowFloor: discretionary reduce(demo) at LTV 0.10, floor 0.30
  open(demo, 0.002 WETH, LTV 0.10): LTV 0.1000
  landed: tx 0x7414721896ce0b8f83044e37d7bac77f980837b3bbae7a891265efff5455c325 block 53828525 status 0x0 gasUsed 83324 / 400000
attack BelowFloor: revert BelowFloor 0x4e7e2916 PASS
-- SellBudget: discretionary reduces on demo at LTV 0.45 until the 5% budget is used, then one more
  open(demo, +0, LTV 0.45): LTV 0.4500, collateral 2000000000000000, sold in window 0
  sale 1: reduce(demo, ask 100 dUSDC) clamped; sold in window 99009936578672 wei WETH, collateral 1900990063421328, LTV 0.4214
  sale 2: reduce(demo, ask 100 dUSDC) clamped; sold in window 99990131362548 wei WETH, collateral 1900009868637452, LTV 0.4211
  sale 3: reduce(demo, ask 100 dUSDC) clamped; sold in window 99999487767315 wei WETH, collateral 1900000512232685, LTV 0.4211
  sale 4: reduce(demo, ask 100 dUSDC) clamped; sold in window 99999933310400 wei WETH, collateral 1900000066689600, LTV 0.4211
  landed: tx 0xf53e8a7d78eab33e5da80e1cb7dabb33f78a5e92dab6d0254527b64d56c63ec5 block 53828535 status 0x0 gasUsed 171390 / 400000
attack SellBudget: revert SellBudget 0x397b9774 PASS
  closed demo; positions live: borrowShares 0 collateral 0; backup: borrowShares 0 collateral 0; demo: borrowShares 0 collateral 0
  corridors restored to their values before the run: 3 (a market without one before keeps the attack corridor)
result: 5 PASS, 0 FAIL (executor 0xB23e0d7e1D0182Db7f6f2637EC4Ac51c10290f40, wallet 0x0B7Ed66f46fe83252a208563A88201BA60490d25, block 53828539)
```

The four sales total 99,999,933,310,400 wei, under the budget: 5% of the 0.002 WETH base is 100,000,000,000,000.
The MoveLimit and UnapprovedMarket gas (67,025 and 35,376) equals P5 B5 and B1b on testnet.

On the same fork, the build source deployed as `0x19c750e1AE531e53da6d5715fE1f23a4817d2F3A` (no moves in the window
before, so two moves were made) gave the same five PASS lines, with reverts of 67,113 (MoveLimit), 35,413
(UnapprovedMarket), 83,710 (BelowFloor) and 180,654 (SellBudget) gas.

The testnet code path (no cheat RPCs, real-time waits) was run on an anvil fork with 2 s blocks, with fork
detection switched off in a scratch copy of the script: 5 PASS in 364 s, with one move already in the window.

## `attack-key.sh`: the agent key outside its permission and the vault's policy

```sh
scripts/attack-key.sh <vault.testnet> [--key-file path] [--rpc URL] [--market live|backup|demo|0x<id>] \
                      [--other-receiver account]
```

The key is a function-call key on the vault's own account, the permission `register_agent` adds
(`vault/src/lib.rs`): receiver the vault, methods `reduce, migrate, exit`, allowance 2 NEAR. The script reads the
key's permission (`view_access_key`) and the vault's `get_state` (registered agent key, the market's `slice_max`
and `corridor_set`). It signs each attempt with the near CLI offline (`--offline … sign-with-access-key-file …
--nonce --block-hash --block-height display`), sends it with RPC `send_tx` and prints the RPC's error as returned:

| # | Attempt | Expected | Lands? |
|---|---|---|---|
| (a) | `withdraw` on the vault | `{"TxExecutionError":{"InvalidTxError":{"InvalidAccessKeyError":{"MethodNameMismatch":{"method_name":"withdraw"}}}}}` | No: refused at submission, no hash, no gas |
| (b) | `reduce` on another account (`--other-receiver`, default `v1.signer-prod.testnet`) | `{"TxExecutionError":{"InvalidTxError":{"InvalidAccessKeyError":{"ReceiverMismatch":{"ak_receiver":"<vault>","tx_receiver":"<other>"}}}}}` | No |
| (c) | `reduce` on the vault, the market's `slice_max` + 1 | `Smart contract panicked: amount above the slice maximum` inside `{"Failure":{"ActionError":…{"FunctionCallError":{"ExecutionError":…}}},"tx":"<hash>"}` | Yes: a hash; gas from the key's allowance |

Output: `attack MethodNameMismatch: … PASS`, `attack ReceiverMismatch: … PASS`, `attack SliceMax: … PASS`,
`result: 3 PASS, 0 FAIL`. The script exits 0, or 1 if any FAIL. The near CLI shows (a) as
`InvalidTxError: InvalidAccessKeyError: MethodNameMismatch {method_name: "withdraw"}` (P5 A1); the script prints
the RPC's JSON form of the same error.

(c) reaches the slice check only after `agent_check`: the registered agent key, before its expiry, after the
vault's cooldown (120 s in the testnet policy), on a market whose corridor is set. With any other key on the
vault, (a) and (b) are refused the same way, but (c) gets `Smart contract panicked: not the agent key` and is a
FAIL. That is the P5 A4 result, and correct for the vault, but it is not the slice check.

### Tested against a local NEAR sandbox (3 Nov)

Nothing in the repository holds an attested agent key: the key is made inside the CVM, and E's key no longer
exists. So the test gave the vault a throwaway key with the permission `register_agent` grants and patched the
vault's `agent` record to it. That is what `vault/tests/sandbox.rs` `patch_agent` does, and it is possible only
in a sandbox. Steps (a scratch harness, not in the repository): `near-sandbox` 2.13.4 (the binary
near-workspaces downloads, `vault/target/debug/build/near-sandbox-*/out/.near/near-sandbox-2.13.4/`), `init`
and `run --rpc-addr 127.0.0.1:3031`. Then `vault.test.near` with `vault/target/near/cmdk_vault.wasm`, `init`
with the three markets (`slice_max` 1,000 USDC, `cooldown_secs` 300), and `add-key … grant-function-call-access
--allowance '2 NEAR' --contract-account-id vault.test.near --function-names 'reduce, migrate, exit'` for the
test key and for a second key. Then `sandbox_patch_state` of `STATE`: `agent` = the test key with a 7-day
expiry, `corridor_set` = true on every market. All signing was offline with the near CLI and sent with
`send_tx`, as the script does.

With the agent key (`--other-receiver test.near`):

```
  key permission: {"FunctionCall":{"allowance":"1999567757479968400000000","method_names":["reduce","migrate","exit"],"receiver_id":"vault.test.near"}}
  vault get_state: registered agent ed25519:HVqeWGiMGzUUkGbxveupZmMiEpsvRvx6uaiCRrtWBjHJ (this key: yes); market 0xa25ac517…535c slice_max 1000000000, corridor_set true
attack MethodNameMismatch: {"TxExecutionError":{"InvalidTxError":{"InvalidAccessKeyError":{"MethodNameMismatch":{"method_name":"withdraw"}}}}} PASS
attack ReceiverMismatch: {"TxExecutionError":{"InvalidTxError":{"InvalidAccessKeyError":{"ReceiverMismatch":{"ak_receiver":"vault.test.near","tx_receiver":"test.near"}}}}} PASS
attack SliceMax: {"Failure":{"ActionError":{"index":0,"kind":{"FunctionCallError":{"ExecutionError":"Smart contract panicked: amount above the slice maximum"}}}},"tx":"7Kj3ed514P3CdWHEz8emHWfMns2GhpeBMXR4tAagGjTN"} PASS
result: 3 PASS, 0 FAIL (vault vault.test.near, key ed25519:HVqeWGiMGzUUkGbxveupZmMiEpsvRvx6uaiCRrtWBjHJ)
```

With the second key (same permission, not the registered agent), (a) and (b) as above, and
`attack SliceMax: {…"ExecutionError":"Smart contract panicked: not the agent key"…} FAIL`.

Not tested: the public testnet RPC (`send_tx` on `rpc.testnet.near.org`, which P5 used on 28 Oct), and a vault
created by the factory with a key registered through `register_agent`.

### Run on NEAR testnet

```sh
scripts/attack-key.sh <vault>.testnet --key-file <path to the key's near-credentials JSON> --other-receiver <factory>.testnet
```

The script needs a key it can sign with. The build's vaults have no full-access key, and the only function-call
key is the agent's, made inside the CVM, so outside the CVM there is nothing to sign with. (a) and (b) depend
only on the key's permission, so any key with the `register_agent` permission on that vault shows them. (c)
needs the registered agent key.
