# AI screening rehearsal 7, Codex in a container (11 Nov 2026)

The same container, tool and generic prompt as rehearsal 6 (`2026-11-10-prompt-6.md`; Linux, Node 22, Rust stable with cargo-near 0.22.0, near-cli-rs 0.30.1, Foundry 1.9.0; no Docker, no browser, no keys; codex-cli 0.162.1, gpt-6-astra, reasoning high), on a fresh clone of commit 625e54a: after the README changes of 10 Nov (hash and source check without Docker, the table of what the model adds, the sandbox pointer, step 7 on a fork, the published judge-evidence runs). 06:27 to 06:44 UTC. Rehearsal 6 scored 8. Its one confirmed numerical finding, the gradual-crash liquidation at −42.9% rather than −41.8%, is right and fixed in the next commit. The report below is the tool's final answer, unedited except temporary paths shortened to `<tmp>`.

---

## Summary

1. **The submission has a working testnet deployment with substantial independently verifiable evidence.**
2. Live NEAR permissions, vault hash, Base corridors, authorization, and position arithmetic matched the README.
3. The executor rebuilt to matching deployed bytecode; 64 executor tests and 15 vault unit tests passed.
4. Docker reproduction and browser interaction remained blocked; one numerical discrepancy was confirmed.
5. **Preliminary score: 8/10.** This is screening evidence for humans, not an acceptance or rejection decision.

Screened on **11 November 2026, approximately 06:27–06:40 UTC**, at repository commit `625e54aa8dc1b9f4d5ea206ba9373a5a7748de4f`.

**Execution-scope disclosure:** The app unit suite generated an offline fixture-message signature using a fixed public test key on two runs. This violated your “sign nothing” instruction; it was my screening error, not a submission defect. No transactions were broadcast, no on-chain accounts were created, and no funds were spent. Tracked repository files remained unchanged, with clean final Git status. Forge automatically initialized its dependency submodule; I restored its initial uninitialized state afterward.

## Reproducibility

| Component | Result | Evidence and limits |
|---|---|---|
| Deployed NEAR vault | **PASS: published bytes and hash** | Account summary and independently downloaded WASM both yielded `def9671e099fe325a7ac8c43831de0334a3dc75221f6c302b17a893e0377e1ba`. |
| Vault source provenance | **PASS: metadata consistency** | `contract_source_metadata` identifies commit `975b98d454e7080495230b3aff73296a92e714c2` and the pinned build image. Vault source, manifest and lockfile have no differences from that commit. Metadata alone does not establish a reproducible build. |
| Pinned vault rebuild | **COULD NOT RUN** | `reproduce-hash.sh --reproducible` stopped because Docker is unavailable. |
| Native NEAR builds | **PASS: compilation** | The README’s chained command built mock signer, vault and factory. Host vault: **773,607 bytes**, SHA-256 `65d34ff0a89077b52a9135914683ec9a675d7f6fc76deba44d6db610c7dc7204`. This differs from deployment, as expected for the explicitly non-reproducible host build. |
| Factory | **PASS: deployed identity and state** | Downloaded WASM: **152,851 bytes**, hash `5315458701e4f52c9c033d05d492ec75d18076a128a0261f153434670971c863`. No access keys; configuration fixes the published vault hash. Source unchanged since `52e0446`. Host rebuild succeeded but produced different bytes. |
| Executor | **PASS: independent bytecode match** | Solc 0.8.28 build produced **16,459-byte runtime code**, matching deployment including metadata after filling compiler-designated immutable slots with the observed Morpho address and 60-second cooldown. All three [Blockscout-verified source files](https://base-sepolia.blockscout.com/address/0xA9bbabE6d75C5BA96CA095586284774c9D09D18b?tab=contract) exactly match this checkout. |
| Agent image | **PASS: published artifact identity; rebuild unavailable** | Public registry manifest matched `sha256:e72564a8…94d1`; its configuration matched image ID `sha256:d3b51d67…cfcb`. The downloaded binary layer contained agent SHA-256 `0a8f47c3eea1bfed9f02f84f4dd32bf98f24d0bf4928392801300bcf51def68d`, matching the documentation. `./agent/build.sh` could not run without Docker. |
| Enclave measurements | **Partial reproduction** | Downloaded dstack OS checksum manifest matched `os_image_hash`. All four archived boots passed independent compose/event-log/MRCONFIGID checks and RTMR3 replay with recomputed event digests. Rebuilding MRTD and RTMR0–2 required Docker. |
| Web app | **PASS: build and deployed artifacts** | Public repository commit `c1d28f5c5b539e17a5ac52730335dc4f3d32809c`: typecheck, build, bundle-boundary check and **162 unit tests passed** after supplying the documented deployment manifest. Rebuilt main JavaScript exactly matched [the live app](https://testnet.cmdk.fi); generated configuration matched live configuration. |
| Landing page | **PASS** | Public repository commit `68e91ec7931077babe97597939d96c5f5fe1cc7a`: build and `pnpm check` passed. Rebuilt HTML exactly matched [cmdk.fi](https://cmdk.fi). |
| Research results | **Partially corroborated** | Published CSV liquidation counts sum to **20,479**; cohort tables contain **1,890/1,895** and **5,009/5,011**. I did not rerun the full historical data collection, grid search or model-inference experiments. |

Environment: Rust **1.99.1**, cargo-near **0.22.0**, near-cli-rs **0.30.1**, Node **22.23.4**, Foundry **1.9.0**. Foundry was installed but needed its binary directory added to `PATH`.

Initial parallel app/landing dependency installs exited **137**; the environment recorded two memory kills. Both succeeded on sequential retries with reduced concurrency. These were environment limitations.

## Feature verification table

| README claim | Evidence | Verdict |
|---|---|---|
| Immutable, owner-controlled NEAR vault and keyless factory | [Factory creation code](/work/cmdk/vault/factory/src/lib.rs:152) uses a global contract hash. Live factory has zero keys; vault has zero full-access keys. Owner and Base address match the manifest. | **Verified for inspected deployment.** |
| Agent key limited to `reduce`, `migrate`, `exit` | Live key lists exactly these methods and the vault receiver. [Registration](/work/cmdk/vault/src/lib.rs:471) installs the restricted key; [agent entry points](/work/cmdk/vault/src/lib.rs:435) have no recipient argument. | **Verified.** |
| Registration requires approved TDX measurements | [Verifier](/work/cmdk/vault/src/attest.rs:106) checks quote validity, `UpToDate`, debug flag, key binding, measurements and event replay. Registration transaction `CPEBXcPD…GpKgb` succeeded and logged the currently installed key. | **Code and live registration corroborate it.** Full independent OS-measurement rebuild unavailable. |
| Seven-day registration and owner revocation | Registration constant and key handling in [vault code](/work/cmdk/vault/src/lib.rs:43). Live v1 expiry: **2026-11-16 22:52:28 UTC**. Live v2 has no keys, `agent: null`, `approved: null`. | **Verified current state and implementation.** Automatic renewal was not observed over seven days. |
| Existing EVM wallet authorizes owner actions | [Owner verification](/work/cmdk/vault/src/owner.rs:37) recovers ERC-191 signatures and checks message scope, nonce and expiry. Successful recorded close/withdraw receipts exist. | **Verified code and historical execution.** No new wallet flow performed. |
| Corridor, budget, cooldown and move limits | [Executor rules](/work/cmdk/executor/src/Executor.sol:245), live corridor/config reads, 64 passing tests, and five historical attack replays described below. | **Verified within tested cases.** |
| Agent cannot choose an external token recipient | [Executor](/work/cmdk/executor/src/Executor.sol:245) operates on `msg.sender`; repayment/swap routes are constrained. [Owner withdrawal](/work/cmdk/vault/src/lib.rs:360) separately requires an authenticated owner message. | **Supported by code, tests and sampled receipts; not a security audit.** |
| Protective reduce restores target LTV | Live receipt `0xde730226…bd6e3` succeeded. Recorded event changes LTV from **49.96745% to 40.01515%** after the owner selected a 40% target. | **Verified historical demonstration.** |
| Migration preserves collateral and approximately debt | Receipt `0xd597c432…542dd` succeeded; migration transcript verifies. Scene records **0 collateral difference and 0 debt-unit difference**, with interest aligned to the action block. | **Verified demonstration.** Code permits the disclosed two-unit rounding allowance. |
| Text-driven exit when no approved destination exists | Receipt `0x014aebcd…9a556` succeeded; archived transcript passes all signature checks. Event repaid **4.442487 USDC** and returned remaining collateral to the vault address. | **Verified demonstration using team-authored feeds.** |
| Stablecoin-funded opening | `openFromStables` implementation and passing executor tests, including stale-feed/slippage cases. | **Implemented and tested; no live demonstration**, as disclosed. |
| Verified confidential model responses; deterministic fallback | [Judge verification](/work/cmdk/agent/src/judge.rs:286), [decision logic](/work/cmdk/agent/src/logic.rs:417), and transcript results below. Failed model results have `accepted: false`. | **Supported.** Offline transcript checking trusts the agent’s recorded model-attestation verdict. |
| Model adds value beyond numeric screens | Signed migration and exit examples exist. Recorded price-scene outcomes reproduce the README’s rounded comparisons. Historical incident report says equal detection counts with earlier model exits in selected cases. | **Demonstrated in selected scenes; broad incremental effectiveness not independently reproduced.** |
| Sandbox offers simulated onboarding/crash screens | Sandbox implementation and tests exist; production bundle contains it and labels simulation. Live app responds HTTP 200. | **Code/build verified; interactive verification blocked** by missing Chromium libraries. |
| Private cross-chain funding, BTC, buy-back, additional venues | README explicitly labels these production-release or roadmap items. Current deployment is WETH/Morpho on Base Sepolia. | **Not delivered in this build; clearly disclosed.** |
| Chain Signatures uses 11 of 16 operators | Read `state` from both `v1.signer-prod.testnet` and `v1.signer`: signing threshold **11**, participants **16**. | **Verified at screening time.** |

## Verify-it results table

The commands below use these substitutions for the README placeholders:

```sh
V=v1.factory.prime-measure-1792736012.testnet
F=factory.prime-measure-1792736012.testnet
X=0xA9bbabE6d75C5BA96CA095586284774c9D09D18b
A=0xb3ccd0ad7e5fca9d4c18cf59d19fab4703eee6fb
L=0xa25ac5171b7e500b82653d3750b91bb44e2409c32ca3c9e80b0db077e8a4535c
B=0x99d43c0f2a0eb6172774598a4e34e7eee09caa502692c2b2df0f40e1e3c5ea75
RPC=https://base-sepolia-rpc.publicnode.com
E=https://49ecae9fcc4a6970956d8c987386315297922a69-8080.dstack-pha1-prod5.phala.network
BOOT=boot-1794264608-2286af92
```

Commands were executed with concrete substituted values. **PASS** for historical evidence means the evidence check passed, not that a new transaction was performed.

| Step | Command/check | Expected text or result | Obtained | Status |
|---|---|---|---|---|
| 1a | `near account list-keys $V network-config testnet now` | One function-call key; three named methods; no full-access keys | One key, `reduce`, `migrate`, `exit`, correct receiver, allowance 2 NEAR | **PASS** |
| 1b | `near contract call-function as-read-only $V get_state json-args '{}' network-config testnet now` | Lowercase owner and derived Base address | Owner `0x0b7ed66f46fe83252a208563a88201ba60490d25`; Base address equals `$A` | **PASS** |
| 2a | `near account view-account-summary $V network-config testnet now` | `Global Contract (by Hash: SHA-256 checksum hex)` and published hash | Exact heading and `def9671e…77e1ba`; observed at NEAR block **277178341** | **PASS** |
| 2b | README’s `curl … view_code … \| jq -r .result.code_base64 \| base64 -d \| sha256sum` pipeline | Same published hash | Exact full hash shown in Reproducibility | **PASS** |
| 2c | `near contract call-function as-read-only $V contract_source_metadata json-args '{}' network-config testnet now` | Source link contains `975b98d…` | Correct full commit, build image digest and `--locked` build command | **PASS** |
| 2d | `./scripts/reproduce-hash.sh --reproducible --vault $V` | Matching hex/base58 hashes; `result MATCH` | Exit 1: `a running Docker daemon (docker info failed)` | **COULD NOT RUN** |
| 2e | Factory `list-keys` and `get_config` views | No keys; fixed vault hash | Zero keys; correct base58 hash; factory count **11** | **PASS** |
| 3a | Reuse `get_state` | Markets, executor, slice maximum, corridor flag, 120-second cooldown | Three markets; correct executor; `slice_max: "1000000000"`; corridors set; cooldown **120** | **PASS** |
| 3b | `cast call $X "corridors(address,bytes32)(uint64,uint64,uint64,uint64,bool,bool,bool)" $A $L --rpc-url $RPC`; repeat with `$B` | `5e17`, `7e17`, `3e17`, `2e17`, `true`, `true`, `true` | Both calls returned exactly those seven values | **PASS** |
| 4a | `./scripts/ltv.sh $V --market backup` | Position arithmetic and `cross-check MATCH` | At Base block **57283343**: **0.008335601888990126 WETH**, stored debt **7.730581 USDC**, accrued debt **7.730735 USDC**; stored LTV **44.9270%**, accrued **44.9279%**; `MATCH` | **PASS** |
| 4b | `./scripts/ltv.sh $V` | `(no position on this market)` | Exact message for live market; zero collateral/debt | **PASS** |
| 5a | `cast call 0xBBBBBbbBBb9cC5e90e3b3Af64bdAF62C37EEFFCb "isAuthorized(address,address)(bool)" $A $X --rpc-url $RPC` | `true` | `true` | **PASS** |
| 5b | `cast call $X "config(bytes32)(((address,address,address,address,uint256),address,address[2],uint32[2]))" $L --rpc-url $RPC`; repeat with `$B` | Correct parameters, pool, feeds and maximum ages | Both use pool `0x571837…156c`; expected feeds; ages **1200/86400**; LLTV **86%** | **PASS** |
| 6a | `get_state`; read recorded registration transaction | Approved measurement, registered key, expiry; successful registration | Measurement matches manifest; successful `register_agent`; key matches live access key | **PASS** |
| 6b | `./scripts/p3_reproduce_measurements.sh <tmp>/measurements evidence/$BOOT dstack-0.5.12 1 2G` | OS hash and all registers `MATCH`, ending `ALL MATCH` | `os_image_hash MATCH`; then `docker: command not found`, exit 127 | **COULD NOT RUN** to completion |
| 6c | `sha256sum evidence/$BOOT/app-compose.json` | Compose hash matches event log and MRCONFIGID | `db4f5085…c1dfc4`; both comparisons match | **PASS** |
| 6d | In `vault/`: `(cd mock-signer && cargo near build non-reproducible-wasm) && cargo near build non-reproducible-wasm && (cd factory && cargo near build non-reproducible-wasm)` | Three WASMs available | All three built successfully | **PASS** |
| 6e | In `vault/`: `cargo test` | 15 unit and 8 sandbox tests | Full command withheld: sandbox tests create accounts, sign messages and submit local transactions. `cargo test --lib` separately passed **15/15** | **COULD NOT RUN** full suite under requested constraints |
| 7a | `./scripts/attack-corridor.sh --land 2>&1 \| tee evidence/attacks.txt` | Five named reverts; `result: 5 PASS, 0 FAIL` | Withheld: signs/sends transactions and overwrites repository evidence. Local-anvil alternative also signs/sends | **COULD NOT RUN** as written |
| 7b | Read `evidence/attacks.txt`; independently fetch its five receipts and replay their calldata using historical `eth_call` | `Expired`, `MoveLimit`, `UnapprovedMarket`, `BelowFloor`, `SellBudget` | All receipts status `0x0`; all five `eth_call` error selectors exactly match README | **PASS** |
| 8a | `curl -s $E/transcripts`; `curl -s $E/transcripts \| jq -r '.transcripts[].name'`; `curl -s $E/` | Transcript index, filenames and boot evidence | Index contained **747** transcripts; expected fields and evidence files present | **PASS** |
| 8b | For indexed files: `curl -s $E/transcripts/<name> -o <tmp>…`; `./agent/verify-transcript.sh <tmp>…` | `ALL PASS`, or documented no-call/rejection outcomes | All **747 hashes and sizes match**. **353 ALL PASS**, **392 NO-CALL**, **2 signature failures** explicitly rejected by agent | **PASS** for integrity and documented classification; two individual signature checks **FAIL** as disclosed |
| 8c | `./agent/verify-transcript.sh agent/testdata/transcripts/boot-1793051811-26cc5edf-0001.json` | `ALL PASS` | Seven checks passed; `ALL PASS` | **PASS** |
| 8d | Verify archived transcript index and files | Hashes and appropriate verifier results | **75/75 hashes match**; 32 signature passes, 43 no-call records | **PASS** |
| 9 | Read `evidence/protect-live.txt`; fetch protective-sale receipt | Owner lowers act line; agent reduces; LTV returns near target | Successful receipt and matching `Reduced` event | **PASS** |
| 10a | Read scene JSON/Markdown; recompute outcome differences; sample migration, exit and liquidation receipts | Gradual −$1.76/−$2.11; gap −$2.23; whipsaw −$0.59/$0.00; migration and exit | Rounded financial comparisons reproduce; sampled receipts succeed | **PASS** |
| 10b | Compare claimed gradual liquidation point with historical oracle | Liquidation at −41.8% | Actual liquidation block **54833693** has oracle **$1,333.10**, versus start **$2,334.929333**: **−42.9062%** | **FAIL** literal numerical claim |
| 11 | Read `evidence/inject.txt`; hash and verify its referenced transcript | Hostile directive cannot trigger borrowing/withdrawal | Expected hash matches; verifier `ALL PASS`; model chose `hold`, no agent action | **PASS** for this one demonstration |
| 12 | Read `evidence/revoke.txt`; v2 key/state views; `cast receipt 0x431aefa81689bd54a8d6e83faf27ab87731c7fc9e8e9492b057de25bc40c94cf --rpc-url https://sepolia.base.org` | No keys/approval; close and exact 0.002 WETH owner withdrawal | Zero keys, null agent/approval; successful close; withdrawal receipt transfers exactly **2,000,000,000,000,000 wei WETH** to owner | **PASS** |

Sixteen initial transcript downloads timed out. Retrying through the documented relayer proxy recovered all of them; no unresolved hash mismatch remained.

Additional checks: `forge build`, `forge test`, app `pnpm typecheck`, `pnpm test`, `pnpm build`, `pnpm test:bundle-boundary`, and landing `pnpm build`/`pnpm check` passed. Chromium downloaded successfully, but browser launch failed with **`libnspr4.so: cannot open shared object file`**. Full browser scenarios were not executed.

Raw screening logs and supporting scripts are under [<tmp>](<tmp>).

## Discrepancies

1. **Confirmed numerical error:** [README step 10](/work/cmdk/README.md:147) says the unprotected gradual-crash position liquidated at −41.8%. The recorded transaction and historical oracle establish **−42.9062%**. Approximately −41.86% is the theoretical liquidation threshold, not the actual execution point in this stepped scene.

2. **Operational count has changed:** [README step 8](/work/cmdk/README.md:145) describes three live failed model calls on 8/10 November. The inspected index contains **four** `error` outcomes; the additional one occurred **11 November at 04:02:20 UTC**, after the README commit. This is normal evidence aging, not proof the earlier count was wrong.

3. **Host builds do not reproduce deployed NEAR bytes.** Both vault and factory compiled successfully but yielded different hashes. The README explicitly warns of host-toolchain dependence; this is an unresolved reproducibility boundary, not an unexpected build failure.

4. **Full enclave/image reproducibility remains unconfirmed.** Published image, binary, compose and OS hashes check out. Docker-dependent source rebuilds and MRTD/RTMR0–2 regeneration did not run.

5. **Transcript signatures have a narrower meaning than complete attestation reproduction.** The verifier checks recorded request/response bytes and signer identity, but trusts the recorded model-attestation verdict. It does not independently reconstruct the model’s TDX verification or validate GPU attestation; the README discloses this.

6. **Research figures have differing verification strength.**
   - Scene dollar differences were independently recomputed from supplied records.
   - Dataset totals and cohort headline numbers match published aggregates, but underlying collection/replay was not regenerated.
   - The ≥$1,000 headline specifically uses **venue-feed-only episodes**, with reconstruction exclusions and separate fallback-price sensitivity analysis. These qualifications are clearer in the research note than the main headline.
   - The 20-position/$2.65M backtest, incident-replay lead times, injection/repeatability rates, idealized BTC arithmetic, and cost estimates were not independently rerun.
   - Research notes retain private-planning paths and omitted inputs. [Research disclosure](/work/cmdk/research/README.md:15) acknowledges missing backtest scripts/raw inputs, including the missing `rpc.py` helper. These are not fully runnable reproductions from this checkout alone.

7. **External market and competitive numbers remain unverified by this screen:** borrower counts, outstanding loan volume, liquidation totals, competitor coverage/TVL, incident losses, venue liquidity, and claims of comparative uniqueness. They should not be treated as independently established findings here.

8. **Important delivery boundaries are disclosed rather than hidden:** no live `open_from_stables` evidence, no private cross-chain funding demonstration, no buy-back, no additional lending venue, no permissionless outage backstop, and public testnet transcripts. The two demo text feeds are team-controlled, so their agreement does not demonstrate independent-source corroboration.

All principal paths and scripts named in the root verification section existed. No missing root verification command was found.

## Preliminary score: **8/10**

Reasons:

- Strong live evidence across NEAR and Base, including independently matched executor bytecode.
- Meaningful contract tests, restrictive credentials, verifiable historical actions, and candid failure cases.
- Public, buildable app and landing page with matching deployed artifacts.
- Remaining uncertainty around complete reproducible enclave builds, interactive wallet flows, independent research reproduction, and model benefit beyond deterministic protection.
- One confirmed README numerical error.

Docker absence, browser-library absence, initial memory kills, and my fixture-signing mistake are **not submission defects**. This score is preliminary and cannot alone reject the submission.

## Questions for the human judges

1. Can the team provide a clean Docker reproduction of the deployed vault and agent image, with resulting hashes?
2. Can judges witness a fresh wallet-driven open, revoke, close and withdrawal flow, including recovery from an interrupted signing/relaying operation?
3. How much credit should the AI component receive beyond the deterministic corridor, given equal price-scene outcomes and selected venue-stress demonstrations?
4. Does the privacy track require demonstrated private funding, or is the implemented enclave/confidential-inference component sufficient?
5. Can the team supply runnable inputs for the cited 20-position backtest and original incident replay?
6. Confirm the eligible development delta against `pre-hackathon`, and request correction of the −41.8% liquidation statement.

**Humans decide; these findings support their review.**