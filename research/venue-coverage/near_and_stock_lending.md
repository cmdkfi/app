# Where NEAR and tokenized stocks can be lent/borrowed — checked 2026-10-21

Source: live reads today (NEAR RPC, Aurora RPC, 1Click token list, Morpho/Aave/Venus/Kamino APIs, DefiLlama yields).

## Tokenized stocks on NEAR
- No NEAR lending market lists stocks: Rhea Lend main (25 assets) and meme market (12), Templar (~$0.8M), Nearlend (~$0); Aurora's Aurigami (14 markets) and Bastion (5).
- Stocks exist on NEAR since near.com x Ondo launch 2026-09-22 (20 assets, non-US, KYC to deposit per FXStreet): Ondo GM tokens bridged from BNB as NEP-141; supplies tiny (TSLAon 35.1, NVDAon 72.8, SPYon 13.3, GOOGLon 20.2, AAPLon 10.1, MSFTon 2.2), 85–99% held in intents.near. https://www.near.org/blog/tokenized-stocks-rwas-ondo
- Public 1Click token list (205 tokens) has no stocks; only RWA is XAUT.
- Ondo GM tokens have almost no lending anywhere: Morpho markets <$1K each; Frankencoin ~$4.2M SPYon on Ethereum.

## NEAR token outside NEAR
- No NEAR lending on Aave V3 (24 markets incl. Horizon), Morpho (by symbol and bridged addresses), Venus (all pools), Kamino (48 markets), or any DefiLlama lending pool (16,164 pools scanned). Only DEX pairs (Uniswap v3 Ethereum NEAR-USDC ~$0.93M etc.).
- Exception: Aurora (EVM on NEAR). Aurigami auWNEAR live, ~13.2K wNEAR cash (~$69K); Bastion cNEAR paused (withdraw/repay only).
- Not checked: small permissionless vaults below DefiLlama thresholds, HyperEVM lenders.
- Rhea Lend on NEAR: wNEAR ~3.38M supplied (~$17.6M), ~0.506M borrowed, ~15% utilization, borrow APR ~2.1% before rewards. Rhea Lend TVL $205M, borrowed $26.5M.

## Stock-collateral lending venues today
| Venue | Chain | Size |
|---|---|---|
| Kamino xStocks Market | Solana | ~$23.4M stocks posted, ~$5.2M USDC borrowed, max LTV 30–73% |
| Kamino Sentora xStocks | Solana | ~$4.3M posted, ~$2.7M PYUSD borrowed |
| Kamino Superstate Opening Bell | Solana | ~$22.2M FWDI posted |
| Jupiter Lend | Solana | ~$9M (SPYx ~$5.4M) |
| Aave V4 Equities Hub | Base | 7 Coinbase stock tokens, CF 65–79%, caps ~$29M collateral, non-US; launched 2026-09-25 (The Block) |
| Frankencoin | Ethereum | ~$4.2M SPYon |
| Morpho | Ethereum | wSPYx/AUSD ~$0.52M |
| Venus bstocks | BNB | ~$0.39M |
| Robinhood Chain (LayerBank, Morpho) | Robinhood Chain | ~$0.29M |
- Aave Horizon holds no stocks (funds/treasuries only).
- Context: ~$111M tokenized stocks in DeFi across 15 apps on 2026-08-15 (CryptoBriefing).

## Liquidation vs deleverage simulation (2026-10-21)
Script: 0.1 BTC at $100k, $5k borrowed, Morpho LLTV 86% (liq at $58,140, LIF 1.0438, full liquidation), agent sells at LTV 70% back to 60%, 0.3% slippage.
| Path | Morpho unprotected: BTC / value after recovery | Agent: BTC / value after recovery |
|---|---|---|
| $100k→$55k→$100k | 0.0099 / $5,987 | 0.0552 / $8,505 |
| $100k→$40k→$100k | 0.0099 / $5,987 | 0.0307 / $7,250 |
| $100k→$75k→$100k | 0.1 / $10,000 | 0.1 / $10,000 (no action) |
| $100k→$60k→$100k (cost case, no liquidation) | 0.1 / $10,000 | 0.0552 / $8,505 |
| $100k→$65k→$100k (cost case) | 0.1 / $10,000 | 0.074 / $9,245 |
At the bottom the two are close (~$5.5k vs ~$6.0k at $55k); the gap is in the recovery. Value includes the $5k USDC the user holds.

### With re-levering on recovery
Same model, plus: when LTV falls to 50% on the way up, borrow and buy BTC back to 60% LTV (debt capped at the original $5k).
| Path | Value after recovery: no re-lever | with re-lever |
|---|---|---|
| $100k→$65k→$100k | $9,245 | $9,509 |
| $100k→$60k→$100k | $8,505 | $9,075 |
| $100k→$55k→$100k | $8,505 | $9,077 (unprotected Morpho: $5,987) |
| $100k→$40k→$100k | $7,250 | $8,309 (unprotected Morpho: $5,987) |
Re-levering narrows the cost case (a $60k dip: $1,495 behind → $925 behind) and widens the gain in crashes.

### With the design's bands (act 70%, target 50%; `01` §3.2, `02` Protect)
The tables above used 70%→60%. With the v1 bands, same model and 0.3% slippage, no re-lever (v1 has none):
| Path | Morpho unprotected: BTC / value after recovery | Vault: BTC / value after recovery |
|---|---|---|
| $100k→$75k→$100k | 0.1 / $10,000 | 0.1 / $10,000 (no action) |
| $100k→$65k or $60k→$100k (cost case) | 0.1 / $10,000 | 0.0591 / $8,811 |
| $100k→$55k→$100k | 0.0099 / $5,987 | 0.0591 / $8,811 |
| $100k→$40k→$100k | 0.0099 / $5,987 | 0.0350 / $7,613 |
Idealized: each deleverage is one transaction to target; real slices are capped by the slice maximum and cooldown.
