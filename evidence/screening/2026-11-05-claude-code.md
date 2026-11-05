# AI screening rehearsal 1, Claude Code (5 Nov 2026)

Run by C per DEPLOY.md step 11 on the same fresh clone of commit 4fbcf01 as the Codex run, with the same prompt (`2026-11-05-prompt.md`). Tool: a Claude Code agent (Claude Opus, Claude Code 2.1.318) with shell, read and search tools under read-only rules, about 80 minutes; a separate headless `claude -p` process was refused by the session's safety check, so the agent ran inside the team's session. Caveat the report itself states: the agent image rebuild reused the team's buildx builder on the team's Mac. The report below is verbatim; the clone was unchanged afterwards.

---

# Screening report: cmdk (cmdkfi/app @ 4fbcf01), NEAR HACKATHON

Screened on 5 Nov 2026 (about 09:10–10:30 UTC) on macOS arm64 with Docker. Under the screening rules (Q-017) this is a screening input, not a decision.

## Summary
1. The live deployment matches the README wherever I could read it. The vault on NEAR testnet has no full-access key and one function-call key limited to `reduce`/`migrate`/`exit`. The corridors (50/70/30/20, migrate and exit allowed), the Morpho authorization, the executor's market config and the LTV math all read back as stated, and the `ltv.sh` cross-check prints MATCH.
2. Three reproducibility claims hold:
   - **Vault wasm:** built from the deployed commit, its hash equals the on-chain Global Contract hash.
   - **Agent measurement:** reproduced from the quote and event log in the on-chain `register_agent` transaction (`ALL MATCH`).
   - **Agent image:** builds reproducibly to the pinned image ID. It also chains, byte for byte, to the compose file and pre-launch script that the vault approved.
3. Tests: executor 64/64, vault 15/15 unit and 8/8 sandbox, agent 59 pass with 1 ignored. The deployed executor bytecode equals a compile of HEAD source, apart from the two immutables (Morpho address and the 60 s cooldown); the metadata hash is identical.
4. Five of the twelve "Verify it" steps point at evidence that is not in the repository: `evidence/attacks.txt`, `evidence/transcripts/`, `evidence/scenes/`, `evidence/inject.txt`, `evidence/revoke.txt`. So do `evidence/<boot>/`, `research_notes/`, `sim/` and `docs/`. The crash scenes, the injection test and the revocation scenarios are therefore unverified.
5. Text-versus-code discrepancies:
   - "four named methods" in the "Why NEAR" section (the code has three).
   - The "retraction cancels a text-only alarm" rule has no code I could find.
   - "17 operators" (testnet shows 16, threshold 11).
   - Plain `cargo test` fails the sandbox suite unless the wasms are built first.
   - The team section is empty.

## Reproducibility

| Item | How | Result | Time |
|---|---|---|---|
| Vault code hash | Read the vault's NEP-330 `contract_source_metadata`: build commit `975b98d…`, image `sourcescan/cargo-near:0.21.1-rust-1.96.0@sha256:ccb22bb4…`. Ran `./scripts/reproduce-hash.sh --reproducible --vault v1.factory.prime-measure-1792736012.testnet` in a clean clone of that commit. | 783,124 bytes, sha256 `def9671e…77e1ba`, base58 `G1Q5ksFhGbgPTN4A2DuvgraYmsbRpfwb78B547gAKXWq`, `result MATCH`. Vault source is identical between 975b98d and HEAD; only `vault/factory/DEPLOY.md` differs. | 21 min 38 s (amd64 image emulated on arm64, other jobs running at the same time). Just over the 20-minute budget; it was already running when I saw that. |
| Agent image | `agent/build.sh screening-agent` at HEAD (two `--no-cache` builds). `SOURCE_DATE_EPOCH=1793856467`. | `REPRODUCIBLE image sha256:e4325d0196aa…1852`, binary `917a280c…84af`, both as stated in MEASUREMENT.md. The registry manifest of the pin `ghcr.io/cmdkfi/agent@sha256:54405980…` (anonymous `imagetools inspect --raw`, manifest sha256 equals the pin) has `config.digest` = `e4325d01…`. | 4 min 27 s |
| Image → compose → approved measurement | Downloaded `app-compose.json`, `vm_config.json`, `tdx_quote.hex` and `event_log.json` from the agent's public evidence endpoint (`AGENT_EVIDENCE_URL` in `cloud/wrangler.jsonc`). | sha256(app-compose.json) = `6dc680d4…7e0b`. That equals the vault's `approved.compose_hash`, the event-log `compose-hash` and MRCONFIGID after `01`. Its `docker_compose_file` is byte-identical to `agent/docker-compose.live.yml`, which pins the image above. Its `pre_launch_script` is byte-identical to `agent/pre-launch.sh` (sha256 `98218161…`). `allowed_envs` = `["NEARAI_API_KEY"]`. The served quote is byte-identical to the quote in the `register_agent` transaction. | < 1 min |
| Agent measurement (MRTD, RTMR0–3, OS image) | `./scripts/p3_reproduce_measurements.sh <work> <ev> dstack-0.5.12 1 2G`, with quote and event log decoded from the arguments of `register_agent` tx `GyV7A726…`. The live `vm_config.json` confirms 1 vCPU and 2 GiB. | `os_image_hash MATCH`, MRTD/RTMR0/1/2 MATCH against dstack-mr, RTMR3 MATCH against the replay, `ALL MATCH`. The values equal the vault's `approved` record. | 7 min 48 s |
| Executor bytecode | `forge inspect Executor deployedBytecode` at HEAD, compared with `cast code 0xA9bb…D18b`. | 16,459 bytes each. Differences are only at immutable slots (Morpho address, `0x3c` = 60 s cooldown). CBOR metadata hash identical. No EIP-1967 proxy slot. | < 1 min |

Caveats:
- The agent build ran on the same machine and the same buildx builder (`cmdk-repro`) the team used, so it is not an independent-machine check. A judge should rerun it elsewhere.
- The dstack-mr toolchain (`scripts/measurements/Dockerfile.dstack-mr`, `verify_measurements.py`) is the team's own.

## Feature verification

| Claim (README) | Evidence | Verdict |
|---|---|---|
| Agent key limited to `reduce`, `migrate`, `exit`; no full-access keys | `near account list-keys`: one key `ed25519:HJ7Gfq…`, "only do [reduce, migrate, exit] … allowance of 2 NEAR". `vault/src/lib.rs:44-46` (`AGENT_METHODS`, 2 NEAR allowance). | Confirmed |
| Agent methods take no recipient; owner path is the only way out | `lib.rs:435-464`: `reduce`/`migrate`/`exit` build executor calls only. `check_tx` (`lib.rs:682-794`) refuses agent calls to tokens or Morpho and per-caller selectors. `withdraw` and `sweep_eth` are owner-only (`lib.rs:360-380`). | Confirmed (code) |
| Executor sends only to the caller; no admin; every call has a deadline | `Executor.sol`: borrow and `withdrawCollateral` go to `msg.sender`; swap paid from the caller's own collateral to the fixed pool. `before(deadline)` on every state-changing function. No owner or upgrade path; bytecode matches source. | Confirmed |
| 10-minute deadline, enforced by the vault and the executor | `evm.rs:19` `DEADLINE_SECS = 600`; `check_tx` refuses a deadline outside (now, now+600] (`lib.rs:789-792`); `Executor.before` | Confirmed |
| Corridor 50/70/30, budget 20%, migrate and exit on; two moves a day | `cast call corridors` on live, backup and demo returns 5e17/7e17/3e17/2e17/true/true/true. `MAX_MOVES = 2` (`Executor.sol:51`). | Confirmed (live) |
| Swap no worse than oracle −1% | `CorridorMath.sol:11,50-62`; `_sellCollateralFor` passes `maxCollateralIn` | Confirmed (code) |
| Stale feed blocks open, never selling or exit | `_requireFresh` only in `open`, `openFromStables` and `migrate` (destination) | Confirmed (code) |
| Morpho callback only from Morpho | `onMorphoRepay` and `onMorphoFlashLoan` require `msg.sender == MORPHO && _locked` | Confirmed (code) |
| On-chain TDX verification: Intel signatures, UpToDate, debug off, approved measurement | `vault/src/attest.rs:107-147` (dcap-qvl `new_prod`, `UpToDate`, debug bit, MRTD/RTMR0-2, RTMR3 replay, compose and OS hashes). `register_agent` tx `GyV7A726…` succeeded with 131.35 Tgas, signer = agent implicit account `f21cf44b…`, arguments carry the quote, collateral and event log. | Confirmed |
| Key valid 7 days; restart means new key | `REGISTRATION_SECS` = 7 days; `get_state.agent.expires_at` = 1794462077 (12 Nov 2026 05:41 UTC) | Confirmed. Expires before the 14 Nov deadline (question below). |
| Owner messages: ERC-191, nonce, expiry | `vault/src/owner.rs` (ecrecover, protocol, network, vault id, nonce > last, expiry ≤ now+3600) | Confirmed (code, unit and sandbox tests) |
| Immutable vault: Global Contract by hash, keyless factory with fixed hash | Account summary: "Global Contract (by Hash) def9671e…", 0 full-access keys. Factory: "Account is locked (no access keys)", `get_config.code_hash` = `G1Q5ks…`, `count` 2. | Confirmed. The factory's own code (hash `53154587…`) has no NEP-330 link and no reproduce step. |
| `revoke_agent` deletes the key and blocks re-registration | `lib.rs:419-426` deletes the key and clears `approved` | Confirmed (code); live revoke not shown (evidence missing) |
| `set_executor` "revokes the old executor's … allowances, sets up the new one" | `lib.rs:381-414` only revokes; setting up the new executor takes a separate `setup` message | Partly: wording overstates what one message does |
| Agent tries `migrate` before `exit` | `agent/src/logic.rs:505-544` | Confirmed (code) |
| Agreement rule: judge plus an on-chain screen, or judge plus two independent text sources | `logic.rs:305-365, 476-495` (`text_corroboration`, distinct `feed_id`) | Confirmed (code). Two of the three text feeds are published by the team (`feeds/`), and the third is Base's status RSS (`judge.rs:389-394`). |
| "a retraction from the same source cancels a text-only alarm" | `grep -ri retract agent/` finds nothing | Not found in code |
| "allowlisted protocol, security and news sources" | Feeds in this build: `status.base.org/history.rss` plus two team demo feeds | No news source in the build |
| Model attestation with fresh nonce, `provider_tee` signature | `judge.rs:284-375`; `verify-transcript.sh` ALL PASS on the prototype transcript | Signature and hashes verifiable offline. The model's TDX quote is not re-verifiable from a transcript: it stores only `quote_sha256` and the agent's own verdict (`ok`, `dcap.status`), and the script trusts those. |
| Protect-live: agent reduce brings LTV to target | `evidence/protect-live.txt`. Base tx `0xde7302…` (block 54394078): from `0xb3cc…e6fb` (vault-derived address) to the executor, selector `reduce` (0x643d3b9e), status 1, `Reduced` with protective = true, LTV 0.4997 → 0.4002 (target 40% after the owner's act-45 corridor, tx `0x3a2de9…`, also from the vault address). The vault refuses `reduce` for the owner caller, so this call came through the agent path. | Confirmed on-chain |
| Chain Signatures: 17 operators, 11 required | `v1.signer-prod.testnet` `state`: 16 participants, threshold 11 | Not confirmed for testnet (17 may be mainnet; README does not say) |
| Test counts: 15 unit and 8 sandbox tests | Counted and run: 15/15 and 8/8 pass | Confirmed, but see the `cargo test` discrepancy |
| Foundry invariant fuzzing | `forge test`: 64 passed, including `invariant_executorHoldsNothing` and `invariant_noViolation` (64 runs, 1,600 calls) | Confirmed |
| Backtest, incident-replay, judge-injection and simulation numbers ($2.65M, 1,890/1,895, 21/30, $8,811…) | Cited files are under `research_notes/` and `sim/`, which are not in the repository | Could not verify |

## Verify-it results

| # | Command | Expected | Got | Verdict |
|---|---|---|---|---|
| 1a | `near account list-keys <vault> network-config testnet now` | one function-call key for `reduce`, `migrate`, `exit`; no full-access | exactly that, 2 NEAR allowance | PASS |
| 1b | `near contract call-function as-read-only <vault> get_state …` | `owner` lower-case wallet, `base_address` | `owner 0x0b7ed66f…0d25` (the team test wallet), `base_address 0xb3ccd0ad…e6fb` | PASS |
| 2a | `near account view-account-summary <vault> …` | `Global Contract (by Hash …)` = `near.vault_code_hash_hex` | `def9671e…77e1ba`, equal | PASS |
| 2b | `./scripts/reproduce-hash.sh --reproducible --vault <vault>` from a clean checkout of the NEP-330 commit | sha256 hex and base58 equal, `result MATCH` | equal, `result MATCH` | PASS. The first attempt, a nested clone inside the clone directory, failed because cargo-near picked up the parent repository; a checkout outside the tree worked. |
| 3 | `get_state` markets; `cast call <executor> "corridors(…)" <base_address> <live id>` and the backup id | seven lines 5e17, 7e17, 3e17, 2e17, true, true, true | exactly that, live and backup (demo the same) | PASS. `cooldown_secs` is 120 in the vault while `deployments/testnet.json` `cooldownSeconds` is 60; the 60 is the executor's `COOLDOWN`. Consistent but easy to misread. |
| 4 | `./scripts/ltv.sh <vault>` | two columns; `cross-check MATCH …` | collateral 0.0083356 WETH ($20.13), debt 7.7297 USDC, LTV 38.3942% / 38.3947%, `cross-check MATCH` | PASS |
| 5a | `cast call Morpho "isAuthorized(…)" <base_address> <executor>` | `true` | `true` | PASS |
| 5b | `cast call <executor> "config(bytes32)…" <live id>` | params, pool `0x5718…156c`, two feeds, `[1200, 86400 [8.64e4]]`; backup the same pool | as expected. Pool fee 100 (0.01%), tokens USDC/WETH, Uniswap factory as in deployments. | PASS |
| 6a | `get_state` → `approved`, `agent`; the `register_agent` tx shows on-chain verification | present | present; tx `GyV7A726…` SuccessValue, 131 Tgas, log "agent registered … until 1794462077" | PASS |
| 6b | `./scripts/p3_reproduce_measurements.sh <work> evidence/<boot> dstack-0.5.12 1 2G` | `ALL MATCH` | `evidence/<boot>/` is not in the repo. Run instead on the quote and event log decoded from the on-chain tx: `ALL MATCH` | COULD NOT RUN as written (files missing); PASS on equivalent on-chain inputs |
| 6c | `sha256sum evidence/<boot>/app-compose.json` | equals the event-log compose hash and MRCONFIGID | not in repo. The file from the agent's public endpoint gives `6dc680d4…`, equal to both | COULD NOT RUN as written; PASS on the served file |
| — | `cargo test` in `vault/` (15 unit, 8 sandbox) | all pass | plain `cargo test`: 15 pass, 8 sandbox FAIL ("No such file or directory"). After the prebuild in the `tests/sandbox.rs` header: 8/8 pass | FAIL as written; PASS with the undocumented prebuild |
| 7 | `./scripts/attack-corridor.sh --land …`; recorded in `evidence/attacks.txt` | 5 PASS | Not run: it sends signed transactions, which the screening rules forbid. `evidence/attacks.txt` is not in the repo; `scripts/ATTACKS.md` has a fork run against the retired prototype executor. | COULD NOT RUN; evidence missing |
| 8 | `evidence/transcripts/`; `./agent/verify-transcript.sh agent/testdata/transcripts/boot-1793051811-26cc5edf-0001.json` | `ALL PASS` | the testdata transcript prints 7 PASS lines then `ALL PASS`. `evidence/transcripts/` is not in the repo. The testdata transcript is from the prototype and has no `judgment_market`. | PASS (testdata only); evidence missing |
| 9 | `evidence/protect-live.txt` | owner lowers the act line, agent reduce, executor tx, LTV at target | present and confirmed on-chain (see the feature table) | PASS |
| 10 | `evidence/scenes/` | crash, gap, whipsaw and venue-stress transactions | directory not in the repo (only `evidence/venue/` from the 3 Nov prototype close-out) | FAIL (missing) |
| 11 | `evidence/inject.txt` | injection memo and bounded result | not in the repo | FAIL (missing) |
| 12 | `evidence/revoke.txt` | revoke, `AccessKeyNotFound`, `Expired`, close, withdraw | not in the repo | FAIL (missing) |

## Discrepancies
1. **Missing evidence:** `evidence/attacks.txt`, `evidence/transcripts/`, `evidence/scenes/`, `evidence/inject.txt`, `evidence/revoke.txt`, `evidence/<boot>/`. The README presents steps 7, 8, 10, 11 and 12 as recorded.
2. **Missing references:** `research_notes/…` (6 paths), `sim/` ("the script and paths ship in `sim/`"), `docs/01-design-and-feasibility.md` §11, `HACKATHON.md`. Every quantitative research claim depends on files a judge cannot open.
3. **"four named methods"** (README "Why NEAR") contradicts the three methods everywhere else and in `AGENT_METHODS`.
4. **Retraction rule** ("a retraction from the same source cancels a text-only alarm but never an on-chain screen") has no implementation I could find in `agent/src`.
5. **"17 operators, 11 required":** testnet `v1.signer-prod.testnet` shows 16 participants, threshold 11.
6. **`v1.signer`** in "What it uses": the build uses `v1.signer-prod.testnet` (the mainnet name is fine for the product; the build should be labelled).
7. **`cargo test` in `vault/`** does not pass as written; the sandbox suite needs three `cargo near build` prebuilds that only `tests/sandbox.rs` documents.
8. **News sources:** "news sources" are described but none ship. The two "independent" text feeds besides Base status are both team-published demo feeds, so the text path can be satisfied by team-controlled sources on testnet. This is disclosed in `feeds/README.md` but not in the README's agent description.
9. **`set_executor`** "sets up the new one": the code only revokes the old executor; setting up the new one is a separate `setup` message.
10. **Transcript verifier:** the README says the model attestation is "checked against an attestation with a fresh nonce". The offline verifier cannot re-check the model's TDX quote; it trusts the agent-recorded `ok` and `dcap.status`.
11. **"three WETH/USDC markets":** the demo market is dUSDC/WETH.
12. **"sells in slices":** live `slice_max` is 1,000 USDC on a 7.7 USDC debt, so the NEAR-side slice cap does not bind in the demo.
13. **Factory code** (hash `5315458701e4…`) has no NEP-330 link and no reproduce instructions, although the README's immutability argument relies on it ("vault code hash fixed inside it"). Its state does show the expected `code_hash` and no keys.
14. **Layout table** omits `cloud/` (the relayer Worker) and `feeds/`.
15. **Prompt naming:** the judge system prompt still calls the product "PRIME" (`judge.rs:32`). Cosmetic.
16. **Team, license, contact:** "_To fill before submission._"

## Preliminary score: 6.5 / 10
- **For:**
  - Unusually strong, verifiable core. Vault wasm, agent image, enclave measurement and executor bytecode all reproduce, and they chain to the on-chain approval.
  - The live state on both chains matches every read-only claim checked.
  - Security boundaries are enforced in contracts and the vault, not in the model.
  - Substantive tests (fork, fuzz, invariant, sandbox) pass.
  - Limitations are disclosed candidly.
- **Against:**
  - Half the demo-scenario steps (7, 10, 11, 12, and the transcripts of 8) have no evidence in the repo, so the AI-specific claims are unverified: the crash scenes, the injection resistance, revocation, and the model's added value.
  - All quantitative research claims (backtest, liquidation dataset, judge-injection rates) cite files that are not published.
  - Several text-versus-code mismatches (items 3–5, 7–10 above).
  - Team section empty.
- The submission is dated 5 Nov against a 14 Nov deadline. Much of the missing evidence may be planned; the score should move up materially if it lands.

## Questions for the human judges
1. Will `evidence/scenes/`, `inject.txt`, `revoke.txt`, `attacks.txt` and `transcripts/` be published before the deadline? Without them the AI-agent claims rest on code reading only.
2. Will `research_notes/` and `sim/` be made public? If not, should the backtest and incident-replay numbers be treated as unsupported?
3. Is the retraction rule implemented elsewhere, or should that sentence go?
4. The agent's registration expires 12 Nov 05:41 UTC, before the 14 Nov deadline. After that, step 1 will still list the key, but the vault will refuse it. Will the agent re-register?
5. On testnet both "independent" text sources besides Base status are team-published. Is that acceptable for judging the agreement rule, and should the README say so?
6. The executor was deployed from source equal to HEAD, and the vault from `975b98d`. Should judges expect a single tagged commit for the submission?
7. Does anyone have an independent machine to rerun `agent/build.sh`? My run reused the team's buildx builder on the team's machine.

## Screening conduct and side effects
- **Nothing sent or signed.** I used only view calls and HTTP GETs: NEAR RPC, Base RPC, the agent's public evidence endpoint and the ghcr manifest.
- **Clone unchanged.** `git status --short --ignored` is empty at 4fbcf01.
- **Scratch work outside the clone,** under the session scratchpad: a clean checkout of the vault's build commit (nesting it inside the clone broke cargo-near), a HEAD checkout with forge-std for the tests, working directories and logs.
- **Local Docker state added:** images `screening-agent:repro-1/2`, `prime-dstack-mr:0.5.12`, and the pulled build images. The existing `cmdk-repro` buildx builder was reused.
- **Unintended:** the near-cli config at `~/Library/Application Support/near-cli/config.toml` was migrated automatically by the tools themselves. cargo-near printed "Migrating config.toml from V1…v4" and `near` later printed "V4 to V5". I did not edit that file deliberately.
- **Not run:** `attack-corridor.sh`, even on a fork, because it signs transactions.
