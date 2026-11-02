# Deploying the vault Global Contract and the factory to NEAR testnet

For the founder to run on Fri 6 Nov 2026 with near-cli-rs 0.30.1. Testnet only. Nothing here was run on testnet
when this file was written; the same sequence (publish by hash, factory `init`, key deletion, `create_vault`,
and the raw-RPC checks of steps 2 and 7) runs in the sandbox test `factory` in `vault/tests/sandbox.rs`.

Steps 2, 3 and 6 move NEAR; each says how much. Step 5 is irreversible: after it the factory has no keys,
so nobody can change, redeploy or withdraw from it. A mistake found after step 5 means a new factory account.

## 0. Variables (set once per shell)

```sh
cd /Users/vf/cmdk/app/vault
ACCOUNT=<your account>.testnet          # pays steps 2 and 3; needs at least 80 NEAR
RELAYER=<relayer account>.testnet       # pays step 6; needs at least 2.1 NEAR
OWNER=<owner EVM address, 0x...>        # the wallet that signs owner messages for vault v1
FACTORY=factory.$ACCOUNT
SIGNER=v1.signer-prod.testnet
MORPHO=0xBBBBBbbBBb9cC5e90e3b3Af64bdAF62C37EEFFCb
RPC=https://rpc.testnet.near.org
```

All signing uses `sign-with-keychain`, as in the research notes' earlier testnet deployments.

## 1. Build both wasms (no NEAR)

```sh
HASH=$(cargo near build non-reproducible-wasm 2>&1 | awk '/checksum bs58/ {print $NF}'); echo "$HASH"
shasum -a 256 target/near/cmdk_vault.wasm
(cd factory && cargo near build non-reproducible-wasm)
ls -l target/near/cmdk_vault.wasm factory/target/near/cmdk_factory.wasm
```

`HASH` is the base58 SHA-256 of the vault wasm, the Global Contract's code hash. On 2 Nov this build gave
767,944 bytes, hex `abab650ee74355348029d0fce8e9995d1cb9682968d1be9bda09b2b3ae287547`, base58
`CZ8JKs2Fpvyr7WHLz1RLdDtgMuhqhpa3Mbb49YZxASmt`; the factory wasm was 152,851 bytes. A non-reproducible build on
another day or toolchain may give a different hash; that is fine as long as the same `HASH` is used in steps 2
and 4. If `HASH` is empty, read the "SHA-256 checksum bs58" line of the build output by hand.

## 2. Publish the vault as a Global Contract by hash (spends about 76.80 NEAR, burnt)

The cost is 0.0001 NEAR per byte, burnt and not refundable: 767,944 bytes = 76.7944 NEAR, plus gas (the sandbox
run burnt 59.8 Tgas, 0.006 NEAR; the root account's balance fell by 76.800357 NEAR in total).

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
Sepolia deployment in `executor/deployments.json`; `cooldown_secs` 120 is §4.1's figure and `slice_max`
1,000,000,000 (1,000 USDC at 6 decimals) is the sandbox tests' value: confirm both before running. dUSDC's
decimals were not checked for this file.

```sh
EXEC=0xB23e0d7e1D0182Db7f6f2637EC4Ac51c10290f40
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
