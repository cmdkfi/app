# Costs per vault and fee benchmarks — checked 2026-10-21

Rates: ETH $2,459.80, NEAR $5.19 (CoinGecko 2026-10-21 11:13 UTC).

## Costs
- Phala Cloud tdx.small (1 vCPU, 2 GB): $0.058/hr ≈ $42.34/mo + 20 GB disk ≈ $2.03/mo → ≈ $44.4/mo. Billed per machine; one enclave can serve many vaults (capacity ~1,000 positions at 5-min ticks is an estimate, untested). https://cloud.phala.com/about/instance-types
- NEAR AI Cloud, Private TEE tier (per 1M tokens in/out): GLM 5.3 Flash $0.08/$0.50 → $0.00031 per call at 2K in / 300 out; Qwen 3.6 35B A3B $0.17/$1.10 → $0.00067; deepseek-v3.2 (3P TEE) $1.10/$1.10 → $0.0025. https://near.ai/models
- Base: 0.008–0.009 gwei today; deleverage tx (Morpho repay with callback + Uniswap v3 swap + withdrawCollateral) estimated 300–450K gas → ≈ $0.009 (≈ $0.09 at 10x gas). Not measured. https://basescan.org/gastracker
- NEAR: account using a Global Contract by hash ≈ 0.005–0.012 NEAR staked (estimate) one-time; `v1.signer.sign` takes 1 yoctoNEAR deposit, a vault call that signs ≈ 0.0015–0.003 NEAR (≈ $0.01) per Base transaction (estimate from observed sign receipts).
- Per vault per month at 5-min ticks, 4 on-chain actions, shared enclave: ≈ $0.2–0.6 with the model called only when a rule pre-check fires; ≈ $3 with the model every tick (GLM); ≈ $22 with deepseek every tick. Single-vault enclave ≈ $45. RPC/indexer costs not included.

## Fee benchmarks
- DeFi Saver: automation fee 0.05% of transaction size per triggered automation, plus swap service fee 0.25% (uncorrelated), 0.10% (correlated), 0.01% (stables); gas charged to the position; monitoring free; Morpho on Base minimum debt $500. https://help.defisaver.com/features/automation/fees
- Coinbase crypto-backed loans: Morpho cbBTC/USDC market on Base, 5.18% borrow APY today, $1.60B borrowed, market fee 0; separate Coinbase fee unverified.
- Aave Labs interface swap fees: 0 bps debt swaps, 15 bps correlated, 25 bps other.
- Kamino: flash loan fee 0.001% per tx; reserve spread 11–20% of borrower interest.
- Vaults: Morpho V2 large vaults mostly 0% management, 0–25% performance; Yearn V3 default 10% performance, 0% management.
