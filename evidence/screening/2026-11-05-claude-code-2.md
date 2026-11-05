# AI screening rehearsal 2, Claude Code (5 Nov 2026, evening)

Run per DEPLOY.md step 11 on a fresh clone of commit ad4eb6a (after the four scenes of step 10 and the venue-stress migrate), from the same Mac as rehearsal 1 (near-cli-rs 0.30.1, cargo-near 0.22.0, Foundry 1.8.6, Docker Desktop 27.4.0). Tool: a Claude Code subagent (Opus) given the Q-017 prompt of `2026-11-05-prompt.md` with two additions: the tool list also named cargo and cargo-near, and the closing sentence read "Humans decide; your report helps them". Started 19:38 UTC, finished 19:59 UTC. The report below is verbatim; the clone was unchanged after the run (`git status --porcelain` empty). Rehearsal 1 scored 6.5; the items it raised were closed the same day. The items below were triaged the same evening (see the commit that adds this file).

---

Cloned commit `ad4eb6a` (Rebalancer state after the scenes …). Started 2026-11-05 19:38:45 UTC, finished 2026-11-05 19:59 UTC.

# Screening report: cmdk (NEAR HACKATHON), commit ad4eb6a

## Summary
1. All 12 "Verify it" steps were attempted. 9 pass as written. Step 7 needs the team's wallet key, so I could not run it, but I confirmed all five recorded reverts on-chain with `cast run`. Steps 9–12 are evidence files, and the transactions I sampled from them check out on-chain.
2. Three independent reproductions match:
   - The vault wasm, rebuilt in the pinned Docker image from commit `975b98d`, hashes to the live Global Contract hash.
   - The agent image, rebuilt twice with no cache, gives image ID `sha256:01027505…`. That equals the config digest of the registry image pinned in the measured compose file.
   - The TDX measurements (MRTD, RTMR0–3, os_image_hash, compose hash) match for both boots, including with a dstack-mr I built fresh myself.
3. Extra checks the README does not ask for also match: the deployed executor bytecode equals the local `forge build` of HEAD byte for byte (immutables masked), and the factory wasm rebuilt on the host gives the published hash.
4. Code matches the main security claims: the agent can call only reduce, migrate and exit, has no recipient, calls carry a 600 s deadline, and recipients are pinned to the caller. The on-chain quote checks, the corridor, budget and move limits, and the 1% oracle bound are all present. 64 Foundry tests pass (including 3 invariants) and 15 vault unit tests pass.
5. The main weak spots are documentation and evidence, not mechanism:
   - The research folders say that scripts, small data tables and runs are included, but they are absent.
   - Some research figures cannot be checked from the repository.
   - The README's default `ltv.sh` check now reads an empty live position, because the position moved to backup.
   - Unexplained vault `v3`; the README's Team section is unfilled.

## Reproducibility

| Item | What I ran | Result |
|---|---|---|
| Vault code hash | Separate clean clone at `975b98d` (the commit in the NEP-330 `link` from `contract_source_metadata`), outside any git repo: `./scripts/reproduce-hash.sh --reproducible --vault v1.factory.prime-measure-1792736012.testnet` (took 14 min, Docker contended by other builds) | `size 783124 bytes`, `sha256 (hex) def9671e099fe325a7ac8c43831de0334a3dc75221f6c302b17a893e0377e1ba`, `sha256 (base58) G1Q5ksFhGbgPTN4A2DuvgraYmsbRpfwb78B547gAKXWq`, `published G1Q5ks…`, `result MATCH`. **MATCH.** Cosmetic: the `toolchain` line prints the host's `rustc 1.95.0`, not the image's Rust 1.96.0. |
| Factory wasm | Host `cargo near build non-reproducible-wasm --locked` in `vault/factory` at HEAD. Factory source is unchanged since `52e0446`; only DEPLOY.md differs. cargo-near 0.22.0, rustc 1.95.0, macOS arm64. | 152851 bytes, sha256 `5315458701e4f52c9c033d05d492ec75d18076a128a0261f153434670971c863`. This equals the on-chain `Local Contract` hash and the README. **MATCH** (same OS and toolchain class as the team's, as the README itself warns). |
| Agent image | Separate clone at `fd0272f` (`sourceCommit`): `agent/build.sh screen2-agent`, two `--no-cache` builds, 5 min 06 s | `build 1/2: image sha256:01027505f3873dc6fff86ffac5819bb28dc45dcbc8760fdc929ed4474c0a6eb3 binary sha256 68e898a7…`, then `REPRODUCIBLE`. `docker buildx imagetools inspect --raw ghcr.io/cmdkfi/agent@sha256:479f98f3…` gives config digest `sha256:01027505…`. **MATCH.** Caveat: built on the same Docker Desktop host and buildx builder (`cmdk-repro`) the team used. Base images are pinned by digest. |
| Image digest to measured compose | Extracted `docker_compose_file` and `pre_launch_script` from `evidence/boot-1793877578-a405e917/app-compose.json` and diffed them | Byte-identical to `agent/docker-compose.live.yml` (image `@sha256:479f98f3…`, `--mode live`) and to `agent/pre-launch.sh`. `sha256(app-compose.json)` = `216124c1…`, which is the vault's approved `compose_hash`. |
| TDX measurements | `scripts/p3_reproduce_measurements.sh <work> evidence/<boot> dstack-0.5.12 1 2G` for both boots | Both end in `ALL MATCH`. The script reused a pre-existing local image `prime-dstack-mr:0.5.12` built by the team. I rebuilt it independently (`--no-cache`, tag `screen2-dstack-mr:0.5.12`, dstack v0.5.12 at `43f69f4`), and its `measure` JSON is byte-identical (`FRESH_EQUALS_PREBUILT`). |
| Quote binds the key | Parsed the a405e917 quote | report_data = `a405e917…ae115c` + 32 zero bytes. Its base58 is `C3H6CqBzBMxUEDcLyZHMqeZwBt9KNr5vnZv5tQ6ouHMu`, the key in `get_state.agent` and `list-keys`. MRCONFIGID = `01` ‖ `216124c1…`. |
| Executor bytecode | `forge build` of HEAD, compared with `cast code 0xA9bb…D18b` | Same length (16459 bytes). Equal including the metadata hash after masking the immutables (MORPHO = `0xBBBB…FFCb`, COOLDOWN = `0x3c` = 60). |
| Tests | `forge test` (fork of Base Sepolia); `cargo test --locked --lib` in vault | 64 passed, 0 failed (3 suites; the invariant suite runs `invariant_executorHoldsNothing`, `invariant_noViolation` and `invariant_callSummary`, 64 runs × depth 25). Vault: 15 passed. The README's "8 sandbox tests" were **not run**: they need wasm builds plus a near-sandbox binary download, which counts as a global install. |

## Feature verification

| Claim (README) | Evidence | Verdict |
|---|---|---|
| Agent key can call only `reduce`, `migrate`, `exit`, with no recipient | `vault/src/lib.rs:46` `AGENT_METHODS = "reduce,migrate,exit"`; `add_access_key_allowance` at :498 is the vault's only key addition. The live `list-keys` shows `only do ["reduce", "migrate", "exit"]`. The method signatures (:435, :446, :459) have no recipient. | Confirmed |
| The agent cannot reach tokens or Morpho, only executor agent selectors | `check_tx` :715–:769: Morpho and token calls are owner-only; `SEL_REDUCE/MIGRATE/EXIT` are agent-only; market ids must be allowlisted | Confirmed |
| Every signed call has a 10-minute deadline, enforced on both sides | `evm.rs:19` `DEADLINE_SECS = 600`; `check_tx` :790; executor modifier `before(deadline)`. Attack Expired was replayed: `[Revert] Expired()` | Confirmed |
| Executor sends tokens only to the caller; no admin | `Executor.sol`: every path uses `msg.sender`. No owner or admin, no upgrade. `_check` asserts the executor's balances are unchanged. The swap pays the pool directly. | Confirmed by reading; not formally proven |
| Swap fills no worse than oracle −1% | `CorridorMath.sol:11, :50` (`maxCollateralIn` = out / (0.99·price)); `uniswapV3SwapCallback` reverts with `Slippage` | Confirmed |
| Floor, daily sell budget, 2 moves/24 h, exit switch, unapproved market | `reduce` :245–301, `_recordMove` (MAX_MOVES = 2), `exit` checks `exitAllowed`, `_agentCorridor` reverts with `UnapprovedMarket`. All five attack txs were replayed on-chain with the matching named reverts. | Confirmed |
| A stale feed blocks open but not reduce or exit | `_requireFresh` is called in `open`, `openFromStables` and `migrate` (destination only); not in `reduce` or `exit` | Confirmed |
| Morpho callbacks accept only Morpho during a call | `onMorphoRepay` and `onMorphoFlashLoan`: `msg.sender != MORPHO \|\| !_locked` reverts | Confirmed |
| On-chain attestation: Intel signature, TCB UpToDate, debug off, approved MRTD/RTMR0–2, RTMR3 replay, compose and OS hash, report_data = caller key | `vault/src/attest.rs:90–145`; `register_agent` :471. Registration tx `8jAe8ZJms584nGJ8ymFvwAeuEbTCsT5uidZ7WCCDQX9S`: `register_agent`, 113.8 Tgas, log `agent registered: ed25519:C3H6Cq… until 1794482645` | Confirmed (code plus tx; I did not re-run dcap-qvl myself) |
| Key valid 7 days; re-registers 24 h before expiry | `REGISTRATION_SECS = 7*24*3600`; agent `REREGISTER_BEFORE_SECS = 86400`, `reregistration_due` | Code confirmed. Not observed live: expiry is 1794482645, about 6.9 days away. |
| Vault immutable: Global Contract by hash, no keys; keyless factory with a fixed hash | v1, v2 and v3 summaries show `Global Contract (by Hash) def9671e…`. v1 has 0 full-access keys; v2 and v3 are locked. Factory: `Account is locked (no access keys)`; `get_config.code_hash` = `G1Q5ks…`. No `deploy_contract`, `use_global_contract` or full-access key anywhere in `vault/src`. | Confirmed |
| `revoke_agent` deletes the key and clears approved in one tx | `lib.rs:419–425`. v2 now has 0 keys and `agent: null, approved: null`. Tx `FYeZcfVX…` is an `owner_call` on v2 with SuccessValue. | Confirmed |
| Withdraw moves funds only to the address the owner names | v2 withdraw tx `0x431aefa8…` (from `0xFC85…`): one WETH Transfer to `0x0B7E…`, value `0x71afd498d0000` = 0.002 WETH | Confirmed |
| Live agent `reduce` (protect-live) | Tx `0xde730226…`: from the vault address `0xB3CC…`, selector `reduce`, status 1. The `Reduced` event shows `protective: true`, LTV 49.97% → 40.02% (target 40% at the time). | Confirmed |
| Venue stress leads to an agent `migrate` with collateral and debt conserved | Tx `0xd597c432…`: from `0xB3CC…`, `migrate` selector, status 1. `movesInWindow(v1)` = 1. Live `ltv.sh --market backup`: collateral 0.008335601888990126 WETH, debt 7.729880 USDC. Transcript `…a405e917-v1-0032.json`: judge `elevated/leave_market/live`, proposal `migrate`, NEAR tx `DkoB3m4B…`, verifier `ALL PASS`. | Confirmed |
| Model answers are signed by NEAR AI Cloud's enclave, with nonce-bound attestation | 27 of 67 live transcripts give `ALL PASS`. The verifier checks the hashes, the `provider_tee` signed text, the EIP-191 signer against the recorded attestation address, and TCB `UpToDate`. As the README says, it trusts the recorded attestation verdict. | Partially confirmed (as disclosed) |
| Injection: the model held | `…v1-0002.json`: sha256 `748c8c59…` matches `evidence/inject.txt`. The request contains `DEADBEEF`; the answer parsed as `low/hold/none`; `proposal: null` | Confirmed (one run, one model) |
| Chain Signatures 11 of 16 on testnet | `v1.signer-prod.testnet state`: `parameters.threshold` = 11, 16 participants, `reconstruction_threshold` 11 for all 4 domains | Confirmed (mainnet 17 not checked) |
| Loop about every 2 s; judge every 6 h; utilization screen at 95% | `main.rs:171` `tick_secs: 2`, `DIGEST_SECS = 6*3600`; `logic.rs:10` `UTILIZATION_SCREEN = 0.95` | Confirmed |
| Text feeds polled "every few minutes" | `main.rs:61` `FEED_POLL_SECS = 60` | Minor mismatch (every minute) |
| Backtest, dataset and judge-replay figures | 2.65M, 1,890/1,895, 5,009/5,011, 21/30 and 2/30, $8,811/$5,987 and 0.059 BTC are all found in `research/*`. Rules 6/8, model 6/8, text 3/8 and 1/6 controls appear only in `research/judge-evidence/BRIEF.md:17`. | Partially supported. The "earlier in 5, 136 vs 25 min, 14 min" leads are not in the repo (the full write-up is cited but not included). The scripts and data needed to recompute anything are absent. |
| Business-model figures (Coinbase 53,000 borrowers, $1.56B, $394M, DeFi Saver, cost $0.70–1.00 a month) | Not checked against external sources | Not confirmed |

## Verify-it results

Environment notes:
- `cast` is not on PATH on this machine; I used `PATH=$HOME/.foundry/bin:$PATH`. `ltv.sh` finds it on its own.
- I used `RPC=https://base-sepolia-rpc.publicnode.com`.
- `<vault>` = `v1.factory.prime-measure-1792736012.testnet`, `<base_address>` = `0xb3ccd0ad7e5fca9d4c18cf59d19fab4703eee6fb`, `<executor>` = `0xA9bbabE6d75C5BA96CA095586284774c9D09D18b`.

| # | Command | Expected (README) | Got | Verdict |
|---|---|---|---|---|
| 1a | `near account list-keys <vault> network-config testnet now` | "exactly one key … a function-call key whose methods are `reduce`, `migrate`, `exit`; no full-access keys" | One key, `ed25519:C3H6CqBzBMxUEDcLyZHMqeZwBt9KNr5vnZv5tQ6ouHMu`, `only do ["reduce", "migrate", "exit"] function calls on v1… with an allowance of 1.9745581684395581 NEAR` | PASS |
| 1b | `near contract call-function as-read-only <vault> get_state …` | `owner` is the wallet (lower case); `base_address` is the derived address | `"owner": "0x0b7ed66f46fe83252a208563a88201ba60490d25"` (= `testWallet`), `"base_address": "0xb3ccd0ad…e6fb"` (= deployments) | PASS |
| 2a | `near account view-account-summary <vault> …` | `Global Contract (by Hash: SHA-256 checksum hex)` + the published hash | `Global Contract (by Hash: SHA-256 checksum hex)  def9671e099fe325a7ac8c43831de0334a3dc75221f6c302b17a893e0377e1ba` (= `vault_code_hash_hex`) | PASS |
| 2b | `./scripts/reproduce-hash.sh --reproducible --vault <vault>` (clean checkout of `975b98d`) | `sha256 (hex)` = summary hash, `sha256 (base58)` = `published`, `result           MATCH` | As expected; `result           MATCH` | PASS |
| 2c | Factory: no keys, `get_config` returns the vault hash, code `5315…c863` | — | `Account is locked (no access keys)`; `Local Contract … 5315458701e4…c863`; `"code_hash": "G1Q5ks…"`, `"count": 3` | PASS |
| 3a | `get_state` markets | `id`, `executor`, `slice_max`, `corridor_set`, `cooldown_secs` | 3 markets, each with `executor 0xa9bb…`, `slice_max "1000000000"`, `corridor_set true`; `cooldown_secs 120` | PASS |
| 3b | `cast call <executor> "corridors(…)" <base_address> <live id>` | `500000000000000000 [5e17]`, `700000000000000000 [7e17]`, `300000000000000000 [3e17]`, `200000000000000000 [2e17]`, `true`, `true`, `true` | Exactly that; backup and demo are identical | PASS |
| 4 | `./scripts/ltv.sh <vault>` | Two columns; ends `cross-check    MATCH with the "at lastUpdate" LTV (WAD)` | Ends `cross-check    MATCH with the "at lastUpdate" LTV (WAD)`. But the live market shows `(no position on this market)`, collateral 0, LTV 0. With `--market backup`: debt 7.729880 USDC, LTV 41.2019%, `ltvOf 412019273846598633`, MATCH. | PASS, but the default check is now trivial (see Discrepancies) |
| 5a | `cast call Morpho "isAuthorized(…)" <base_address> <executor>` | `true` | `true` (the retired prototype executor gives `false`) | PASS |
| 5b | `cast call <executor> "config(bytes32)(…)" <live id>` | Params, pool `0x5718…156c`, two feeds, `[1200, 86400 [8.64e4]]`; backup gives the same pool | `(…0x7D0e…, 860000000000000000 [8.6e17]), 0x57183717A087d2fe3Ad890873877244c3B96156c, [0x4aDC…fc7cb1, 0xd30e…5165], [1200, 86400 [8.64e4]]`; backup shows the same pool. The pool is fee 100, USDC/WETH, from the Uniswap factory `0x4752…` (`getPool` returns it). | PASS |
| 6a | `get_state` `approved` and `agent` | Measurement plus key and `expires_at` | `approved` = the six values in `deployments/testnet.json` (compose `216124c1…`); `agent.expires_at 1794482645` | PASS |
| 6b | `./scripts/p3_reproduce_measurements.sh <work> evidence/<boot> dstack-0.5.12 1 2G` | `os_image_hash MATCH`, MRTD and RTMR0–3 `MATCH`, `ALL MATCH` | Both boots: `os_image_hash MATCH (8db40cb2…)`, mrtd/rtmr0/rtmr1/rtmr2/rtmr3 `MATCH`, `ALL MATCH` | PASS |
| 6c | `sha256sum evidence/<boot>/app-compose.json` | = event-log `compose-hash` = MRCONFIGID after `01` | `216124c1…` = event `compose-hash` = `mr_config_id 01216124c1…` | PASS |
| 7 | `./scripts/attack-corridor.sh --land …` | 5 lines `attack … PASS`, then `result: 5 PASS, 0 FAIL` | Not run: it signs and sends Base txs with the team's wallet key. `evidence/attacks.txt` has the exact expected lines. I replayed all 5 txs with `cast run`: `Expired()`, `MoveLimit()`, `UnapprovedMarket()`, `BelowFloor()`, `SellBudget()`; all status false, from the test wallet to the executor. | COULD NOT RUN (needs a wallet key); recorded evidence verified on-chain |
| 8a | `curl -s $E/transcripts` | Index with `name, bytes, sha256, boot, vault, twin, loop` | 67 entries (66 `v1`, 1 `v2`) with those fields plus `path`. All 67 files downloaded; sha256 and bytes match the index. | PASS |
| 8b | `./agent/verify-transcript.sh <name>` | `PASS` lines then `ALL PASS <name>`; a loop without a judge call prints `NO-CALL judge outcome: …` | 27 give `ALL PASS`, 0 FAIL. 37 give `NO-CALL judge outcome: ` (empty outcome, judge `null`) and 3 give `NO-CALL judge outcome: error` (boot b52d4abb loops 1, 3, 5); exit code 3. The bundled prototype transcript gives `ALL PASS`. | PASS |
| 9 | `evidence/protect-live.txt` | Owner lowers the act line, agent `reduce`, Base tx, LTV at target | `set_corridor` 40/45 (Base `0x3a2de9a9…` status 1, from `0xB3CC…`); `Reduced` event in `0xde730226…`, `protective: true`, ltvAfter 4.0015e17; restore `0xc5e432a2…` status 1 | PASS |
| 10 | `evidence/scenes/` | Gradual 45% crash (unprotected liquidated past −41.9%), one-block gap −25% to −45%, whipsaw, venue-stress migrate with identical collateral and debt | Gradual: unprotected liquidated in `0xec71d2fc…` (Morpho `liquidate`, status 1) at oracle 1333.10 = −42.91%; AI and keeper not liquidated. Gap: all three liquidated; the agent's reduce `0xbf743bbf…` mined after its liquidation, status false. Whipsaw: none liquidated. Venue stress: migrate `0xd597c432…`, collateral diff 0 wei, debt diff 0 units. | PASS |
| 11 | `evidence/inject.txt` | Memo fed in; any proposal is one of three methods | Transcript hash matches the file; judge `hold/none`; no proposal | PASS |
| 12 | `evidence/revoke.txt` | revoke deletes the key and clears approved; list-keys goes from 1 to 0; agent stops; close; withdraw of exactly 0.002 WETH to the owner | v2 now: 0 keys, `agent null, approved null`; revoke tx SuccessValue; close `0x8c8949e3…` status 1; withdraw Transfer 0.002 WETH `0xFC85…` → `0x0B7E…`. The enclave log lines are not independently checkable. | PASS (on-chain parts) |

## Discrepancies
1. **Research folders overstate what is included.**
   - `research/liquidation-dataset/DATA_NOT_INCLUDED.md` says the folder holds "every script, and the data files under 5 MB" and that `data/counts_market_year.csv`, `data/headline.csv` and similar "are included". The folder actually holds only README.md, BRIEF.md, DATA_NOT_INCLUDED.md and `data/raw/step1_readme_original.md`.
   - `research/README.md` says the dataset and scripts are internal.
   - The README's "dataset and replay in `research/liquidation-dataset/`" and "`research/judge-evidence/README.md`, with the runs" do not match: `runs/` holds only `step3_discovery/models.md`.
2. **Incident-replay leads are not in the repo.** "Earlier in 5 of them (136 minutes versus 25 …, 14 minutes …)" cannot be found. Only BRIEF.md:17 supports rules 6/8, model 6/8, text 3/8 and 1/6 controls, and it cites `incident_replay_results.md`, which is not included. `judge-evidence/README.md`'s own outcome table (clean/retrieval, 0/6 control exits) measures something different.
3. **Step 4 default check is now trivial.** After the venue-stress scene (19:35 UTC) the v1 position is on the backup market. `./scripts/ltv.sh <vault>` (default live) returns an empty position, so its `MATCH` compares 0 with 0. The Limitations line "above its 7.7 USDC debt" is now true only on backup.
4. **Unexplained vault `v3`.** `v3.factory…` exists (factory `count: 3`) with owner `0x69a7c62974475f2a5af5006bc219e9a62098f714`, which is not the test wallet, and is not mentioned anywhere in the README.
5. **"One exit" per day versus per market.** The worst-case paragraph says "or one exit" per day. The code closes only the exited market for the agent (`closedForAgent[user][id]`), so one exit per approved market with a position is possible. The guarantee table's "one exit per market" is the accurate version.
6. **Feed poll interval.** "Text feeds every few minutes" does not match `FEED_POLL_SECS = 60` (one minute).
7. **Ambiguous threshold wording.** The guarantee table says "a threshold of 11 operators (16 on testnet, 17 on mainnet)", which reads as 16 being a threshold. On-chain the threshold is 11 and there are 16 participants. The "Why NEAR" paragraph phrases it correctly.
8. **Scene net values are not comparable across twins.** The twins start with different cash; whipsaw, for example, starts with 6.38, 2.26 and 6.96 dUSDC. Change from the start, which I computed:
   - Gradual crash: AI and keeper −1.760; unprotected −2.114.
   - Gap: all three −2.231.
   - Whipsaw: AI and keeper −0.586; unprotected ≈ 0.

   In every scene the AI twin and the deterministic keeper end with practically identical results, so the scenes show no model contribution. The README does not claim one there. Positions are about $4–5 of collateral.
9. **Undocumented error outcomes.** 3 transcripts (boot b52d4abb) show judge outcome `error`, and 37 have no judge call. The README describes only the no-call case.
10. **Step 8 command writes into the repo.** The README's `curl -s -O $E/transcripts/<name>` then `./agent/verify-transcript.sh <name>` saves the file into the checkout if run from the repo root, leaving the tree dirty, which breaks step 2b's clean-tree requirement if done first.
11. **Two "cooldown" values.** Executor `COOLDOWN` = 60 s (deployments `cooldownSeconds: 60`) and vault `cooldown_secs` = 120 are separate layers, but the README never says the two numbers differ.
12. **Deploy block mismatch.** `executor/deployments-2026-11-04.json` has `"block": 54354897`, but the code first appears at block 54354973 (= `executorDeployBlock`).
13. **Team section unfilled.** "Team, license, contact" still reads `_To fill before submission._`
14. **Screening prompt differs.** The repo's `evidence/screening/2026-11-05-prompt.md` differs from the prompt I was given: its tool list omits cargo and cargo-near, and it says "a low AI score alone cannot reject" instead of "your report helps them".
15. **Not confirmed:** the mainnet MPC figure (17), the business-model and market figures, the cost per vault, the TEE.fail, Bankr and Bloom incident details, and the enclave log lines quoted in `revoke.txt` and `inject.txt` (they are not in any public artifact I could fetch).

Side effects on this machine, outside the clone:
- One `near` call printed "Migrating config.toml from V4 to V5…", meaning the CLI rewrote its own user config file.
- Cargo and Foundry caches were used, and Docker images `screen2-agent:repro-{1,2}` and `screen2-dstack-mr:0.5.12` were created.
- Extra clones were made under the screener's scratch directory (`vault-975b98d`, `agent-fd0272f`, `exec-test`, `work-a405`, `tx`). The vault and agent reproductions need a clean tree at another commit, outside any git repo.
- The main clone stayed clean (`git status --porcelain` empty).

## Preliminary score: 8.5 / 10
- **For:**
  - Every reproducibility claim I could test held, bit for bit: vault wasm, factory wasm, agent image, TDX measurements (with an independently rebuilt measurer) and executor bytecode.
  - The live state matches the README's view steps exactly.
  - The security model is implemented as described, in code I read: protocol-scoped agent key, deadline, caller-pinned recipients, on-chain quote verification, corridor limits.
  - Tests pass, and the negative paths are proven by real on-chain reverts.
  - The README is unusually candid about limits (no backstop, unaudited, trusted attestation verdict in transcripts, testnet-only feeds owned by the team).
- **Against:**
  - Research claims that carry the "why it matters" story (backtest, the 20,479-liquidation replay, the model's added value) cannot be recomputed from the repo, and two notes say the material is included when it is not.
  - Demos are tiny and on team-controlled markets, oracle and liquidator.
  - The scenes show no difference between the AI agent and the deterministic keeper.
  - Some README checks have drifted from the live state (step 4, `v3`).
  - The submission is incomplete in places (Team section).
- This is a screening score only; the decision is for the judges.

## Questions for the human judges
1. Who owns vault `v3` (owner `0x69a7…f714`), and is it part of the submission?
2. Will the team provide the scripts and small result tables that `DATA_NOT_INCLUDED.md` says are included, and the `incident_replay_results.md` behind the "136 vs 25 minutes" claim? The README offers extracts on request.
3. The scenes show the AI agent and the deterministic keeper with practically identical outcomes. Is there evidence beyond the offline replay that the model adds value in the live build? The venue-stress `migrate` was triggered by a judge result plus the utilization screen.
4. Should step 4 be read against the backup market now that the live position has migrated, or should the README name the market?
5. Is the README's per-day "one exit" bound meant per market, as the code allows?
6. The transcript verifier trusts the agent's recorded verdict on the model's TDX quote. Do the judges want the raw NEAR AI attestation independently re-verified for at least one transcript?
7. The README's Team section is unfilled. Will it be completed before the deadline?
