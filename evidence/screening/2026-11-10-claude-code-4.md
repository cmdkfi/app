# AI screening rehearsal 4, Claude Code (10 Nov 2026)

Run per DEPLOY.md step 11 on a fresh clone of commit 22a5191 (after the 9 Nov agent image with the NEAR RPC failover, boot 2286af92), with a copy of the web app at `cmdkfi/cmdk-app` branch `feat/guided-flow` commit 916b7e8 (the code deployed at testnet.cmdk.fi; the repository is private) beside it, from the same Mac as rehearsals 1 to 3 (near-cli-rs 0.30.1, cargo-near 0.22.0, Foundry, Docker Desktop 27.4.0). Tool: a Claude Code subagent (Claude Opus 5.5) given `2026-11-10-prompt.md`: the Q-017 prompt of `2026-11-05-prompt.md`, the tool list of rehearsal 3, and a note on the web app and the landing page. The Codex rehearsal ran at the same time and shared Docker (3.8 GB of memory). Started about 12:22 UTC, finished about 12:59 UTC (the report's own "12:00–14:30 UTC" is an estimate; these times are from the run's files). Rehearsal 3 scored 8. The report below is the agent's final answer, unedited; the checkouts were unchanged afterwards.

---

# Screening report: cmdk (NEAR HACKATHON, 1–14 Nov 2026)

Screened on 10 Nov 2026 (about 12:00–14:30 UTC). Inputs: the protocol repository `cmdkfi/app` at `22a5191` (clean tree); the web app `cmdkfi/cmdk-app` at branch `feat/guided-flow`, commit `916b7e8`; live state on NEAR testnet and Base Sepolia; https://testnet.cmdk.fi and https://cmdk.fi.

Everything was read-only: view calls, GET requests and local builds. No transactions were sent, nothing was signed, no account was created, and no POST went to the relayer. Builds ran in clones under `/tmp`, and both checkouts are unchanged. This is a screening (a reproducibility check, feature verification and a preliminary score); humans decide.

Tools: near-cli-rs 0.30.1, Foundry (forge/cast), cargo-near 0.22.0, rustc 1.95.0 (macOS arm64), Docker Desktop (3 CPUs and 3.8 GB in its VM, shared with another screening run), Node 22.14 and pnpm 11.7.0.

## Summary

1. **Every read-only "Verify it" step (1–6, 8–12) passed or matched its evidence.** Step 7 sends transactions from the team's wallet, so I did not run it. Its five recorded reverts were confirmed on Base Sepolia, each with the expected error selector.
2. **Reproducibility is strong.**
   - Vault: the pinned Docker build of the commit named in the on-chain NEP-330 metadata reproduces the published Global Contract hash (`G1Q5ks…`, 783,124 bytes): `result MATCH`.
   - Factory: the host rebuild gives the published hash `53154587…` (152,851 bytes).
   - Executor: the deployed runtime bytecode is byte-identical to `forge build` of the source, metadata hash included.
   - Agent image: two clean rebuilds give image ID `d3b51d67…`, the published image's config digest.
   - Enclave: MRTD and RTMR0–3 of the running agent were recomputed from the public dstack image: `ALL MATCH`.
   - Transcripts: 351 judge-call transcripts verify `ALL PASS` (319 of the 689 served live, 32 of the 75 archived). The only 2 failures are ones the agent itself had marked unsigned and rejected.
3. **The code matches the security claims I could check.** Examples: a NEAR function-call key limited to `reduce`/`migrate`/`exit`; on-chain DCAP quote verification bound to the agent key; caller-scoped executor with no admin; 1% oracle bound on swaps; `MAX_MOVES = 2`; 10-minute deadlines. Tests: vault 15 unit + 8 sandbox, executor 64 (54 fork, 9 math, 1 invariant campaign), web app 159 unit + 85 browser. All passed, with one caveat on browser-test parallelism.
4. **Main design concern: whoever creates a vault chooses its policy, not the owner.** The factory and the public relayer accept any executor, tokens, slice maximum and cooldown. The owner's `setup` signature does not name the executor. The web app adopts the owner's newest vault (≥ v10) without comparing its executors to the published manifest. Second concern: the model attestation proves a genuine TDX key with a fresh nonce, but does not pin which code serves the model (no measurement compared, NVIDIA part unverified).
5. **The remaining discrepancies are minor documentation drift.**
   - The live site was deployed by hand from `feat/guided-flow`, not from `main` as the app README says.
   - The transcript-error count in step 8 is out of date.
   - One evidence file is missing from the repo copy of the current boot.
   - CORS covers only the team's origins.
   - The tick cadence measured above the stated "about two seconds".

   **Preliminary score: 8.5 / 10.**

## Reproducibility

| Item | What I did | Result |
|---|---|---|
| Vault code hash (README step 2) | Read `contract_source_metadata` on `v1`: link `https://github.com/cmdkfi/app/tree/975b98d…`, build environment `sourcescan/cargo-near:0.21.1-rust-1.96.0@sha256:ccb22bb4…`. Cloned the checkout at `975b98d` into `/tmp/cmdk-screen-repro/app` (outside any other git repository) and ran `./scripts/reproduce-hash.sh --reproducible --vault v1.factory.prime-measure-1792736012.testnet` | **MATCH.** `size 783124 bytes`, `sha256 (hex) def9671e099fe325a7ac8c43831de0334a3dc75221f6c302b17a893e0377e1ba`, `sha256 (base58) G1Q5ksFhGbgPTN4A2DuvgraYmsbRpfwb78B547gAKXWq`, `published G1Q5ks…`, `result MATCH`. Took 26 min 26 s (amd64 image under emulation). Between `975b98d` and HEAD, `vault/` changed only in `factory/DEPLOY.md` and `tests/sandbox.rs`, so the deployed source is the current source |
| All vaults run that code | `view-account-summary` on v1–v11 | All 11 show `Global Contract (by Hash) def9671e…`. v2, v5, v7, v8 and v9 have no keys. v1, v3, v4, v6, v10 and v11 each have one function-call key and no full-access key |
| Factory code hash | Cloned `52e0446`, ran `cargo near build non-reproducible-wasm` in `vault/factory/` (cargo-near 0.22.0, rustc 1.95.0, macOS arm64, the toolchain the README names) | **MATCH:** 152,851 bytes, `5315458701e4f52c9c033d05d492ec75d18076a128a0261f153434670971c863`, equal to the factory's on-chain hash. Factory has no access keys. `get_config` gives `code_hash G1Q5ks…`, `count 11` |
| Executor bytecode (no reproduction step in the README; done as extra) | `forge build` of `executor/` at HEAD (forge-std pinned at `f3dae6e`), compared with `cast code 0xA9bb…D18b` after masking immutables | **Identical:** 16,459 bytes, same code and same CBOR metadata hash. Immutables: `MORPHO = 0xBBBB…EFFCb`, `COOLDOWN = 60`. The executor is not verified on Blockscout, so this local comparison is the only source-to-deployment link |
| Agent TDX measurement (step 6) | `./scripts/p3_reproduce_measurements.sh /tmp/cmdk-screen-mr evidence/boot-1794264608-2286af92 dstack-0.5.12 1 2G` (dstack-mr built from source; no cached image) | **ALL MATCH:** `os_image_hash MATCH`; MRTD and RTMR0–2 equal the values dstack-mr computes; RTMR3 equals the event-log replay. Took 7 min 10 s |
| Compose hash and key binding | `shasum -a 256 app-compose.json`, compared with the event log and the quote | `db4f5085…` equals the event log's `compose-hash` and the quote's MRCONFIGID (`01` + hash). Quote report_data[0..32] = `2286af92…` = hex of the registered agent key `ed25519:3Kmykr…`, which is the implicit account that sent `register_agent` |
| Repo evidence equals live evidence | sha256 of the 5 boot files, repository against `relayer.cmdk.fi/agent/evidence/*` | All 5 identical |
| Agent image | Registry: `docker buildx imagetools inspect --raw ghcr.io/cmdkfi/agent@sha256:e72564a8…` | Config digest `sha256:d3b51d67…`, equal to `imageId` in `deployments/testnet.json` |
| Agent image rebuild (`agent/build.sh` at `4171c10`, clone in `/tmp/cmdk-screen-agent`) | Two `--no-cache` buildx builds, `SOURCE_DATE_EPOCH=1794264093`. The first attempt died with `cannot allocate memory` while another screening run shared Docker's 3.8 GB; the retry with Docker idle succeeded in 6 min 40 s | **MATCH:** `REPRODUCIBLE image sha256:d3b51d670192b3b77b64241e70dca28eb97fa446128c3aca79ba548c18efcfcb`, binary sha256 `0a8f47c3eea1bfed9f02f84f4dd32bf98f24d0bf4928392801300bcf51def68d`. Equal to the published config digest and `imageId`, and to the binary hash in `agent/MEASUREMENT.md` |

## Verify-it results

Addresses: vault `v1.factory.prime-measure-1792736012.testnet`, executor `0xA9bbabE6d75C5BA96CA095586284774c9D09D18b`, `RPC=https://base-sepolia-rpc.publicnode.com`.

| Step | Command | Expected | Got | Verdict |
|---|---|---|---|---|
| 1a | `near account list-keys <vault> …` | Exactly one function-call key (`reduce`, `migrate`, `exit`); no full-access key | One key `ed25519:3Kmykr9L…`: `only do ["reduce", "migrate", "exit"] function calls on v1… with an allowance of 2 NEAR` | PASS |
| 1b | `near contract call-function as-read-only <vault> get_state …` | `owner` is the wallet in lower case; `base_address` is the derived address | `owner 0x0b7ed66f…0d25`, `base_address 0xb3ccd0ad…e6fb`, `owner_nonce 13`, `signer v1.signer-prod.testnet`. Factory count is 11. v3's owner is `0x69a7…` | PASS |
| 2a | `near account view-account-summary <vault> …` | `Global Contract (by Hash: SHA-256 checksum hex)` with the published hash | `def9671e099fe325…e1ba`, equal to `near.vault_code_hash_hex` | PASS |
| 2b | `./scripts/reproduce-hash.sh --reproducible --vault <vault>` (clean clone of `975b98d`) | `sha256 (hex)` equal to the hash; base58 equal; `result MATCH` | As expected (see Reproducibility) | PASS |
| 2c | Factory: no keys; `get_config` returns the vault hash | — | No keys; `code_hash G1Q5ks…`. Host rebuild reproduced its hash | PASS |
| 3a | `get_state` → markets, `cooldown_secs` | Markets with `id`, `executor`, `slice_max`, `corridor_set`; cooldown 120 | 3 markets (live, backup, demo), each with executor `0xa9bb…`, `slice_max 1000000000`, `corridor_set true`; `cooldown_secs 120` | PASS |
| 3b | `cast call <executor> "corridors(…)" <base_address> <live id>` | 5e17, 7e17, 3e17, 2e17, true, true, true | Exactly those seven lines. The backup and demo markets give the same | PASS |
| 4 | `./scripts/ltv.sh <vault> --market backup` | Collateral, debt and LTV in two columns; `cross-check MATCH`; without `--market`, `(no position on this market)` | Collateral 0.008335601888990126 WETH; debt 7.730581 USDC (7.730660 accrued); LTV 44.3513 % / 44.3518 %; `executor ltvOf 443513683921857959`; `cross-check MATCH with the "at lastUpdate" LTV (WAD)`. Without `--market`: `(no position on this market)` | PASS |
| 5a | `cast call Morpho "isAuthorized(…)" <base_address> <executor>` | `true` | `true` | PASS |
| 5b | `cast call <executor> "config(bytes32)(…)" <live id>` | Market parameters, pool `0x5718…156c`, two feeds, `[1200, 86400]` | Exactly that. The backup id gives the same pool. Uniswap factory `getPool(USDC, WETH, 100)` = `0x5718…156c` (fee 100), so it is the public 0.01 % pool | PASS |
| 6a | `get_state` → `approved`, `agent` | Approved measurement; agent key with `expires_at` | `approved` equals the six values in `deployments/testnet.json`. `agent` = `3Kmykr…`, `expires_at 1794869548` (16 Nov 22:52 UTC, 7 days after the 9 Nov registration) | PASS |
| 6b | The `register_agent` transaction | On-chain quote verification | `CPEBXcPD…` (archival RPC): signer `2286af92…` (implicit account of the agent key), method `register_agent`, `SuccessValue`, log `agent registered: ed25519:3Kmykr… until 1794869548`, 113.8 Tgas on the vault receipt | PASS |
| 6c | `./scripts/p3_reproduce_measurements.sh … dstack-0.5.12 1 2G` | `os_image_hash MATCH`, each register `MATCH`, `ALL MATCH` | As expected | PASS |
| 6d | `sha256sum evidence/<boot>/app-compose.json` | Compose hash in the event log and in MRCONFIGID after `01` | `db4f5085…` in all three | PASS |
| Vault tests (after step 6) | Build the 3 wasms, then `cargo test` in `vault/` (HEAD clone) | 15 unit and 8 sandbox tests | `15 passed`, `8 passed` (44.6 s), `0 failed` | PASS |
| 7 | `./scripts/attack-corridor.sh --land` | Five reverts, `result: 5 PASS, 0 FAIL` | **COULD NOT RUN:** it sends transactions from the team's wallet, which screening forbids. Checked `evidence/attacks.txt` against the chain instead: all 5 transactions are `status 0x0`, `to` = executor, `from` = test wallet. Blockscout decodes the reverts as `Expired()` 0x203d82d8, `0x013da4fb` (= `MoveLimit()`), `UnapprovedMarket()` 0x99b2f06d, `BelowFloor()` 0x4e7e2916 and `0x397b9774` (= `SellBudget()`); I computed the selectors with `cast sig` | COULD NOT RUN (sends transactions); evidence verified |
| 8a | `curl -s $E/transcripts` | Index sorted by name with `name`, `bytes`, `sha256`, `boot`, `vault`, `twin`, `loop` | 689 entries (boots `14fda74d`, 644; `2286af92`, 45), sorted, with those fields plus `path` and a `note`. All 644 older files fetched through `relayer.cmdk.fi` match their index sha256 | PASS |
| 8b | `./agent/verify-transcript.sh /tmp/<name>` | One `PASS` per check, then `ALL PASS`; `NO-CALL judge outcome: ` for loops without a judge call | Ran on all 689 live transcripts, the 75 archived in `evidence/transcripts/`, the 3 in `evidence/scenes/` and the offline testdata file. **Live (689):** 319 `ALL PASS`; 357 `NO-CALL judge outcome: `; 3 `NO-CALL judge outcome: error`; 8 `NO-CALL judge outcome: skipped_attestation_failed`; 2 `4 FAILED`, which the agent itself recorded as `signature_not_verified` (NEAR AI answered 404 "Signature not found"; `accepted false`, `rules only`). **Archive (75):** 32 `ALL PASS`, 40 no-call, 3 `error`. **Scenes (3) and testdata (1):** `ALL PASS` | PASS; the README's count of error transcripts is stale (Discrepancies) |
| 8c | Worker copies `https://relayer.cmdk.fi/agent/evidence/<file>` and `/agent/transcripts/<name>` "with CORS" | Same bytes, CORS | Same bytes. CORS is sent only for `https://cmdk.fi`, `https://testnet.cmdk.fi` and `http://localhost:5173` (`cloud/wrangler.jsonc:15`) | PASS (CORS is allowlisted) |
| 9 | `evidence/protect-live.txt` | Owner lowers the act line, agent `reduce`, LTV back at target | `Reduced` event: `protective: true`, LTV 0.4997 → 0.4002 at target 0.40. Base `0xde730226…` has `status 1`, `from 0xb3ccd0ad…` (the vault's derived address), `to` = executor | PASS |
| 10 | `evidence/scenes/` | Three price scenes, venue-stress `migrate`, `exit` on v4 | Gradual crash: agent and keeper each −1.76 USD from start (4.6699 → 2.9101), unprotected −2.11 (→ 2.5554, liquidated). Venue stress: borrow block 54926168 at 1793907102, migrate `0xd597c432…` block 54927197 at 1793907308 = 3.4 min later, `from` v1's address; the moved debt 7.729880 USDC and collateral 0.008335602 WETH equal today's backup position. Exit `0x014aebcd…`: `status 1`, `from` v4's address `0xd3983a65…`, method `exit` at 16:29:09Z; items pushed 16:23:10 (6 min). Manual broadcast `0xd90f46ed…`: `status 1`. Gap scene's revert is explained in `gap.md:114` | PASS (sampled) |
| 11 | `evidence/inject.txt` | Hostile memo; proposal stays within three methods | The judge answered `hold`. Its transcript `boot-1793877578-a405e917-v1-0002.json` has sha256 `748c8c59…` (as cited) and `ALL PASS` | PASS |
| 12 | `evidence/revoke.txt` | `revoke_agent` deletes the key and clears `approved`; close; withdraw 0.002 WETH to the owner | v2 today: no keys (`Account is locked`), `agent null`, `approved null`. Revoke transaction `FYeZcfVX…` (archival): `owner_call` with a `DeleteKey` receipt, success. Withdraw `0x431aefa8…`: `status 1`, WETH `Transfer` from `0xfc85…` to `0x0b7e…`, value `0x71afd498d0000` = 2,000,000,000,000,000 wei (0.002 WETH) | PASS |

## Feature verification

| Claim (README) | Evidence | Verdict |
|---|---|---|
| The agent key is limited by the protocol to `reduce`, `migrate`, `exit`, valid 7 days, and replaced on re-registration | `vault/src/lib.rs:43-46` (`REGISTRATION_SECS = 7*24*3600`, `AGENT_METHODS = "reduce,migrate,exit"`, 2 NEAR allowance), `:471-508`; live `list-keys` | PASS |
| `register_agent` verifies Intel signatures, TCB UpToDate, debug off, approved MRTD/RTMR0–2, and replays RTMR3 for compose and OS hash; report_data = caller key | `vault/src/attest.rs:122-166` (dcap-qvl `new_prod`, `status != "UpToDate"`, `td_attributes[0] & 1`, register compare, `replay_rtmr3`), `:100-109`; `lib.rs:472-481` (caller must be the implicit account, signer = predecessor); tests in `attest.rs` | PASS |
| Agent methods: allowlisted market with a corridor set, slice maximum on `reduce`, cooldown on every method, registration not expired | `lib.rs:435-465`, `:584-589`, `:623-636` | PASS |
| Every signed Base transaction is decoded back and allowlisted: chain id, gas and fee caps, value 0 except `sweep_eth` to the owner, selector per caller, market ids, deadline within (now, now + 600 s] | `lib.rs:638-680` (decode, `check_tx`), `:682-799`; `evm.rs:12-19` | PASS |
| Owner actions are ERC-191 messages verified with `ecrecover`, with nonce and expiry | `vault/src/owner.rs:51-82` (nonce > last, not expired, expiry ≤ now + 3600 s, vault id, protocol, network) | PASS |
| `revoke_agent` deletes the key and clears the approved measurement in one transaction | `lib.rs:419-426`; live v2 state; transaction `FYeZcfVX…` receipts | PASS |
| `set_executor` disables the agent on that market until `setup`; once unused, the old executor loses Morpho authorization and allowances | `lib.rs:381-414` (`corridor_set = false`; revoke calls) | PASS (code; not exercised live) |
| Factory: keyless, vault code hash fixed, account created with no keys and the Global Contract by hash attached in one batch | `vault/factory/src/lib.rs` (`use_global_contract(self.code_hash)` in the same batch as `create_account`, then `init`); factory has no keys; hash reproduced | PASS |
| "Nobody, including us, can … create vaults with other code under the factory" | True for code. Policy is another matter: `create_vault(owner, policy)` takes the executor, tokens, `slice_max` and `cooldown_secs` from the caller (`factory/src/lib.rs` `validate` checks formats only). The relayer does the same (`scripts/relayer.py:231-253`) | PASS for the claim as worded; design concern (Discrepancies, item 1) |
| Executor: immutable, no admin, caller-scoped; tokens go only to the caller, the fixed pool or Morpho | `executor/src/Executor.sol`: no owner or upgrade path; borrow and withdraw go to `msg.sender`; swap callback pays only `_pool`, set during the swap (`:420-434`); `_check` keeps executor balances unchanged; deployed bytecode identical to source | PASS |
| Swap fills no worse than 1 % below Morpho's oracle | `CorridorMath.sol:11` (`SLIPPAGE_BPS = 100`), `Executor.sol:420-430` (`owed > maxIn` reverts), `:556-575` (`minOut`) | PASS |
| Floor, daily sell budget, protective session (act → target, 24 h), at most 16 discretionary sales | `Executor.sol` `reduce` `:245-302`, `_recordSale`, `SALE_SLOTS = 16`, `SESSION = 24 hours`; invariant campaign (64 runs × 25 depth, `invariant_noViolation` and `invariant_executorHoldsNothing` pass); fork tests | PASS |
| At most two moves per 24 h, no recipient; collateral and debt conserved within one unit | `MAX_MOVES = 2`, `_recordMove`; `migrate` post-checks `MigrationMismatch` and `AboveActLine` (`:348-352`) | PASS |
| `exit` needs the owner's switch and then closes the market to the agent | `exit` checks `c.exitAllowed`, sets `closedForAgent` | PASS |
| A stale feed blocks new risk but never selling or exit | `_requireFresh` is called in `open`, `openFromStables` and `migrate` (destination only), not in `reduce` or `exit`; fork test `test_exit_not_refused_on_stale_feed` | PASS |
| Morpho callbacks accept only Morpho during the executor's own operation | `onMorphoRepay` and `onMorphoFlashLoan` require `msg.sender == MORPHO && _locked` | PASS |
| Agent loop: ticks every 2 s, feeds every 60 s, judge on a new screen, a new feed item or every 6 h, thinking on, judge in its own task | `agent/src/main.rs:61-63,173` (`DIGEST_SECS = 6*3600`, `FEED_POLL_SECS = 60`, `tick_secs: 2`); `judge.rs:1-13`; `logic.rs:400` `want_judge` | PASS in code. Measured cadence is slower (Discrepancies) |
| Screens: LTV above act, collateral worth zero, utilization > 95 %, stale or negative feed; agreement rule (screen on that market, or two text sources with different feed ids) | `logic.rs:77-112`, `:310-314` (`TEXT_MIN_SOURCES = 2`), `:441-483` | PASS |
| Fresh key per boot; no code loaded at runtime; image pinned by digest | `main.rs:1095` (`SigningKey::generate(OsRng)`, not persisted); compose pins `ghcr.io/cmdkfi/agent@sha256:e72564a8…`; the config digest in the registry equals `imageId` | PASS (image rebuilt to the same ID) |
| Model answers are signed by NEAR AI's enclave and checked against an attestation with a fresh nonce | `agent/src/judge.rs:285-316`: fresh 32-byte nonce; report_data must bind signing address and nonce; DCAP verifies the quote; debug off. The transcript verifier recovers the EIP-191 signer as the attested address (351 PASS) | PARTIAL: the model enclave's measurement (`mr_td`, RTMRs) is recorded but not compared with any expected value, and `nvidia_verified` is always `false`. The check proves "a genuine, non-debug TDX VM holds this signing key", not which code serves the model |
| An injected memo cannot make the agent move funds | `inject.txt` plus code: the model schema has no amount or recipient; the vault allowlist gives the agent only three executor selectors | PASS |
| A compromised agent's worst case is bounded per day (budget, moves, one exit per market, 16 sales) | The executor code above. The bounds hold per position and are enforced on Base | PASS |
| Revocation is final within 10 minutes | Key deleted on NEAR; executor deadline ≤ 600 s (`DEADLINE_SECS`), checked by both the vault and the executor (`before(deadline)`) | PASS |
| Web app at https://testnet.cmdk.fi (subagent check; details below) | The live assets and `/config.json` are byte-identical to a local build of `916b7e8`. Typecheck, 159/159 unit tests and the bundle-boundary check pass. Browser scenarios: 78/85 at default parallelism; the 7 failures are all in `onboarding.spec.ts`, which passes 16/16 with `--workers=1`. All 17 screenshot comparisons pass. The sandbox runs end to end with no network calls; S1 shows live market reads; the honesty footer is present | PASS with the discrepancies listed |
| Landing https://cmdk.fi keeps the build/production split | BTC, any-chain deposits, Confidential Intents and buy-back are labelled "Production release"; other venues are "Roadmap"; figures match the protocol README | PASS |
| Research numbers cited (backtest $2.65M, 20,479 liquidations, 1,890/1,895 and 5,009/5,011, incident replay 6 of 8, 136 vs 25 min, $8,811 vs $5,987, cost $0.70–1.00) | Found in the cited files: `liquidation-backtest/backtest.md` ($681.7k + $1.97M = $2.65M), `liquidation-dataset/README.md:794-795`, `judge-evidence/incident-replay-results.md:28-31,96-97`, `venue-coverage/near_and_stock_lending.md:36,58`, `costs/costs-and-pricing.md:22` | PASS (traced to the files; not recomputed) |
| External market figures (Coinbase 53,000 borrowers, $1.56B, $394M, DeFi Saver, Bloom $954k, and others) | Not checked against outside sources | UNVERIFIED |

## Discrepancies

1. **Vault policy is chosen by whoever creates the vault, not by its owner** (design; I did not exploit it).
   - **Who sets the policy.** `create_vault(owner, policy)` on the factory, and `POST /create-vault` on the public relayer, accept any `executor`, `loan_token`, `collateral_token`, `slice_max` and `cooldown_secs` for any `owner` (`vault/factory/src/lib.rs` `validate`; `scripts/relayer.py:231-253`).
   - **What the owner signs.** The owner's `setup` message names market ids, not executors. The vault then signs `approve(executor, max)` on both tokens and `setAuthorization(executor, true)` on Morpho for the executor stored in its own state (`vault/src/lib.rs:289-320`).
   - **What the web app does.** It adopts the owner's newest vault numbered 10 or higher: "a vault the wallet already owns is adopted instead" (`cmdk-app/app/src/protocol/near/vault.ts:85-113`). It never compares that vault's market executors with the published manifest. `configMatchesPublished` compares only the app's own config (`components/runner.ts:136-140`), and the setup facts take `vm.executor` from the vault (`protocol/base/reads.ts:249`).
   - **Consequence.** Anyone could create a vault for someone else's address with a hostile executor. If that owner then used the app, the setup signature would grant that executor unlimited allowances on the position address. The fix is cheap: compare `get_state.markets[*].executor` (and the tokens) with `deployments/testnet.json` before adoption and setup, and show them in the setup review.
   - **README wording.** The README's immutability statements are accurate for code. "The slice maximum is fixed when the vault is created" is acknowledged under Limitations, but who fixes it is not.
2. **The model attestation does not pin the model-serving code.** `judge.rs:285-316` checks DCAP validity, debug off and the report_data binding, but not `mr_td` or RTMRs against expected values. `nvidia_verified` is hard-coded `false`. "Every model decision is attributable … from the model-serving enclave" therefore rests on NEAR AI's key distribution, not on a measured image. This bounds the attribution claim, not fund safety, because the model cannot name amounts or recipients.
3. **Step 8's error-transcript count is stale.** The README says error transcripts are "the three such transcripts … from 4 Nov". There are 3 from 4 Nov in the archive, plus 2 from 8 Nov and 1 from 10 Nov (`boot-1794264608-2286af92-v11-0002.json`, a connection error to `cloud-api.near.ai`) on the live volume. The README also does not mention the 8 `skipped_attestation_failed` transcripts (9–10 Nov, NEAR AI attestation HTTP 500 "Failed to validate API key") or the 2 `signature_not_verified` transcripts, which `verify-transcript.sh` prints as `4 FAILED`. The agent handled all of these correctly (rules only, `accepted false`); only the documentation lags.
4. **A registration file is missing from the repo copy of the current boot.** `evidence/boot-1794264608-2286af92/index.json` lists `registration-v1.json`, but the file is not in that directory. It is served live, along with `registration-v4/v10/v11.json`. The older boot directories include theirs.
5. **CORS is narrower than "served for browsers, with CORS".** It applies only to the team's origins (`cloud/wrangler.jsonc:15`). `cloud/README.md:83` lists `https://cmdk.fi` and localhost but omits `https://testnet.cmdk.fi`, which the config includes.
6. **The measured tick cadence is slower than "about every two seconds".** The configured tick is 2 s. `/agent/vaults` showed v1 at 9,435 ticks and v4 at 17,592 over about 49,800 s of boot uptime, which is 5.3 s and 2.8 s per tick.
7. **Two vaults still carry the previous boot's key.** v3 and v6 still hold the key `2QwVDK…` registered under the old compose hash `216124c1…` (valid until 15 Nov), and the running agent does not watch them. That matches the design (the agent watches only vaults that approve the current measurement, and the old key was in memory only), but a judge looking at `list-keys` on v3/v6 sees a live-looking key with no running holder.
8. **Web app** (from the subagent's check of `cmdk-app` at `916b7e8`):
   - (a) The app README says it is "deployed from `main`". The live site came from a manual `workflow_dispatch` on `feat/guided-flow`; `main` (`61f71cc`) is 30 commits behind. The live bundle is nonetheless byte-identical to a build of `916b7e8`.
   - (b) The README says `dist/` has no "sandbox" string. It has 42; the README is stale since Phase 6b.
   - (c) `pnpm test` before `pnpm build` gives 158 pass + 1 skip; 159 after the build.
   - (d) 7 of 85 browser scenarios (`onboarding.spec.ts`) time out at default parallelism and all 16 in that file pass with `--workers=1`, so the suite is load-sensitive.
   - (e) Several source citations are off by 1–6 lines (`owner/message.ts` against `owner.rs` and `p2_owner.py`; `base/abi.ts` against `chain.rs`). The cited content is correct nearby.
   - (f) "Not insurance … on S5" appears only in the shared footer.
   - (g) The landing page says revocation is "Live on testnet: Revoking the agent takes one signature". The protocol supports it (step 12), but the web app has no revoke action.
9. **Executor is not source-verified on Blockscout or Basescan.** I verified the match locally (Reproducibility), but a judge without Foundry cannot.
10. **The README omits the agent key's NEAR allowance.** It is 2 NEAR (`AGENT_KEY_ALLOWANCE`). A re-registration with the same key returns early without renewing the allowance (`lib.rs:494`). Under the stated cadence this is not material, but it is an undocumented limit on agent actions per key.

## Preliminary score: 8.5 / 10

**Why it scores high:**
- **Verification is unusually complete and it reproduces.** Every read-only step ran as written on a fresh machine. The vault, factory and executor deployments trace bit for bit to source. The enclave's measurements reproduce from public inputs and are bound on-chain to the agent key. Signed model transcripts verify offline.
- **The claims are precise and the code backs them.** The three-layer bounds (NEAR function-call key, the vault's decode-and-allowlist, the immutable caller-scoped executor) are implemented as described and tested (23 vault tests, 64 executor tests including fork and invariant tests).
- **The live evidence is real and on-chain.** Protective reduce, migrate under venue stress, text-path exit, revoke, close and withdraw were all confirmed.
- **The build/production split is honest** in the README, the landing page and the app. Failures (gap scene, missed broadcast, Phala KMS incident) are disclosed, not hidden.
- **The web app is live, reads real chain state, and is built from the screened commit.**

**Why not higher:**
- The vault-policy/adoption gap (Discrepancies, item 1) is the most material finding: a third party controls the executor that the owner's `setup` authorizes, and the app does not check it.
- The model-attestation check is weaker than "from the model-serving enclave" suggests (item 2).
- The NEAR Intents and Confidential Intents, BTC and any-chain features, which carry much of the pitch's novelty and two of the three tracks, are not demonstrable on testnet. This is disclosed, but it is unproven.
- Several small documentation drifts exist (items 3–8).
- The system is unaudited.

## Questions for the human judges

1. Should vault creation bind the policy to the owner? Options: the owner signs the policy, the factory fixes the executor, or the app refuses to adopt a vault whose executors differ from the manifest. Is a fix planned before submission?
2. Does the team intend to pin NEAR AI's model-serving measurement (and verify the NVIDIA attestation)? If not, how much weight should "every model decision is attributable" carry?
3. The Privacy track rests on Confidential Intents (production release) and on transcripts that are public in this build. What in the testnet build should count toward that track?
4. Will `feat/guided-flow` be merged so that the live site corresponds to `main` and to the CI that runs on `main`?
5. The 7 onboarding browser tests that fail under parallel load: is there a real-time dependency in the app or only in the fixture?
6. Is revocation going to be exposed in the web app, given the landing page lists it as "Live on testnet"?
7. Will the executor be source-verified on a public explorer so judges without Foundry can confirm the bytecode?
