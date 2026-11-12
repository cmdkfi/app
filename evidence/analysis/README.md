# Automated checks on the deployed contracts and the agent (12 Nov 2026)

What free, open-source tools find in the code as deployed: the executor at `0xA9bbabE6d75C5BA96CA095586284774c9D09D18b` (source `executor/src/`), the vault (`vault/`, source commit `975b98d` of the published hash) and the agent (`agent/`), all at commit `48d1ff1`. This is not an audit and is not presented as one: these tools match known patterns and fuzz stated properties; the logic that matters most (the vault's owner-signature and attestation checks, the calldata it builds, the corridor accounting) is what a human review is for. Every tool ran on a copy of the repository; nothing in the contract code changed. The files in this folder are the tools' outputs, trimmed only of local paths.

| Tool | Version | Target | Result | File |
|---|---|---|---|---|
| Slither (Trail of Bits) | 0.11.6 | executor, 7 contracts, 102 detectors | 60 results: 1 High, 22 Medium, 30 Low, 6 Informational, 1 Optimization; none survived reading (below) | `slither.txt` |
| Aderyn (Cyfrin) | 0.6.8 | executor `src/` | 2 High, 7 Low; none survived reading | `aderyn.md` |
| Semgrep, `p/smart-contracts` rules (Decurity) | 1.185.0 | executor `src/` | 1 Warning (false positive), 27 Info (style and gas) | `semgrep.txt` |
| Medusa (Trail of Bits), assertion mode | 1.5.1 | `CorridorMath` through `MedusaMath.sol` | 7 of 7 properties hold over 51.8 million calls in 30 minutes, 4 workers | `medusa.txt`, `MedusaMath.sol` |
| Halmos (a16z), symbolic execution | 0.3.3 | `CorridorMath` through `Halmos.t.sol` and `HalmosNarrow.t.sol` | 2 properties proved for every input in range (`corridorValid`, `remainingBudget`); 6 timed out with no counterexample, on full and on 64-bit domains | `halmos.txt`, the two harnesses |
| Foundry invariants | forge 1.8.6 | `executor/test/Invariant.t.sol` on the Base Sepolia fork | 6000 runs of depth 50 (300,000 calls; CI runs 64 of 25): one break, explained below; a second 6000-run pass with the test corrected held all invariants | `forge-invariant-long.txt` |
| cargo-audit (RustSec) | advisory DB of 12 Nov | vault workspace (487 crates), agent (508) | advisories listed below; none in a contract path beyond near-sdk's own allocator | `cargo-audit-vault.txt`, `cargo-audit-agent.txt` |
| cargo clippy, pedantic and nursery groups | rustc 1.95.0 | vault workspace, agent | 0 warnings at the default level; 134 and 345 style items (counts by lint) | `clippy.txt` |
| Rustle (BlockSec), NEAR static analyzer | v2.0 (2023) | vault | could not run: it pins rustc 1.67 and LLVM 15, near-sdk 5.29.1 requires Rust 1.93 | – |

## Executor: every High and Medium, read at the line

| Finding | Where | Reading | Verdict |
|---|---|---|---|
| Slither High `uninitialized-state`: `_sales` never initialized | `Executor.sol:71` | A nested mapping to a fixed array of `Sale` slots, written field by field; mappings have no initializer | False positive |
| Slither Medium `reentrancy-no-eth` (3); Aderyn High H-1 "state change after external call" (21 instances) | `reduce`, `exit`, `migrate`, `close`, `open`, constructor | Every external entry point carries `nonReentrant`, a transient lock (lines 148 to 152). The callbacks `onMorphoRepay`, `onMorphoFlashLoan` and `uniswapV3SwapCallback` require the lock to be held and the sender to be Morpho or the pool bound for that swap. External calls go to Morpho (immutable), the market oracle, the Chainlink feeds and the Uniswap pool fixed per market in the constructor. State written after the calls (cooldown stamp, session markers, `closedForAgent`) matters only to the same caller's next transaction | Accepted design: the lock replaces checks-effects-interactions ordering |
| Slither Medium `uninitialized-local` (3): `repaid` in `close` and `exit`, `borrowed` in `_borrowTo` | lines 231, 368, 524 | Zero unless there is debt to repay or room to borrow; used in events only | Intended zero |
| Slither Medium `unused-return` (16); Aderyn L-5 (5) | constructor `approve` ×2, `MORPHO.borrow` ×2, tuple reads of `market()`, `position()`, `latestRoundData()`, `repay` | The two constructor `approve` calls ignore the boolean; WETH, USDC and dUSDC return true, and a failed approval would make every later Morpho pull revert, which the testnet record shows does not happen. `borrow`'s return is not needed: the debt is re-read from Morpho and compared with the target. The rest keep the tuple fields they use | Benign; the `approve` return is the one line a paid audit would ask to wrap in `require` |
| Aderyn High H-2 unsafe cast `uint192(collateral)` | line 589 | Collateral in wei; 2^192 wei is about 6e39 ETH | Unreachable |
| Semgrep Warning `uniswap-callback-not-protected` | line 420 | The callback reverts unless `msg.sender == _pool`, a transient variable set only for the duration of the executor's own swap (lines 543 to 570) | False positive |
| Aderyn L-7 unsafe ERC20 operation | `_pull`, `_transfer` | `_call` (line 621) checks the call succeeded, decodes a returned boolean when present and requires code at the token address: the SafeERC20 checks | Equivalent to SafeERC20 |
| Slither Informational `dead-code`: `_recordSale`, `_recordMove`, `_activeCollateral` never used | lines 504, 583, 593 | Called at lines 275, 296 and 315 | False positive; worth noting because, if true, the sell budget and move limit would never be recorded |

Low and Informational: `timestamp` (7) is the cooldown and 24-hour window logic; `calls-loop` (8) is the constructor's per-market setup and the feed freshness loop over at most two feeds; `reentrancy-benign` and `reentrancy-events` (15) are the lock argument again; naming (2) is the upper-case immutables; `low-level-calls` (1) is the token helper; `cache-array-length` (1) is gas; Aderyn L-1 to L-4 and L-6 are literals, a loop with a revert, an immutable candidate, array length caching and the zero-initialised locals; the 27 Semgrep Info items are style and gas patterns.

## The invariant run's one break: a dust position

`invariant_noViolation` broke once in 6000 runs with "protective reduce went below target". Foundry shrank the sequence to four calls (replayed with full traces in `forge-invariant-long.txt`):

1. The alt-market oracle moves to $231.
2. A position opens with 32,750,779,856 wei of the test token (3.3e-8 tokens, worth 7 loan units, 0.000007 USDC) and borrows 1 loan unit (LTV 14.3%).
3. The oracle drops to $41. The collateral is worth 1.34 loan units, which `value()` rounds down to 1; debt is 1; LTV reads exactly 100%, above the 70% act line.
4. A protective `reduce`: `repayToLtv(1, 1, 0.5e18)` is 1, capped at the debt of 1, so the whole debt is repaid; the pool sells 24,392,683,172 wei for that unit, inside the 1% bound of 24,636,610,002, leaving 26% of the collateral and no debt. LTV after is 0; the test expected at least the 50% target.

Reading: `repayToLtv` is capped at the debt, so a position whose collateral is worth no more than its debt at the oracle (LTV at or above 100%) is repaid in full. For a position of real size that state is a loan already past Morpho's 86% liquidation line, where a full repay that leaves the owner the remaining collateral and no debt is the best available outcome. The contract is unchanged; the test now accepts `debtAfter == 0` on a protective reduce (commit of 12 Nov). A second 6000-run pass of depth 50 with that correction held all three invariants: the executor holds no tokens after any sequence, no corridor rule is violated, no caller exceeds the move limit.

## Rust: advisories and lints

cargo-audit on the vault workspace: 9 advisories and 14 warnings (2 unmaintained, 12 "unsound"). All of `rustls` (4 advisories), `rustls-webpki`, `zstd` and `zstd-safe` (3 advisories and 10 unsound warnings), `lru`, `rustix` and `unsafe-libyaml` arrive through `near-workspaces`, a dev-dependency used by the sandbox tests only; none of them is in the wasm. Two items are in the contract: `crypto-bigint` 0.7.10 (RUSTSEC-2026-0387, timing variability in operations meant to be constant time) through `dcap-qvl → p256/ecdsa`, used to verify the TDX quote's ECDSA signatures over public data, so there is no secret whose timing could leak; and `wee_alloc` (unmaintained) through `near-sdk` 5.29.1 itself. Agent, 508 crates: one advisory, `crypto-bigint` 0.5.5 (the same RUSTSEC-2026-0387) through `alloy-signer` (via `dstack-sdk`), and two unmaintained compile-time macro crates, `derivative` and `paste`. The agent signs Base transactions through Chain Signatures and NEAR transactions with an ed25519 function-call key; whether any secp256k1 operation with a secret runs through `alloy-signer` is a question for the next agent build, when the upstream fix lands.

clippy with the pedantic and nursery groups: vault 134 warnings, agent 345, all style (pass-by-value, lossless casts, docs, `must_use`, `use_self`), zero at the default level. The truncating casts flagged in the vault are in `tests/sandbox.rs`; the one `unchecked_time_subtraction` in the agent (`main.rs`, the tick loop's sleep) is guarded by `if el < every` on the line before.

## How to repeat

```sh
# Solidity (from executor/, after `git submodule update --init`)
pip install slither-analyzer==0.11.6 semgrep && slither . --exclude-dependencies --filter-paths "lib/|test/|script/"
brew install cyfrin/tap/aderyn && aderyn --src src .
semgrep --config p/smart-contracts src/
# Medusa: copy MedusaMath.sol into src/, `medusa init`, set targetContracts to ["CorridorMathMedusa"] and timeout 1800, `medusa fuzz`
# Halmos: copy Halmos.t.sol into test/, `pip install halmos==0.3.3`, `halmos --contract CorridorMathHalmos --solver-timeout-assertion 300000`
# Long invariant run: raise `default.invariant.runs` and `.depth` in the forge-config lines of test/Invariant.t.sol, then `forge test --match-contract ExecutorInvariant`
# Rust (from vault/ and agent/)
cargo install cargo-audit && cargo audit
cargo clippy --all-targets -- -W clippy::pedantic -W clippy::nursery
```
