# AI screening rehearsal 3, Claude Code (6 Nov 2026, evening)

Run per DEPLOY.md step 11 on a fresh clone of commit 88aa6b9 (after the Phala KMS incident redeploy, the research data release and the exit scene on v4), from the same Mac as rehearsals 1 and 2 (near-cli-rs 0.30.1, cargo-near 0.22.0, Foundry 1.8.6, Docker Desktop 27.4.0). Tool: a Claude Code subagent (Opus) given the Q-017 prompt of `2026-11-05-prompt.md` with the tool list extended (cargo, cargo-near, rustup; Foundry in ~/.foundry/bin; Docker Desktop running). Docker's disk had 254 MB free during the run, which blocked the agent-image rebuild and step 2's script as written; the operator freed 17 GB afterwards. Started 17:04:18 UTC, finished 17:21:27 UTC. The report below is the agent's final answer, unedited.

---

# Screening report: cmdk (cmdkfi/app @ 88aa6b9)

- Start (UTC): 2026-11-06T17:04:18Z
- Finish (UTC): 2026-11-06T17:21:27Z
- Role: screening, not deciding (rules Q-017). This report covers reproducibility, feature checks, structure and a preliminary score. Humans decide.
- Mode: read-only. I used view calls, RPC reads, `cast call`, `cast receipt` and `cast run` (a local replay), and local builds and tests in `/tmp/screen3-build`. I sent no transactions and signed nothing. The checkout was clean at the end (`git status` shows 0 paths).
- Environment: macOS arm64. near-cli-rs 0.30.1, cast/forge 1.8.6, cargo-near 0.22.0, Docker 27.4.0. Docker's VM disk had only 254 MB free (99% used); this matters for steps 2 and 3, see below.

## Summary

1. Steps 1–6 of "Verify it" can be run and checked. Every live reading matched the README: one function-call key limited to `reduce`/`migrate`/`exit` and no full-access key; the Global Contract hash; the corridors; `isAuthorized`; the market config and pool; the LTV with an executor `ltvOf` MATCH; and all six TDX measurements, ending in `ALL MATCH`.
2. Reproducibility is good. In the pinned image, the vault wasm rebuilt bit for bit (`def9671e…` = on-chain `G1Q5ks…`). The live executor's runtime bytecode equals a local `forge build` of `executor/src` byte for byte, with only the immutables differing. The agent image digest resolves in the registry to the `imageId` in `deployments/testnet.json`. The agent image itself was **not** rebuilt (Docker disk full).
3. Every Base transaction I checked from steps 7–12 is on-chain with the stated status. The 5 attack reverts replay with the named errors. The agent-broadcast `exit` (0x014aebcd…), `reduce`, `migrate` and `withdraw` are all confirmed. All transcripts I fetched or found archived verify with `agent/verify-transcript.sh`.
4. Test suites pass. Executor (Foundry): 64/64, including the invariant campaign. Vault: 15 unit and 8 sandbox tests, exactly as the README says. Agent: 61 passed, 1 ignored.
5. The discrepancies are minor. The main one: the README's opening section says `exit` has "no live transaction in the evidence", while step 10 and `evidence/scenes/exit-v4.md` document a live exit, which I confirmed on-chain. Also, the "Verify" URLs in `evidence/inject.txt` are stale after the CVM was replaced, and one wording ("within a unit") is slightly loose.

## Reproducibility

| Item | What I did | Result |
|---|---|---|
| Vault wasm (README step 2) | (a) Ran `./scripts/reproduce-hash.sh --reproducible --vault v1.factory…` exactly as written. I used a clean clone of `975b98d`, the commit named in the vault's NEP-330 `link`, in `/tmp` outside any git repository. | **COULD NOT RUN (environment):** the container failed with `No space left on device (os error 28)` while unpacking `ring`; Docker's VM disk had 254 MB free. I did not prune the machine's Docker data, which belongs to the user. |
| | (b) Ran the same build by hand: the same image pinned by digest (`sourcescan/cargo-near:0.21.1-rust-1.96.0@sha256:ccb22bb4…`), the same command (`cargo near build non-reproducible-wasm --locked`), the same NEP-330 env vars and the same mount path. The only change: the cargo registry was bind-mounted to the host so it would fit. | **MATCH.** The wasm is 783,124 bytes; sha256 hex `def9671e099fe325a7ac8c43831de0334a3dc75221f6c302b17a893e0377e1ba`; base58 `G1Q5ksFhGbgPTN4A2DuvgraYmsbRpfwb78B547gAKXWq`. This equals the account summary's hash, `view_account.global_contract_hash`, `deployments/testnet.json` and the factory's `get_config.code_hash`. v1–v4 all use this hash. The build took about 2.5 minutes. |
| Factory | Read the on-chain code with RPC `view_code`. | 152,851 bytes, sha256 `5315458701e4…c863`; both match the README. No access keys ("Account is locked"). `get_config` returns the vault hash and `count: 4`. I did not rebuild it: the README says that build was not done in the pinned image. |
| Executor (not a README step) | `forge build` of `executor/` at 88aa6b9 with solc 0.8.28 and via-ir, then compared with `cast code 0xA9bb…D18b`. | **MATCH** with the immutables masked. Both are 16,459 bytes and the CBOR metadata is equal. The immutables are `MORPHO` = 0xBBBB…FFCb and `COOLDOWN` = 0x3c (60 s). |
| Agent TDX measurements (README step 6) | `./scripts/p3_reproduce_measurements.sh /tmp/screen3-build/p3 evidence/boot-1793948092-14fda74d dstack-0.5.12 1 2G` | **PASS:** `os_image_hash MATCH`; MRTD, RTMR0–2 and RTMR3 each MATCH; `ALL MATCH`. Caveat: the script reuses a local Docker image `prime-dstack-mr:0.5.12` when one exists, and one was already on this machine, so I did not rebuild the `dstack-mr` tool from the dstack source. |
| Agent image | `agent/build.sh` builds the image twice with no cache and needs "about 4 GB free in Docker's disk". | **COULD NOT RUN (environment):** 254 MB free in Docker's VM. What I did check: `docker buildx imagetools inspect ghcr.io/cmdkfi/agent@sha256:479f98f3…` returns config digest `sha256:01027505…6eb3`, which equals `agent.imageId`. The live `app-compose.json` pins that same digest. `git diff fd0272f..88aa6b9` of the agent's image inputs (`Dockerfile`, `Cargo.*`, `src/`) is empty, so the source at HEAD is the source of the deployed image. |
| Tests | `forge test` (executor); the vault's three wasms then `cargo test`; `cargo test --locked` (agent). | Executor: 64 passed, 0 failed, with invariants `invariant_callSummary`, `invariant_executorHoldsNothing` and `invariant_noViolation` (64 runs, 1,600 calls). Vault: 15 unit + 8 sandbox passed. Agent: 61 passed, 1 ignored. |

## Verify-it results

Notation: V = `v1.factory.prime-measure-1792736012.testnet`, EX = `0xA9bbabE6d75C5BA96CA095586284774c9D09D18b`, BA = `0xb3ccd0ad7e5fca9d4c18cf59d19fab4703eee6fb`, RPC = `https://base-sepolia-rpc.publicnode.com`.

| Step | Command | Expected | Got | Verdict |
|---|---|---|---|---|
| 1a | `near account list-keys V network-config testnet now` | Exactly one function-call key for `reduce`, `migrate`, `exit`; no full-access key | One key, `ed25519:2QwVDKF…SHio`, `only do ["reduce","migrate","exit"] … allowance of 2 NEAR` | PASS |
| 1b | `near contract call-function as-read-only V get_state …` | `owner` is the wallet (lower case); `base_address` is derived | `owner 0x0b7ed66f…0d25` (= `testWallet`), `base_address 0xb3ccd0ad…e6fb` (= testnet.json) | PASS |
| 1c | `get_state` of v2, v3, v4 | Vaults beyond v1 and v2 exist; v3 was created from the app | v2 and v4: owner is the test wallet; v3: owner `0x69a7c629…f714`; v5 does not exist; factory `count: 4` | PASS ("the team's own test vault" for v3 cannot be verified on-chain) |
| 2a | `near account view-account-summary V …` | `Global Contract (by Hash …)` with the published hash | `def9671e…e1ba`, `0 full access keys and 1 function-call-only` | PASS |
| 2b | `./scripts/reproduce-hash.sh --reproducible --vault V` | `result MATCH` | Script exit 1: Docker ENOSPC. The equivalent manual build in the pinned image gave a MATCH (see Reproducibility) | COULD NOT RUN as written (environment); content PASS by the equivalent build |
| 3a | `get_state` → `markets`, `cooldown_secs` | id, executor, `slice_max`, `corridor_set`; 120 s | 3 markets (live, backup, demo), executor EX, `slice_max 1000000000`, `corridor_set true`; `cooldown_secs 120` | PASS |
| 3b | `cast call EX "corridors(…)" BA <live>` | `5e17, 7e17, 3e17, 2e17, true, true, true` | Exactly that (and the same for backup and demo) | PASS |
| 3c | `MAX_MOVES` is a constant | — | `executor/src/Executor.sol:51` `MAX_MOVES = 2` | PASS |
| 4 | `./scripts/ltv.sh V --market backup` | Two columns; ends `cross-check MATCH …` | Collateral 0.008335601888990126 WETH; debt 7.729880 USDC; LTV 41.5980% (at `lastUpdate`) / 41.5989% (accrued); `cross-check MATCH`. Without `--market`: `(no position on this market)` | PASS |
| 5a | `cast call Morpho "isAuthorized(address,address)(bool)" BA EX` | `true` | `true` | PASS |
| 5b | `cast call EX "config(bytes32)(…)" <live>` | Pool `0x5718…156c`, feeds, `[1200, 86400]`; backup uses the same pool | Matches; backup has its own oracle `0x20Ff…d538` and the same pool. The pool's token0/1 are USDC/WETH, fee 100; Uniswap factory `getPool(USDC,WETH,100)` = the same pool, so it is the public 0.01% pool | PASS |
| 6a | `get_state` → `approved`, `agent` | The approved measurement; the key and `expires_at` | All six values equal `deployments/testnet.json`; agent key `2QwVDKF…`, `expires_at 1794553767` | PASS |
| 6b | The `register_agent` transaction | On-chain quote verification | `CjN1nLHF…FNU5` (from `evidence/boot-…14fda74d/registration-v1.json`): `register_agent` on V, `SuccessValue`, log `agent registered: ed25519:2QwVDKF… until 1794553767`, about 113 Tgas in the vault receipt. The check itself is in `vault/src/attest.rs:107-150` | PASS |
| 6c | `./scripts/p3_reproduce_measurements.sh <work> evidence/boot-1793948092-14fda74d dstack-0.5.12 1 2G` | `os_image_hash MATCH`, each MATCH, `ALL MATCH` | Exactly that | PASS (caveat: the `dstack-mr` Docker image was already cached) |
| 6d | `sha256sum evidence/<boot>/app-compose.json` | = `compose-hash` = MRCONFIGID after `01` | `216124c1…bdb2`, equal to the event-log `compose-hash` and to `mr_config_id 01216124c1…` | PASS |
| 6e | Vault `cargo test` after building the 3 wasms | 15 unit, 8 sandbox | 15 passed; 8 passed | PASS |
| 7 | `./scripts/attack-corridor.sh --land` | 5 reverts, `result: 5 PASS, 0 FAIL` | Not run: it sends transactions, which my rules forbid. Read `evidence/attacks.txt` (5 PASS lines in the stated order); each of the 5 transactions is on-chain with `status 0x0`; `cast run` replays revert with `Expired()`, `MoveLimit()`, `UnapprovedMarket()`, `BelowFloor()`, `SellBudget()`; the selectors match `cast sig` | PASS (evidence); live run: COULD NOT RUN (sends txs) |
| 8a | `curl -s $E/transcripts` | Index with `name`, `bytes`, `sha256`, `boot`, `vault`, `twin`, `loop` | 101 entries (v1: 55, v4: 46), all from boot `14fda74d`, with the stated fields | PASS |
| 8b | Fetch transcripts and run `./agent/verify-transcript.sh` | `PASS` lines, then `ALL PASS <name>` | v1-0001, v1-0055, v4-0045, v4-0046: ALL PASS, and each SHA-256 equals its index entry. Offline testdata transcript: ALL PASS | PASS |
| 8c | Archived transcripts of the first CVM in `evidence/transcripts/` | Verify the same way; three `NO-CALL judge outcome: error` from 4 Nov (HTTP 402) | 75 files, all SHA-256s match `index.json`; 32 ALL PASS, 40 `NO-CALL judge outcome:` (empty), 3 `NO-CALL judge outcome: error` (boot b52d4abb, 2026-11-04 13:56–14:00, body contains `402 Payment Required`) | PASS |
| 9 | `evidence/protect-live.txt` | Owner lowers the act line; agent `reduce`; LTV back at target | `set_corridor` 40/45; `Reduced` event with protective=true, LTV 0.4997 → 0.4002; tx `0xde730226…` status 1, from BA to EX | PASS |
| 10a | Price scenes (`gradual-crash`, `gap`, `whipsaw`) | Changes in net value: gradual −1.76/−1.76/−2.11; gap −2.23 ×3; whipsaw −0.59/−0.59/0.00 | Recomputed from the md tables (start = 0.002 × start price + start cash − debt): gradual −1.760/−1.760/−2.114; gap −2.231 ×3; whipsaw −0.586/−0.586/0.000. The gap scene's agent `reduce` `0xbf743bbf…` reverted on-chain (`status false`); it was mined 19 s after the liquidation, as stated | PASS (the keeper's start cash is assumed equal to its borrow; the md lists only ai and unprotected) |
| 10b | Venue stress | Utilization 0.59 → 0.99, APR 0.55% → 2.78%, `leave_market`, migrate 3.5 min after the borrow | md: 59.46% → 99.23%, 0.55% → 2.78%. Borrow `0x9eefef8e…` at 19:31:42 UTC, migrate `0xd597c432…` at 19:35:08 UTC (3 min 26 s), both status 1. The judge transcript says "elevated, leave_market, live" | PASS |
| 10c | Exit on v4 | Agent's `exit` sold 0.002006 WETH, repaid 4.442487 USDC, about 7 min after the items, Base `0x014aebcd…` broadcast by the agent | Receipt status 1, block 55303401, 16:29:09 UTC, gas 329,202, from v4's Base address to EX, input decodes to `exit(bytes32,uint256)` on the live market. The served transcript v4-0046 equals `evidence/scenes/exit-v4-transcript-0046.json` byte for byte and records `proposal.base.hash` = that transaction. Run 2's hand broadcast `0xd90f46ed…`: status 1 | PASS |
| 11 | `evidence/inject.txt` | Hostile memo; the proposal stays inside 3 methods | The model answered `hold`. The transcript `boot-1793877578-a405e917-v1-0002.json` in `evidence/transcripts/` has SHA-256 `748c8c59…2a15` as stated and gives ALL PASS. **However**, the file's own "Verify" commands fail: `https://relayer.cmdk.fi/agent/transcripts/boot-1793877578-…-0002.json` → HTTP 404, and the old enclave URL does not respond | PASS on content; the Verify commands in `inject.txt` FAIL (stale URLs) |
| 12 | `evidence/revoke.txt` and its Verify commands | v2 has 0 keys; agent and approved are null; `withdraw` moves exactly 0.002 WETH to the owner | `list-keys` v2: empty; `get_state`: `agent null`, `approved null`. Withdraw `0x431aefa8…` status 1, one WETH Transfer from `0xfc85…67cf` to `0x0b7e…0d25` of `0x71afd498d0000` = 2,000,000,000,000,000 wei. Close `0x8c8949e3…` status 1 | PASS |

## Feature verification

| Claim | Evidence | Verdict |
|---|---|---|
| The agent key is limited by the protocol to `reduce`, `migrate`, `exit` | `vault/src/lib.rs:46` `AGENT_METHODS = "reduce,migrate,exit"`, `:498` `add_access_key_allowance(…, me, AGENT_METHODS)`; live `list-keys` on v1 and v4 | Verified |
| Key valid 7 days; the agent re-registers 24 h before expiry | `lib.rs:43` `REGISTRATION_SECS = 7*24*3600`, checked at `:631`; `agent/src/logic.rs:803` `REREGISTER_BEFORE_SECS = 86_400`. The expiry is enforced by the vault, not as a protocol key expiry | Verified |
| On-chain quote verification: report_data = the agent key, debug off, TCB UpToDate, the 6 approved values | `vault/src/attest.rs:90-150` (report_data, `UpToDate`, debug bit, MRTD/RTMR0-2, RTMR3 replay, compose and OS hashes); unit tests in attest.rs pass | Verified |
| Factory has no keys and fixes the code hash; vaults are created keyless with a Global Contract by hash, in one batch | `vault/factory/src/lib.rs:152` `create_account().transfer().use_global_contract(code_hash)` + `init`; no `add_key`; the factory has no setter (pub fns: `init`, `create_vault`, `on_derived_key`, `get_config`, `vault_id`); live: the factory is locked and the vaults have 0 full-access keys | Verified. The hash is held in the factory's state, not compiled into its code; this is immutable because the factory has no keys and no setter, as step 2 of the README says |
| Owner messages: `ecrecover`, nonce, expiry | `vault/src/owner.rs:37-80` (`env::ecrecover`, nonce strictly increasing, expiry and maximum expiry ahead); tests pass | Verified |
| Slice maximum and cooldown on NEAR | `lib.rs:439` `amount ≤ slice_max`; `:623-633` `agent_check` (predecessor = self, signer key = agent, unexpired, cooldown) | Verified |
| 10-minute deadline on every signed executor call | `lib.rs:790` deadline in (now, now + `DEADLINE_SECS`]; executor modifier `before(deadline)` at `Executor.sol:155-157` on every state-changing method | Verified |
| Executor has no admin and is immutable | `Executor.sol`: only `MORPHO`/`COOLDOWN` immutables; config is set in the constructor; no owner, upgrade path or delegatecall; deployed bytecode = source | Verified |
| Recipients pinned to the caller | All `withdrawCollateral` and `borrow` calls name `msg.sender`/`user` or the executor/pool inside a callback (`Executor.sol:236, 376, 413-429, 528`); invariant `executorHoldsNothing` passes | Verified (by reading the code and the tests; no formal proof) |
| Swaps at no worse than the oracle minus 1% | `CorridorMath.sol:11` `SLIPPAGE_BPS = 100`; `Slippage`/`PartialFill` checks at `Executor.sol:428, 552, 571-573` | Verified |
| Floor, daily sell budget, protective session, 2 moves a day, exit switch, 16 sale slots | `Executor.sol:245-300` (session, floor, budget), `:51-52` `MAX_MOVES = 2`, `SALE_SLOTS = 16`, `:362` `ExitDisabled`, `:309` `MigrateDisabled`; on-chain attack reverts | Verified |
| A stale feed refuses open but not selling or exit | `_requireFresh` only in `open` (`:179`), `openFromStables` (`:199`) and `migrate` destination (`:316`); not in `reduce`/`exit` | Verified |
| The Morpho callback accepts calls only from Morpho during its own call | `Executor.sol:386-387, 404-405` (`msg.sender == MORPHO && _locked`) | Verified |
| An exit closes the market for the agent until the owner opens it again | `:377` `closedForAgent = true`; `:533` reset in the owner's `open`; this is why exit-v4 run 3 could exit again after the owner reopened | Verified |
| Judge: four options; called on a screen, a new feed item and every 6 h; the text path needs two feed ids | `agent/src/judge.rs:30`; `main.rs:59` `DIGEST_SECS = 6*3600`, `:61` feed poll 60 s; `logic.rs:400` `want_judge`, `:338` `text_corroboration`, utilization screen 0.95 (`:10`) | Verified |
| `leave_market` → migrate if a destination qualifies, else exit | `logic.rs:500-542`; the live exit on v4 (no destination) | Verified |
| The agent key is generated in the enclave; no KMS derivations | `agent/src/main.rs:1090` `SigningKey::generate(OsRng)`; `tee.rs:99-102`: only `info()` and `get_quote()` | Verified |
| Chain Signatures threshold 11 of 16 on testnet | `v1.signer-prod.testnet` `state`: `threshold 11`, 16 participants | Verified |
| Model calls are signed by the model enclave and checked against a fresh-nonce attestation | Transcript fields `attestation.nonce`, `report_data_binds_address_and_nonce: true`, `signature.recovered == attested`; the verifier passes. As the README says, the model's TDX quote itself is not re-verified from a transcript | Verified to the extent stated |
| Live evidence for `reduce`, `migrate`, `exit`, revoke, close, withdraw | Base receipts above | Verified (`open_from_stables`: tests only, as stated) |
| Research numbers (backtest, 20,479 liquidations, injection replay 21/30 → 2/30, incident lead 136 vs 25 min) | Present in `research/liquidation-dataset/README.md:795`, `research/judge-evidence/README.md:341`, `research/judge-evidence/incident-replay-results.md:30,96`, `research/liquidation-backtest/backtest.md:424` | Traced to the research files; not re-run (the README calls them simulations) |
| Any-chain and Confidential Intents deposits, privacy of the funding route, `relever` | The README labels them as production release, not demonstrated | Not testable on testnet (correctly labelled) |

## Discrepancies

1. **README §"What the hackathon build runs" (line 9)** says "the agent's `exit` and the owner's `open_from_stables` have executor and vault tests but no live transaction in the evidence". Step 10 and `evidence/scenes/exit-v4.md` document a live, agent-broadcast `exit` on 6 Nov (Base `0x014aebcd…`, which I confirmed), plus an earlier hand-broadcast exit (`0xd90f46ed…`). The list of live evidence there ("steps 6 to 12") also leaves out `exit`. The line is out of date.
2. **`evidence/inject.txt` "Verify" section**: `curl -s -O https://relayer.cmdk.fi/agent/transcripts/boot-1793877578-a405e917-v1-0002.json` now returns HTTP 404 (a 10-byte `not found` body), and the alternative enclave URL `29fc9b08…dstack-pha-prod9…` does not respond, because that CVM was replaced on 6 Nov. The file exists, with the right hash, in `evidence/transcripts/`, but the commands in `inject.txt` no longer run as written.
3. **README step 8** says the archive of the first CVM's transcripts includes "the scenes of step 10". The v4 exit scene of 6 Nov is from the new CVM (boot `14fda74d`). Its transcripts are in `evidence/scenes/exit-v4-transcript-*.json` and on the live endpoint, not in `evidence/transcripts/`. This is minor.
4. **README line 47** says "In the three price scenes of step 10 the two ended within a unit of each other". In `gradual-crash` the AI agent's and the keeper's debt differ by 4 loan-token units (0.575224 vs 0.575228) and their net value by 5 µUSD. Whipsaw is 1 unit and gap is identical. This is substantively true but not literally "within a unit" in every scene.
5. **Step 2 as written** needs more free Docker disk than this machine had. The script's error message did not suggest that the failure was environmental, and the README states no disk requirement for step 2 (`agent/build.sh` does state one, 4 GB). I suggest the README add one.
6. **Step 6 reproduction** reuses a cached `prime-dstack-mr:0.5.12` image when one is present (`p3_reproduce_measurements.sh` lines 29-33). A judge on a machine where it exists does not independently rebuild `dstack-mr`. This is a methodological note, not an error.
7. Not discrepancies, just noted: the attack run (step 7) used a sell budget of 0.05, not the default 0.20, and `attacks.txt` discloses this. The venue-stress, crash and text-path scenes are produced by the team itself: its own borrow, its own mock oracle, and two of the three feeds. The README labels all of these.

Numbers I could not confirm: that v3 is the team's own vault (it has a different owner address); Phala's e-mail and the scope of the KMS incident; the user and market figures in the business section (Coinbase, DeFi Saver and others: these are outside the repository); and the factory rebuild on Rust 1.95.0 (I did not run it).

## Preliminary score: 8 / 10

Reasons for:
- An unusually complete, executable verification path. Every view-call step I could run matched its expected text exactly.
- Strong reproducibility, independently confirmed. The vault wasm is bit-identical in the pinned image, the deployed executor bytecode equals the source, and the TDX measurements (MRTD, RTMR0–3, compose hash, OS image hash) all match.
- The security claims are enforced in code I could read and tested by passing suites (fork, fuzz and invariant tests for the executor; sandbox tests for the vault). The attacks against the corridor are shown by reverts that are on-chain and can be replayed.
- Live evidence for the agent's actions (`reduce`, `migrate`, `exit`), revocation and the owner's withdraw, each tied to on-chain receipts and transcripts that verify.
- Careful scoping: testnet limits, production-release items, simulated research and team-controlled scenes are labelled.

Reasons against:
- Some text is stale or contradictory (discrepancies 1–3), in a README that asks judges to check every sentence.
- I could not rebuild the agent image here, and the step 2 script failed in my environment. Both are environment limits, but a judge on a typical laptop may hit the same wall.
- Much of the case for the AI and the corridor (backtests, incident replay) is simulation. The live scenes run on team-controlled oracles and feeds and team-induced stress. The contracts are unaudited.

A low score from this screen cannot reject an entry. This score is a starting point for the human judges.

## Questions for the human judges

1. Does the team intend to update README line 9 now that a live agent `exit` is in the evidence? Should `open_from_stables` be the only action without a live transaction?
2. Should `evidence/inject.txt` point at `evidence/transcripts/` (or a URL that still serves the old boot's files) so its Verify commands work again?
3. How much weight should the team-controlled scenes carry (mock oracle, self-induced utilization spike, our-own feeds for the text path) compared with the simulated research results?
4. Is one shared enclave serving all vaults, with the operator name in the compose hash, an acceptable trust model for the AI Security track, given the TEE.fail caveat the README itself cites?
5. Would a judge with more Docker disk please confirm `agent/build.sh` → `REPRODUCIBLE image sha256:01027505…`, and step 2's script reaching `result MATCH` unmodified?
6. Is v3 (owner `0x69a7c629…f714`) a team vault or a third-party user? The README says "the team's own test vault".
