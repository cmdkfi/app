# AI screening rehearsal 6, Codex in a container (10 Nov 2026)

Run on a fresh clone of commit 830c058 inside a Linux container (Debian, Node 22, Rust stable with cargo-near 0.22.0, near-cli-rs 0.30.1, Foundry 1.9.0, python3, jq; no Docker, no browser, no keys), the same box and the same generic prompt (`2026-11-10-prompt-6.md`, the Q-017 prompt without cmdk-specific notes) we used to screen other hackathon entries the same evening. Tool: codex-cli 0.162.1, model gpt-6-astra at reasoning effort high, `codex exec --dangerously-bypass-approvals-and-sandbox` (the container is the sandbox); 18:30 to 18:43 UTC. The container's disk ran out during the run (vault builds, web app install and research downloads stopped with ENOSPC); the Docker-based rebuilds could not run. Rehearsal 5 scored 8 (Codex) and 8.5 (Claude Code). The three discrepancies in the README it found are fixed in 577f77a. The report below is the tool's final answer, unedited except temporary paths shortened to `<tmp>`.

---

## Summary

1. **The core testnet submission is substantially verified:** deployed contracts, restricted agent access, protective actions and owner withdrawals have supporting live evidence.
2. **Executor reproducibility passed:** source and creation bytecode matched Blockscout; all 64 local executor tests passed.
3. **Vault and enclave reproducibility remain incomplete:** Docker is unavailable, and host builds encountered insufficient disk space.
4. **Evidence is unusually detailed, but research reproducibility is weaker:** some cited model runs and backtest inputs remain private.
5. **Preliminary score: 8.0/10.** This is screening, not an acceptance or rejection decision; humans decide under Q-017.

## Reproducibility

Screened on **10 November 2026**, checkout **`830c05838c67af2125f18446e468f7480ddee600`**. The original checkout remained clean. No transactions were submitted, accounts created, messages signed or paid inference requested. Builds and downloaded evidence used `/tmp`.

Tools checked: Node 22.23.4, Rust 1.99.1, cargo-near 0.22.0, near-cli-rs 0.30.1, Foundry 1.9.0 and Python 3.13.5. Foundry was installed but initially absent from `PATH`; adding `$HOME/.foundry/bin` resolved the affected commands.

| Component | Result | Evidence and limits |
|---|---|---|
| **Executor source and build** | **PASS** | Initialized the pinned `forge-std` submodule in a temporary clone. `forge build` succeeded with Solidity 0.8.28. All three contract source files exactly matched Blockscout’s verified sources. Compiled creation bytecode plus published constructor arguments exactly matched Blockscout’s creation bytecode. Live runtime code also matched its published runtime code: 16,459 bytes. [Verified contract](https://base-sepolia.blockscout.com/address/0xA9bbabE6d75C5BA96CA095586284774c9D09D18b?tab=contract). |
| **Executor tests** | **PASS** | `forge test`: **64 passed, 0 failed, 0 skipped**. Included fork tests, fuzz tests and an invariant campaign reporting 64 runs and 1,600 calls. Execution occurred locally, without broadcasts. |
| **Published vault identity** | **PASS** | Live global-contract hash was `def9671e099fe325a7ac8c43831de0334a3dc75221f6c302b17a893e0377e1ba`, matching the manifest. NEP-330 identifies source commit `975b98d454e7080495230b3aff73296a92e714c2`; its vault source, manifest and lockfile have no differences from the screened checkout. |
| **Reproducible vault build** | **COULD NOT RUN** | Ran the documented script, including from a clean temporary checkout of the NEP-330 commit. It reported: `a running Docker daemon (docker info failed)`. No independently rebuilt matching vault WASM was obtained. |
| **Host WASM build sequence** | **COULD NOT RUN to completion** | Ran the exact three-build chain from `vault/`. The mock-signer build stopped during ABI compilation with `No space left on device (os error 28)`. The subsequent vault and factory commands were consequently not reached. |
| **Factory identity** | **PASS for live identity** | No access keys; published local-code hash `5315458701e4f52c9c033d05d492ec75d18076a128a0261f153434670971c863`; `get_config` returned the expected vault hash and count **11**. Its host-specific WASM was not reproduced. |
| **Agent image identity** | **PASS for published artifact identity** | Anonymous registry reads reproduced manifest digest `sha256:e72564a8e1fe513a578dc37863491a84f7873d7bfba28314ce59ac3f5ebc94d1` and image configuration digest `sha256:d3b51d670192b3b77b64241e70dca28eb97fa446128c3aca79ba548c18efcfcb`. Image inputs at declared source commit `4171c10` match the checkout. This verifies registry identity, not a source rebuild. |
| **Agent image rebuild** | **COULD NOT RUN** | `./build.sh` in the temporary agent directory stopped with `docker: command not found`. |
| **TDX measurements** | **PARTIAL** | The documented script downloaded the OS release and printed `os_image_hash MATCH`, then stopped at Docker. Independently replayed RTMR3 successfully and matched the compose hash against the event log and quote’s MRCONFIGID. MRTD/RTMR0–2 were not independently rebuilt. |
| **Web app and landing page** | **PARTIAL** | Both live sites returned HTTP 200; app JavaScript and configuration were accessible. Read the public app at commit `c1d28f5…` and landing page at `68e91ec…`. App `pnpm install --frozen-lockfile` failed with `ERR_PNPM_ENOSPC`; landing `pnpm check` also triggered an install that exhausted space. Direct landing copy-check execution reported missing build artifacts. No completed frontend build or browser test run. |
| **Research artifacts** | **PARTIAL** | All **155 available files** checked against the dataset’s published checksum table matched. The public release lists **17 assets totaling 1,968,184,899 bytes**, as documented. Full download/replay exceeded available disk space; some other research inputs are explicitly unpublished. |

## Feature verification table

“Verified” below means supported by the checks performed, not a security audit or universal guarantee.

| README claim | Code and live evidence | Verdict |
|---|---|---|
| Immutable per-user NEAR vault and keyless factory | [Factory creation](/work/cmdk/vault/factory/src/lib.rs:128) attaches the global contract by hash. Live factory has zero keys; vault has zero full-access keys and the published global hash. | **Verified** |
| Agent credential permits only `reduce`, `migrate`, `exit` | [Registration](/work/cmdk/vault/src/lib.rs:471) installs the restricted key. Live key list contains exactly those methods, restricted to this vault, with a 2 NEAR allowance. | **Verified** |
| Seven-day registration, attestation and approved measurements | [Attestation verifier](/work/cmdk/vault/src/attest.rs:107) checks DCAP verification, `UpToDate`, debug disabled, key binding and measurements. Live registration transaction `CPEBXcPD…` finalized successfully; state records expiry **2026-11-16 22:52:28 UTC**. | **Verified implementation and deployed registration; full measurement rebuild incomplete** |
| Owner authorization uses an existing EVM wallet | [Owner verification](/work/cmdk/vault/src/owner.rs:55) checks recovered address, protocol, network, vault, nonce and expiry. Recorded close/withdraw receipts exist on Base. | **Verified code and historical execution; no new signed rehearsal** |
| Agent cannot select an external recipient | [Agent entry points](/work/cmdk/vault/src/lib.rs:435) accept no recipient; [transaction allowlist](/work/cmdk/vault/src/lib.rs:682) refuses agent token transfers. Executor callbacks repay Morpho/pay the configured pool; exit returns collateral to the caller. | **Supported by code, tests and receipts** |
| Default corridor is 50/70/30 with 20% discretionary sell budget | Live and backup `corridors` both returned the exact documented seven values. [Reduction logic](/work/cmdk/executor/src/Executor.sol:258) distinguishes protective sessions from budgeted discretionary sales. | **Verified** |
| Cooldowns, two moves/day and 16 discretionary sales/window | Live NEAR cooldown is 120 seconds; executor configuration uses 60 seconds. [Rolling sale and move records](/work/cmdk/executor/src/Executor.sol:576) implement the limits; corresponding tests passed. | **Verified** |
| Migration preserves collateral and approximately preserves debt | [Migration checks](/work/cmdk/executor/src/Executor.sol:305) enforce conservation/rounding bounds. Live receipt `0xd597c432…` records the move to backup; archived comparison records unchanged collateral and accrued debt. | **Verified demonstrated flow** |
| Exit repays debt and returns remaining collateral | [Exit implementation](/work/cmdk/executor/src/Executor.sol:360); successful receipt `0x014aebcd…`: **4.442487 USDC repaid**, **0.002005781652667964 WETH sold**, **0.001991909367059108 WETH returned**. | **Verified** |
| Swaps enforce a 1% oracle-relative bound; stale feeds do not block reduce/exit | [Slippage math](/work/cmdk/executor/src/CorridorMath.sol:11), [swap callback](/work/cmdk/executor/src/Executor.sol:419), freshness checks and passing tests support this distinction. | **Verified implementation; oracle accuracy itself remains outside the guarantee** |
| Revocation prevents new agent actions; previously signed calls expire | [Revocation](/work/cmdk/vault/src/lib.rs:419) clears approval and removes the key. Live v2 has no keys, `agent: null`, `approved: null`. Historical expired call reproduced error selector `0x203d82d8`. | **Verified within the disclosed signing-path assumptions** |
| Model judgment is bounded, market-specific and separated from amount calculation | [Decision logic](/work/cmdk/agent/src/logic.rs:417) applies judgments per market and computes/clamps amounts. Migration preference appears at line 500. Archived migration and exit transcripts substantiate use. | **Verified code and recorded decisions** |
| Model responses have attributable enclave signatures; failed answers are rejected | [Model attestation](/work/cmdk/agent/src/judge.rs:284). All **316 accepted live answers** passed the supplied verifier. Two unsigned responses were rejected and marked `rules only`. | **Verified signatures; offline verification trusts the recorded attestation verdict** |
| Two-source text corroboration protects discretionary actions | [Corroboration](/work/cmdk/agent/src/logic.rs:338) counts distinct feed IDs. Two testnet feeds are team-controlled, as disclosed. | **Implemented demonstration; not independent editorial corroboration** |
| `open_from_stables` works | [Vault entry](/work/cmdk/vault/src/lib.rs:342); executor `test_openFromStables` passed. README explicitly supplies no live transaction. | **Test-supported; live unverified** |
| App rejects vaults with an unexpected policy before setup | Public app’s [`policyMatches`](https://github.com/cmdkfi/cmdk-app/blob/c1d28f5c5b539e17a5ac52730335dc4f3d32809c/app/src/protocol/near/vault.ts#L73) compares markets, executors, tokens, slice maximum and cooldown; `setupIntent` rechecks the result before requesting a signature. | **Verified source; browser behavior untested here** |
| Any-chain/BTC deposits, Confidential Intents, buy-back and additional venues | Root README and landing content label these as production-release or roadmap work. Current demonstrated collateral is WETH on Base Sepolia. | **Not implemented/demonstrated in this build; appropriately disclosed** |
| Backtests establish the quoted protection results | Local CSV totals confirm **20,479 liquidations**, and headline tables contain **1,890/1,895** and **5,009/5,011**. Full replay was not rerun; smaller incident/model studies lack public underlying runs. | **Published-table consistency verified; independent reproduction incomplete** |

## Verify-it results table

The table abbreviates the concrete arguments substituted into README placeholders:

```text
V = v1.factory.prime-measure-1792736012.testnet
F = factory.prime-measure-1792736012.testnet
X = 0xA9bbabE6d75C5BA96CA095586284774c9D09D18b
A = 0xb3ccd0ad7e5fca9d4c18cf59d19fab4703eee6fb
L = 0xa25ac5171b7e500b82653d3750b91bb44e2409c32ca3c9e80b0db077e8a4535c
B = 0x99d43c0f2a0eb6172774598a4e34e7eee09caa502692c2b2df0f40e1e3c5ea75
RPC = https://base-sepolia-rpc.publicnode.com
E = https://49ecae9fcc4a6970956d8c987386315297922a69-8080.dstack-pha1-prod5.phala.network
BOOT = boot-1794264608-2286af92
```

For steps offering archived evidence instead of a read-only executable reproduction, **PASS applies to checking that evidence**, not recreating the transaction.

| Step | Command or evidence checked | Expected text/result | Actual result | Status |
|---|---|---|---|---|
| 1a | `near account list-keys V network-config testnet now` | One function-call key; methods `reduce`, `migrate`, `exit`; no full-access key | Exactly one such key: `ed25519:3Kmykr9LYfH6STHHD2dDpENbFsait9UZqcfWbDw7pAVg` | **PASS** |
| 1b | `near contract call-function as-read-only V get_state json-args '{}' network-config testnet now` | Lowercase owner and derived Base address | Owner `0x0b7ed66f46fe83252a208563a88201ba60490d25`; Base address equals A | **PASS** |
| 2a | `near account view-account-summary V network-config testnet now` | `Global Contract (by Hash: SHA-256 checksum hex)` and manifest hash | Exact expected label/hash at NEAR block **277104542** | **PASS** |
| 2b | `./scripts/reproduce-hash.sh --reproducible --vault V` | Matching hex/base58 hashes; `result MATCH` | Missing running Docker daemon; repeated at the deployed source commit with the same result | **COULD NOT RUN** |
| 2c | Factory key list, summary and `get_config` view | No keys; fixed published vault hash | No keys; correct factory code hash; vault hash `G1Q5ksFhGbgPTN4A2DuvgraYmsbRpfwb78B547gAKXWq` | **PASS** |
| 3a | Step 1 `get_state` | Market IDs, executors, slice maxima, `corridor_set`, cooldown 120 | Three markets; expected executor; `slice_max: "1000000000"`; corridors set; cooldown 120 | **PASS** |
| 3b | `cast call X "corridors(address,bytes32)(uint64,uint64,uint64,uint64,bool,bool,bool)" A L --rpc-url $RPC`; repeated with B | `500000000000000000`, `700000000000000000`, `300000000000000000`, `200000000000000000`, `true`, `true`, `true` | Exact seven values, including cast’s scientific-notation annotations, on both markets | **PASS** |
| 4a | `./scripts/ltv.sh V --market backup` | Two LTV columns; `cross-check MATCH with the "at lastUpdate" LTV (WAD)` | At Base block **57068207**: collateral **0.008335601888990126 WETH**; debt **7.730581 USDC** stored / **7.730686** accrued; LTV **45.6506% / 45.6512%**; exact `MATCH` ending | **PASS** |
| 4b | `./scripts/ltv.sh V` | `(no position on this market)` | Exact expected text for live market | **PASS** |
| 5a | `cast call 0xBBBBBbbBBb9cC5e90e3b3Af64bdAF62C37EEFFCb "isAuthorized(address,address)(bool)" A X --rpc-url $RPC` | `true` | `true` | **PASS** |
| 5b | `cast call X "config(bytes32)(((address,address,address,address,uint256),address,address[2],uint32[2]))" L --rpc-url $RPC`; repeated with B | Correct market parameters; pool `0x57183717A087d2fe3Ad890873877244c3B96156c`; ages `[1200, 86400]` | Exact expected pool, feeds, ages and parameters; separate live/backup oracle addresses | **PASS** |
| 6a | `get_state`; public registration evidence; `near transaction view-status CPEBXcPDmvp2QmS3zakVDtRQ46Z8fvPsrfU5cS8GpKgb network-config testnet` | Approved measurements, registered key/expiry, successful registration | Measurements match manifest; registration finalized with `SuccessValue` and expected registration log | **PASS** |
| 6b | `./scripts/p3_reproduce_measurements.sh <tmp>/measure evidence/BOOT dstack-0.5.12 1 2G` | OS hash and all measurement registers `MATCH`; `ALL MATCH` | `os_image_hash MATCH`; then `docker: command not found`. Supplemental RTMR3 replay matched | **COULD NOT RUN fully** |
| 6c | `sha256sum evidence/BOOT/app-compose.json` | Compose hash matches event log and MRCONFIGID | `db4f508517a01b31986dc9ec10eefd7491eb249cc209fb6b395c449e91c1dfc4`; both comparisons matched | **PASS** |
| Tests after 6 | In `vault/`: `(cd mock-signer && cargo near build non-reproducible-wasm) && cargo near build non-reproducible-wasm && (cd factory && cargo near build non-reproducible-wasm)` | Three WASMs available for sandbox tests | First build failed with ENOSPC during ABI compilation | **COULD NOT RUN to completion** |
| Tests after 6 | `cargo test` in `vault/` | 15 unit and 8 sandbox tests | Not executed: prerequisites failed; additionally, these tests generate keys/sign messages and sandbox accounts, conflicting with the explicit “sign nothing/create no accounts” restriction | **COULD NOT RUN within scope** |
| 7a | `./scripts/attack-corridor.sh --land 2>&1 \| tee evidence/attacks.txt` | Five named `PASS` lines; `result: 5 PASS, 0 FAIL` | Not executed: signs/broadcasts transactions and overwrites repository evidence | **COULD NOT RUN within scope** |
| 7b | Read `evidence/attacks.txt`; fetch five receipts; replay their calls using historical `eth_call` | `Expired`, `MoveLimit`, `UnapprovedMarket`, `BelowFloor`, `SellBudget` | Recorded text matches; all five receipts have status 0; read-only replays returned exactly `0x203d82d8`, `0x013da4fb`, `0x99b2f06d`, `0x4e7e2916`, `0x397b9774` | **PASS, read-only evidence** |
| 8a | `curl -s $E/transcripts`; `curl -s $E/transcripts \| jq -r '.transcripts[].name'` | Sorted index with documented fields | **693 unique, sorted entries**; all 693 downloaded sizes and SHA-256 values matched | **PASS** |
| 8b | `curl -s $E/transcripts/boot-1794264608-2286af92-v4-0022.json -o <tmp>`; supplied verifier on that file | Seven `PASS` checks, then `ALL PASS` | Exact expected result | **PASS** |
| 8c | `./agent/verify-transcript.sh agent/testdata/transcripts/boot-1793051811-26cc5edf-0001.json` | `ALL PASS` | Seven checks passed, followed by `ALL PASS` | **PASS** |
| 8d | Extended verification of live calls and archived scene transcripts | Valid signed responses pass; documented unsigned responses fail | **403 files checked: 358 passed, 43 `NO-CALL`, 2 failed**. The failures were the disclosed unsigned responses; both were unaccepted and marked `rules only` | **PASS for documented behavior; two individual signature checks FAIL as expected** |
| 9 | Read `evidence/protect-live.txt`; fetch receipt `0xde730226…` | Protective reduction returns LTV to approximately 40% after owner changes corridor | Successful `Reduced` event: LTV **49.96745% → 40.01515%**; repaid **3.849781 USDC** | **PASS, read-only evidence** |
| 10 | Read scene JSON/Markdown; fetch receipts; recompute net-value changes from saved balances | Gradual crash: −1.76/−1.76/−2.11; gap: −2.23 each; whipsaw: −0.59/−0.59/0.00; migration and exit evidence | Rounded changes matched. Migration/exit receipts matched. Gap reduce `0xbf743bbf…` reverted in block **54862969**, after liquidation | **PASS, read-only evidence; scenes not recreated** |
| 11 | Read `evidence/inject.txt`; hash and verify its referenced transcript | Hostile memo, bounded/no action, valid transcript | Hash `748c8c59…` matched; verifier `ALL PASS`; model chose `hold`, market `none` | **PASS for this recorded trial** |
| 12 | Read `evidence/revoke.txt`; v2 key/state views; documented `cast receipt 0x431aefa81689bd54a8d6e83faf27ab87731c7fc9e8e9492b057de25bc40c94cf --rpc-url https://sepolia.base.org` | No keys; agent/approval null; owner receives exactly 0.002 WETH | All matched. Withdrawal receipt status 1 contains the expected WETH transfer from v2’s Base address to its owner | **PASS, read-only evidence** |

## Discrepancies and verification gaps

1. **“With the runs” overstates public research availability.** Root [README line 149](/work/cmdk/README.md:149) describes injection research “with the runs,” but [research/README.md](/work/cmdk/research/README.md:13) explicitly says the underlying model runs remain private. The referenced `research_notes/...` replay commands and several run files do not exist in this checkout. This is disclosed in the research overview, but the root wording should be corrected.

2. **Research reproduction is not portable as written.** The dataset instructions assume Python 3.14.6, an initially absent `.venv`, large release downloads and macOS `-dynamiclib` compilation. `DATA.md` documents Linux substitutions, but these are not literal executions of the original commands. The available Python is 3.13.5.

3. **The text-only incident metric needs clearer naming.** Root README says text alone succeeded in **3/8**. The thinking-on results table reports **4/8 “useful exits”** but **3/8 exits before PONR**. The root figure agrees with the latter metric; readers should not confuse the two.

4. **The large replay’s “saved” count is not entirely attributable to intervention.** Its CSV supports 1,890 and 5,009 saved observed-liquidation episodes, but the stricter `saved_with_noaction_model_support` counts are **1,884 and 5,008**. These are simulated outcomes, not observed prevented liquidations.

5. **The transcript failure inventory is incomplete.** Besides the documented three errors, eight attestation failures and two missing signatures, the captured live index contains **six `no_answer` and one `invalid_schema` outcomes**. These were unaccepted; signature validity alone does not imply a usable model answer.

6. **No fresh end-to-end wallet flow was performed.** HTTP availability, app source and historical receipts support the submission, but they do not establish current interactive onboarding/closing reliability. Frontend installation failed because of disk capacity; browser tests were not completed.

7. **Full source-to-binary verification remains incomplete for NEAR and the agent.** Matching deployed hashes, source metadata and registry digests are meaningful, but do not replace the Docker rebuilds. Rust/frontend ENOSPC failures are environment limitations, not established submission defects.

8. **Several numerical claims were not independently reproduced.** This includes the 20-position/$2.65M backtest, thin-buffer keeper comparisons, injection/incident replay statistics, and the idealized $8,811-versus-$5,987 simulation. Supporting notes exist, but some inputs are private and the arithmetic illustration has no executable script.

9. **External market and cost figures remain unconfirmed in this screening.** Examples include Coinbase borrower/loan/liquidation totals, competitor adoption/TVL figures, cited exploit losses, stock/NEAR lending totals, the $0.70–1.00 monthly estimate and 2–3-cent action estimate. They should not be treated as independently verified judging evidence.

10. **Important limitations are accurately disclosed rather than missing features:** team-maintained test liquidity and mock scenarios; no outage backstop; oracle-relative rather than market-relative slippage protection; public owner-position linkage and transcripts; no demonstrated Intents/private funding; no live `open_from_stables`; and no audit. The successful checks do not remove these limitations.

## Preliminary score: **8.0/10**

The submission earns strong credit for a functioning NEAR-to-Base design, protocol-enforced agent restrictions, reproducible executor bytecode, passing tests, verifiable historical transactions and signed model evidence. The app and landing repositories make the submission more complete, although their interactive behavior was not verified here.

The remaining uncertainty concerns full vault/enclave rebuilds, unpublished research evidence, limited independent text corroboration and demonstrated dependence on operator-managed test infrastructure. Environment-blocked checks are recorded as uncertainty, not treated as failed functionality.

**This score cannot by itself reject the submission.**

## Questions for the human judges

1. Can the pinned Docker vault and agent builds be repeated on a sufficiently provisioned machine, using the deployed source commits?
2. Will the team provide the underlying incident/injection runs and sampled backtest inputs for independent review?
3. Can a human complete a fresh wallet onboarding, protection, revocation and withdrawal rehearsal through the deployed app?
4. How should the privacy track credit confidential inference while private funding and private transcript access remain future work?
5. Are the disclosed outage dependency, shared enclave and team-operated test liquidity acceptable for the prototype’s claimed scope, and what must change before mainnet?