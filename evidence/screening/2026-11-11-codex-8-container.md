# AI screening rehearsal 8, Codex in a container (11 Nov 2026)

The same container and tool as rehearsals 6 and 7 (Linux, Node 22, Rust stable with cargo-near 0.22.0, near-cli-rs 0.30.1, Foundry 1.9.0; no Docker, no browser libraries, no keys; codex-cli 0.162.1, gpt-6-astra, reasoning high), on a fresh clone of commit 02e3bb3 (after `evidence/README.md`, the liquidation figure and the step 8 count). One change to the prompt (`2026-11-11-prompt-8.md`): signatures made locally by test suites with throwaway or fixture keys are allowed, nothing may reach a network. With that, the vault's sandbox tests, the agent's tests and the step 7 fork run were executed. 07:23 to 07:40 UTC. Rehearsal 7 scored 8. The report below is the tool's final answer, unedited except temporary paths shortened to `<tmp>`.

---

**Summary**

1. Screened commit `02e3bb33df0b0104400913a1cbbf18d25325e505` on 11 November 2026.
2. Live NEAR/Base state and transaction receipts substantiate the core borrowing-protection implementation.
3. Executor, vault, agent, and app unit tests passed; the local fork reproduced all five attack refusals.
4. Docker-dependent reproduction and browser execution remain unverified; several research claims have incomplete independent reproduction.
5. **Preliminary score: 8.5/10.** This is screening evidence, not an acceptance or rejection decision; humans decide.

No public-network transactions were submitted, accounts created, or funds spent. Signing and state changes occurred only in local tests and the local fork. Final tracked working-tree and index checks were clean. Logs and supplementary downloads are under `<tmp>`.

**Reproducibility**

Toolchain: Node `22.23.4`, Rust `1.99.1`, cargo-near `0.22.0`, near-cli-rs `0.30.1`. Foundry was installed outside PATH; adding `/home/screen/.foundry/bin` resolved that environment issue. Docker was unavailable.

| Artifact/check | Command or method | Expected | Observed result |
|---|---|---|---|
| Three NEAR WASMs | In `vault/`: `(cd mock-signer && cargo near build non-reproducible-wasm) && cargo near build non-reproducible-wasm && (cd factory && cargo near build non-reproducible-wasm)` | Successful builds | **PASS:** all three built. |
| Vault tests | In `vault/`: `cargo test` | 15 unit and 8 sandbox tests pass | **PASS:** 15/15 unit and 8/8 sandbox tests. |
| Executor tests | In `executor/`: `forge test` | Successful fork, fuzz and invariant checks | **PASS:** 64 passed, zero failed or skipped. Foundry automatically initialized its declared dependency; that initialization was removed afterward. |
| Executor source/deployment match | Rebuild; `cast code`; compare compiler immutable references; inspect Blockscout API | Deployed code matches source | **PASS:** 16,459-byte runtime matches after accounting for immutable values, which equal Morpho’s documented address and cooldown `60`. Creation bytecode plus constructor arguments also matches Blockscout. All three published Solidity source files equal this checkout. |
| Deployed vault identity | README `view_code` pipeline and account summary | Published SHA-256 | **PASS:** `def9671e099fe325a7ac8c43831de0334a3dc75221f6c302b17a893e0377e1ba`. |
| Pinned vault rebuild | Clean checkout of `975b98d454e7080495230b3aff73296a92e714c2` outside the repository; `./scripts/reproduce-hash.sh --reproducible --vault …` | `result           MATCH` | **COULD NOT RUN:** script reports missing running Docker daemon. This was also attempted from the supplied checkout. |
| Host-built vault | SHA-256 of generated WASM | Successful host build; identical deployment hash is not promised | **PASS build, different hash:** 773,607 bytes; `65d34ff0a89077b52a9135914683ec9a675d7f6fc76deba44d6db610c7dc7204`. This does **not** establish bit-for-bit reproduction. |
| Factory | Read deployed WASM, keys and configuration; host build | Published identity, no keys, fixed vault hash | **PASS identity/state:** deployed WASM is 152,851 bytes with hash `5315458701e4f52c9c033d05d492ec75d18076a128a0261f153434670971c863`. Host build is 151,703 bytes with a different hash, consistent with the README’s toolchain caveat. |
| Agent tests | In `agent/`: `cargo test` | Tests pass | **PASS on retry:** 65 passed, one explicitly ignored sandbox test. First attempt failed during linking with `ld terminated with signal 9 [Killed]`; retry after other builds finished succeeded. |
| Agent image | Read GHCR manifest by published digest | Manifest/image identifiers match | **PASS identity reference:** manifest SHA-256 is `e72564a8e1fe513a578dc37863491a84f7873d7bfba28314ce59ac3f5ebc94d1`; its config digest matches published image ID `d3b51d67…`. |
| Agent image reproduction | `agent/build.sh`, two uncached Docker builds | `REPRODUCIBLE image …` | **COULD NOT RUN:** Docker unavailable. Registry identity is not a source rebuild. |
| Enclave measurements | Compose hashing, quote inspection and event-log replay | Matching approved measurements | **PASS partial verification:** all four archived boots’ compose hashes match their event logs and quote MRCONFIGID; RTMR3 replays match. Current quote MRTD and RTMR0–2 match the deployment manifest. **COULD NOT RUN** independent OS/register reconstruction without Docker. |

The deployed vault’s source metadata names commit `975b98d…` and the documented pinned build image. `vault/src`, `Cargo.toml` and `Cargo.lock` have no differences between that commit and the screened checkout. Factory source is unchanged from the cited `52e0446`.

The public app and landing repositories were also checked in temporary directories:

| Check | Expected | Observed result |
|---|---|---|
| App `pnpm install --frozen-lockfile` | Install pinned dependencies | **PASS**, pnpm `11.7.0`. |
| App `pnpm dev` | Serve local app | **PASS**, localhost returned HTTP 200. |
| App `pnpm typecheck` | No TypeScript errors | **PASS**. |
| App `pnpm test` | 162 tests | Initially 161 passed and one skipped because generated configuration was absent; after the documented build generated it, **162 passed, zero skipped**. |
| App `pnpm build` | Production bundle | **PASS**, using the published deployment manifest. |
| App `pnpm test:bundle-boundary` | Exclude browser fixtures and wallet controls | **PASS:** `Production bundle excludes browser fixtures and wallet controls.` |
| `pnpm exec playwright install chromium` | Install browser | **PASS**. |
| App `pnpm test:browser` | 88 browser scenarios | **COULD NOT RUN assertions:** all 88 reported launch failures because `libnspr4.so` is missing. These are environment failures, not 88 demonstrated product defects. |
| App/live comparison | Published app corresponds to source | **PASS:** commit `c1d28f5…` builds byte-identical entry JavaScript and CSS; configuration matches live JSON. |
| Landing `pnpm install`, `pnpm build`, `pnpm check` | Build and copy/link checks pass | **PASS:** commit `68e91ec…`; checker reports `No CHECK lines.` Built HTML is byte-identical to live HTML. |

Screenshot-baseline rewriting was not performed: it changes expected results rather than verifying them. Public sites initially returned 403 through some clients, but subsequent `curl` requests succeeded; this is **not** recorded as a deployment outage.

**Feature verification table**

“Verified” below means supported by the inspected implementation and stated tests/read-only evidence, not formally proven or audited.

| README claim | Evidence | Verdict |
|---|---|---|
| Immutable, owner-controlled NEAR vault and keyless factory | [Factory creation code](/work/cmdk/vault/factory/src/lib.rs:125); live vault uses the published global-contract hash; factory has zero keys and configuration pins that hash. Factory count is 11. | **Verified for inspected deployment.** |
| Existing EVM wallet authorizes owner actions with nonce and expiry | [Owner signature verification](/work/cmdk/vault/src/owner.rs:37), including `ecrecover`, network/vault binding, nonce and expiry checks; passing owner-message sandbox tests. | **Verified.** No real wallet interaction attempted. |
| Agent key can call only `reduce`, `migrate`, `exit` | [Registration/key grant](/work/cmdk/vault/src/lib.rs:471); live access-key listing contains exactly those three methods and no full-access key. | **Verified.** |
| On-chain TDX quote verification binds the agent key and approved code measurement | [Attestation verifier](/work/cmdk/vault/src/attest.rs:107); successful live registration transaction `CPEBXcPD…`; quote/manifest and event-log checks. | **Verified implementation and registration evidence; full independent measurement rebuild unavailable.** |
| Agent registration lasts seven days and renews before expiry | [Registration expiry](/work/cmdk/vault/src/lib.rs:490), [renewal logic](/work/cmdk/agent/src/logic.rs:808); live expiry `2026-11-16T22:52:28Z`. | **Verified code/current state; renewal over seven days was not observed during this session.** |
| NEAR enforces market allowlist, slice cap and cooldown | [Agent methods](/work/cmdk/vault/src/lib.rs:435); live state has 1,000-USDC caps and 120-second cooldown; passing policy/key sandbox tests. | **Verified.** |
| Immutable executor rechecks policy against Morpho/oracle data | [Executor](/work/cmdk/executor/src/Executor.sol:230); runtime/source match, live configuration and passing tests. | **Verified.** |
| Corridor defaults are 50/70/30%, daily discretionary budget 20%, two moves/day | Live `corridors` values match on live and backup markets; [constants](/work/cmdk/executor/src/Executor.sol:49); local attack refusals. | **Verified.** |
| Agent outputs stay with its position; no arbitrary recipient | [Migration/exit paths](/work/cmdk/executor/src/Executor.sol:310), recipient-pinning tests, and decoded historical receipts. | **Verified for implemented paths.** Swaps necessarily transfer collateral to the pool. |
| Protective selling works | Receipt `0xde730226…`: repaid 3.849781 USDC; LTV moved from 49.96745% to 40.01515% after owner lowered target/act lines. | **Verified historical execution.** |
| Migration preserves collateral/debt within documented rounding allowance | Receipt `0xd597c432…`: moved 7.729880 USDC debt and `0.008335601888990126` WETH; conservation tests pass. | **Verified.** |
| Agent can exit when code finds no approved migration destination | [Decision logic](/work/cmdk/agent/src/logic.rs:417), signed exit transcript and receipt `0x014aebcd…`. | **Verified demonstrated case.** Migration-first behavior is agent policy, not an executor requirement, as disclosed. |
| Revocation, owner close and withdrawal work | Live v2 has no keys, `agent:null`, `approved:null`; close/withdraw receipts match [recorded evidence](/work/cmdk/evidence/revoke.txt:1). | **Verified.** Historical loop-stop timing remains log evidence. |
| Model output is bounded and cryptographically attributable | [Judgment validation/attestation](/work/cmdk/agent/src/judge.rs:138); offline verification of archived and downloaded transcripts. | **Verified within verifier limitations:** recorded model-attestation verdict is trusted, not independently reverified from transcripts. |
| Numeric protection continues independently of model decisions | [Protective decision logic](/work/cmdk/agent/src/logic.rs:417), passing agent tests and signed/rules-only scene transcripts. | **Verified implementation and examples; continuous availability is not established.** |
| Text agreement requires corroboration | [Text gate](/work/cmdk/agent/src/logic.rs:338) requires critical risk and distinct recent feed IDs. | **Qualified:** distinct IDs are verified; independent publishers or semantically supporting claims are not established by that gate. Two demo feeds belong to the team, as disclosed. |
| `open_from_stables` exists | Vault/executor implementations and passing tests, including executor `test_openFromStables`. | **Verified in tests only.** README correctly discloses no live evidence transaction. |
| Walletless simulated app flow and policy-safe vault adoption | [App policy comparison](https://github.com/cmdkfi/cmdk-app/blob/c1d28f5c5b539e17a5ac52730335dc4f3d32809c/app/src/protocol/near/vault.ts#L73), unit tests, matching live bundle. | **Source/build verified; interactive browser flow not verified here.** |
| Private any-chain deposits/withdrawals, BTC deposits, buy-back, additional venues | README explicitly places these in production plans; current evidence concerns WETH/Base Sepolia/Morpho. | **Not delivered in this build; correctly scoped as future work.** |
| Research headline: 20,479 liquidations; 1,890/1,895 and 5,009/5,011 saved | Summed published market-year CSV; checked [size-headline results](/work/cmdk/research/liquidation-dataset/data/step2_size_headline.csv:1). | **Published aggregates confirmed; full historical data collection and replay not independently reproduced.** |
| AI adds lead time, not improved detection in the cited incident replay | [Published replay report](/work/cmdk/research/judge-evidence/incident-replay-results.md:1) supports the README’s distinction. Price scenes also show essentially equal AI/rule outcomes. | **Document-supported, not independently rerun inference.** |

**Verify-it results table**

The table uses these substitutions for README placeholders:

```text
V = v1.factory.prime-measure-1792736012.testnet
F = factory.prime-measure-1792736012.testnet
X = 0xA9bbabE6d75C5BA96CA095586284774c9D09D18b
B = 0xb3ccd0ad7e5fca9d4c18cf59d19fab4703eee6fb
L = 0xa25ac5171b7e500b82653d3750b91bb44e2409c32ca3c9e80b0db077e8a4535c
K = 0x99d43c0f2a0eb6172774598a4e34e7eee09caa502692c2b2df0f40e1e3c5ea75
RPC = https://base-sepolia-rpc.publicnode.com
E = https://49ecae9fcc4a6970956d8c987386315297922a69-8080.dstack-pha1-prod5.phala.network
BOOT = boot-1794264608-2286af92
```

| Step | Command/check | Expected text or behavior | Actual result and status |
|---|---|---|---|
| 1a | `near account list-keys V network-config testnet now` | Exactly one restricted key; no full-access keys | **PASS:** key `ed25519:3Kmykr9…`, methods `["reduce","migrate","exit"]`, receiver V, allowance 2 NEAR. |
| 1b | `near contract call-function as-read-only V get_state json-args '{}' network-config testnet now` | Lowercase wallet owner and derived Base address | **PASS:** owner `0x0b7ed66f46fe83252a208563a88201ba60490d25`; Base address B. |
| 2a | `near account view-account-summary V network-config testnet now` | `Global Contract (by Hash: SHA-256 checksum hex)` and published hash | **PASS:** exact hash `def9671e…`; zero full-access and one function-call key. |
| 2b | README’s `curl` `view_code` → `jq -r .result.code_base64` → `base64 -d` → `sha256sum` pipeline, substituting V | Same published hash | **PASS:** `def9671e099fe325a7ac8c43831de0334a3dc75221f6c302b17a893e0377e1ba`. |
| 2c | `near contract call-function as-read-only V contract_source_metadata json-args '{}' network-config testnet now` | Link to `975b98d…` and pinned build information | **PASS:** exact commit and image digest match README. |
| 2d | `./scripts/reproduce-hash.sh --reproducible --vault V`, from clean pinned checkout | Hash match and `result           MATCH` | **COULD NOT RUN:** missing Docker daemon. |
| 2e | Factory `list-keys`, `get_config`, and deployed-code read | No keys; fixed vault hash; published factory hash | **PASS:** all match; count 11. |
| 3 | `cast call X "corridors(address,bytes32)(uint64,uint64,uint64,uint64,bool,bool,bool)" B L --rpc-url $RPC`; repeat with K | `5e17`, `7e17`, `3e17`, `2e17`, `true`, `true`, `true` | **PASS:** exact seven values on both markets. NEAR state also matches cap/cooldown fields. |
| 4 | `./scripts/ltv.sh V --market backup` | Two debt/LTV columns and `cross-check    MATCH with the "at lastUpdate" LTV (WAD)` | **PASS:** block 57,300,146; collateral `0.008335601888990126` WETH; debt 7.730581 USDC stored / 7.730739 accrued; LTV 44.9026% / 44.9035%; exact `MATCH` text. |
| 4, default | `./scripts/ltv.sh V` | `(no position on this market)` | **PASS:** live market has zero borrow shares and collateral; quoted text present. |
| 5a | `cast call 0xBBBBBbbBBb9cC5e90e3b3Af64bdAF62C37EEFFCb "isAuthorized(address,address)(bool)" B X --rpc-url $RPC` | `true` | **PASS:** `true`. |
| 5b | `cast call X "config(bytes32)(((address,address,address,address,uint256),address,address[2],uint32[2]))" L --rpc-url $RPC`; repeat with K | Fixed pool, documented feeds, maximum ages `[1200,86400]` | **PASS:** pool `0x57183717…`, both feed addresses and ages match. |
| 6a | `get_state`; `near transaction view-status CPEBXcPDmvp2QmS3zakVDtRQ46Z8fvPsrfU5cS8GpKgb network-config testnet` | Approved measurement, registered key/expiry, successful registration | **PASS:** matching measurement/key; registration `SuccessValue`. |
| 6b | `./scripts/p3_reproduce_measurements.sh <tmp>/measurements evidence/BOOT dstack-0.5.12 1 2G` | OS hash/register matches and `ALL MATCH` | **COULD NOT RUN:** Docker prerequisite absent; full reconstruction was not invoked. Independent non-Docker subset reported above passed. |
| 6c | `sha256sum evidence/BOOT/app-compose.json` | Compose hash matching event log and MRCONFIGID | **PASS:** `db4f508517a01b31986dc9ec10eefd7491eb249cc209fb6b395c449e91c1dfc4`. |
| Between 6–7 | Three-WASM build command followed by `cargo test` in `vault/` | 15 unit / 8 sandbox tests | **PASS:** exact counts, no failures. |
| 7, public-network command | `./scripts/attack-corridor.sh --land 2>&1 \| tee evidence/attacks.txt` | Five named reverts, `result: 5 PASS, 0 FAIL` | **COULD NOT RUN as written:** broadcasts public-network transactions and overwrites repository evidence. Prohibited by screening rules. |
| 7, documented alternative | `anvil --fork-url …`; documented fresh-key/token funding steps; `scripts/attack-corridor.sh --rpc http://127.0.0.1:8545 --key-file <tmp>/k.json --land` | Same five refusals locally | **PASS:** Expired `0x203d82d8`; MoveLimit `0x013da4fb`; UnapprovedMarket `0x99b2f06d`; BelowFloor `0x4e7e2916`; SellBudget `0x397b9774`; `result: 5 PASS, 0 FAIL`. Only temporary key-file location differed. |
| 8a | `curl -s $E/transcripts`; `curl -s $E/transcripts \| jq -r '.transcripts[].name'` | Sorted index with required metadata | **PASS:** 747 entries; count, sorting and required fields verified. |
| 8b | `curl -s $E/transcripts/<name> -o <tmp><name>`; `./agent/verify-transcript.sh <tmp><name>` | Per-check `PASS`, then `ALL PASS` | **PASS:** downloaded `boot-1794264608-2286af92-v4-0038.json`; bytes/hash match index; all seven verifier checks pass. This is a sample, not verification of every live transcript. |
| 8c | Verifier on prototype fixture and archived transcripts | Valid signatures pass; no-call outcomes distinguished | **PASS:** prototype passes. All 75 archived files match index hashes; 32 return `ALL PASS`, 43 return documented `NO-CALL`, including three recorded errors. Migration and exit transcript checks also pass. |
| 9 | Read `evidence/protect-live.txt`; fetch its Base receipt | Protective reduce and target restoration | **PASS:** receipt status 1 and decoded event values match the file, including approximately 40.015% resulting LTV. |
| 10 | Read scene Markdown/JSON; fetch action receipts; verify migration/exit transcripts | Gradual-crash, gap, whipsaw, migration and exit evidence | **PASS for recorded evidence:** all 38 listed price-scene action receipts match recorded blocks. Recalculated rounded changes match −1.76/−2.11, −2.23, and −0.59/0.00 USD. Separately checked failed gap reduce has failed status. Migration and exit receipts match amounts. No public scenes were rerun. |
| 11 | Read `evidence/inject.txt`; `sha256sum` and verifier on `boot-1793877578-a405e917-v1-0002.json` | Hostile memo ignored/bounded; authentic transcript | **PASS:** hash `748c8c59…`, `ALL PASS`; model chose `hold`, market `none`. Demonstrates this case, not general injection immunity. |
| 12 | v2 `list-keys`, `get_state`; `cast receipt 0x431aefa81689bd54a8d6e83faf27ab87731c7fc9e8e9492b057de25bc40c94cf --rpc-url https://sepolia.base.org`; close receipt | No agent key/approval; close and exact owner withdrawal | **PASS:** no keys; null agent/approval; successful close; WETH Transfer from v2’s address to owner for exactly `2000000000000000` wei. |

**Discrepancies and verification limits**

- **Cooldown wording is imprecise.** README step 3 calls the executor cooldown “per position.” The implementation keys it by caller across markets: [Executor.sol:476](/work/cmdk/executor/src/Executor.sol:476). This is a stricter shared cooldown, not an independently available cooldown for each market position.
- **“Independent sources” needs qualification.** The code counts distinct feed IDs and recent cited items; it does not establish independent ownership or semantic corroboration. The README does disclose that two demo feeds are team-controlled.
- **Reproducibility is not complete here.** Deployed hashes, source provenance, executor bytecode, image manifest, app bundle and landing HTML were checked. Docker-dependent source/image/OS-measurement reproduction was not completed. Host WASM hash differences are expected under the README’s stated caveat, not evidence that deployment is wrong.
- **Browser prerequisites are incomplete for this container.** Installing Chromium alone did not install `libnspr4.so`. The app’s CI uses `playwright install --with-deps chromium`, whereas its development instructions show browser installation without `--with-deps`.
- **App test order matters.** Running unit tests before generating configuration skips one test. Building and rerunning gives all 162 passes.
- **Research aggregates are not a reproduced experiment.** I confirmed the 20,479 total and reported saved-position aggregates from published tables. I did not regenerate historical data, rerun the full policy grid, or establish causal savings. The tables also record policy-induced liquidations among previously surviving episodes; the headline saved counts are not universal protection.
- **Some research paths are intentionally unavailable.** `research/README.md` explicitly identifies internal backtest inputs, missing older scripts/helpers, and planning-repository paths. The original incident-replay report references unavailable/private paths. The 20-position “all survive / $2.65M penalties avoided” result and original incident lead-time comparison were therefore not independently reproduced.
- **Other quantitative claims remain unconfirmed:** the idealized BTC-return arithmetic beyond its published table; current competitor/customer/market-size figures; external exploit loss figures; MPC operator thresholds; sustained loop latency; historical model-error counts across every live transcript; and the $0.70–1.00 monthly / 2–3-cent action cost estimates.
- **Comparative superlatives remain judgments.** “Tightest limits” and uniqueness relative to competitors are not established by this repository screening.
- **Disclosed scope limitations are material:** no live `open_from_stables` evidence; no demonstrated private/any-chain funding; no buy-back; no outage-independent backstop; public testnet transcripts; and a documented manual broadcast recovery in an earlier exit run. These were not treated as hidden discrepancies.

No missing path or script was found among the main README’s verification artifacts.

**Preliminary score: 8.5/10**

This is an assessment of the whole submission, not an official weighted judging rubric.

- Strong evidence: functioning NEAR/Base integration, reproducible executor bytecode, passing contract/sandbox/agent tests, matching live frontend builds, signed transcripts, and independently reproduced attack refusals.
- Meaningful engineering: NEAR access-key restrictions, attestation-bound registration, owner signatures and executor enforcement are central to the design.
- Remaining gaps: Docker reproduction, interactive browser verification, incomplete independent research reproduction, limited demonstrated privacy/cross-chain scope, and unresolved availability assumptions.

A low AI score alone cannot reject a submission; this score likewise does not imply acceptance or a security audit.

**Questions for the human judges**

1. How should the Privacy track credit confidential execution when private funding is future work and current transcripts are public?
2. Can a Docker-equipped reviewer independently repeat the pinned vault, agent-image and dstack measurement builds?
3. Can judges complete a browser walkthrough, including wallet rejection/recovery paths, in an environment with Chromium’s system dependencies?
4. Should the team provide the missing original backtest inputs/harness and clearer evidence for economic and comparative claims?
5. Is the demonstrated AI contribution—venue-specific decisions and reported lead time—sufficiently distinguished from the deterministic corridor’s protection?
6. Does the disclosed pre-hackathon baseline and subsequent development satisfy the organizers’ eligibility interpretation?