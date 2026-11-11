# AI screening rehearsal 13, Codex in a container (11 Nov 2026)

The same container, tool and prompt as rehearsal 8 (Linux, Node 22, Rust stable with cargo-near 0.22.0, near-cli-rs 0.30.1, Foundry 1.9.0; no Docker, no browser libraries, no keys; codex-cli 0.162.1, gpt-6-astra, reasoning high; local signatures with throwaway or fixture keys allowed, nothing reaches a network), on a fresh clone of commit 48d1ff1, the state after the day's changes (published runs and research, the evidence index, the README corrections of rehearsals 9 to 12; web app read from its `main` at 46dc42e with the light design, the desktop-only testnet flow and the Venue stress scene; landing at d4dd3b9). 20:01 to 20:41 UTC: longer than rehearsals 6 to 8 because the tool fetched a Chromium and ran the browser suite three times inside the 3 GB container. Rehearsal 8 scored 8.5. The report below is the tool's final answer, unedited except temporary paths shortened to `<tmp>`.

---

## Summary

1. Screened cmdk at commit `48d1ff12cb87070100957670b7933702d030af13`, including its public web-app and landing repositories, on 11 November 2026.
2. The deployed vault configuration, executor, agent registration, protective actions, migration, exit, and revocation have substantial corroborating evidence.
3. Passed: 15 vault unit tests, 8 sandbox tests, 65 agent tests, 64 Solidity tests, and 174 web-app unit tests.
4. Browser tests initially had two timing failures; both passed in isolation. Docker-dependent source and measurement rebuilds could not run.
5. **Preliminary score: 8.5/10.** This is screening evidence for human judges, not an acceptance decision; a low AI score alone cannot reject.

## Reproducibility

### Scope and execution conditions

| Component | Screened revision |
|---|---|
| Protocol repository | `48d1ff12cb87070100957670b7933702d030af13` |
| [Web app](https://github.com/cmdkfi/cmdk-app) | `46dc42ebeebeab986384fc5c92ccb17ce882dba6` |
| [Landing page](https://github.com/cmdkfi/landing) | `d4dd3b996c44077aa8b15fc75d1c584114f0389f` |

Environment: Linux ARM64, Node 22.23.4, pnpm 11.7.0, Rust 1.99.1, cargo-near 0.22.0, near-cli 0.30.1, Foundry 1.9.0; no Docker.

No public-network transactions, account creation, funding, deployments, or paid inference were performed. Transaction-producing tests used local NEAR sandboxes or a local Anvil fork. The temporary fork was stopped and its generated key deleted.

The original checkout remained clean: final `git status --short` produced no output. Clones, downloaded evidence, browser libraries, and logs were stored under `<tmp>`; builds created ordinary build outputs.

Some initial installations/builds were killed by the environment’s memory limit. They succeeded after reducing concurrency or memory use. Those initial resource failures are not counted as protocol defects.

### Artifact results

| Artifact | Result |
|---|---|
| **Deployed NEAR vault bytes** | **PASS.** RPC-downloaded Wasm SHA-256 equals the manifest and account’s global-contract hash: `def9671e099fe325a7ac8c43831de0334a3dc75221f6c302b17a893e0377e1ba`. Base58: `G1Q5ksFhGbgPTN4A2DuvgraYmsbRpfwb78B547gAKXWq`. |
| **Vault source metadata** | **PASS for metadata/source continuity.** `contract_source_metadata` identifies commit `975b98d454e7080495230b3aff73296a92e714c2`. Diffing that revision against HEAD for `vault/src`, `vault/Cargo.toml`, and `vault/Cargo.lock` produced no differences. This does not substitute for reproducing the published binary. |
| **Pinned vault rebuild** | **COULD NOT RUN.** `./scripts/reproduce-hash.sh --reproducible --vault …` exited with “a running Docker daemon (docker info failed)”. |
| **Host Wasm builds** | **PASS.** Mock signer, vault, and factory built successfully. Host vault: 773,607 bytes, SHA-256 `65d34ff0a89077b52a9135914683ec9a675d7f6fc76deba44d6db610c7dc7204`. This differs from deployment; the README explicitly warns that host builds depend on toolchain/platform. |
| **Factory** | **PASS for deployed state; binary reproduction incomplete.** Published hash `5315458701e4f52c9c033d05d492ec75d18076a128a0261f153434670971c863` matched. Factory has no access keys; `get_config` points to the published vault hash and reported 12 vaults. Host rebuild produced 151,703 bytes and hash `cbb326fcb2bf7812505371cec19f9152cc9042db4b293326461f71d0d9ad69a0`, rather than the documented macOS/Rust-1.95 artifact. |
| **Solidity executor** | **PASS, exact runtime reproduction.** Foundry produced the same 16,459 runtime bytes after inserting the documented constructor constants—Morpho address and 60-second cooldown—into the compiler’s immutable-reference locations. SHA-256: `9d56fd7fd6dac219fa292785e65b965d49cc8a8161570eedf3f771c10e377c5d`. [Comparison log](<tmp>/executor-rebuild-check.txt). |
| **Explorer verification** | **PASS.** The three executor source files equal the [Blockscout-verified source](https://base-sepolia.blockscout.com/address/0xA9bbabE6d75C5BA96CA095586284774c9D09D18b?tab=contract). Compiler/settings and RPC runtime bytes agree with the verified deployment. |
| **Published agent image** | **PASS for distribution integrity.** Registry manifest digest matches `sha256:e72564a8e1fe513a578dc37863491a84f7873d7bfba28314ce59ac3f5ebc94d1`; image configuration matches `sha256:d3b51d670192b3b77b64241e70dca28eb97fa446128c3aca79ba548c18efcfcb`. The extracted binary hashes to the documented `0a8f47c3eea1bfed9f02f84f4dd32bf98f24d0bf4928392801300bcf51def68d`. |
| **Agent image rebuild** | **COULD NOT RUN.** `./build.sh` requires Docker and stopped with `docker: command not found`. Image integrity is verified; source-to-image reproducibility is not independently established here. |
| **TDX measurements** | **PARTIAL.** Compose SHA-256 and independently replayed RTMR3 match the recorded quote/event log. Full OS-image, MRTD, and RTMR0–2 reconstruction requires Docker and was not run. |
| **Web-app production build** | **PASS.** Locally built primary JavaScript and CSS assets match the deployed assets byte for byte. The live sandbox rendered correctly with simulation labels. |
| **Landing page** | **PASS.** Build, local serving, file preview, and supplied content/link checks passed. Live site returned HTTP 200. |

### Research reproducibility

I checked the supplied data rather than treating narrative results as independently reproduced experiments.

- **155 available files matched their recorded hashes.** Fifteen listed artifacts were not downloaded; the larger release/raw-data requirements prevented a complete historical replay in this run.
- `liquidations.parquet` contains **20,479 included Morpho liquidations**, plus 1,502 excluded Aave records.
- Independently joining the supplied replay results and case plan, applying the documented collateral and price-data filters, reproduced **1,890 of 1,895** avoided observed liquidations in training and **5,009 of 5,011** in holdout.
- The default’s advantage over the parameter grid is supported for liquidation count. It should not be read as superiority on every equity or economic metric.
- Stored hostile-input results support the reported **21/30 exit proposals, reduced to 2/30 by agreement rules**. I did not rerun paid model inference.
- The original 20-position experiment cannot be reconstructed completely from this checkout: original scripts/data are missing, and a retained script imports missing `rpc.py`. This is disclosed in [research/README.md](/work/cmdk/research/README.md:16).
- Historical replay build commands use macOS-specific `-dynamiclib`/`.dylib`; the documented Linux changes would require editing them. See [DATA.md](/work/cmdk/research/liquidation-dataset/DATA.md:45).

## Feature verification table

“Verified” means supported by inspected code and the stated tests/live evidence—not a security audit or proof against every possible attack.

| README claim | Evidence | Verdict |
|---|---|---|
| Immutable NEAR vault and keyless factory | Live vault is a Global Contract referenced by the published hash; no full-access key. Factory has no keys and fixes the vault hash. Creation uses the global contract in [factory code](/work/cmdk/vault/factory/src/lib.rs:128). | **Verified configuration/design.** Exact Rust source-to-deployment rebuild remains outstanding. |
| Agent credential permits only `reduce`, `migrate`, `exit` | Live key lists exactly those methods and the vault itself as receiver. Registration constructs this limited key in [lib.rs](/work/cmdk/vault/src/lib.rs:471). Sandbox tests passed. | **Verified.** |
| Owner actions require wallet signatures, nonce, and expiry | Verification in [owner.rs](/work/cmdk/vault/src/owner.rs:57); owner-message sandbox tests passed; historical revoke/close/withdraw evidence corroborated. | **Verified implementation and recorded execution.** |
| Agent cannot choose an arbitrary recipient or arbitrary executor calldata | Vault constructs the three calls in [lib.rs](/work/cmdk/vault/src/lib.rs:435); executor acts on `msg.sender` and returns remaining assets there. Exact executor runtime reproduced. | **Verified within the inspected paths and tests.** |
| Corridor, sell budget, cooldown, migration count, and deadlines constrain the agent | Live corridor is 50% target / 70% act / 30% floor / 20% budget; NEAR cooldown 120 seconds, Base 60 seconds. Enforcement in [Executor.sol](/work/cmdk/executor/src/Executor.sol:245); five local-fork attacks produced expected reverts. | **Verified.** Protective sales are explicitly exempt from the discretionary budget. |
| Oracle-based swap limit and target/floor calculations | [CorridorMath.sol](/work/cmdk/executor/src/CorridorMath.sol:11), fixed pool/feed configuration, executor tests and exact deployed-code match. | **Verified implementation.** Target/floor are not exact post-fill guarantees; README discloses overshoot. |
| Attested agent key registered on-chain | Successful archival registration transaction; matching live key, approval, and expiry. Quote verification in [attest.rs](/work/cmdk/vault/src/attest.rs:119). | **Supported by code, tests, and live registration.** Full independent measurement reconstruction incomplete. |
| Signed model responses and rules-only fallback | Offline verification of 793 live-index records; signature/request/response checks in [verify-transcript.sh](/work/cmdk/agent/verify-transcript.sh:53). Two unsigned responses were rejected; rules-only outcomes recorded. | **Verified with an explicit trust limit:** the offline verifier trusts the recorded model-attestation verdict. |
| Agent watches multiple approved vaults | Relayer/agent reads showed four watched vaults; observed check intervals approximately 3.6–7.8 seconds. Revoked v2 absent. Screens and action selection in [logic.rs](/work/cmdk/agent/src/logic.rs:77). | **Verified at screening time.** Continuous uptime is not established. |
| Live protective reduction returns LTV toward target | Successful Base receipt reports approximately **49.96745% → 40.01515%** after owner changed the target/act settings. | **Verified**, including the small target difference. |
| Migration preserves the position | Successful migration receipt; **0.008335601888990126 WETH** moved with recorded debt **7.729880 USDC**. Current position is on backup. [Migration checks](/work/cmdk/executor/src/Executor.sol:305). | **Verified for the demonstrated migration.** |
| Exit repays debt and leaves collateral at the vault-controlled address | Successful exit receipt: **4.442487 USDC repaid**, **0.002005782373218876 WETH sold**, **0.001991908818334372 WETH remaining**. [Exit code](/work/cmdk/executor/src/Executor.sol:360). | **Verified.** Feed-triggered demonstration used team-authored feeds. |
| Revocation removes authority; owner can close and withdraw | v2 has no keys, no registered agent, and no approved measurement. Successful revoke, close, and withdrawal receipts; withdrawal transfers exactly **0.002 WETH** to the owner. | **Verified.** A subsequent rejected agent attempt was not observed; the README correctly presents that as hypothetical. |
| Stablecoin-funded opening | Implemented in [Executor.sol](/work/cmdk/executor/src/Executor.sol:192) and vault owner actions; relevant suites passed. README explicitly supplies no live transaction. | **Code/test verified; not live-demonstrated.** |
| Guided app, recovery handling, sandbox, policy validation | Production assets match deployment; 174 unit tests passed. Browser scenarios cover onboarding, recovery, policy mismatch, revocation, simulated crashes and migration. Policy checks in [vault.ts](<tmp>/cmdk-app/app/src/protocol/near/vault.ts:73). | **Substantially verified**, with browser timing failures detailed below. Live signing is desktop-only. |
| AI improves protection beyond deterministic rules | Recorded price scenes show essentially identical AI/keeper results. Migration and text-triggered exit are corroborated. Historical lead-time results are stored experiments, not fresh inference runs. | **Partially demonstrated.** No demonstrated AI advantage in the three price scenes. |
| Any-chain/private funding, BTC, additional venues, buy-back, outage backstop | README labels these as production-release/roadmap items. Current build uses WETH and Morpho on Base Sepolia; transcripts and owner-position linkage are public. | **Not implemented/demonstrated in this submission build; appropriately disclosed.** |

## Verify-it results table

The following aliases represent the README’s placeholders:

```text
V   = v1.factory.prime-measure-1792736012.testnet
V2  = v2.factory.prime-measure-1792736012.testnet
F   = factory.prime-measure-1792736012.testnet
X   = 0xA9bbabE6d75C5BA96CA095586284774c9D09D18b
B   = 0xb3ccd0ad7e5fca9d4c18cf59d19fab4703eee6fb
L   = 0xa25ac5171b7e500b82653d3750b91bb44e2409c32ca3c9e80b0db077e8a4535c
K   = 0x99d43c0f2a0eb6172774598a4e34e7eee09caa502692c2b2df0f40e1e3c5ea75
RPC = https://base-sepolia-rpc.publicnode.com
E   = https://49ecae9fcc4a6970956d8c987386315297922a69-8080.dstack-pha1-prod5.phala.network
BOOT = boot-1794264608-2286af92
```

| Step | Command or prescribed inspection | Expected text/result | Observed result | Status |
|---|---|---|---|---|
| 1a | `near account list-keys V network-config testnet now` | One function-call key; `reduce,migrate,exit`; no full-access key | Exactly that; key `ed25519:3Kmykr9LYfH6STHHD2dDpENbFsait9UZqcfWbDw7pAVg` | **PASS** |
| 1b | `near contract call-function as-read-only V get_state json-args '{}' network-config testnet now` | Lowercase wallet owner and derived Base address | Owner `0x0b7ed66f46fe83252a208563a88201ba60490d25`; Base address B | **PASS** |
| 2a | `near account view-account-summary V network-config testnet now` | Global Contract by published hash | Published hash matched | **PASS** |
| 2b | README `view_code`/Base64/SHA-256 pipeline, reproduced below | Same published hash | `def9671e…0377e1ba` | **PASS** |
| 2c | `near contract call-function as-read-only V contract_source_metadata json-args '{}' network-config testnet now` | Source link names `975b98d…` | Matching source revision and pinned build information | **PASS** |
| 2d | `./scripts/reproduce-hash.sh --reproducible --vault V` | Matching hex/base58 hashes; `result MATCH` | Exit 1: missing running Docker daemon | **COULD NOT RUN** |
| 2e | Factory `list-keys`, `view-account-summary`, and `get_config` read calls | No keys; documented factory hash; fixed vault hash | All matched; count 12 | **PASS** |
| 3a | `get_state`, as in 1b | Markets, executor, `slice_max`, `corridor_set`, cooldown | Three markets; correct executor; `slice_max=1000000000`; cooldown 120 | **PASS** |
| 3b | `cast call X "corridors(address,bytes32)(uint64,uint64,uint64,uint64,bool,bool,bool)" B L --rpc-url "$RPC"`; repeated with K | `5e17`, `7e17`, `3e17`, `2e17`, then three `true` values | Exact advertised seven-line values for both markets | **PASS** |
| 4a | `./scripts/ltv.sh V --market backup` | Position figures and `cross-check MATCH with the "at lastUpdate" LTV (WAD)` | At block 57527267: collateral 0.008335601888990126 WETH; stored debt 7.730581 USDC; accrued debt 7.730789; LTV 44.6174% / 44.6186%; `MATCH` | **PASS** |
| 4b | `./scripts/ltv.sh V` | `(no position on this market)` | Exact message for live market; cross-check matched | **PASS** |
| 5a | `cast call 0xBBBBBbbBBb9cC5e90e3b3Af64bdAF62C37EEFFCb "isAuthorized(address,address)(bool)" B X --rpc-url "$RPC"` | `true` | `true` | **PASS** |
| 5b | `cast call X "config(bytes32)(((address,address,address,address,uint256),address,address[2],uint32[2]))" L --rpc-url "$RPC"`; repeated with K | Correct market, fixed pool, feeds, ages `[1200,86400]` | All matched manifest, including pool `0x57183717A087d2fe3Ad890873877244c3B96156c` | **PASS** |
| 6a | `get_state`; inspect/read `registration-v1.json` transaction through archival NEAR RPC | Approved measurement and registered key with expiry | Matching boot/key; successful registration; expiry `1794869548` | **PASS** |
| 6b | `./scripts/p3_reproduce_measurements.sh <work dir> evidence/<boot> dstack-0.5.12 1 2G` | OS-image and register `MATCH` results, then `ALL MATCH` | Not launched because required Docker is absent | **COULD NOT RUN** |
| 6c | `sha256sum evidence/boot-1794264608-2286af92/app-compose.json` | Compose hash matches event log and quote | `db4f508517a01b31986dc9ec10eefd7491eb249cc209fb6b395c449e91c1dfc4`; matched | **PASS** |
| 7a | `./scripts/attack-corridor.sh --land 2>&1 \| tee evidence/attacks.txt` | Five advertised reverts | Not executed: would send public transactions and overwrite repository evidence; team key also absent | **COULD NOT RUN under screening rules** |
| 7b | Documented fresh-key local fork alternative: `scripts/attack-corridor.sh --rpc "$R" --key-file /tmp/k.json --land` | Five advertised reverts; `result: 5 PASS, 0 FAIL` | Exact expected selectors and result; all transactions confined to localhost | **PASS** |
| 8a | `curl -s "$E/transcripts"`; `curl -s "$E/transcripts" \| jq -r '.transcripts[].name'` | Sorted index with required fields | 793-record snapshot; required fields present; every downloaded size/hash matched | **PASS** |
| 8b | `curl -s "$E/transcripts/<name>" -o "/tmp/<name>"`; `./agent/verify-transcript.sh "/tmp/<name>"` | `ALL PASS`, or documented no-call/failure outcomes | Selected signed record: `ALL PASS`. Complete snapshot: 379 `ALL PASS`, 412 `NO-CALL`, two `4 FAILED` unsigned-response records | **PASS for documented behavior; two actual signature-verification FAIL records** |
| 8c | `./agent/verify-transcript.sh agent/testdata/transcripts/boot-1793051811-26cc5edf-0001.json` | `ALL PASS` | `ALL PASS` | **PASS** |
| 9 | Inspect `evidence/protect-live.txt`; read cited Base receipt | Owner adjustment, agent reduce, successful receipt, target approached | Successful receipt; approximately 49.96745% → 40.01515% LTV | **PASS** |
| 10 | Inspect `evidence/scenes/`; read receipts and recompute recorded balances | Advertised crash, gap, whipsaw, migration, and exit outcomes | Recomputed outcomes matched; 45 referenced successful action receipts had status 1; expected gap-reduce receipt had status 0 | **PASS for recorded demonstrations** |
| 11 | Inspect `evidence/inject.txt`; hash and verify cited transcript | Hostile memo cannot authorize arbitrary borrowing/withdrawal | Transcript hash matched; `ALL PASS`; judgment `hold`, no agent action | **PASS for this example** |
| 12 | Inspect `evidence/revoke.txt`; repeat v2 keys/state reads; read revoke/close/withdraw receipts | No key/approval; stopped watcher; successful close and 0.002-WETH withdrawal | v2 keys empty, agent/approval null, absent from watcher; receipts and transfer matched | **PASS** |

The exact deployed-byte pipeline was:

```bash
curl -s https://rpc.testnet.near.org \
  -H 'content-type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"query","params":{"request_type":"view_code","finality":"final","account_id":"v1.factory.prime-measure-1792736012.testnet"}}' |
  jq -r .result.code_base64 |
  base64 -d |
  sha256sum
```

For step 7, I started `anvil --fork-url https://base-sepolia-rpc.publicnode.com` and followed the fresh-key, local impersonation, and token-funding commands in [ATTACKS.md](/work/cmdk/scripts/ATTACKS.md). The observed reverts were:

```text
Expired          0x203d82d8 PASS
MoveLimit        0x013da4fb PASS
UnapprovedMarket 0x99b2f06d PASS
BelowFloor       0x4e7e2916 PASS
SellBudget       0x397b9774 PASS
result: 5 PASS, 0 FAIL
```

### Build and test commands

| Working directory / command | Expected | Observed | Status |
|---|---|---|---|
| `vault/`: `(cd mock-signer && cargo near build non-reproducible-wasm) && cargo near build non-reproducible-wasm && (cd factory && cargo near build non-reproducible-wasm)` | Three Wasm artifacts | All three built | **PASS** |
| `vault/`: `cargo test` | 15 unit and 8 sandbox tests | Exactly 15 + 8 passed | **PASS** |
| `agent/`: `cargo test` | Tests succeed | 65 passed, 0 failed, 1 ignored | **PASS**, with one explicitly ignored test |
| Temporary protocol clone: `git submodule update --init`; `executor/`: `forge test` | Initialize `forge-std`; tests succeed | 64 passed across three suites, including fuzz/invariant tests | **PASS** |
| Temporary agent copy: `./build.sh` | Two builds yield reproducible image | Docker command unavailable | **COULD NOT RUN** |
| Web app `app/`: `pnpm install --frozen-lockfile`; `pnpm dev` | Dependencies install; localhost:5173 serves app | Installation succeeded after resource-limited retry; HTTP 200 | **PASS** |
| Web app: `pnpm typecheck`; `pnpm build` | Type checking and production build succeed | Both succeeded | **PASS** |
| Web app: `pnpm test` | README says 162 unit tests | **174** passed, none failed | **PASS; README count stale** |
| Web app: `pnpm test:bundle-boundary` | No production test-fixture/wallet-control markers | `Production bundle excludes browser fixtures and wallet controls.` | **PASS** |
| Web app: `pnpm exec playwright install --with-deps chromium` | Browser and system libraries installed | System dependency installation failed at `su` authentication | **COULD NOT RUN as written** |
| Browser workaround | Usable Chromium without changing source/system installation | Downloaded Chromium and extracted Debian libraries under `/tmp`; browser launched | **PASS** |
| Web app: `pnpm test:browser` | Browser scenarios pass | **108 passed, 14 skipped, 2 failed**, exit 1 | **FAIL on the full run** |
| Diagnostic: `pnpm test:browser --last-failed --workers=1` | Determine whether failures persist in isolation | Both close-flow tests passed, 1.6 and 1.4 seconds | **PASS on isolated rerun** |
| Web app: `pnpm test:visual:update` | Deliberately rewrite screenshot baselines | Not executed; changes reference files rather than verifying them | **COULD NOT RUN under no-modification constraint** |
| Landing: `pnpm install`; `pnpm dev` | Install; local server | Succeeded; HTTP 200 | **PASS** |
| Landing: `pnpm build` | Static production build | Three pages built | **PASS** |
| Landing: `pnpm preview --host 127.0.0.1 --port 4321` | Serve production build | HTTP 200 | **PASS** |
| Landing: `pnpm preview:file` | Export standalone local preview | `.local-preview/index.html` produced | **PASS** |
| Landing: `pnpm check` | Content, links, sizes, and indexing checks succeed | `No CHECK lines.` | **PASS** |

Logs: [vault](<tmp>/vault-tests.log), [agent](<tmp>/agent-tests.log), [Solidity](<tmp>/forge-tests-retry.log), [fork attacks](<tmp>/fork-attack.log), [browser full run](<tmp>/app-browser-tests-final.log), [browser isolated rerun](<tmp>/app-browser-tests-isolated.log).

### Transcript and scene details

The 793-record transcript snapshot contained:

| Outcome | Count |
|---|---:|
| Signed answer | 372 |
| Cryptographically verified but empty answer | 6 |
| Cryptographically verified but invalid schema | 1 |
| No judge call | 400 |
| Judge-call error | 4 |
| Attestation failure; call skipped | 8 |
| Signature not verified; answer rejected | 2 |

The two signature failures are `boot-1793948092-14fda74d-v1-0267.json` and `boot-1793948092-14fda74d-v4-0071.json`. They match the README’s disclosure and record rules-only operation. “ALL PASS” verifies transcript checks, not the quality of a model decision.

An additional 78 archived/scene records produced 35 `ALL PASS` and 43 `NO-CALL`, with no signature-verification failures.

Recomputed scene changes from each twin’s own starting value:

| Scene | AI agent | Deterministic keeper | Unprotected |
|---|---:|---:|---:|
| Gradual crash | −$1.759712 | −$1.759707 | −$2.114420 |
| Gap | −$2.231114 | −$2.231114 | −$2.231114 |
| Whipsaw | −$0.585605 | −$0.585606 | −$0.000002 |

Public block timestamps confirm migration **206 seconds** after the stress borrow, and the failed gap reduction **19 seconds after liquidation**. The exit landed at **2026-11-06 16:29:09 UTC**, consistent with the recorded six-minute delay from the feed push.

## Discrepancies

1. **Owner opening LTV is described too narrowly.** The README says the CLI accepts values “inside the corridor’s bounds.” The owner `open`/`openFromStables` checks require the requested LTV to be below market LLTV, not below the corridor’s act line. See [Executor.sol:180](/work/cmdk/executor/src/Executor.sol:180). A local-fork `eth_call` opening at 80% against a 70% act line succeeded. This concerns owner authority, not an agent escape.

2. **Web-app unit-test count is stale.** Its README says 162; the current suite runs 174, all passing.

3. **The complete browser suite did not pass on its first executable run.** Two close scenarios timed out, including their configured retries. Both passed quickly with one worker. This suggests timing/resource sensitivity but does not erase the failed full-run result. Fourteen browser tests were skipped.

4. **Host Wasm builds did not reproduce deployed hashes.** This is consistent with the README’s platform/toolchain warning. The pinned vault rebuild, complete TDX measurement reproduction, and agent image rebuild remain unverified because Docker is unavailable.

5. **Transcript verification has narrower meaning than complete attestation verification.** It verifies recorded response signatures and hashes but trusts the recorded model-attestation verdict. The README discloses that model measurements are not compared against a published value and GPU attestation is not verified.

6. **Historical research is only partly reproducible from the checkout.** Missing original backtest scripts, the missing `rpc.py` dependency, external data artifacts, planning-repository paths, and macOS-specific compilation commands prevent a complete unchanged Linux replay. These limitations are mostly disclosed.

7. **Several numerical claims were not independently reproduced:** the original 20/20 sampled-position replay and $2.65M penalty estimate; the idealized BTC examples, including $8,811 versus $5,987; the 837-day extreme-move/53-minute analysis; fresh model lead-time and repeatability experiments; and the web app’s published live latency measurements. Stored reports are evidence of the team’s experiments, not fresh confirmation of every result.

8. **Business, market-size, incident, and cost figures remain unconfirmed in this screening.** This includes borrower/loan/liquidation totals, competitor assets and adoption, Rhea/tokenized-stock figures, cited incident losses, $0.70–$1 monthly operating cost, and 2–3-cent action cost. The cost estimate assumes five-minute checks, while this build checks much more frequently. “Tightest limits” is a comparative claim, not established by the implementation tests.

9. **Current scope is narrower than the eventual product.** Private/any-chain funding, BTC collateral, additional venues, buy-back, and an outage-independent backstop are absent. The README generally labels them correctly. The live app additionally restricts signing flows to desktop; phones receive the sandbox/desktop notice.

10. **Demonstrations have important boundaries.** Markets, liquidity maintenance, mock crash prices, liquidators, and two text feeds are team-controlled testnet infrastructure. The gap demonstrates failed protection; the whipsaw demonstrates its economic cost. The injection example does not establish general prompt-injection resistance.

## Preliminary score: **8.5 / 10**

This is a screening assessment, not an official weighted judging rubric or security rating.

The submission earns substantial credit for functioning NEAR/Base integration, narrow agent permissions enforced by contracts, exact executor reproduction, extensive passing protocol tests, deployed web assets matching source builds, and unusually detailed evidence that includes failures and unfavorable outcomes.

The remaining uncertainty is concentrated in Docker-dependent reproducibility, complete attestation verification, browser timing stability, historical replay completeness, and the difference between demonstrated features and production roadmap. The evidence supports serious human review; it does not establish production readiness or universal liquidation protection.

## Questions for the human judges

1. Can an independent Docker-equipped runner reproduce the pinned vault Wasm, agent image, and complete TDX measurements?
2. Can the team provide a clean full browser-suite run and a supervised desktop-wallet rehearsal of opening, revocation, closing, and withdrawal?
3. How should the Privacy track credit confidential agent/inference execution when funding privacy is deferred and testnet transcripts are public?
4. What additional evidence demonstrates model value beyond deterministic rules for borrower positions, given the equal price-scene outcomes and successful hostile false exits in the stored experiments?
5. Can the team provide portable replay commands and complete artifacts for the historical numerical claims judges intend to credit?
6. Please confirm provenance and credited work against the disclosed late-October prototype and November import, applying Q-001 rather than treating pre-November work as automatically ineligible.