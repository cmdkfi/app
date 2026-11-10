# Deploying the vault Global Contract and the factory to NEAR testnet

Written for Fri 6 Nov 2026 with near-cli-rs 0.30.1; **run on Wed 4 Nov 2026** by C with the founder's go (decided
4 Nov: the schedule is not fixed, reviews come after everything is ready). Testnet only. The same sequence (publish
by hash, factory `init`, key deletion, `create_vault`, and the raw-RPC checks of steps 2 and 7) runs in the sandbox
test `factory` in `vault/tests/sandbox.rs`.

**Run record (4 Nov).** `ACCOUNT` `prime-measure-1792736012.testnet`, `RELAYER` `relayer.prime-measure-1792736012.testnet`,
`OWNER` `0x0B7Ed66f46fe83252a208563A88201BA60490d25`. Step 1: reproducible build from commit `975b98d`, 783,124
bytes, SHA-256 hex `def9671e099fe325a7ac8c43831de0334a3dc75221f6c302b17a893e0377e1ba`, `HASH`
`G1Q5ksFhGbgPTN4A2DuvgraYmsbRpfwb78B547gAKXWq`; factory wasm 152,851 bytes. Step 2: tx
`8PsReqwVUKeWqFbgN2DXTYtQM5PvRD9mZP2BYbRoFJng`, 78.3124 NEAR burnt plus 0.00056 NEAR fee; the account went from
149.31 to 70.99 NEAR, so a second publish needs a faucet top-up of about 10 NEAR first. Step 3: tx
`5oex8xeH5wNBtaLsx97NAW7MRMGWRDs9ip891hRZos2d`. Step 4: tx `EYZ7syKuHXFTEdynWhBqr1LpmFf19AraPFU6wKZ2XV8j`, `get_config`
as expected. Step 5: tx `6vsJtei5B6AKSfnfAh3VhyCfya8PhAjZjUJT6W6BswYC`, key list `[]`. Step 6: tx
`5gEoJutK5xCupYUThWBnseyvcV9jutHdLUtRojJbCeZk`, log `creating vault: v1.factory.prime-measure-1792736012.testnet
0xb3ccd0ad7e5fca9d4c18cf59d19fab4703eee6fb`, 0.354 Tgas burnt in the transaction. Step 7: `global_contract_hash` =
`HASH`, `code_hash` `11111111111111111111111111111111`, amount 2 NEAR, storage 645 bytes, no keys, `get_state` as
expected (owner lower case, `owner_nonce` 0, three markets with `corridor_set` false), `count` 1.

Steps 2, 3 and 6 move NEAR; each says how much. Step 5 is irreversible: after it the factory has no keys,
so nobody can change, redeploy or withdraw from it. A mistake found after step 5 means a new factory account.

## 0. Variables (set once per shell)

```sh
cd <repo>/vault
ACCOUNT=<your account>.testnet          # pays steps 2 and 3; needs at least 81 NEAR
RELAYER=<relayer account>.testnet       # pays step 6; needs at least 2.1 NEAR
OWNER=<owner EVM address, 0x...>        # the wallet that signs owner messages for vault v1
FACTORY=factory.$ACCOUNT
SIGNER=v1.signer-prod.testnet
MORPHO=0xBBBBBbbBBb9cC5e90e3b3Af64bdAF62C37EEFFCb
RPC=https://rpc.testnet.near.org
```

All signing uses `sign-with-keychain`, as in the research notes' earlier testnet deployments.

## 1. Build both wasms (no NEAR)

The vault is built reproducibly (decided 3 Nov): `cargo near build reproducible-wasm` (cargo-near 0.22.0) builds
inside the Docker image pinned in `vault/Cargo.toml`, `[package.metadata.near.reproducible_build]`
(`sourcescan/cargo-near:0.21.1-rust-1.96.0`, identified by its digest), with `--locked` against the committed
`Cargo.lock`. It first clones the local repository at `HEAD` into a temporary directory, so only committed files
are built; uncommitted changes give a warning and are left out. It writes NEP-330 build info into the wasm
(`contract_source_metadata`): `link` `https://github.com/cmdkfi/app/tree/<commit>` (from `package.repository`)
and the image digest. The build does not contact GitHub; `cargo near deploy` would (it checks the commit is on the
remote), and this file does not use it. A verifier rebuilds from that link, so it works once `cmdkfi/app` is
public with the commit in its history.

Decided 3 Nov: `cmdkfi/app` is made public on Friday at deploy time (a week before the deadline), so no mirror is
needed; the build runs from this checkout, on the exact commit to be published. Before building: that commit
pushed to `origin/main`, Docker running with room to pull the image and build, and the working tree clean
(`git status --short` prints nothing).

```sh
cd <repo>/vault
git rev-parse HEAD                          # the commit named in the wasm's link
HASH=$(cargo near build reproducible-wasm 2>&1 | awk '/checksum bs58/ {print $NF}'); echo "$HASH"
shasum -a 256 target/near/cmdk_vault.wasm
(cd factory && cargo near build non-reproducible-wasm)
ls -l target/near/cmdk_vault.wasm factory/target/near/cmdk_factory.wasm
```


`HASH` is the base58 SHA-256 of the vault wasm, the Global Contract's code hash. On 3 Nov (after V6 and the
`stables_in` 0 refusal) the non-reproducible build (`cargo near build non-reproducible-wasm`, Rust 1.95.0 on the
host) gave 782,389 bytes, hex `bcd0111f4ae942d3a04a6e66e9cbbb9377d12039358fb2881cdcc24866a8203d`, base58
`Di3fRwTG356m252JJgEL58EBnMKrwD4tSR5NALksn24c`; the factory wasm was 152,851 bytes. The reproducible build uses
another toolchain (Rust 1.96.0 in the image) and embeds the build info, so its size and hash differ; that is
fine as long as the same `HASH` is used in steps 2 and 4. Record its size and hash here after the build. If
`HASH` is empty, read the "SHA-256 checksum bs58" line of the build output by hand.

## 2. Publish the vault as a Global Contract by hash (spends about 78.24 NEAR, burnt)

The cost is 0.0001 NEAR per byte, burnt and not refundable: 782,389 bytes (3 Nov, non-reproducible build) =
78.2389 NEAR, plus gas (the sandbox run burnt 59.8 Tgas, about 0.006 NEAR). The final figure is read from
Friday's reproducible wasm: its size from step 1 × 0.0001 NEAR.

```sh
near contract deploy-as-global use-file target/near/cmdk_vault.wasm as-global-hash $ACCOUNT \
  network-config testnet sign-with-keychain send
```

Check (read-only); expected output: the value of `HASH`.

```sh
curl -s $RPC -H 'Content-Type: application/json' -d '{"jsonrpc":"2.0","id":"cmdk","method":"query","params":
  {"request_type":"view_global_contract_code","finality":"final","code_hash":"'$HASH'"}}' | jq -r .result.hash
```

## 3. Create the factory account (spends 2 NEAR, locked for good)

```sh
near account create-account fund-myself $FACTORY '2 NEAR' autogenerate-new-keypair save-to-keychain \
  sign-as $ACCOUNT network-config testnet sign-with-keychain send
```

The factory's storage in the sandbox was 153,142 bytes (code plus state), which locks 1.53142 NEAR; the rest
pays the gas of steps 4 and 5. After step 5 the whole balance stays in the factory with no way out.

## 4. Deploy the factory and `init` it with the hash (gas only, paid by the factory account)

```sh
near contract deploy $FACTORY use-file factory/target/near/cmdk_factory.wasm \
  with-init-call init json-args '{"code_hash":"'$HASH'","signer":"'$SIGNER'","path":"cmdk-base","morpho":"'$MORPHO'"}' \
  prepaid-gas '30 Tgas' attached-deposit '0 NEAR' network-config testnet sign-with-keychain send
```

Check before going on (read-only); expected: `code_hash` equal to `HASH`, `signer` `v1.signer-prod.testnet`,
`path` `cmdk-base`, `morpho` as above, `count` 0.

```sh
near contract call-function as-read-only $FACTORY get_config json-args '{}' network-config testnet now
```

## 5. Delete every key of the factory account (gas only; irreversible)

```sh
FKEYS=$(curl -s $RPC -H 'Content-Type: application/json' -d '{"jsonrpc":"2.0","id":"cmdk","method":"query","params":
  {"request_type":"view_access_key_list","finality":"final","account_id":"'$FACTORY'"}}' \
  | jq -r '[.result.keys[].public_key] | join(",")'); echo "$FKEYS"
near account delete-keys $FACTORY public-keys "$FKEYS" network-config testnet sign-with-keychain send
```

Check (read-only); expected output: `[]`.

```sh
curl -s $RPC -H 'Content-Type: application/json' -d '{"jsonrpc":"2.0","id":"cmdk","method":"query","params":
  {"request_type":"view_access_key_list","finality":"final","account_id":"'$FACTORY'"}}' | jq -c .result.keys
```

## 6. Create vault v1 from the relayer (spends 2 NEAR deposit plus gas)

The deposit becomes the vault's balance (it pays the agent key's gas later). The policy below uses the Base
Sepolia executor in `deployments/testnet.json` (`base.executor`, written by the root `DEPLOY.md` step 2);
`cooldown_secs` 120 is §4.1's figure and `slice_max` 1,000,000,000 (1,000 USDC at 6 decimals) is the sandbox
tests' value: confirm both before running. dUSDC's decimals were not checked for this file.

```sh
EXEC=$(jq -r .base.executor ../deployments/testnet.json); echo $EXEC   # must print the step 2 executor
USDC=0x036CbD53842c5426634e7929541eC2318f3dCF7e; DUSDC=0xeb68Fd5bAAAfD4e46b43bAa7fEAb3216B87b10d5
M='"collateral_token":"0x4200000000000000000000000000000000000006","executor":"'$EXEC'","slice_max":"1000000000"'
POLICY='{"cooldown_secs":120,"markets":[
 {"id":"0xa25ac5171b7e500b82653d3750b91bb44e2409c32ca3c9e80b0db077e8a4535c","loan_token":"'$USDC'",'$M'},
 {"id":"0x99d43c0f2a0eb6172774598a4e34e7eee09caa502692c2b2df0f40e1e3c5ea75","loan_token":"'$USDC'",'$M'},
 {"id":"0x55872b53dba6147b6badf9d4277bc166dfb7d5f8ac8d491da093960dbef6d585","loan_token":"'$DUSDC'",'$M'}]}'
near contract call-function as-transaction $FACTORY create_vault json-args '{"owner":"'$OWNER'","policy":'"$POLICY"'}' \
  prepaid-gas '150 Tgas' attached-deposit '2 NEAR' sign-as $RELAYER network-config testnet sign-with-keychain send
```

The transaction's logs include `creating vault: v1.<factory> 0x...` (the vault's Base address). The sandbox
run burnt 20.5 Tgas in total.

`create_vault` first repeats the checks of the vault's `init` that need no vault (owner and token addresses 20
bytes of hex, market ids 32 bytes, a non-zero executor, at least one market, no duplicate ids). A refusal there
fails the transaction in the factory's own call: the protocol returns the 2 NEAR to the relayer (only gas is
spent) and the vault number is not used. If the signer call fails, the factory returns the deposit to the
relayer. After these checks, the only way for the deposit to stay in the factory (for good, step 5) is an
`init` failure the factory could not foresee; then no account is created and the next call creates `v2`.

## 7. Read-only checks of the vault

```sh
V=v1.$FACTORY
# Expected: global_contract_hash = HASH, code_hash 11111111111111111111111111111111, amount about 2 NEAR.
curl -s $RPC -H 'Content-Type: application/json' -d '{"jsonrpc":"2.0","id":"cmdk","method":"query","params":
  {"request_type":"view_account","finality":"final","account_id":"'$V'"}}' \
  | jq '.result | {global_contract_hash, code_hash, amount, storage_usage}'
# Expected: [] (the vault has no keys).
curl -s $RPC -H 'Content-Type: application/json' -d '{"jsonrpc":"2.0","id":"cmdk","method":"query","params":
  {"request_type":"view_access_key_list","finality":"final","account_id":"'$V'"}}' | jq -c .result.keys
# Expected: owner = OWNER (lower case), signer v1.signer-prod.testnet, path cmdk-base, base_address = the address
# in step 6's log, morpho as above (lower case), owner_nonce 0, three markets with corridor_set false.
near contract call-function as-read-only $V get_state json-args '{}' network-config testnet now
# Expected: count 1.
near contract call-function as-read-only $FACTORY get_config json-args '{}' network-config testnet now
```

Optional cross-check of the Base address with the signer itself (read-only): the result is
`secp256k1:` + base58 of 64 bytes; its keccak256, last 20 bytes, is `base_address` (`scripts/relayer.py` does
this computation).

```sh
near contract call-function as-read-only $SIGNER derived_public_key \
  json-args '{"path":"cmdk-base","predecessor":"'$V'","domain_id":0}' network-config testnet now
```
