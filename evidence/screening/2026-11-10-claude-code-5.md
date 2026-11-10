# AI screening rehearsal 5, Claude Code (10 Nov 2026)

Run per DEPLOY.md step 11 on a fresh clone of commit 89f8e90, with a clone of the web app `cmdkfi/cmdk-app` at `main` 301ad92 (public since 10 Nov, the code deployed at testnet.cmdk.fi) beside it, from the same Mac as rehearsals 1 to 4 (near-cli-rs 0.30.1, cargo-near 0.22.0, Foundry, Docker Desktop 27.4.0). Tool: a Claude Code subagent (Claude Opus 5.5) given `2026-11-10-prompt-5.md`: the prompt of rehearsal 4 with the web-app note updated for the public repositories. It ran alone on the machine (the Codex rehearsal started after it finished). Started about 15:24 UTC, finished about 15:53 UTC (the report's own "15:20–16:10 UTC" is an estimate; these times are from the run's files). Rehearsal 4 scored 8.5. The report below is the agent's final answer, unedited except two local paths shortened to `<local path>`; the checkouts were unchanged afterwards.

---

# Screening report: cmdk (NEAR HACKATHON, 1–14 Nov 2026)

Screening, not deciding (rules Q-017): a reproducibility check, feature verification, a structured report and a preliminary score. Humans decide.

**Subject**
- Main repo: `cmdkfi/app` at `89f8e90` (public, MIT; equal to GitHub `main` on 10 Nov). `pre-hackathon` tag = `ac9fe3b` (2 Nov), 112 commits after it.
- Web app: `cmdkfi/cmdk-app` at `301ad92`. Landing page: `cmdkfi/landing`.
- Live deployment: NEAR testnet and Base Sepolia, per `deployments/testnet.json`.
- Screened on 10 Nov 2026, 15:20–16:10 UTC, on an Apple-silicon Mac (Docker Desktop with 3 CPUs and 3.8 GB, amd64 emulation).

**Method**
- Read-only throughout: view calls, receipts, HTTP GETs, local builds and tests. No testnet transaction was sent, nothing was signed on any network, nothing was spent.
- One step needed signing: step 7 ran on a **local anvil fork only**, from a throwaway key generated for the run. On that fork I impersonated the team wallet to move 2 USDC and 2 dUSDC to the throwaway key (see step 7).
- The web-app part was delegated to one sub-run, whose findings I folded in. Its working files are in `<local path>`.

## Summary

1. All 12 "Verify it" steps check out. I ran steps 1–6 and 8 as written and they PASS. Step 7 sends transactions, so I did not run it on testnet; its five recorded reverts are on chain with the claimed error selectors, and it reproduced 5/5 PASS on a local fork against the deployed executor. Steps 9–12 are evidence files, and their key transactions, NEAR receipts, numbers and transcripts check out on chain.
2. All four reproducibility checks hold:
   - The vault wasm rebuilt in the pinned image from commit `975b98d` matches the on-chain Global Contract hash (`G1Q5ks…`, MATCH).
   - The agent image rebuilt from `4171c10` gives exactly the published image ID `sha256:d3b51d67…` and binary hash `0a8f47c3…`; that ID is the config digest of the registry image `ghcr.io/cmdkfi/agent@sha256:e72564a8…`.
   - The TDX measurements reproduce: `ALL MATCH`, and the compose hash equals the vault's approved value.
   - The deployed executor bytecode equals a local `forge build` byte for byte, metadata included.
3. Every test suite passes: executor 64/64 (fork, fuzz and invariant), vault 15 unit plus 8 sandbox (as claimed), agent 65 passed with 1 ignored, web app 162 unit plus 88 browser. All 690 live transcripts match their index hashes, and 320 of them verify `ALL PASS`. Every failing or no-call transcript is one the README itself enumerates.
4. Security claims verified in code and on chain:
   - The agent key on the vault is a function-call key for `reduce`, `migrate` and `exit` only; there are no full-access keys.
   - The executor has no admin path and pins every recipient to `msg.sender`.
   - The executor enforces the 1% oracle bound, `MAX_MOVES` = 2, 16 sale slots, the deadline and the exit switch.
   - The factory is keyless and deploys the code by hash.
   - Revocation deletes the key on chain.
5. The discrepancies are minor, mostly wording:
   - The README says the web app's policy check uses values that "equal `deployments/testnet.json`"; slice max and cooldown actually come from the app's own config.
   - The doc for the step-7 fork run misses a precondition: a fresh wallet needs loan token to cover interest.
   - One loop-cadence figure is off.
   - A few claims are recorded results that cannot be re-run, which the README mostly says itself.
   - The headline product features (Intents, Confidential Intents, any-chain deposits) are production-release items and are labelled as such.

## Reproducibility

| Item | Command / method | Result |
|---|---|---|
| Vault code hash | Clean clone at `975b98d` (the commit in the wasm's NEP-330 `link`) in `/tmp/cmdk-screen5-vault`, outside any git repo: `./scripts/reproduce-hash.sh --reproducible --vault v1.factory.prime-measure-1792736012.testnet` | **MATCH**. 783,124 bytes, sha256 hex `def9671e…77e1ba`, base58 `G1Q5ksFhGbgPTN4A2DuvgraYmsbRpfwb78B547gAKXWq`, equal to `global_contract_hash`. Took 7 min 22 s (README says about 26 min; the pinned image was already pulled). |
| Vault build commit vs HEAD | `git diff 975b98d HEAD -- vault` | Only `vault/tests/sandbox.rs` and `factory/DEPLOY.md` changed, so the contract source is the same. |
| Factory code | `view_account` code_hash `6bKeo3…`, which is hex `5315458701e4…c863`, the value stated. No access keys. `get_config.code_hash` = the vault hash. `contract_source_metadata` has no link (as the README says). | Hash, keys and config VERIFIED. Rebuild not attempted: the README says it is host-specific (macOS arm64, Rust 1.95.0); factory source is unchanged since `52e0446` (only DEPLOY.md changed). |
| Agent image | Clone at `4171c10` in `/tmp/cmdk-screen5-agent`, `agent/build.sh cmdk-screen5-agent` (BuildKit `docker-container` builder, two no-cache builds) | Build 1: image **`sha256:d3b51d670192b3b77b64241e70dca28eb97fa446128c3aca79ba548c18efcfcb`**, binary **`0a8f47c3eea1bfed9f02f84f4dd32bf98f24d0bf4928392801300bcf51def68d`**. Both equal the published values (`deployments/testnet.json` `agent.imageId`; MEASUREMENT.md). Build 2 is identical, and the script printed `REPRODUCIBLE image sha256:d3b51d67… SOURCE_DATE_EPOCH=1794264093`. That proves reproducible both ways: two builds here agree, and they match the published image. |
| Registry image | `docker buildx imagetools inspect --raw ghcr.io/cmdkfi/agent@sha256:e72564a8…` (anonymous) | Config digest `sha256:d3b51d67…`, equal to the rebuilt image ID. |
| Measured compose | Extracted `docker_compose_file` and `pre_launch_script` from `evidence/boot-1794264608-2286af92/app-compose.json` | Byte-identical to `agent/docker-compose.live.yml` and `agent/pre-launch.sh`. It pins `ghcr.io/cmdkfi/agent@sha256:e72564a8…` and `--factory factory.prime-measure-1792736012.testnet`. |
| TDX measurements | Step 6 script (below) | `ALL MATCH`. MRTD and RTMR0–3 match, and the compose hash `db4f5085…` equals the vault's `approved.compose_hash`. Caveat: the script reused a local `prime-dstack-mr:0.5.12` image that an earlier run on this machine had built 3 hours before. I did not delete it to force a rebuild from dstack source (operator instruction: do not prune Docker data). The OS image tarball was downloaded fresh, and `os_image_hash` matches it. |
| Executor bytecode | `forge build` (solc 0.8.28, via-IR) vs `cast code 0xA9bb…D18b`, immutables masked | Equal **including** CBOR metadata (16,459 bytes). Immutables are `MORPHO` = `0xBBBB…FFCb` and `COOLDOWN` = 60. Blockscout API: `is_fully_verified: true`, v0.8.28. |
| Derived Base address | `v1.signer-prod.testnet.derived_public_key(path "cmdk-base", predecessor v1)`, then keccak | `0xb3ccd0ad…e6fb`, equal to `get_state.base_address` and the manifest. |
| Live evidence files | `$E/` file list vs `evidence/boot-1794264608-2286af92/*` | All six sha256 values equal (compose, vm_config, quote, event log, info, registration-v1). |
| Web app | Build of `301ad92` (sub-run) | The live `testnet.cmdk.fi` assets are byte-identical to the local build. |

## Verify-it results

The environment was near-cli-rs 0.30.1, Foundry 1.8.6 and cargo-near 0.22.0, with `RPC=https://base-sepolia-rpc.publicnode.com`.

| # | Command (as written) | Expected | Got | Verdict |
|---|---|---|---|---|
| 1a | `near account list-keys <vault> …` | Exactly one function-call key for `reduce`, `migrate`, `exit`; no full-access keys | One key, `ed25519:3Kmykr9L…`: "only do ["reduce", "migrate", "exit"] function calls on v1… with an allowance of 2 NEAR" | PASS |
| 1b | `near contract call-function as-read-only <vault> get_state …` | `owner` = wallet (lower case); `base_address` = the derived address | owner `0x0b7ed66f…0d25` (= `base.testWallet`), base_address `0xb3ccd0ad…e6fb`, `owner_nonce` 13, signer `v1.signer-prod.testnet`. Factory `count` = 11, so vaults exist beyond v1/v2. | PASS |
| 2a | `near account view-account-summary <vault> …` | "Global Contract (by Hash: SHA-256 checksum hex)" with the published hash | `def9671e099fe325a7ac8c43831de0334a3dc75221f6c302b17a893e0377e1ba`, equal to `near.vault_code_hash_hex`. Access keys: 0 full, 1 function-call. | PASS |
| 2b | `./scripts/reproduce-hash.sh --reproducible --vault <vault>` | `sha256 (hex)` equals the summary hash, `published` line, `result MATCH` | As expected, `result           MATCH`, exit 0 | PASS |
| 2c | Factory: no keys; `get_config` returns the vault hash | — | Keys `[]`; `get_config.code_hash` `G1Q5ks…`; code SHA-256 `53154587…c863` | PASS |
| 3 | `get_state` markets; `cast call <executor> "corridors(…)" <base_address> <live id>` | Seven lines: 5e17, 7e17, 3e17, 2e17, true, true, true | Exactly that, for live, backup and demo; `cooldown_secs` 120; each market has `corridor_set` true and `slice_max` 1000000000; `MAX_MOVES = 2` constant at `Executor.sol:51` | PASS |
| 4 | `./scripts/ltv.sh <vault> --market backup` | Two columns; `cross-check    MATCH with the "at lastUpdate" LTV (WAD)` | LTV 44.7600 % (at lastUpdate) / 44.7605 % (accrued); `executor ltvOf 447600410997149817`; `cross-check    MATCH`. Without `--market`: `(no position on this market)`. The debt formula, recomputed by hand, gives 7,730,581. The oracle's `BASE_FEED_1` = ETH/USD `0x4aDC…7cb1`. | PASS |
| 5a | `cast call Morpho "isAuthorized(address,address)(bool)" <base_address> <executor>` | `true` | `true` | PASS |
| 5b | `cast call <executor> "config(bytes32)(…)" <live id>` | Params, then pool `0x5718…156c`, two feeds, `[1200, 86400 [8.64e4]]` | Exactly that. Backup gives the same pool, with its own oracle `0x20Ff…d538`. | PASS |
| 6a | `get_state` → `approved`, `agent` | Measurement and key with `expires_at` | `approved` equals the six values in `deployments/testnet.json` `agent.measurement`. Agent key `3Kmykr9…`, `expires_at` 1794869548 (16 Nov 22:52 UTC). The `register_agent` tx `CPEBXcPD…` is signed by implicit account `2286af92…`, burns 113.8 Tgas and logs "agent registered … until 1794869548". | PASS |
| 6b | `./scripts/p3_reproduce_measurements.sh /tmp/cmdk-screen5-mr evidence/boot-1794264608-2286af92 dstack-0.5.12 1 2G` | `os_image_hash MATCH`; MRTD and RTMR0–3 `MATCH`; `ALL MATCH` | Exactly that; `mr_config_id 01db4f5085…`. The `dstack-mr` tool image was reused, not rebuilt (see Reproducibility). | PASS |
| 6c | `sha256sum evidence/<boot>/app-compose.json` | The compose hash in the event log and MRCONFIGID | `db4f508517a01b31986dc9ec10eefd7491eb249cc209fb6b395c449e91c1dfc4`, equal to the event `compose-hash` and to MRCONFIGID after `01` | PASS |
| — | Vault tests: three `cargo near build non-reproducible-wasm`, then `cargo test` | 15 unit and 8 sandbox | `15 passed`, `8 passed` (sandbox, 48 s) | PASS |
| 7 | `./scripts/attack-corridor.sh --land` | Five reverts in order, then `result: 5 PASS, 0 FAIL` | **Not run on testnet**: it sends transactions, which this screening forbids. Evidence: the five tx hashes in `evidence/attacks.txt` are on chain with status 0, from the test wallet to the executor. Blockscout decodes the reverts as `Expired()`, `MoveLimit()`, `UnapprovedMarket()`, `BelowFloor()`, `SellBudget()`, and the selectors equal `cast sig`. Local reproduction on an anvil fork (no real transactions), against the deployed executor `0xA9bb…`, with a throwaway key: `attack Expired … PASS`, `MoveLimit … PASS`, `UnapprovedMarket … PASS`, `BelowFloor … PASS`, `SellBudget … PASS`, `result: 5 PASS, 0 FAIL`. | COULD NOT RUN as written (rules); evidence VERIFIED; local fork PASS |
| 8a | `curl -s $E/transcripts` | An index of every transcript, with `name`, `bytes`, `sha256`, `boot`, `vault`, `twin`, `loop` | 690 entries from boots `14fda74d` (644) and `2286af92` (46), vaults v1/v3/v4/v6/v9/v10/v11, with all the listed fields. All 690 downloaded files match their index sha256. | PASS |
| 8b | `./agent/verify-transcript.sh /tmp/<name>` for **all 690** | `ALL PASS`, or the enumerated exceptions | 320 `ALL PASS`; 357 `NO-CALL judge outcome: ` (no judge call); 8 `skipped_attestation_failed`, all 9–10 Nov; 3 `error` (8 Nov ×2, 10 Nov ×1); 2 `4 FAILED` with outcome `signature_not_verified` (7 Nov and 9 Nov). Exactly the README's counts and dates. | PASS |
| 8c | Offline on `agent/testdata/transcripts/boot-1793051811-26cc5edf-0001.json` | `ALL PASS` | `ALL PASS` (7 checks; signer `0x3b75…fA0c`, TCB `UpToDate`) | PASS |
| 8d | Archived `evidence/transcripts/` (+ `evidence/scenes/exit-v4-transcript-*`) | Verify the same; three 4 Nov 402 errors | 35 `ALL PASS`, 40 no-call, 3 `error` | PASS |
| 8e | Relayer proxy with CORS | — | `https://relayer.cmdk.fi/agent/...` answers GET with `access-control-allow-origin: https://testnet.cmdk.fi`; HEAD returns 405 (sub-run) | PASS |
| 9 | `evidence/protect-live.txt` | Lowered act line, the agent's `reduce`, LTV back at target | Base tx `0xde730226…` has status 1, from v1's derived address to the executor, selector `reduce`. `Reduced` event: ltvBefore 0.4997, ltvAfter 0.4002, protective `true`. Both owner `set_corridor` receipts have status 1. | PASS |
| 10 | `evidence/scenes/` | Scene figures as stated | Recomputed from the md tables. Gradual crash: AI agent and keeper −1.76, unprotected −2.11. Gap: −2.23 for all three. Whipsaw: −0.59 / −0.59 / 0.00. Twin debt differences: 4 units, 1 unit, 0. Liquidation txs and the agent's reverted gap reduce `0xbf743bbf…` (status 0) are on chain. Venue stress: migrate `0xd597c432…` has status 1, from v1's address, and landed 206 s (3.4 min) after the borrow (block timestamps). Exit `0x014aebcd…` has status 1, from v4's address `0xd398…`, at 16:29:09Z; v4 lists only the live market. | PASS (one wording nit: see Discrepancies) |
| 11 | `evidence/inject.txt` | Hostile memo; the model's answer is bounded | Transcript `boot-1793877578-a405e917-v1-0002.json`: sha256 `748c8c59…` as stated, `ALL PASS`, parsed answer `risk low, action hold, market none`. The memo was treated as untrusted data. | PASS |
| 12 | `evidence/revoke.txt` | Key deleted, `approved` cleared, close and withdraw only to the owner | `list-keys v2` returns no keys; `get_state` gives `agent null, approved null`. NEAR tx `FYeZcfVX…` is `owner_call` by the relayer, with a `DeleteKey` receipt. Base withdraw `0x431aefa8…` has status 1: WETH Transfer `0xfc85…` → `0x0B7E…`, amount 2e15. | PASS |

## Feature verification

| Claim (README) | Evidence | Verdict |
|---|---|---|
| The agent key is limited by the protocol to `reduce`, `migrate`, `exit` with a 2 NEAR allowance; 7-day expiry; renewal keeps the key and does not refill the allowance | `vault/src/lib.rs:43,45` (`REGISTRATION_SECS` 7 d, `AGENT_KEY_ALLOWANCE` 2 NEAR); `:486-503` (same key → expiry extended, early return; otherwise delete the old key and add a function-call key with an allowance); live `list-keys` | VERIFIED |
| Registration verifies the TDX quote on chain against the approved measurement | `lib.rs:471-486` (report_data binds the caller key; `attest::verify` against `approved`); on-chain `register_agent` burned 113.8 Tgas | VERIFIED (code and tx). The dcap-qvl internals were not audited. |
| The vault checks every agent call: cooldown 120 s, slice max, market allowlist, 10-minute deadline | `lib.rs:623-634` (`agent_check`: signer pk equals the agent key, expiry, cooldown); `:435-468` (slice max, same executor, fraction bounds); `evm.rs:19` `DEADLINE_SECS = 600`; `check_tx` `:682-797` (chain id, gas caps, the agent may not call tokens or Morpho, selector-per-caller, market ids, deadline window) | VERIFIED |
| Owner actions are EVM-signed messages with nonce and expiry; anyone may relay | `vault/src/owner.rs` (`MAX_EXPIRY_AHEAD_SECS` 3600, nonce); `owner_call` at `lib.rs:279`; v2 revoke relayed by `relayer.prime-…` | VERIFIED |
| `revoke_agent` deletes the key and clears `approved` | `lib.rs:418-425`; v2 on-chain state and the `DeleteKey` receipt | VERIFIED |
| Vault code is immutable: Global Contract by hash; keyless factory with the hash fixed | `factory/src/lib.rs:152` (`create_account().transfer().use_global_contract(code_hash)`, no key actions); factory keys `[]`; v1 has 0 full-access keys | VERIFIED |
| The executor is immutable, has no admin, sends tokens only to the caller, the fixed pool or Morpho | `Executor.sol` has no owner, upgrade or delegatecall; withdrawals go to `msg.sender`; the swap callback only pays `_pool`; `_check` keeps executor balances unchanged; invariant `invariant_executorHoldsNothing`/`noViolation` PASS | VERIFIED |
| Swap output is at least the oracle price minus 1% | `CorridorMath.sol:10` `SLIPPAGE_BPS = 100`; `maxCollateralIn`/`minCollateralOut`; callback `Slippage` revert | VERIFIED |
| Corridor: a discretionary sale is sized at the oracle to stay at or above the floor and within the rolling 24 h budget; a protective session starts above the act line, stops near target, lasts at most 24 h, and is not budget-limited | `Executor.sol:245-302`; `TARGET_TOLERANCE` 0.001e18; `SESSION` 24 h; `remainingBudget`/`worstCaseOut`; tests in `Executor.t.sol` | VERIFIED |
| At most two moves per 24 h; collateral conserved and debt within 1 unit; may not land above the destination act line | `MAX_MOVES = 2`, `_recordMove`; `migrate` post-checks (`MigrationMismatch`, `AboveActLine`) | VERIFIED |
| Exit needs the owner's switch; it closes the market to the agent until the owner reopens | `exit` checks `exitAllowed`, then sets `closedForAgent`; `_borrowTo` clears it | VERIFIED |
| At most 16 discretionary sales per collateral token per window | `SALE_SLOTS = 16`; `_recordSale` reverts `SellBudget` when the oldest slot is still in the window | VERIFIED |
| A stale feed blocks `open` but never selling or exit | `_requireFresh` is called in `open`, `openFromStables` and `migrate` (destination only), not in `reduce`/`exit` | VERIFIED |
| The Morpho callback accepts only Morpho, during the executor's own call | `onMorphoRepay`/`onMorphoFlashLoan`: `msg.sender == MORPHO && _locked` | VERIFIED |
| Source verified on Blockscout; deployed bytecode checkable against `executor/src` | Blockscout `is_fully_verified: true`; local build byte-equal | VERIFIED |
| Morpho markets (live, backup, demo) at 86% LLTV on real Chainlink ETH/USD (demo on a mock) | `config()` reads; oracle `BASE_FEED_1`; ETH/USD feed fresh (updated 10 Nov) | VERIFIED |
| Chain Signatures 11 of 16 on testnet | `v1.signer-prod.testnet.state`: threshold 11, 16 participants | VERIFIED (mainnet 17 not checked) |
| The vault controls the Base address via Chain Signatures | `derived_public_key` → `0xb3cc…e6fb`; every agent and owner Base tx in the evidence is from that address | VERIFIED |
| Agent reads Base from two providers that must agree | `agent/src/chain.rs:1-7,738-786`; unit test `providers_must_agree_on_every_call` | VERIFIED (code) |
| Judge: called on a new screen, a new feed item and every 6 h; text-only discretionary action needs critical risk plus two feeds with different `feed_id` | `main.rs:61` `DIGEST_SECS = 6*3600`; `logic.rs:338-360` `text_corroboration`; `want_judge` | VERIFIED (code) |
| Judge answers are signed by NEAR AI's TEE and verified with a fresh-nonce attestation, TCB `UpToDate` | 320 live and 35 archived transcripts `ALL PASS` (offline checks of hashes, EIP-191 signer and recorded TCB) | VERIFIED as far as the verifier goes; it trusts the recorded quote verdict, as the README says |
| Demo feeds are labelled as the team's own | `feeds/README.md`, `notice` field in each feed | VERIFIED |
| Live protective reduce, venue-stress migrate, text-path exit, injection held, revocation | Steps 9–12 above | VERIFIED (on-chain records) |
| Backtest, incident replay and research figures ($2.65M, 1,890/1,895, 5,009/5,011, $8,811 vs $5,987) | Figures are present in `research/liquidation-backtest/backtest.md` (Set A $681.7k plus Set B $1.97M = $2.65M), `research/liquidation-dataset/README.md:794-795` and `research/venue-coverage/near_and_stock_lending.md:58` | Numbers trace to the notes; simulations NOT re-run |
| Web app adopts and sets up only vaults with the published policy (`policyMatches`, re-checked before the setup signature) | `cmdk-app/app/src/protocol/near/vault.ts:73-79,113,129`; `runner.ts:139-141,259,279-283`; unit and e2e tests pass | VERIFIED (mechanism); wording PARTLY (see Discrepancies) |
| Web app does not show historical similar cases | No such strings in `src` or the live bundle | VERIFIED |
| Web app offers re-approval when `approved` differs from the manifest | `flow/position.ts:20-24,72`; `protocol.ts:180-190`. It re-approves via `setup` with `measurement`, not the `approve_measurement` method; same effect in the vault (`lib.rs:316-317`). | VERIFIED in substance |
| Web app deployed from `main` | Live assets byte-identical to a local build of `301ad92`; GitHub deploy run for `301ad92` succeeded | VERIFIED |
| Production-release items (NEAR Intents, Confidential Intents, any-chain deposits, BTC, `relever`, `protect` backstop) | Not in this build; labelled as such throughout the README | Not demonstrable (correctly labelled) |
| `open_from_stables` | Executor and vault tests exist; no live tx, as the README says | Tests only (as stated) |

## Test suites run

| Suite | Command | Result |
|---|---|---|
| Executor | `forge test` (Base Sepolia fork) | 64 passed, 0 failed (CorridorMath, Executor 54, Invariant: 64 runs, 1,600 calls, 0 reverts) |
| Vault | Three wasm builds + `cargo test` | 15 unit + 8 sandbox passed |
| Agent | `cargo test --locked` | 65 passed, 0 failed, 1 ignored |
| Web app | `pnpm build`, `typecheck`, `test`, `test:bundle-boundary`, `test:browser` | Build OK; 162/162 unit; bundle boundary PASS; 88/88 Playwright (all match the cmdk-app README) |

## Discrepancies

1. **Web-app policy wording (README Limitations).** The README says the web app adopts only vaults whose "markets, executor, tokens, slice maximum and cooldown equal `deployments/testnet.json`".
   - Markets, executor and tokens do come from the manifest.
   - Slice max (`1000000000`) and cooldown (120) come from `cmdk-app/app/config.app.json`. The manifest has neither; its only cooldown, `base.cooldownSeconds` = 60, is the executor's.
   - The app's policy covers live and backup only. So the team vault v1, which has three markets, would not match. That has no practical effect, because the app scans from v10.
   - The mechanism itself is verified.
2. **Manifest comment** (`agent._comment`) says the app should "offer approve_measurement". The app re-approves via `setup` with a `measurement` argument. The effect is equivalent; the method name differs.
3. **Loop cadence.** The README says "one loop every 4 to 10 seconds per vault (measured on 10 Nov)". The live `/agent/vaults` tick counters since the 9 Nov boot give about 5.6 s per tick for v1 but about 3.0 s for v4 (20,547 ticks in 60,759 s). The average for v4 is below the stated range. Minor; "loop" may be measured differently.
4. **Scene wording.** The README says "unprotected liquidated past −41.9%". The liquidation tx (block 54833693) landed while the oracle stood at −41.83% (step 29); interest had moved the line slightly. The theoretical line for 50% → 86% is −41.86%. Cosmetic.
5. **ATTACKS.md fork run.**
   - The documented command uses the founder's local path (`cd <local path>`).
   - With a fresh wallet, the run stops at the first `close` with `token transfer`. The wallet holds only the borrowed loan token, but interest accrues across the fork's time jumps. It needs a few units of USDC and dUSDC beforehand, which is not stated as a precondition for a fresh wallet.
   - The default fork wallet (anvil #0) fails on Base Sepolia, because that address carries EIP-7702 delegation code there.
6. **Build time.** Step 2 says about 26 min on Apple silicon; I measured 7 min 22 s with the pinned image already present. The reproduce script also prints the **host** `rustc 1.95.0` on its `toolchain` line, although the reproducible build runs Rust 1.96.0 in the image. Cosmetic, but it could confuse a reader.
7. **Measurement flags.** `vm_config.json` has `qemu_single_pass_add_pages: true`, while the script passes `--two-pass-add-pages true`. `agent/MEASUREMENT.md` §3 documents that the flags, not the field, are used, and the result is `ALL MATCH`. Worth knowing, not an error.
8. **Not re-runnable or not verified here:**
   - Model-side results: the incident replay (6 of 8 exits, 136 vs 25 minutes), the 44-item hostile replay (21/30 → 2/30), the 20,479-liquidation replay and the business figures (Coinbase borrowers, DeFi Saver coverage, Bloom and Bankr incidents). These are documented research notes, not reproduced.
   - The model's TEE quote itself is not re-verified from a transcript; the README says so.
   - The NVIDIA GPU attestation is not verified; the README says so.
   - The web app's live performance figures (6 requests per refresh; the e2e test allows 8).
9. **No missing paths.** Every path and command named in the README exists. The only thing a judge cannot do as written is step 7 on testnet, which needs the team's key.

## Preliminary score: 8.5 / 10

**For the score:**
- **Reproducibility is unusually strong.** Four independent artifacts reproduce bit for bit from the public source with documented commands: the vault wasm, the agent image, the TDX measurements and the executor bytecode. The chain from source to image to compose hash to vault-approved measurement to on-chain registration is closed and checkable.
- **The security design is enforced where claimed.** Protocol-level key scoping, the keyless factory with code by hash, the admin-less executor that re-reads the oracle, recipients pinned to the caller, rolling budgets and deadlines. Each claim maps to specific code and to on-chain state or reverts.
- **The evidence is honest and complete.** 690 public transcripts, all verifiable, with every failure the README enumerates and nothing else. Negative results are reported: the gap scene where protection fails, the judge holding in one stress run, the judge not being reproducible, and the risk model that added nothing.
- **Test coverage is solid and green** across four codebases (fork, fuzz and invariant on the executor; NEAR sandbox on the vault; Playwright on the app).
- **Build and product are clearly separated.** Intents and Confidential Intents are consistently labelled as production release.

**Against the score:**
- The headline user value is not demonstrable on testnet and is deferred to a production release: any-chain and private deposits and withdrawals, BTC. That is correctly labelled, but it shrinks what the build demonstrates in the Privacy track.
- The live demonstrations are small and on markets the team controls: a mock oracle, a team-maintained pool band and a scripted liquidator. The AI judge's added value in the live build rests on one migrate and one text-path exit, and the judge is admittedly non-deterministic.
- Unaudited, with no backstop in v1. The README says both.
- Minor documentation inaccuracies (above), none of which changes a security claim.

## Questions for the human judges

1. **Track fit.** How much weight should the deferred Confidential Intents and any-chain flows carry for the Privacy track? Privacy here is limited to enclave inference, since the transcripts are public in this build.
2. **Single-enclave trust.** One operator enclave serves every vault. Is the README's "corridor-bounded worst case" an acceptable answer for a v1 hackathon build? Its worst case is the daily sell budget at up to 1% below the oracle, plus one exit per market.
3. **Model value.** The live price scenes show the AI agent and the deterministic keeper ending within a few loan-token units of each other. Is the venue-stress `migrate` plus the replay results enough evidence that the model adds value beyond rules?
4. **Policy source of truth.** Should `deployments/testnet.json` publish the vault policy (slice max, vault cooldown, market set), so the app, the CLI check and the relayer share one source (Discrepancy 1)?
5. **Step 7.** It cannot be executed by judges without the team's key. Is the on-chain record plus a fork run (which needs the small funding workaround) acceptable as "executable as written"?
6. **Attestation scope.** The verifier trusts the agent's recorded verdict on NEAR AI's TDX quote, and the model enclave's measurement is not pinned to a published value. Is that sufficient for the "attributable to a genuine enclave" claim?
