# Liquidation dataset brief (research, before 1 November 2026)

Goal: extend the pre-hackathon backtest from 21 replayed positions in two crash weeks to a dataset covering about two years of liquidations and survivors on the markets PRIME targets, then use it for four things, in order: (1) calibrate the corridor defaults and slice sizes on 2024–2025 and report on a 2026 holdout the tuning never saw; (2) a similar-case retrieval table for the agent's judge; (3) a small risk model that runs inside the enclave, kept only if it beats the plain screens on the holdout; (4) nothing else: no LLM fine-tuning (the hosted model is fixed and attested; eight text incidents are not training data). Work step by step; stop and report after each step.

Read `CLAUDE.md` at the repo root first. This is research: scripts and data live under `research_notes/Liquidation dataset/`, nothing goes into any product code, and nothing is committed by you.

## Rules

- Free public sources only; no paid keys, no agentcash. If a source rate-limits, cache to disk and resume; never re-download what is cached.
- Python runs with `python3 -I`. Scripts go in `scripts/`, raw pulls in `data/raw/`, processed tables in `data/`, notes in `README.md` of this folder (results, provenance, assumptions labelled [ASSUMPTION], exact commands to reproduce).
- Every number in the note is computed by a script that is in the folder. Every table says which rows it came from.
- Secrets: none are needed. Do not read `~/.config/prime/*` or `~/.near-credentials`.
- Do not guess: if an API changes shape, read its schema; after two distinct failed approaches to a source, stop and write what you learned.
- Keep the existing dataset untouched (`research_notes/Voices on AI managed crypto loans/backtest_data/`); extend by new files.

## What exists (reuse)

- `research_notes/Voices on AI managed crypto loans/backtest.md`: method, §7 provenance and workarounds, §9 schema. Read §2, §7 and §9 before writing code.
- `research_notes/Voices on AI managed crypto loans/backtest_scripts/`: working fetchers and the simulator. `gql.py` (api.morpho.org/graphql), `rpc.py` (free RPC helpers: mevblocker/flashbots logs, drpc archive `eth_call` wrapped in Multicall3, 3 calls per batch), `fetch_morpho_liqs.py` (Morpho `marketTransactions` liquidations for market 0x9103c3b4e834476c9a62ea009ba2c884ee42e94e6e314a26f04d312434191836, Base cbBTC/USDC), `fetch_morpho_cases.py` (hourly `historicalState` per borrower and all own transactions), `fetch_aave_liqs.py` (Aave V3 Ethereum pool 0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2 `LiquidationCall` logs, topic 0xe413a321e8681d831f4dbccbca790d2952b56f977908e45be37335533e005286), `fetch_oracles.py` (Chainlink rounds via `getRoundData` on the proxy, phase 1, through Multicall3, because Base aggregators have read access control and drpc caps Base `eth_getLogs` at about 100 blocks), `fetch_binance.py` (klines 1m/1h/1d), `sim.py` and `prep.py` (the corridor simulator: scaled corridor, protective session, budgets, oracle rounds, slippage; policies `P2-…`). Copy what you need into `scripts/` and adapt; do not edit the originals.
- `backtest_data/`: `positions.csv`, `position_events.csv`, `oracle_rounds.csv`, `binance_1h.parquet` (1 Sep 2025 – 22 Oct 2026), `liquidations_*_all.csv` for the two windows, `sim_*.csv`.
- The design's intent for this data: `docs/01-design-and-feasibility.md` §4.9 (screens and features the agent computes) and §4.9.1 (calibration, risk model, retrieval). The corridor: §3 (target 50%, act 70%, floor 30%, sell budget 20%/day; v1 has no buy-back).

## Step 1: pull (stop and report when done)

Markets and windows:
- Morpho Blue, Base: cbBTC/USDC (market above) and WETH/USDC (find the market unique key with the highest borrow among WETH-collateral USDC-loan markets via the Morpho API `markets` query; record it), from each market's creation to 25 Oct 2026.
- Morpho Blue, Ethereum: WETH/USDC and wstETH/USDC (same method, chainId 1), same window.
- Aave V3 Ethereum core pool: every `LiquidationCall` from 1 Jan 2025 to 25 Oct 2026 (logs in 2,000-block steps with the existing helper; cache per step), decoded as in `fetch_aave_liqs.py`; keep all assets but mark the WETH/wstETH/WBTC/cbBTC-collateral, USDC/USDT-debt subset.
- Prices: Binance 1h klines for BTCUSDT and ETHUSDT over the whole window (extend the existing 1h file); 1m klines only for the ±3 days around each liquidation cluster you later replay (see step 2). Chainlink rounds for the Base BTC/USD and ETH/USD proxies and the Ethereum ETH/USD and BTC/USD aggregators over the whole window, with the existing round reader; this is the slow part, so cache per 10,000 rounds and run it in the background.
- Positions: for Morpho, every liquidated borrower's hourly `historicalState` from 14 days before its first liquidation to 7 days after, plus all its own transactions in the window; and **survivors**: borrowers whose hourly LTV exceeded 70% at any time in the window and who were not liquidated in the following 30 days (sample up to 2,000 per market by seeded random draw; record the seed). For Aave, hourly state needs archive calls (3 per batch on drpc), so pull hourly state for a seeded sample of 300 liquidated and 300 surviving (>70% LTV) accounts, and the full event list for everyone.

Outputs (`data/`): `liquidations.parquet` (one row per liquidation event, all venues, unified columns as in the existing `liquidations_*_all.csv` plus `venue`, `chain`, `market`, `collateral_asset`, `debt_asset`); `positions_hourly.parquet` (position × hour: `collateral`, `debt`, `ltv`, `oracle_px`, `binance_px`, `liquidated_within_6h`, `liquidated_within_24h`, `is_survivor_sample`); `borrowers.parquet` (one row per sampled borrower with venue, market, selection, first liquidation ts if any); `oracle_rounds_ext.csv`; `binance_1h_ext.parquet`. `README.md`: counts per market and year, how long each source took, what failed, assumptions.

## Step 2: features and calibration (stop and report)

Features at each hour, computed exactly as the agent would (§4.9 step 2): `ltv`, `dist_to_line` (liquidation LTV − ltv), price change over 1h/6h/24h, realized volatility over 24h (hourly log returns), `oracle_vs_binance_pct`, `utilization` and `borrow_rate` where the venue exposes them (Morpho API market history; Aave reserve data at the hour if cheap, else [ASSUMPTION] omitted), `hours_since_owner_action`, `position_size_usd`. Labels: liquidated within 6h, within 24h.

Calibration protocol: replay the corridor policy with the existing simulator on the liquidated positions (and the survivors, where the policy would also act) with the oracle rounds and 1m prices for the clusters; training set = events before 1 Jan 2026, test set = 2026 events. Grid over act line {65, 70, 75}, target {45, 50, 55}, sell budget {10, 20, 30}%, slice maximum {5, 10, 20}% of collateral, cooldown {60, 300} s. Choose on the training set by survival first, then equity versus actual, then cost (slippage, gas, sales). Report the chosen setting's results on the 2026 test set next to the design's defaults (70/50/20%). Produce the same headline table as `backtest.md` §1 for both sets, and a per-asset view (cbBTC versus ETH-type collateral) so we know whether one corridor fits all assets.

## Step 3: retrieval table (stop and report)

`retrieval.parquet`: one row per (position, hour) where any screen fires (`ltv > act`, `oracle_vs_binance_pct > 2`, `utilization > 95%`, feed stale), with the feature vector, the outcome (liquidated within 24h, hours to liquidation, penalty paid, what the owner did), and a short text summary line the judge can read ("cbBTC/USDC on Base, 2026-02-05 14:00, LTV 78%, price −12% in 24h, oracle 1.8% above Binance; liquidated 3h later, 12% of collateral seized"). Plus `retrieve.py`: given a feature vector, return the k nearest rows by standardized Euclidean distance, with their summaries. Show five example queries and what they return.

## Step 4: risk model (stop and report)

`sklearn` `HistGradientBoostingClassifier` on the step-2 features predicting `liquidated_within_24h`, trained on pre-2026 rows (positives and survivors), evaluated on 2026 rows: AUC, precision and recall at the operating point that fires on 5% of hours, and the lead time it gives before liquidation. Baseline to beat: the plain screens (`ltv > act line` alone, and `ltv > act or oracle gap > 2%`). Report both; if the model does not beat the baseline on the 2026 holdout by a margin that survives a bootstrap over positions, say so and recommend dropping it. If it does, save the model as a single file with its feature list and the exact training command, and state its size.

## Step 5 (optional, after step 3): Jev as a typed judge on the incident replay

TypeSafe's Jev is on OpenRouter (`typesafe/jev-1.13`; alias `~typesafe/jev-latest`; typed-choice output, 32K context, about $0.042 per million input tokens, $0 output; checked 26 Oct 2026). Re-run the incident replay's J2 inputs (`research_notes/Voices on AI managed crypto loans/incident_replay/`, script `backtest_scripts/incident_replay_llm.py`, judge prompts inside it) through Jev as a typed choice among {hold, reduce_light, reduce_to_target, leave_market} with risk ∈ {low, elevated, high, critical}, once without and once with the three nearest retrieval rows from step 3 as context. Score it with the existing `analyze` metrics (useful exits, lead versus the point of no return, control false alarms) next to the GLM judge and the rules. An OpenRouter key is needed: ask the founder for its path under `~/.config/prime/`, read it from the file, never print it. Record cost and latency. It stays an advisory, non-attested signal whatever the result.

## Reporting

After each step, write the results into `README.md` of this folder and reply with: what ran, counts, time taken, what failed, and the decisions the founder must make before the next step. Do not start the next step until told.
