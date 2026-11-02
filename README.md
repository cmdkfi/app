# cmdk

Borrow against your crypto without selling it, guarded by an AI agent that cannot send your money anywhere.

**Status: NEAR HACKATHON build, 1–14 November 2026. Runs on NEAR testnet and Base Sepolia. Unaudited; mainnet after an audit.**

## Baseline disclosure

The commit tagged `pre-hackathon` is a prototype of the mechanics (executor on Base Sepolia, vault on NEAR testnet, agent in a Phala TDX enclave, owner and relayer scripts) written 26–31 October 2026, after the hackathon announcement of 19 October (HACKATHON.md Q-001). Everything after that tag is built in the hackathon window.

## Layout

| Directory | What it is |
|---|---|
| `executor/` | Solidity, Foundry. The immutable executor on Base that re-checks every agent action against Morpho's oracle and the owner's corridor |
| `vault/` | Rust, near-sdk. The vault on NEAR owned by the user's EVM wallet; verifies the agent's TDX quote, signs Base transactions through Chain Signatures |
| `agent/` | Rust. The agent that runs in a TDX enclave, watches the loan and proposes `reduce`, `migrate` or `exit` |
| `scripts/` | Python. Owner CLI, relayer, pool rebalancer, evidence scripts |

A full README for judges, with "Verify it" steps, replaces this file before submission.

## License

MIT, see `LICENSE`.
