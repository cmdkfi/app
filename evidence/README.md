# Evidence: what is here and what has been verified where

One page for a reviewer who cannot run everything: which artifact was rebuilt from source and compared, on which machine, and which checks need no Docker at all. "Reproduced" means built again from the named commit on another machine and compared byte for byte; "identity" means the deployed bytes compared with the published hash. Every run below started from a fresh clone of the named commit; dates are 2026.

| Artifact | Published value | Reproduced from source | Identity and provenance, no Docker needed |
|---|---|---|---|
| Vault wasm (NEAR global contract by hash) | SHA-256 `def9671e099fe325a7ac8c43831de0334a3dc75221f6c302b17a893e0377e1ba`, base58 `G1Q5ksFhGbgPTN4A2DuvgraYmsbRpfwb78B547gAKXWq`; source commit `975b98d`; image `sourcescan/cargo-near:0.21.1-rust-1.96.0` pinned by digest | MATCH with `scripts/reproduce-hash.sh --reproducible`: Mac (Apple silicon, amd64 emulation) with Docker, 10 Nov (7 min 22 s with the image cached, 26 min without), 11 Nov and four times on 12 Nov (20 to 40 min) | `view_code` bytes hash to the published value; `contract_source_metadata` names the commit and the image: also in a Linux container without Docker, 10 and 11 Nov |
| Factory wasm | SHA-256 `5315458701e4f52c9c033d05d492ec75d18076a128a0261f153434670971c863`, 152,851 bytes; host build, source `vault/factory/` unchanged since `52e0446` | Not reproducible by design (host toolchain); the same hash on macOS arm64 with cargo-near 0.22.0 and Rust 1.95.0, 5 Nov, and again on 12 Nov | No access keys and `get_config` returning the vault hash: checked on every run |
| Agent image (`ghcr.io/cmdkfi/agent`) | manifest `sha256:e72564a8…`, image id `sha256:d3b51d67…`, binary SHA-256 `0a8f47c3…`, source commit `4171c10` | Two uncached builds identical to the published image id and binary hash: Mac with Docker, 10 Nov, 11 Nov and 12 Nov | Registry manifest, image configuration and the binary layer's hash match the manifest: Linux container, 11 Nov |
| Enclave measurements, boot `2286af92` | compose SHA-256 `db4f508517a01b31986dc9ec10eefd7491eb249cc209fb6b395c449e91c1dfc4`; MRTD, RTMR0–3 and OS hash in `boot-1794264608-2286af92/` | `ALL MATCH` with `scripts/p3_reproduce_measurements.sh`: Mac with Docker, 10, 11 and 12 Nov | OS image hash against the dstack release; compose hash against the event log and the quote's MRCONFIGID; RTMR3 replayed from the event log: Linux container, 10 and 11 Nov |
| Executor, Base Sepolia `0xA9bbabE6d75C5BA96CA095586284774c9D09D18b` | Blockscout-verified, solc 0.8.28, 200 runs, viaIR | Source files identical to the checkout; runtime bytecode (16,459 bytes) and creation bytecode rebuilt and equal: 10, 11 and 12 Nov (Foundry, no Docker needed) | — |
| Web app (`cmdkfi/cmdk-app` `main`) | deployed at https://testnet.cmdk.fi | Build of `main` byte-identical to the live bundle and config: 10 Nov, 11 Nov (c1d28f5) and 12 Nov (1db9392) | — |
| Landing (`cmdkfi/landing`) | https://cmdk.fi | Build of 68e91ec identical to the live HTML: 11 Nov | — |

Test suites: executor 64, vault 15 unit and 8 sandbox, agent 65 (1 ignored), all passing on the Mac on 10 Nov and again on 12 Nov; the web app has 174 unit tests and 124 browser scenarios (110 run on the desktop project and 14 are skipped there: the phone notice and the sandbox on mobile), run by its CI on every push and passing on the Mac and in Docker on 12 Nov. In a Linux container without Docker: executor 64, vault 15 unit and 8 sandbox, agent 65, web app 174 unit; there the browser suite, in 3 GB of memory, passed 108 with two close-flow scenarios timing out under parallel load, both passing when rerun alone. The sandbox tests sign locally, so a reviewer under a "sign nothing" rule skips them.

"Verify it" steps 1 to 12 of the README: all PASS or corroborated read-only on every run from 10 to 12 Nov. Step 7 sends transactions; its five reverts are on chain (`attacks.txt`), reproduced by read-only replay of the recorded calldata and 5 of 5 on a local anvil fork with a fresh key (`scripts/ATTACKS.md`), 10 to 12 Nov. Transcripts: every entry of the live index matched its hash and size each time (690 on 10 Nov, 747 on 11 Nov, 912 on 12 Nov).

## Folders

- `attacks.txt`: the five executor reverts of step 7, with transaction hashes.
- `boot-<id>/`: one folder per enclave boot (four boots, 4 to 9 Nov): `app-compose.json`, the quote, the event log, the registration and the measurements, as served by the enclave at the time.
- `deploy/`: deployment logs (executor, factory, vaults), 3 to 6 Nov.
- `incident-2026-11-06-phala-kms.md`: the one infrastructure incident, with what it changed.
- `inject.txt`: step 11, a hostile feed item and the signed transcript of the judge's answer.
- `protect-live.txt`: step 9, the protective reduce on a live position.
- `revoke.txt`: step 12, revocation, close and withdrawal on vault v2.
- `scenes/`: step 10, the price scenes, the venue stress and the exit, each with its data file, transaction table and transcripts.
- `analysis/`: outputs of the automated checks of 12 Nov 2026 (below), with the harnesses used.
- `transcripts/`: the archived transcript index of the first enclave (4 to 6 Nov) and the transcripts the scenes cite.
- `venue/`: the venue setup and the pool-band rebalancing logs of 3 Nov.

## Automated checks (12 Nov 2026)

Free, open-source tools run on the code as deployed, at commit `48d1ff1`, with every output in `analysis/` (local paths removed, nothing else edited). Not an audit: these tools match known patterns and fuzz stated properties; the vault's signature and attestation logic and the corridor accounting still need a human review.

- Solidity executor: Slither 0.11.6, Aderyn 0.6.8 (9 results) and Semgrep's `p/smart-contracts` rules (28). Slither could not build its IR for `reduce`, `migrate` and `ltvOf`; the cause, found by test, is a nested library call (`M.ltv(…, M.value(…))`) in exactly those three, so it ran again on an analysis copy with the inner call hoisted (`analysis/slither-analysis-copy.patch`, three lines, same semantics) and covered every function: 59 results, no High. None of either run's results survived reading at the cited line: the reentrancy warnings meet a transient lock on every entry point and on the callbacks, the "uninitialized" state is a mapping, the unsafe cast is unreachable, the Uniswap callback is bound to the executor's own swap, and the first run's "dead code" claim about the functions that record sales and moves came from its IR failure. The one item a paid audit would ask about: the two `approve` return values the constructor ignores, harmless on WETH and USDC.
- Corridor arithmetic: Medusa 1.5.1 fuzzed the seven properties of `CorridorMath` for 30 minutes, 51.8 million calls, no failure; Halmos 0.3.3 proved the two linear ones (`corridorValid`, `remainingBudget`) for every input and timed out, without a counterexample, on the six that multiply and divide symbolic values.
- Executor on the fork: the invariant suite at 6000 runs of depth 50 (CI runs 64 of 25) broke once on a dust position, a 1-unit debt repaid in full by a protective reduce after its collateral's value rounded to 1 unit; the cap "repay at most the debt" working as designed, the test corrected the same day, and a second 6000-run pass held all invariants.
- Rust: cargo-audit's advisories in the vault all sit in the sandbox tests' dependencies, including the `crypto-bigint` timing advisory (version 0.5.5, host test chain only; the wasm carries the patched 0.7.10); the contract path carries only near-sdk's own unmaintained allocator, a warning. The agent's one advisory is the same `crypto-bigint` 0.5.5, through a signer path in `dstack-sdk` that the agent never calls. clippy with the pedantic and nursery groups reports nothing at the default level in the vault or the agent. Rustle, the one NEAR-specific analyzer, pins a 2023 toolchain and cannot build against near-sdk 5.

