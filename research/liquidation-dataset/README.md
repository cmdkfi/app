# Liquidation dataset — Steps 1–4

Step 1 is closed under the founder's reduced scope. Aave is excluded from Steps 2–4. Steps 2–4 are complete; stopped after Step 4. Generated 2026-10-27T17:52:25.814086+00:00.

All writes are confined to this folder. All Python commands use `-I`; sources are public and free. No secrets, mainnet transactions, commits, or changes to the original backtest files.

## Step 1 closure

The fixed cutoff remains 26 October 2026 00:00 UTC exclusive. Morpho event history begins at each market's creation.

Counts below come from `data/counts_market_year.csv`; hourly rows now include explicitly flagged missing-hour rows. Borrower counts are unique within market-year and are not additive across years.

| venue | chain | pair | year | liquidations | liquidated_borrowers | survivor_anchors | hourly_rows |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Morpho | 8453 | cbBTC/USDC | 2024 | 19 | 15 | 8 | 11841 |
| Morpho | 8453 | cbBTC/USDC | 2025 | 3451 | 3085 | 780 | 1944649 |
| Morpho | 8453 | cbBTC/USDC | 2026 | 10554 | 9556 | 1212 | 5159641 |
| Morpho | 8453 | WETH/USDC | 2024 | 319 | 299 | 135 | 219709 |
| Morpho | 8453 | WETH/USDC | 2025 | 1867 | 1663 | 523 | 1085982 |
| Morpho | 8453 | WETH/USDC | 2026 | 3910 | 3369 | 1342 | 2317229 |
| Morpho | 1 | WETH/USDC | 2024 | 0 | 0 | 0 | 0 |
| Morpho | 1 | WETH/USDC | 2025 | 0 | 0 | 0 | 0 |
| Morpho | 1 | WETH/USDC | 2026 | 36 | 22 | 62 | 41666 |
| Morpho | 1 | wstETH/USDC | 2024 | 113 | 32 | 132 | 80278 |
| Morpho | 1 | wstETH/USDC | 2025 | 104 | 93 | 228 | 158687 |
| Morpho | 1 | wstETH/USDC | 2026 | 106 | 95 | 123 | 103375 |

Survivor populations and selections come from `data/survivor_selection/*.json`. The original wstETH selection is unchanged; seed 20261026.

| chain | pair | population | screened | survivors |
| --- | --- | --- | --- | --- |
| 8453 | cbBTC/USDC | 73876 | 4387 | 2000 |
| 8453 | WETH/USDC | 23807 | 5470 | 2000 |
| 1 | WETH/USDC | 298 | 298 | 62 |
| 1 | wstETH/USDC | 1183 | 1183 | 483 |

Saved 11,123,057 requested position-hours for 20,892 borrower positions, including 971,570 hours with `missing_position_hour=true` and null balances. The original missing hours are retained; additional gaps belong to the replacement market. No position state is imputed. Labels are null when the snapshot or any requested hour through the label horizon is missing (`label_window_missing_6h` / `label_window_missing_24h`), or when negative follow-up is right-censored. Owner-event rows: 647,649.

### Aave disposition

The authorized additional attempt used 500-block steps and a hard two-hour deadline. It ended after 0.303 seconds: PublicNode returned HTTP 403, Ankr required authentication, and LlamaRPC returned HTTP 525. No further endpoint was tried. 2,248 original 2,000-block pages remain missing. Aave is excluded from Steps 2–4; the 911 previously collected target-subset events are preserved in `data/aave_target_subset_side_table.parquet`. Historical Aave rows retained in `liquidations.parquet` have `included_steps_2_4=false`. Raw all-asset logs remain cached; observed zeros outside these pages mean unknown coverage.

### Replacement Ethereum WETH market

Selected `0x94b823e6bd8ea533b4e33fbc307faea0b307301bc48763acc4d4aa4def7636cd`, created 2026-02-02T11:37:11+00:00, with 22,179,386.599694 USDC cumulative borrowing. Selection used creation date first among markets passing the large-market threshold, then cumulative volume for ties. It still has no pre-2026 history and is holdout-only. The original highest-current-borrow selection is retained in `data/markets_step1_original.json`.

[ASSUMPTION] “Large” means at least 10,000,000 USDC gross cumulative Borrow-event volume through the fixed cutoff, including repeat borrowing. The oldest markets are tiny by that measure. The threshold and all candidates are recorded in `data/eth_weth_selection.json`; selection is not based on replay outcomes.

Candidate rows come from the cached markets list and complete Borrow-event pages, summarized in `data/eth_weth_selection.json`:

| market | created_utc | cumulative_borrow_usdc | borrow_events |
| --- | --- | --- | --- |
| 0x7dde86a1e94561d9690ec678db673c1a6396365f7d1d65e129c5fff0990ff758 | 2024-01-17T07:36:59+00:00 | 29819.441895 | 61 |
| 0xf9acc677910cc17f650416a22e2a14d5da7ccb9626db18f1bf94efe64f92b372 | 2024-01-17T07:37:47+00:00 | 3816.493129 | 11 |
| 0x577ef4e9add200a2397c3eea1420584561c596d883b5361b18b0d35b62f3c724 | 2025-02-24T00:28:47+00:00 | 0.0 | 0 |
| 0xe3446542f42bf0f08582bf5cfdc1d94f3873cc9a91fc6009ca629d9c84d08c65 | 2025-11-27T01:04:59+00:00 | 0.0 | 0 |
| 0x94b823e6bd8ea533b4e33fbc307faea0b307301bc48763acc4d4aa4def7636cd | 2026-02-02T11:37:11+00:00 | 22179386.599694 | 1649 |
| 0x9cd7e77c4da7cb0216f7525af26271a2281f5fca5f16f78b83f36043d6e0a194 | 2026-04-12T10:39:59+00:00 | 0.0 | 0 |
| 0x7f8aa8ea57fd7d7fc2b7d807c81478eacf2f52919e5e0e0c72a2c375b8a5b42c | 2026-04-29T13:41:11+00:00 | 0.91 | 1 |
| 0x58fa2543a8e941ea9513ed2074977a3711567a87872e865d6c409ea33b999095 | 2026-06-12T06:56:11+00:00 | 7.100186 | 6 |
| 0x9fc79d4a7c4b0d51e41a7930e15b5187ec2c383215a28bb0b8576e658a6e89a1 | 2026-06-12T12:54:11+00:00 | 1.0 | 1 |
| 0x2d60540b94a0a0cb3cc6dad6c7360cf8a4a32273da0b4da73aea9810dd2a2c1e | 2026-06-16T13:37:47+00:00 | 500.0 | 2 |
| 0xb06f75b14cdfb5f518e6c4a2711a62bdf81dc7080a9491fe3a981339af14cb5c | 2026-10-02T21:32:47+00:00 | 1000.0 | 10 |
| 0xddac43c721a67ea9f19b1c57d2de12039c99f3f271c90123f7d8fc9edb591ee6 | 2026-10-07T12:52:23+00:00 | 101982223.863124 | 1 |

### Oracle handling

`data/oracle_phase_bounds.json` verifies proxy-to-aggregator mappings with `phaseAggregators(phaseId)` and first/last round timestamps. An overlap between consecutive aggregators does not prove the exact activation time: every round in the ambiguous interval is unusable. `usable_for_replay` is persisted in `data/oracle_rounds_ext.csv`; replay uses Binance in unresolved spans. The replacement ETH feed's first phase was unreadable; phase 2 was verified as active at the market's creation block, then fetched successfully.

[ASSUMPTION] Non-overlapping phase interiors are treated as usable under the requested timestamp-based boundary inference. Overlaps and gaps use Binance. Exact activation transactions were not fetched; the replacement market's creation-block phase check is separate historical evidence.

The actual market configurations are cached in `data/market_oracle_config.json`: Base WETH has a USDC/USD denominator; Ethereum WETH has its own ETH/USD feed; wstETH has an exchange-rate numerator and USDC/ETH denominator. Supplemental quote-feed rounds were pulled rather than silently substituting a standard ETH/USD stream.

[ASSUMPTION] stETH/ETH parity is accepted. Morpho's selected wstETH oracle prices through the wstETH/ETH exchange rate, so parity matches that venue's exchange-rate pricing rather than a secondary-market stETH discount. Historical share-ratio samples are held as of their observation; no future sample is interpolated.

Oracle counts come from `data/oracle_rounds_ext.csv`:

| chain | feed | rounds | usable_rounds |
| --- | --- | --- | --- |
| base | BTC/USD | 242227 | 228254 |
| base | ETH/USD | 248101 | 237166 |
| base | USDC/USD Morpho | 1057 | 1020 |
| eth | BTC/USD | 56450 | 36979 |
| eth | ETH/USD | 74283 | 48214 |
| eth | ETH/USD Morpho | 18792 | 10399 |
| eth | USDC/ETH Morpho | 9189 | 8373 |

## Assumptions retained from the initial pull

- [ASSUMPTION] “To 25 Oct 2026” includes the whole UTC day, represented by the exclusive cutoff above.
- [ASSUMPTION] A common price window starts at the beginning of 2024, before every selected market's creation. Extra prefix prices are retained.
- [ASSUMPTION] For the unchanged markets, highest borrow means current `borrowAssets` among the exact collateral/USDC token addresses, sorted descending at the cached selection snapshot. All loan assets have the same decimals; listed-only filtering is not imposed.
- [ASSUMPTION] Stablecoin loan units stand in for USD when deriving `oracle_px = collateralValue / collateral` and LTV from Morpho's hourly API state. The raw response retains integer amounts.
- [ASSUMPTION] Binance BTCUSDT represents cbBTC's underlying BTC; ETHUSDT represents WETH. Use the candle open at the exact snapshot hour, avoiding that hour's future close.
- [ASSUMPTION] wstETH Binance prices equal ETHUSDT times the most recent historical stETH share ratio, sampled with the original reader's block spacing and held until the next sample. This assumes stETH/ETH parity and omits any secondary-market discount. `binance_underlying_px` preserves unconverted ETHUSDT. No future ratio sample is used.
- [ASSUMPTION] Liquidated borrower windows are clipped to market creation and the dataset cutoff. No state is invented for missing API hours. Zero-collateral rows have null LTV/oracle unit price.
- [ASSUMPTION] API time-series timestamps denote the hourly state used for labels; no interpolation or future-hour close is substituted. Survivor eligibility is based on observed API hours and requires positive collateral value; missing hours and zero-value collateral do not establish eligibility.
- [ASSUMPTION] Survivor sampling uses a sorted historical Borrow-event population shuffled with seed 20261026; it accepts borrowers with a >70% hourly LTV and no liquidation in the following complete 30 days, stopping at 2,000 eligible borrowers. Closed accounts remain in the population. The survivor history window is 14 days before through 7 days after the qualifying hour. A later liquidation beyond those 30 days does not disqualify that earlier survival episode.
- [ASSUMPTION] A sample is a borrower within a venue/chain/market; one address on different markets is a different position. Overlapping liquidated/survivor hours are merged by position and timestamp.
- [ASSUMPTION] Liquidation labels refer to events strictly after the snapshot and within the inclusive horizon. Right-censored negatives are null; follow-up-observed flags distinguish them.
- [ASSUMPTION] Aave token decimals are taken only from the earlier fetcher's explicit token map. Unrecognized assets retain addresses and raw amounts instead of assuming decimals.

## Validation, failures and timings

`data/validation.json` records passed key, arithmetic, label, price continuity, requested-round and survivor checks: 11,123,057 hourly rows and 4,545 survivor episodes. These validate internal consistency, not missing-source completeness. Missing balances remain null.

Initial failure/recovery details are archived in `data/raw/step1_readme_original.md`: Morpho schema and pagination adaptations, rate limits, and the earlier Aave/phase-archive failures. New cached HTTP errors remain in `data/raw/`. Status files preserve measured elapsed time per command; jobs overlap and earlier overwritten invocations are timing lower bounds. See `data/source_times.csv` and the additional `data/closure_*_status.json` files.

## Reproduction

Use Python 3.14 and the versions pinned in `scripts/requirements.txt`. From the repository root:

```sh
python3 -I 'research/liquidation-dataset/scripts/close_step1.py' candidates
python3 -I 'research/liquidation-dataset/scripts/close_step1.py' select_market
python3 -I 'research/liquidation-dataset/scripts/close_step1.py' replacement_pull
python3 -I 'research/liquidation-dataset/scripts/close_step1.py' oracle_config
python3 -I 'research/liquidation-dataset/scripts/close_step1.py' extra_oracles
python3 -I 'research/liquidation-dataset/scripts/close_step1.py' replacement_oracle
'research/liquidation-dataset/scripts/.venv/bin/python3' -I 'research/liquidation-dataset/scripts/assemble.py'
'research/liquidation-dataset/scripts/.venv/bin/python3' -I 'research/liquidation-dataset/scripts/validate.py'
'research/liquidation-dataset/scripts/.venv/bin/python3' -I 'research/liquidation-dataset/scripts/report.py'
```

Successful exact HTTP pages are immutable and reused on restart. Do not restart the failed Aave attempt without a new instruction; `close_step1.py aave` is the recorded attempt command. All table assembly and validation are offline. No Step 3 or Step 4 code has run.

## Step 2 result

**Keep the existing defaults on this evidence.** The frozen grid winner caused 304 holdout episodes to be liquidated, versus 296 under the defaults. It also had more training liquidations (853 versus 801). No product settings were changed.

The best setting *inside the requested grid* is act **65%**, target **55%**, discretionary sell budget **20%**, maximum slice **20%**, cooldown **60 seconds**. The execution delay remains two minutes. It was frozen in `data/selected_policy.json` before the holdout command started. The defaults benchmark is 70% / 50% / 20%, with the original simulator's 25% slice and 120-second cooldown; those latter values lie outside the requested grid. All 162 grid settings and their training scores are in `data/calibration_grid.csv`.

### Hourly features and causal labels

`data/features_hourly.parquet` contains 11,123,057 rows. It has LTV, distance to LLTV, 1h/6h/24h price changes, 24h realized volatility, oracle-versus-Binance percentage, utilization, continuous borrow rate, hours since owner action, collateral-value position size, the two liquidation labels, and missing-state/window flags. There are 10,098,382 usable 6h labels and 9,946,510 usable 24h labels. Missing market-history rows: 0.

- [ASSUMPTION] Hourly price changes are decimal returns between observable hour-open prices. Realized volatility is the sample standard deviation of the preceding 24 hourly log returns, unannualized. wstETH uses the exchange-rate-adjusted price with only ratio samples available by that hour.
- [ASSUMPTION] `borrow_rate = log1p(borrowApy)` is continuous annual APR; utilization is a fraction. `position_size_usd` means collateral value at the venue oracle. `oracle_vs_binance_pct` is 100 times `(oracle / Binance - 1)`.
- Owner-action time uses only SupplyCollateral, WithdrawCollateral, Borrow and Repay events at or before the snapshot. Liquidator actions do not reset it. Missing balances and derived position features stay null; market-wide observations can still be present on flagged hours.

### Replay construction and selection

The plan covers 22,129 first-liquidation and selected-survivor episodes. 19,807 pass the missing-state/split/overlap screen; 19,794 also pass the real-state reconstruction check. The scored cohorts contain 5,936 training and 13,858 holdout episodes. These are episodes, not the full number of liquidation events or unique wallets.

- [ASSUMPTION] Replay windows are three days either side of the first liquidation or qualifying survivor hour, clipped to the available Step 1 context. They start/end on observed hourly boundaries. The minute-price pull covers those windows plus one completed minute for the first decision; overlapping windows share cached pages. This uses the brief's ±3-day replay-cluster span within the larger Step 1 histories.
- Whole replay episodes with a missing requested hour are excluded. Overlapping survivor/liquidation episodes on the same position are counted once, with liquidation episodes taking precedence. The original survivor cohorts remain in the dataset.
- Training episode ends must be before 1 January 2026; training survivor qualification also requires the full 30-day follow-up to end before that date. Holdout starts must be in 2026. No straddling episode informs selection.
- [ASSUMPTION] Endpoint reproduction requires collateral error no greater than max(1e-8 token, 1% of max(start, observed end)), and debt error no greater than max($1, 1% of max(start, observed end)). 13 cases failed this fixed check and were excluded. Their residuals are recorded in `data/replay_validation.parquet`; many are dust residuals on positions with zero start/end balances.
- [ASSUMPTION] The literal corridor is used, matching the grid's percentages; floor is 30%, no exchange signal, no buy-back. Interest uses the market's observed APR at episode start, held constant as in the reference simulator, without fitting it to later state. Gas is $0.03 per Base attempt and $5 per Ethereum attempt. Slippage, owner-flow clamping, protective sessions, the one-percent oracle execution ceiling, two-minute landing delay, and minute OHLC4 fills follow the original simulator. Protective sessions are not limited by the discretionary sell budget.
- Grid ranking is survival first (fewest liquidated episodes), then total episode-end equity versus actual, then slippage plus gas, then sales. Remaining exact ties favor settings closest to defaults, then their names. Ranking reads only training results. The headline continuation below is post-selection and never enters the ranking.
- The sample includes every liquidated borrower but only a seeded subset of qualifying survivors. It is enriched for stressful episodes and does not estimate unconditional liquidation probabilities across all loans. Market selection uses the specified present-day/historical-volume universe, not a point-in-time investment universe.

Exclusions below come from `data/replay_case_plan.parquet` and `data/replay_validation.parquet`; full case identities and reasons are retained.

| split | excluded_reason | episodes |
| --- | --- | --- |
| holdout | missing_position_hours | 1293 |
| holdout | overlaps_liquidation_episode | 8 |
| holdout | split_boundary_or_followup_crosses_2026 | 3 |
| train | missing_position_hours | 894 |
| train | overlaps_liquidation_episode | 17 |
| train | split_boundary_or_followup_crosses_2026 | 107 |
| holdout | actual_replay_endpoint_mismatch | 9 |
| train | actual_replay_endpoint_mismatch | 4 |

### Headline table

Rows come from `data/evaluation_results.parquet`, aggregated in `data/headline.csv`. “Saved” uses observed liquidations as the denominator; the stricter supported count additionally requires the no-action model to liquidate. Positive equity differences are not pure penalty savings: leverage reduction can benefit from falling prices and lag during rallies.

| metric | train default | train selected | holdout default | holdout selected |
| --- | --- | --- | --- | --- |
| Episodes | 5,936 | 5,936 | 13,858 | 13,858 |
| Observed liquidated episodes | 4,880 | 4,880 | 12,094 | 12,094 |
| Saved vs observed liquidation | 4,108 | 4,052 | 11,811 | 11,803 |
| Saved with no-action model support | 4,097 | 4,041 | 11,807 | 11,799 |
| Liquidated under policy, all episodes | 801 | 853 | 296 | 304 |
| Observed survivors liquidated in policy model | 29 | 25 | 13 | 13 |
| Liquidated only with policy vs no-action model | 2 | 1 | 5 | 7 |
| Real penalties, protocol formula | $6,702,481.63 | $6,702,481.63 | $11,464,541.89 | $11,464,541.89 |
| Policy penalties | $218,685.87 | $3,096.74 | $43.76 | $148.16 |
| Median extra collateral, % of start | +23.32% | +21.49% | +29.44% | +28.47% |
| Net equity vs actual at bottom | $23,716,786.11 | $23,726,676.03 | $36,193,214.56 | $34,235,282.11 |
| Net equity vs actual at episode end | $19,592,035.92 | $21,420,779.95 | $42,859,314.88 | $40,332,761.39 |
| Managed equity at cutoff | $59,116,479.68 | $57,671,957.57 | $123,782,079.80 | $120,567,164.69 |
| Actual leftover held passively at cutoff | $25,403,644.21 | $25,403,644.21 | $64,617,263.51 | $64,617,263.51 |
| Slippage | $208,605.92 | $170,271.04 | $186,679.76 | $171,405.17 |
| Gas | $58,594.44 | $169,442.69 | $35,560.26 | $77,551.29 |
| Sales | 55,804 | 95,256 | 139,035 | 196,120 |

“Who could not be saved” is retained case by case in `data/unsaved_cases.parquet`, including order blocking, reverts, lack of landed sales, and fallback-price flags. The no-action model misses 15 observed liquidation episodes and liquidates 142 episodes that had no real liquidation in the replay window. Those discrepancies are reported rather than credited as certain policy saves. Among episodes liquidated in both, model-minus-real first-liquidation timing has median -2 seconds; the model acts at the earliest allowed crossing, so timing can precede actual keepers.

- [ASSUMPTION] “Bottom” is the episode's lowest Binance minute low, marked at that bar's opening timestamp with the last recorded state; “end” uses the episode-end minute close, consistently with the original backtest.
- [ASSUMPTION] “At cutoff” continues each endpoint to 26 October 2026 00:00 UTC using completed hourly Binance closes as proxy oracle/market prices, minute ticks, the same fixed APR, and no later owner flows. Policy remains active; actual leftover is passive under the same liquidation model. wstETH uses fetched as-of ratios, not an extrapolated yield. This is a hypothetical continuation, not the borrowers' observed later equity. It is evaluated only after policy selection and can use 2026 prices for training-origin episodes without feeding them into calibration.

### Per-asset view

Rows from `data/per_asset.csv`, grouping the same eligible case-policy rows into cbBTC and ETH-type collateral. `saved_with_noaction_model_support` excludes cases the model would already save without intervention. The replacement Ethereum WETH market contributes only to holdout; ETH-type training comes from Base WETH and Ethereum wstETH.

| split | policy | asset_group | cases | observed_liquidated | saved_with_noaction_model_support | policy_liquidated | equity_vs_actual_end | slippage | gas |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| holdout | DEFAULT | ETH-type | 4260 | 3255 | 2994 | 263 | $5,296,271.94 | $27,269.97 | $32,483.37 |
| holdout | DEFAULT | cbBTC | 9598 | 8839 | 8813 | 33 | $37,563,042.94 | $159,409.79 | $3,076.89 |
| holdout | SELECTED | ETH-type | 4260 | 3255 | 2992 | 266 | $5,206,315.04 | $24,279.97 | $73,392.33 |
| holdout | SELECTED | cbBTC | 9598 | 8839 | 8807 | 38 | $35,126,446.35 | $147,125.20 | $4,158.96 |
| train | DEFAULT | ETH-type | 2403 | 1854 | 1107 | 760 | $6,001,946.64 | $132,242.99 | $57,452.07 |
| train | DEFAULT | cbBTC | 3533 | 3026 | 2990 | 41 | $13,590,089.27 | $76,362.93 | $1,142.37 |
| train | SELECTED | ETH-type | 2403 | 1854 | 1050 | 814 | $7,926,115.82 | $99,962.68 | $167,867.93 |
| train | SELECTED | cbBTC | 3533 | 3026 | 2991 | 39 | $13,494,664.13 | $70,308.36 | $1,574.76 |

`data/oracle_source_sensitivity.csv` separates episodes with any Binance fallback from those wholly on reconstructed venue feeds, using the same frozen policies. This is a coverage sensitivity report, not another fit.

### Validation and timing

The unchanged simulator is copied to `scripts/sim_reference.py`; its SHA-256 is recorded in `data/simulator_parity.json`. The compiled P2 path in `scripts/sim_grid.cpp` matched it in 112 comparisons across positions, policies and actual-event replays, including balances, liquidations, costs, sales and bottom marks. Full continuation paths also matched in 3 asset-specific checks. C++ only accelerates the research computation; Python entry points run with `-I`.

Independent feature checks passed on 11,235 sampled rows, while all 11,123,057 rows were checked for missing-state and label-mask consistency. Split and freeze-order assertions passed. Raw quotes and events remain cached. A continuation cache initially rejected a NumPy boolean; converting it to a built-in boolean fixed serialization, and completed parity checks were reused. No provider failure was hidden as an empty successful page.

`data/final_validation.json` additionally verifies unique case-policy keys, finite outputs, aggregate reconciliation, Step 1 closure before Step 2, and selection frozen before holdout execution.

Step 1 follow-up command timings (seconds, overlapping; source: `data/closure_*_status.json`):

| command | seconds |
| --- | --- |
| closure_extra_oracles | 67.041 |
| closure_inspect_feeds | 1.663 |
| closure_oracle_config | 0.5 |
| closure_phase_bounds | 1.12 |
| closure_replacement_oracle | 14.064 |
| closure_replacement_pull | 133.535 |
| closure_select_market | 11.096 |

Replacement-market components inside its total: liquidation pull 2.448s, histories 2.760s, transaction census/owners 72.667s, survivors 55.349s, remaining survivor transactions 0.310s. Cached existing-market pages were reused.

Step 2 measured command timings (seconds; `data/step2_source_times.csv`). Recorded wall span is 1,468.062s including setup gaps and overlapping work, not the sum of command times. Last-invocation timers omit the earlier failed serialization attempt.

| command | seconds |
| --- | --- |
| step2_case_plan | 69.075 |
| step2_continuation | 150.277 |
| step2_features | 14.369 |
| step2_market_history | 18.839 |
| step2_minute_prices | 254.517 |
| step2_replay_baseline | 95.176 |
| step2_replay_holdout | 74.025 |
| step2_replay_oracles | 2.509 |
| step2_replay_parity | 14.252 |
| step2_replay_select | 17.771 |
| step2_replay_train | 89.189 |
| step2_selected_results | 2.506 |
| step2_tables | 16.277 |

### Step 2 reproduction

After Step 1 closure, run these from the repository root using the local environment. Every successful request and completed case is reused on restart; `train_run_manifest.json` prevents reuse across incompatible grid settings.

```sh
'research/liquidation-dataset/scripts/.venv/bin/python3' -I 'research/liquidation-dataset/scripts/step2_data.py' market_history
'research/liquidation-dataset/scripts/.venv/bin/python3' -I 'research/liquidation-dataset/scripts/step2_data.py' case_plan
'research/liquidation-dataset/scripts/.venv/bin/python3' -I 'research/liquidation-dataset/scripts/step2_data.py' minute_prices
'research/liquidation-dataset/scripts/.venv/bin/python3' -I 'research/liquidation-dataset/scripts/step2_data.py' features
'research/liquidation-dataset/scripts/.venv/bin/python3' -I 'research/liquidation-dataset/scripts/replay.py' oracles
'research/liquidation-dataset/scripts/.venv/bin/python3' -I 'research/liquidation-dataset/scripts/replay.py' parity
'research/liquidation-dataset/scripts/.venv/bin/python3' -I 'research/liquidation-dataset/scripts/replay.py' baseline
'research/liquidation-dataset/scripts/.venv/bin/python3' -I 'research/liquidation-dataset/scripts/replay.py' train
'research/liquidation-dataset/scripts/.venv/bin/python3' -I 'research/liquidation-dataset/scripts/replay.py' select
'research/liquidation-dataset/scripts/.venv/bin/python3' -I 'research/liquidation-dataset/scripts/replay.py' holdout
'research/liquidation-dataset/scripts/.venv/bin/python3' -I 'research/liquidation-dataset/scripts/summarize_step2.py' selected_results
'research/liquidation-dataset/scripts/.venv/bin/python3' -I 'research/liquidation-dataset/scripts/summarize_step2.py' continuation
'research/liquidation-dataset/scripts/.venv/bin/python3' -I 'research/liquidation-dataset/scripts/summarize_step2.py' tables
'research/liquidation-dataset/scripts/.venv/bin/python3' -I 'research/liquidation-dataset/scripts/validate_step2.py'
'research/liquidation-dataset/scripts/.venv/bin/python3' -I 'research/liquidation-dataset/scripts/validate_results.py'
'research/liquidation-dataset/scripts/.venv/bin/python3' -I 'research/liquidation-dataset/scripts/report.py' complete
```

The compiler is the local C++17 toolchain (`c++`); no external simulation package or paid service is required. See `scripts/requirements.txt` for Python versions.

## Step 3 decisions accepted

The founder retained the default corridor, included flagged Binance-fallback episodes, retained both small Ethereum cohorts with cohort flags, and excluded the 13 reconstruction failures. The requested loss analysis follows, then Step 3.

## Why episodes are lost under the defaults

Source: DEFAULT rows of `data/unsaved_cases.parquet`, reconciled exactly to DEFAULT in `data/evaluation_results.parquet`. Denominators are **all 19,794 eligible DEFAULT episodes**, including saved and survivor episodes; the 13 reconstruction failures are excluded. These are simulated policy liquidations, not a claim that every loss occurred in the observed borrower history. Instrumented replays in `data/default_execution_diagnostics.parquet` reproduce every original output metric without changing policy math. `data/default_loss_analysis.parquet` joins episode definitions, execution diagnostics and loss flags; CSV breakdowns retain all denominators.

“Lost / episodes” and “loss_pct_of_group” mean losses divided by every eligible episode in that row’s group. “pct_of_split_losses” divides by all losses in the same train/holdout split. Split follows Step 2; year and ISO calendar week use the UTC **episode end**, not its liquidation time. Start size means collateral × replay oracle USD price, not debt or equity.

[ASSUMPTION] Primary reason is a mutually exclusive diagnostic hierarchy: an order block before first liquidation, then a revert, then no sale landed before first liquidation, then any fallback-price span, otherwise other. It is not a causal attribution. The overlapping flag table also shows every applicable condition; fallback coverage alone does not prove that fallback caused liquidation. For surviving episodes, execution flags cover the full episode. A landed sale after first liquidation cannot count as saving that episode.

[ASSUMPTION] The first decision is the first debt-bearing oracle update/minute tick, measured **before** the simulator’s liquidation check. This records opportunities lost to immediate liquidation rather than reporting the cleared post-liquidation balance. The gap is 100 × (oracle / last completed Binance minute close − 1) at the first blocked or reverted attempt before first liquidation; reverted fill gaps are also stored separately. Gaps are venue-equivalent for wstETH. LTV bands assign exactly 50% to 50–70%, exactly 70% to 50–70%, exactly 80% to 70–80%; zero-collateral starts are undefined. Size-band edges are lower-inclusive.

### The dominant ETH-type factor

**holdout: 244 of 263 ETH-type losses (92.78%) never queued a sale because every attempt passing the price-gap check was below the simulator’s $0.01 minimum order.** 242 of these started with less than $0.04 of collateral; with a 25% maximum slice, such a position normally cannot reach the minimum. The largest candidate sale among these failed attempts was $0.00917940. Median time from first debt-bearing decision to liquidation was 58.39 hours. Under the frozen defaults these dust positions had no executable sale opportunity, even when they had time; earlier triggering alone does not remove the minimum-size constraint. Separately, 8 ETH-type losses were already above act at the first decision and had under one hour to liquidation; 8 had less than the 120-second fill delay (including a liquidation before the first decision). These categories can overlap.

**train: 630 of 760 ETH-type losses (82.89%) never queued a sale because every attempt passing the price-gap check was below the simulator’s $0.01 minimum order.** 620 of these started with less than $0.04 of collateral; with a 25% maximum slice, such a position normally cannot reach the minimum. The largest candidate sale among these failed attempts was $0.00996364. Median time from first debt-bearing decision to liquidation was 28.41 hours. Under the frozen defaults these dust positions had no executable sale opportunity, even when they had time; earlier triggering alone does not remove the minimum-size constraint. Separately, 97 ETH-type losses were already above act at the first decision and had under one hour to liquidation; 93 had less than the 120-second fill delay (including a liquidation before the first decision). These categories can overlap.

This isolates the dominant **mechanical** factor in the training result; it does not establish that a different live executor could profitably rescue cents-sized debt. The default simulation’s sale-size floor is economically material to event-count survival. No dust filter, policy retuning, or Step 4 model has been introduced.

### Overlapping execution and opportunity flags

| split | flag | episodes | lost | loss_pct_of_group | pct_of_split_losses |
| --- | --- | --- | --- | --- | --- |
| holdout | order_blocking | 335 | 93 | 27.76 | 31.42 |
| train | order_blocking | 587 | 163 | 27.77 | 20.35 |
| holdout | revert | 92 | 0 | 0.00 | 0.00 |
| train | revert | 169 | 1 | 0.59 | 0.12 |
| holdout | no_landed_sale | 294 | 281 | 95.58 | 94.93 |
| train | no_landed_sale | 911 | 774 | 84.96 | 96.63 |
| holdout | fallback_price | 164 | 4 | 2.44 | 1.35 |
| train | fallback_price | 48 | 2 | 4.17 | 0.25 |
| holdout | other | 13114 | 11 | 0.08 | 3.72 |
| train | other | 4606 | 18 | 0.39 | 2.25 |
| holdout | dust_limited | 271 | 262 | 96.68 | 88.51 |
| train | dust_limited | 757 | 637 | 84.15 | 79.53 |
| holdout | above_act_under_1h | 10 | 10 | 100.00 | 3.38 |
| train | above_act_under_1h | 118 | 118 | 100.00 | 14.73 |
| holdout | below_120s_opportunity | 10 | 10 | 100.00 | 3.38 |
| train | below_120s_opportunity | 113 | 113 | 100.00 | 14.11 |

### primary reason

| split | primary_reason | episodes | lost | loss_pct_of_group | pct_of_split_losses |
| --- | --- | --- | --- | --- | --- |
| holdout | fallback price | 161 | 1 | 0.62 | 0.34 |
| holdout | no landed sale | 200 | 191 | 95.50 | 64.53 |
| holdout | order blocking | 335 | 93 | 27.76 | 31.42 |
| holdout | other | 13114 | 11 | 0.08 | 3.72 |
| holdout | revert | 48 | 0 | 0.00 | 0.00 |
| train | fallback price | 39 | 0 | 0.00 | 0.00 |
| train | no landed sale | 664 | 620 | 93.37 | 77.40 |
| train | order blocking | 587 | 163 | 27.77 | 20.35 |
| train | other | 4606 | 18 | 0.39 | 2.25 |
| train | revert | 40 | 0 | 0.00 | 0.00 |

### asset group

| split | asset_group | episodes | lost | loss_pct_of_group | pct_of_split_losses |
| --- | --- | --- | --- | --- | --- |
| holdout | ETH-type | 4260 | 263 | 6.17 | 88.85 |
| holdout | cbBTC | 9598 | 33 | 0.34 | 11.15 |
| train | ETH-type | 2403 | 760 | 31.63 | 94.88 |
| train | cbBTC | 3533 | 41 | 1.16 | 5.12 |

### market

| split | market_name | episodes | lost | loss_pct_of_group | pct_of_split_losses |
| --- | --- | --- | --- | --- | --- |
| holdout | Base WETH/USDC | 4041 | 253 | 6.26 | 85.47 |
| holdout | Base cbBTC/USDC | 9598 | 33 | 0.34 | 11.15 |
| holdout | Ethereum WETH/USDC | 45 | 1 | 2.22 | 0.34 |
| holdout | Ethereum wstETH/USDC | 174 | 9 | 5.17 | 3.04 |
| train | Base WETH/USDC | 2080 | 749 | 36.01 | 93.51 |
| train | Base cbBTC/USDC | 3533 | 41 | 1.16 | 5.12 |
| train | Ethereum wstETH/USDC | 323 | 11 | 3.41 | 1.37 |

### end year

| split | end_year | episodes | lost | loss_pct_of_group | pct_of_split_losses |
| --- | --- | --- | --- | --- | --- |
| holdout | 2026 | 13858 | 296 | 2.14 | 100.00 |
| train | 2024 | 376 | 90 | 23.94 | 11.24 |
| train | 2025 | 5560 | 711 | 12.79 | 88.76 |

### end ISO week

| split | end_iso_week | episodes | lost | loss_pct_of_group | pct_of_split_losses |
| --- | --- | --- | --- | --- | --- |
| holdout | 2026-W02 | 14 | 1 | 7.14 | 0.34 |
| holdout | 2026-W03 | 13 | 0 | 0.00 | 0.00 |
| holdout | 2026-W04 | 132 | 5 | 3.79 | 1.69 |
| holdout | 2026-W05 | 368 | 7 | 1.90 | 2.36 |
| holdout | 2026-W06 | 6132 | 112 | 1.83 | 37.84 |
| holdout | 2026-W07 | 571 | 0 | 0.00 | 0.00 |
| holdout | 2026-W08 | 7 | 1 | 14.29 | 0.34 |
| holdout | 2026-W09 | 90 | 3 | 3.33 | 1.01 |
| holdout | 2026-W10 | 19 | 0 | 0.00 | 0.00 |
| holdout | 2026-W11 | 30 | 0 | 0.00 | 0.00 |
| holdout | 2026-W12 | 22 | 0 | 0.00 | 0.00 |
| holdout | 2026-W13 | 5 | 0 | 0.00 | 0.00 |
| holdout | 2026-W14 | 40 | 1 | 2.50 | 0.34 |
| holdout | 2026-W15 | 18 | 0 | 0.00 | 0.00 |
| holdout | 2026-W16 | 19 | 1 | 5.26 | 0.34 |
| holdout | 2026-W17 | 18 | 0 | 0.00 | 0.00 |
| holdout | 2026-W18 | 6 | 0 | 0.00 | 0.00 |
| holdout | 2026-W19 | 14 | 0 | 0.00 | 0.00 |
| holdout | 2026-W20 | 19 | 0 | 0.00 | 0.00 |
| holdout | 2026-W21 | 204 | 3 | 1.47 | 1.01 |
| holdout | 2026-W22 | 431 | 1 | 0.23 | 0.34 |
| holdout | 2026-W23 | 2825 | 30 | 1.06 | 10.14 |
| holdout | 2026-W24 | 1471 | 111 | 7.55 | 37.50 |
| holdout | 2026-W25 | 22 | 1 | 4.55 | 0.34 |
| holdout | 2026-W26 | 390 | 2 | 0.51 | 0.68 |
| holdout | 2026-W27 | 153 | 5 | 3.27 | 1.69 |
| holdout | 2026-W28 | 27 | 2 | 7.41 | 0.68 |
| holdout | 2026-W29 | 22 | 0 | 0.00 | 0.00 |
| holdout | 2026-W30 | 12 | 0 | 0.00 | 0.00 |
| holdout | 2026-W31 | 26 | 1 | 3.85 | 0.34 |
| holdout | 2026-W32 | 29 | 0 | 0.00 | 0.00 |
| holdout | 2026-W33 | 34 | 1 | 2.94 | 0.34 |
| holdout | 2026-W34 | 18 | 0 | 0.00 | 0.00 |
| holdout | 2026-W35 | 27 | 0 | 0.00 | 0.00 |
| holdout | 2026-W36 | 40 | 2 | 5.00 | 0.68 |
| holdout | 2026-W37 | 40 | 1 | 2.50 | 0.34 |
| holdout | 2026-W38 | 51 | 5 | 9.80 | 1.69 |
| holdout | 2026-W39 | 24 | 0 | 0.00 | 0.00 |
| holdout | 2026-W40 | 2 | 0 | 0.00 | 0.00 |
| holdout | 2026-W41 | 278 | 0 | 0.00 | 0.00 |
| holdout | 2026-W42 | 119 | 0 | 0.00 | 0.00 |
| holdout | 2026-W43 | 76 | 0 | 0.00 | 0.00 |
| train | 2024-W02 | 1 | 0 | 0.00 | 0.00 |
| train | 2024-W03 | 1 | 0 | 0.00 | 0.00 |
| train | 2024-W04 | 1 | 1 | 100.00 | 0.12 |
| train | 2024-W07 | 3 | 0 | 0.00 | 0.00 |
| train | 2024-W10 | 1 | 0 | 0.00 | 0.00 |
| train | 2024-W12 | 4 | 0 | 0.00 | 0.00 |
| train | 2024-W16 | 4 | 0 | 0.00 | 0.00 |
| train | 2024-W18 | 2 | 0 | 0.00 | 0.00 |
| train | 2024-W22 | 1 | 0 | 0.00 | 0.00 |
| train | 2024-W24 | 1 | 0 | 0.00 | 0.00 |
| train | 2024-W26 | 1 | 0 | 0.00 | 0.00 |
| train | 2024-W27 | 34 | 28 | 82.35 | 3.50 |
| train | 2024-W28 | 25 | 11 | 44.00 | 1.37 |
| train | 2024-W29 | 1 | 1 | 100.00 | 0.12 |
| train | 2024-W30 | 4 | 0 | 0.00 | 0.00 |
| train | 2024-W31 | 3 | 2 | 66.67 | 0.25 |
| train | 2024-W32 | 113 | 16 | 14.16 | 2.00 |
| train | 2024-W34 | 2 | 0 | 0.00 | 0.00 |
| train | 2024-W35 | 17 | 4 | 23.53 | 0.50 |
| train | 2024-W36 | 14 | 2 | 14.29 | 0.25 |
| train | 2024-W37 | 20 | 4 | 20.00 | 0.50 |
| train | 2024-W38 | 4 | 0 | 0.00 | 0.00 |
| train | 2024-W39 | 1 | 0 | 0.00 | 0.00 |
| train | 2024-W40 | 16 | 5 | 31.25 | 0.62 |
| train | 2024-W41 | 2 | 1 | 50.00 | 0.12 |
| train | 2024-W42 | 3 | 0 | 0.00 | 0.00 |
| train | 2024-W43 | 8 | 6 | 75.00 | 0.75 |
| train | 2024-W44 | 9 | 3 | 33.33 | 0.37 |
| train | 2024-W45 | 5 | 1 | 20.00 | 0.12 |
| train | 2024-W46 | 2 | 0 | 0.00 | 0.00 |
| train | 2024-W47 | 6 | 0 | 0.00 | 0.00 |
| train | 2024-W48 | 14 | 1 | 7.14 | 0.12 |
| train | 2024-W49 | 10 | 3 | 30.00 | 0.37 |
| train | 2024-W50 | 4 | 1 | 25.00 | 0.12 |
| train | 2024-W51 | 13 | 0 | 0.00 | 0.00 |
| train | 2024-W52 | 26 | 0 | 0.00 | 0.00 |
| train | 2025-W01 | 3 | 0 | 0.00 | 0.00 |
| train | 2025-W02 | 8 | 1 | 12.50 | 0.12 |
| train | 2025-W03 | 29 | 10 | 34.48 | 1.25 |
| train | 2025-W04 | 23 | 16 | 69.57 | 2.00 |
| train | 2025-W05 | 229 | 138 | 60.26 | 17.23 |
| train | 2025-W06 | 177 | 34 | 19.21 | 4.24 |
| train | 2025-W07 | 8 | 2 | 25.00 | 0.25 |
| train | 2025-W08 | 1 | 0 | 0.00 | 0.00 |
| train | 2025-W09 | 111 | 11 | 9.91 | 1.37 |
| train | 2025-W10 | 123 | 17 | 13.82 | 2.12 |
| train | 2025-W11 | 128 | 19 | 14.84 | 2.37 |
| train | 2025-W12 | 1 | 0 | 0.00 | 0.00 |
| train | 2025-W13 | 1 | 0 | 0.00 | 0.00 |
| train | 2025-W14 | 108 | 56 | 51.85 | 6.99 |
| train | 2025-W15 | 571 | 259 | 45.36 | 32.33 |
| train | 2025-W16 | 5 | 3 | 60.00 | 0.37 |
| train | 2025-W17 | 2 | 1 | 50.00 | 0.12 |
| train | 2025-W19 | 4 | 2 | 50.00 | 0.25 |
| train | 2025-W20 | 5 | 2 | 40.00 | 0.25 |
| train | 2025-W21 | 20 | 0 | 0.00 | 0.00 |
| train | 2025-W22 | 7 | 1 | 14.29 | 0.12 |
| train | 2025-W23 | 18 | 0 | 0.00 | 0.00 |
| train | 2025-W24 | 4 | 0 | 0.00 | 0.00 |
| train | 2025-W25 | 19 | 7 | 36.84 | 0.87 |
| train | 2025-W26 | 73 | 13 | 17.81 | 1.62 |
| train | 2025-W27 | 9 | 1 | 11.11 | 0.12 |
| train | 2025-W28 | 11 | 0 | 0.00 | 0.00 |
| train | 2025-W29 | 10 | 0 | 0.00 | 0.00 |
| train | 2025-W30 | 20 | 3 | 15.00 | 0.37 |
| train | 2025-W31 | 12 | 2 | 16.67 | 0.25 |
| train | 2025-W32 | 29 | 2 | 6.90 | 0.25 |
| train | 2025-W33 | 16 | 1 | 6.25 | 0.12 |
| train | 2025-W34 | 33 | 0 | 0.00 | 0.00 |
| train | 2025-W35 | 89 | 1 | 1.12 | 0.12 |
| train | 2025-W36 | 60 | 1 | 1.67 | 0.12 |
| train | 2025-W37 | 9 | 0 | 0.00 | 0.00 |
| train | 2025-W38 | 15 | 3 | 20.00 | 0.37 |
| train | 2025-W39 | 56 | 2 | 3.57 | 0.25 |
| train | 2025-W40 | 10 | 1 | 10.00 | 0.12 |
| train | 2025-W41 | 11 | 0 | 0.00 | 0.00 |
| train | 2025-W42 | 488 | 7 | 1.43 | 0.87 |
| train | 2025-W43 | 237 | 6 | 2.53 | 0.75 |
| train | 2025-W44 | 12 | 0 | 0.00 | 0.00 |
| train | 2025-W45 | 576 | 45 | 7.81 | 5.62 |
| train | 2025-W46 | 178 | 0 | 0.00 | 0.00 |
| train | 2025-W47 | 1225 | 35 | 2.86 | 4.37 |
| train | 2025-W48 | 670 | 9 | 1.34 | 1.12 |
| train | 2025-W49 | 33 | 0 | 0.00 | 0.00 |
| train | 2025-W51 | 70 | 0 | 0.00 | 0.00 |
| train | 2025-W52 | 3 | 0 | 0.00 | 0.00 |

### start LTV

| split | ltv_band | episodes | lost | loss_pct_of_group | pct_of_split_losses |
| --- | --- | --- | --- | --- | --- |
| holdout | 50–70% | 2588 | 6 | 0.23 | 2.03 |
| holdout | 70–80% | 7908 | 211 | 2.67 | 71.28 |
| holdout | above 80% | 2974 | 64 | 2.15 | 21.62 |
| holdout | below 50% | 310 | 9 | 2.90 | 3.04 |
| holdout | undefined (zero collateral) | 78 | 6 | 7.69 | 2.03 |
| train | 50–70% | 1127 | 40 | 3.55 | 4.99 |
| train | 70–80% | 2261 | 197 | 8.71 | 24.59 |
| train | above 80% | 2275 | 540 | 23.74 | 67.42 |
| train | below 50% | 200 | 20 | 10.00 | 2.50 |
| train | undefined (zero collateral) | 73 | 4 | 5.48 | 0.50 |

### first decision above act

| split | above_act_at_first_decision | episodes | lost | loss_pct_of_group | pct_of_split_losses |
| --- | --- | --- | --- | --- | --- |
| holdout | no | 2912 | 21 | 0.72 | 7.09 |
| holdout | yes | 10946 | 275 | 2.51 | 92.91 |
| train | no | 1364 | 61 | 4.47 | 7.62 |
| train | yes | 4572 | 740 | 16.19 | 92.38 |

### LTV and first decision

| split | ltv_band | above_act_at_first_decision | episodes | lost | loss_pct_of_group | pct_of_split_losses |
| --- | --- | --- | --- | --- | --- | --- |
| holdout | 50–70% | no | 2556 | 6 | 0.23 | 2.03 |
| holdout | 50–70% | yes | 32 | 0 | 0.00 | 0.00 |
| holdout | 70–80% | yes | 7908 | 211 | 2.67 | 71.28 |
| holdout | above 80% | yes | 2974 | 64 | 2.15 | 21.62 |
| holdout | below 50% | no | 307 | 9 | 2.93 | 3.04 |
| holdout | below 50% | yes | 3 | 0 | 0.00 | 0.00 |
| holdout | undefined (zero collateral) | no | 49 | 6 | 12.24 | 2.03 |
| holdout | undefined (zero collateral) | yes | 29 | 0 | 0.00 | 0.00 |
| train | 50–70% | no | 1124 | 40 | 3.56 | 4.99 |
| train | 50–70% | yes | 3 | 0 | 0.00 | 0.00 |
| train | 70–80% | no | 2 | 0 | 0.00 | 0.00 |
| train | 70–80% | yes | 2259 | 197 | 8.72 | 24.59 |
| train | above 80% | yes | 2275 | 540 | 23.74 | 67.42 |
| train | below 50% | no | 197 | 20 | 10.15 | 2.50 |
| train | below 50% | yes | 3 | 0 | 0.00 | 0.00 |
| train | undefined (zero collateral) | no | 41 | 1 | 2.44 | 0.12 |
| train | undefined (zero collateral) | yes | 32 | 3 | 9.38 | 0.37 |

### start collateral size USD

| split | size_band_usd | episodes | lost | loss_pct_of_group | pct_of_split_losses |
| --- | --- | --- | --- | --- | --- |
| holdout | $0.04–<1 | 110 | 4 | 3.64 | 1.35 |
| holdout | $100–<1k | 2310 | 3 | 0.13 | 1.01 |
| holdout | $10k–<100k | 2246 | 2 | 0.09 | 0.68 |
| holdout | $1k–<10k | 2915 | 2 | 0.07 | 0.68 |
| holdout | $1–<100 | 5128 | 7 | 0.14 | 2.36 |
| holdout | < $0.04 | 277 | 271 | 97.83 | 91.55 |
| holdout | >= $100k | 794 | 1 | 0.13 | 0.34 |
| holdout | unknown | 78 | 6 | 7.69 | 2.03 |
| train | $0.04–<1 | 292 | 26 | 8.90 | 3.25 |
| train | $100–<1k | 707 | 6 | 0.85 | 0.75 |
| train | $10k–<100k | 999 | 4 | 0.40 | 0.50 |
| train | $1k–<10k | 898 | 3 | 0.33 | 0.37 |
| train | $1–<100 | 1537 | 28 | 1.82 | 3.50 |
| train | < $0.04 | 813 | 722 | 88.81 | 90.14 |
| train | >= $100k | 617 | 8 | 1.30 | 1.00 |
| train | unknown | 73 | 4 | 5.48 | 0.50 |

### first blocked/reverted sale oracle gap

| split | gap_band_pct | episodes | lost | loss_pct_of_group | pct_of_split_losses |
| --- | --- | --- | --- | --- | --- |
| holdout | 0.5–0.85% | 24 | 0 | 0.00 | 0.00 |
| holdout | 0.85–1% | 82 | 6 | 7.32 | 2.03 |
| holdout | 0–0.5% | 3 | 0 | 0.00 | 0.00 |
| holdout | 1–2% | 198 | 66 | 33.33 | 22.30 |
| holdout | 2–5% | 45 | 21 | 46.67 | 7.09 |
| holdout | <= 0% | 31 | 0 | 0.00 | 0.00 |
| holdout | not applicable | 13475 | 203 | 1.51 | 68.58 |
| train | 0.5–0.85% | 15 | 0 | 0.00 | 0.00 |
| train | 0.85–1% | 213 | 62 | 29.11 | 7.74 |
| train | 0–0.5% | 27 | 0 | 0.00 | 0.00 |
| train | 1–2% | 316 | 99 | 31.33 | 12.36 |
| train | 2–5% | 34 | 2 | 5.88 | 0.25 |
| train | <= 0% | 20 | 0 | 0.00 | 0.00 |
| train | >5% | 2 | 0 | 0.00 | 0.00 |
| train | not applicable | 5309 | 638 | 12.02 | 79.65 |

### Gap magnitudes among losses with a blocked or reverted sale

| split | count | min | median | max |
| --- | --- | --- | --- | --- |
| holdout | 93 | 0.86 | 1.10 | 2.46 |
| train | 163 | 0.87 | 1.16 | 2.12 |

Full numeric first-block, first-revert and fill-gap values, timing, owner-risk actions, and liquidation trigger kind are in `data/default_loss_analysis.parquet`. Dust attempts are distinguished from explicit price blocks. Other includes insufficient protection despite landed sales or subsequent owner flows; the data do not justify assigning every residual loss to one cause.

Diagnostics took 101.401s; 395,880 original simulator metrics were checked. No data source was queried. Reproduce with `python3 -I scripts/loss_analysis.py` followed by `python3 -I scripts/report_losses.py` from this folder using the local virtual environment.

## Step 3 — retrieval completed

The default corridor is retained: act 70%, target 50%, daily sell budget 20%, maximum slice 25%, cooldown 120 seconds and execution delay 120 seconds. `data/retrieval.parquet` contains one row per position/hour whenever any specified screen fires. Its outcomes are **observed Morpho events and owner actions**, not the counterfactual DEFAULT replay. Aave remains excluded. At the Step 3 stop, no Step 4 model or Step 5 judge call had been run.

### Counts and scope

Rows: `data/retrieval.parquet`, built from `data/features_hourly.parquet`; totals in `data/step3_build_status.json`. Flags overlap and must not be added together.

| measure | count |
| --- | --- |
| Input feature hours | 11123057 |
| Reconstruction-failure positions excluded | 13 |
| Hours excluded with those positions | 8568 |
| Screen-triggered hours retained | 4983502 |
| Unique retained positions | 20876 |
| Known positive 24h outcomes | 422144 |
| Known negative 24h outcomes | 4474502 |
| Unknown/masked 24h outcomes | 86856 |
| Retained hours with missing position state | 62838 |
| Incomplete feature vectors | 79953 |
| Rows eligible for nearest-neighbor search | 4879553 |
| Mature pre-2026 scaler/candidate rows | 1587760 |
| Binance-fallback flag at the hour | 54039 |
| Hours in a replay episode containing fallback | 11464 |
| Small Ethereum cohort hours | 253913 |
| Feed-staleness unknown/partly unknown | 54039 |

The parquet file is 434,269,072 bytes (414.15 MiB). Of the retained rows, 97,504 have no active positive-collateral/positive-debt balance. They are retained because a market-wide screen can fire even for an inactive position; `active_borrow` makes this explicit. 3,600,674 lie outside eligible six-day replay windows. Retrieval uses the full pulled hourly context, with `in_validated_replay_episode` distinguishing the overlap.

[ASSUMPTION] Excluding the 13 failed reconstructions means excluding **all hours of their 13 market/borrower positions**, not just the failed six-day intervals. This conservatively removes 8,568 source hours. Exact keys and windows are in `data/retrieval_reconstruction_exclusions.csv`. The small Ethereum cohorts remain: `cohort_flag` distinguishes Ethereum WETH (holdout-only) and wstETH (the unchanged 483-survivor census). No new survivor sampling was done.

Market/year counts below come from `data/retrieval_counts.csv`; positive outcomes count position-hours, so multiple hours may precede the same liquidation. `known_labels` excludes missing/censored windows; `index_eligible` additionally requires a complete feature vector and observed current state.

| split | year | market_name | hours | known_labels | positive_24h | index_eligible | fallback_hours |
| --- | --- | --- | --- | --- | --- | --- | --- |
| holdout | 2026 | Base WETH/USDC | 866444 | 858144 | 78762 | 857434 | 19943 |
| holdout | 2026 | Base cbBTC/USDC | 2378110 | 2359234 | 219296 | 2357521 | 29833 |
| holdout | 2026 | Ethereum WETH/USDC | 9822 | 9727 | 465 | 9706 | 0 |
| holdout | 2026 | Ethereum wstETH/USDC | 78627 | 68685 | 2015 | 66702 | 0 |
| train | 2024 | Base WETH/USDC | 60694 | 56388 | 5723 | 55802 | 0 |
| train | 2024 | Base cbBTC/USDC | 3048 | 2462 | 295 | 2288 | 0 |
| train | 2024 | Ethereum wstETH/USDC | 47495 | 29764 | 664 | 27682 | 4263 |
| train | 2025 | Base WETH/USDC | 431319 | 428343 | 38097 | 424698 | 0 |
| train | 2025 | Base cbBTC/USDC | 989974 | 988973 | 74632 | 988426 | 0 |
| train | 2025 | Ethereum wstETH/USDC | 117969 | 94926 | 2195 | 89294 | 0 |

### Screens, flags and no imputation

Overlapping screen counts from `data/retrieval_screen_counts.csv`:

| split | screen_ltv | screen_oracle_gap | screen_utilization | screen_feed_stale |
| --- | --- | --- | --- | --- |
| holdout | 3246940 | 0 | 442 | 132063 |
| train | 1473577 | 44 | 83057 | 162193 |

Screens are exactly LTV > 0.70, oracle/Binance percentage gap > 2 (a positive gap, not its absolute value), utilization > 0.95, or a known stale feed. Their four boolean columns identify why a row entered. The full Step 2 feature vector is preserved without replacing missing values. Missing rows can enter through market-wide utilization or staleness; their missing-state and label-window booleans remain visible and they cannot enter the nearest-neighbor candidate pool.

[ASSUMPTION] Staleness uses the last usable round as of the hour and the phase-boundary rules from Step 1. Operational thresholds are 1,200 seconds for Base BTC/ETH price feeds, 3,600 seconds for Ethereum ETH and USDC/ETH feeds, and 86,400 seconds for the Base USDC/USD quote feed and the fetched wstETH exchange-rate observation. These are declared research thresholds, not newly verified feed heartbeat configurations. `data/retrieval_feed_thresholds.json` records every component. An unresolved phase yields unknown component age, not fresh age; unknown alone does not trigger the stale screen. A known stale component can still trigger it when another component is unknown.

`binance_fallback` means the replay oracle reconstruction would require Binance at that hour; `episode_binance_fallback` means the hour lies in an eligible replay episode containing any fallback span. Both are retained and displayed in summaries. The original hourly Morpho `oracle_px` and its feature gap remain unchanged; these flags do not replace that observed feature with Binance. `feed_staleness_unknown` identifies incomplete age evidence.

[ASSUMPTION] The accepted stETH/ETH parity treatment is preserved. Morpho’s wstETH oracles price through the exchange rate, so parity matches the venue convention used in this reconstruction; wstETH/Binance comparisons use the fetched exchange rate as of the hour.

### Observed outcomes and summaries

The outcome window is `(hour, hour + 24h]`. `liquidated_within_24h`, liquidation-event count, collateral seized, debt repaid, penalty estimate and owner-action counts all use cached observed transactions. `hours_to_liquidation` is the lead to the first liquidation **within that 24-hour window**; it is null for negatives and unknown windows. Later events are not exposed by an outcome that has only matured for 24 hours. Owner actions count SupplyCollateral, WithdrawCollateral, Borrow and Repay separately, with a readable action string. Events timestamped exactly at the hour belong to prior/current state, not the future window.

[ASSUMPTION] `penalty_paid_24h_usd` is the contractual liquidation-incentive estimate: sum of actual repaid USDC × `[1 / (0.3 × LLTV + 0.7) − 1]`. USDC is treated as USD, as in Step 2. It is not a separately observed fee transfer or a Binance-marked realized loss; it excludes bad debt and gas. `collateral_seized_24h` is the actual token quantity. The summary’s seized percentage divides this quantity by collateral at the query hour, so later owner deposits can make it exceed 100%. Unknown label windows have null outcome fields, never imputed zeros.

Every row has a readable `summary`, the complete feature vector, the four screen flags, source/cohort/state flags, and observed outcomes. Case-level replay diagnostics stay in separate tables so the judge cannot confuse simulated saves with historical events.

### Retrieval method and interface

`scripts/retrieve.py` returns the exact k nearest rows by standardized Euclidean distance. The frozen scaler uses 1,587,760 complete, labeled hours whose full 24-hour outcomes matured strictly before 1 January 2026, across 6,610 positions. The default candidate pool is the same mature pre-2026 library. A query excludes its own market/borrower position when supplied via `--row-id`. Candidates must also have matured by `--as-of`. The optional `--pool past` explicitly permits mature holdout rows; none of the five examples uses that option.

[ASSUMPTION] All eleven Step 2 numeric features receive equal weight after population-standard-deviation scaling, with no clipping, log transform or imputation. Asset/market and outcome metadata do not enter the distance. LTV and distance-to-line are both retained as the brief’s feature vector requires, even though they are redundant in these equal-LLTV markets. Thus neighbors can cross assets/chains, and multiple nearby hours or positions from one market event can dominate. They are contextual examples, not independent votes or a calibrated liquidation probability. The default library is event-enriched by the original borrower selection.

Feature units: LTV, distance-to-line, returns, realized volatility, utilization and annual continuous borrow rate are fractions; oracle gap is in percentage points; owner inactivity is hours; position size is collateral value in USD. Query JSON must supply every feature listed in `data/retrieval_scaler.json`, as finite numbers. Invalid/incomplete vectors are rejected, with no imputation. For an external vector without `--as-of`, the documented data cutoff is used. The frozen scaler cannot support a pre-2026 as-of query; that requires a separately fitted earlier scaler.

From this folder, using the local environment:

```sh
scripts/.venv/bin/python3 -I scripts/retrieve.py --row-id 403770 --k 3
scripts/.venv/bin/python3 -I scripts/retrieve.py --queries-file data/retrieval_example_queries.json --output data/retrieval_examples_reproduced.json
# For your own complete feature object:
scripts/.venv/bin/python3 -I scripts/retrieve.py --features-file data/query.json --as-of 2026-10-26T00:00:00Z --k 5
```

### Five example queries and returned neighbors

Exact queries and all returned summaries/flags are saved in `data/retrieval_example_queries.json` and `data/retrieval_examples.json`. These are demonstrations selected deterministically by observable features, not accuracy estimates. The stress query is near 85.5% LTV after a daily fall over 5%; the other four are near 78% and cover dust, both small Ethereum cohorts and fallback coverage. All five originate in 2026, retrieve only pre-2026 rows, and exclude the query position. Displayed feature values are rounded below; JSON retains full precision.

| feature | Q1 | Q2 | Q3 | Q4 | Q5 |
| --- | --- | --- | --- | --- | --- |
| ltv | 0.85500031 | 0.77998609 | 0.77999706 | 0.77999327 | 0.78000424 |
| dist_to_line | 0.0049996851 | 0.080013912 | 0.080002937 | 0.080006729 | 0.079995758 |
| price_change_1h | -0.011825377 | -0.001840156 | 0.0046942482 | 0.0028335772 | 0.00088799555 |
| price_change_6h | -0.040808 | -0.0067384106 | 0.011845577 | 0.00032967779 | -0.013652818 |
| price_change_24h | -0.065523314 | 0.020046247 | 0.015229689 | -0.007176944 | -0.0027640785 |
| realized_volatility_24h | 0.0062849272 | 0.0075660066 | 0.0018398225 | 0.0032016997 | 0.0037992778 |
| oracle_vs_binance_pct | -0.2313014 | -0.15700244 | 0.094293962 | -0.45827525 | -0.068180376 |
| utilization | 0.89567251 | 0.82349566 | 0.90179923 | 0.90635067 | 0.8965683 |
| borrow_rate | 0.049013798 | 0.044866863 | 0.045384667 | 0.047280025 | 0.046581251 |
| hours_since_owner_action | 258.04083 | 1561.0903 | 71.163611 | 920.96028 | 137.48028 |
| position_size_usd | 1984.7187 | 0.010063 | 1053.8948 | 124.04329 | 10.010143 |

**Q1: cbBTC stress hour**, row 403,770, as of 2026-01-29T18:00:00+00:00; 1,587,760 eligible candidates, k=3.

| row | distance | summary |
| --- | --- | --- |
| 3475110 | 0.702886 | Base WETH/USDC, 2025-06-20 23:00 UTC, LTV 85.3%, 24h price -5.3%, oracle gap +0.02%; no liquidation in next 24h; owner: no observed owner actions in next 24h |
| 3516978 | 0.702908 | Base WETH/USDC, 2025-06-20 23:00 UTC, LTV 85.2%, 24h price -5.3%, oracle gap +0.02%; no liquidation in next 24h; owner: no observed owner actions in next 24h |
| 3886542 | 0.702973 | Base WETH/USDC, 2025-06-20 23:00 UTC, LTV 85.2%, 24h price -5.3%, oracle gap +0.02%; no liquidation in next 24h; owner: no observed owner actions in next 24h |

**Q2: Base WETH dust position**, row 3,602,807, as of 2026-01-28T05:00:00+00:00; 1,587,760 eligible candidates, k=3.

| row | distance | summary |
| --- | --- | --- |
| 3879508 | 0.614487 | Base WETH/USDC, 2025-04-12 00:00 UTC, LTV 77.7%, 24h price +2.9%, oracle gap -0.05%; no liquidation in next 24h; owner: no observed owner actions in next 24h |
| 4883847 | 0.628047 | Ethereum wstETH/USDC, 2025-11-19 00:00 UTC, LTV 80.0%, 24h price +3.0%, oracle gap -0.52%; no liquidation in next 24h; owner: no observed owner actions in next 24h; flags: small_eth_wsteth_483_survivors |
| 3389645 | 0.632609 | Base WETH/USDC, 2025-01-28 14:00 UTC, LTV 76.8%, 24h price +2.5%, oracle gap -0.02%; no liquidation in next 24h; owner: no observed owner actions in next 24h |

**Q3: Ethereum WETH small cohort**, row 4,738,837, as of 2026-09-05T22:00:00+00:00; 1,587,760 eligible candidates, k=3.

| row | distance | summary |
| --- | --- | --- |
| 2899738 | 0.320642 | Base cbBTC/USDC, 2025-06-16 08:00 UTC, LTV 77.9%, 24h price +1.5%, oracle gap -0.01%; no liquidation in next 24h; owner: Borrow ×3 |
| 1311679 | 0.340357 | Base cbBTC/USDC, 2025-06-16 08:00 UTC, LTV 79.0%, 24h price +1.5%, oracle gap -0.01%; no liquidation in next 24h; owner: no observed owner actions in next 24h |
| 1671963 | 0.342349 | Base cbBTC/USDC, 2025-06-16 08:00 UTC, LTV 77.0%, 24h price +1.5%, oracle gap -0.01%; no liquidation in next 24h; owner: no observed owner actions in next 24h |

**Q4: Ethereum wstETH cohort**, row 4,846,793, as of 2026-05-27T21:00:00+00:00; 1,587,760 eligible candidates, k=3.

| row | distance | summary |
| --- | --- | --- |
| 4159308 | 0.454853 | Base WETH/USDC, 2025-06-08 15:00 UTC, LTV 78.5%, 24h price -0.0%, oracle gap -0.02%; no liquidation in next 24h; owner: no observed owner actions in next 24h |
| 1692091 | 0.468232 | Base cbBTC/USDC, 2025-06-11 13:00 UTC, LTV 78.0%, 24h price +0.0%, oracle gap -0.12%; no liquidation in next 24h; owner: no observed owner actions in next 24h |
| 2218014 | 0.483625 | Base cbBTC/USDC, 2025-03-29 09:00 UTC, LTV 77.4%, 24h price -1.5%, oracle gap -0.05%; no liquidation in next 24h; owner: no observed owner actions in next 24h |

**Q5: Binance-fallback episode**, row 1,084,723, as of 2026-09-01T12:00:00+00:00; 1,587,760 eligible candidates, k=3.

| row | distance | summary |
| --- | --- | --- |
| 283042 | 0.280435 | Base cbBTC/USDC, 2025-04-05 17:00 UTC, LTV 78.7%, 24h price -0.8%, oracle gap -0.04%; no liquidation in next 24h; owner: no observed owner actions in next 24h |
| 1569766 | 0.280907 | Base cbBTC/USDC, 2025-04-05 17:00 UTC, LTV 78.0%, 24h price -0.8%, oracle gap -0.04%; no liquidation in next 24h; owner: no observed owner actions in next 24h |
| 94666 | 0.281137 | Base cbBTC/USDC, 2025-04-05 17:00 UTC, LTV 78.8%, 24h price -0.8%, oracle gap -0.04%; no liquidation in next 24h; owner: no observed owner actions in next 24h |

### Validation, timing and failures

All 4,983,502 rows passed unique-key, screen, exclusion, missing-label and feature-completeness checks. 548 sampled rows matched the original Step 2 features and labels; 535 known windows matched direct event/owner-transaction queries. An independent centered-variance calculation reproduced the scaler. A full global distance sort verified all 15 returned neighbors for the five queries, and the public CLI reproduced their outputs. Validation records are `data/step3_validation.json` and `data/step3_neighbor_validation.json`.

Timings below come from the corresponding saved status JSON and example output, in seconds. Successful invocation times can overlap; their sum is not elapsed wall time.

| task | seconds |
| --- | --- |
| DEFAULT loss diagnostics | 101.40 |
| Loss report | 0.41 |
| Retrieval table build | 148.68 |
| Five examples, including selection | 2.31 |
| Whole-table and source validation | 11.35 |
| Full-sort neighbor and CLI validation | 5.00 |
| Summary readability migration | 9.05 |

Recorded wall span from starting the loss diagnostics to this report is 1236.719s (20.61 minutes), including coding and validation gaps. The table build took 148.680s; the five searches together took 1.045s, including disk reads. No external source was queried or re-downloaded in this task.

Failures/limitations: no unresolved build or validation failure. An initial build was intentionally stopped to constrain disclosed outcomes to the matured 24-hour horizon, then rebuilt. The independent neighbor validator initially attempted to mutate a read-only pandas array; copying its mask fixed that check. A count export initially omitted a boolean/object positive-count column; the export now explicitly counts integers and reconciles to the table. Missing/censored windows, unresolved oracle phases, the small Ethereum cohorts and the inherited Aave exclusion remain explicit dataset limitations. The phase and heartbeat assumptions are not claims of new on-chain verification.

### Reproduce this analysis and Step 3

Run from this folder. No downloads, credentials, fitting of a risk classifier, or commits are required. Reporting scripts replace their own generated sections; run the loss report before the Step 3 report.

```sh
scripts/.venv/bin/python3 -I scripts/loss_analysis.py
scripts/.venv/bin/python3 -I scripts/report_losses.py
scripts/.venv/bin/python3 -I scripts/build_retrieval.py
scripts/.venv/bin/python3 -I scripts/retrieval_examples.py
scripts/.venv/bin/python3 -I scripts/validate_retrieval.py
scripts/.venv/bin/python3 -I scripts/validate_neighbors.py
scripts/.venv/bin/python3 -I scripts/report_step3.py
```

## Step 4 decisions accepted

The founder specified >=$1,000 start collateral as primary, >=$100 as secondary, and fallback-only sensitivity; required >=5 percentage points of recall gain at no lower precision with a positive 95% position-bootstrap interval and >=1h earlier median warning. Keep/drop uses a matched 5% alert budget, with original fixed screens and their union with model alerts also reported.

## Step 2 by position size

151 eligible episodes had an observed zero starting collateral balance and no position-derived oracle price. Their starting value is $0; no oracle price or missing balance is imputed. They are excluded by both size cuts. This resolves the initial size-partition validation failure.

These are **DEFAULT-only** re-aggregations of the existing Step 2 replay, with no new simulation, calibration or changed execution rules. Source: `data/evaluation_results.parquet`, joined to `data/replay_case_plan.parquet` and `data/default_loss_analysis.parquet`. Exact outputs: `data/step2_size_headline.csv`, `data/step2_size_per_asset.csv`, and `data/step2_size_exclusions.csv`. The default remains 70% act / 50% target / 20% daily discretionary budget / 25% maximum slice / 120-second cooldown and execution delay.

**Primary cohort: collateral value at episode start ≥ $1,000. Secondary cohort: ≥ $100.** These are nested cohorts, not disjoint bins. Start value is `C0 × oracle_start`, the same baseline state used by Step 2. The main tables exclude every replay episode with any Binance-fallback span; fallback-only and all-price results are sensitivity analyses. All 13 reconstruction failures remain excluded.

[ASSUMPTION] For Step 2, an episode is either wholly venue-feed-based or classified as fallback sensitivity if any of its replay span uses fallback. This preserves the meaning of an aggregate replay outcome. Step 4 likewise assigns all hours of fallback episodes to sensitivity, even if some individual hours have valid venue feeds. Size membership is fixed at the episode start; it is not reselected after prices or the owner change the balance.

### Default headline, venue-feed episodes only

| metric | >= $1,000 train | >= $1,000 holdout | >= $100 train | >= $100 holdout |
| --- | --- | --- | --- | --- |
| Episodes | 2,468 | 5,897 | 3,174 | 8,174 |
| Observed liquidated episodes | 1,895 | 5,011 | 2,475 | 7,038 |
| Saved vs observed liquidation | 1,890 | 5,009 | 2,469 | 7,034 |
| Saved with no-action model support | 1,884 | 5,008 | 2,462 | 7,033 |
| Liquidated under default | 14 | 5 | 20 | 8 |
| Observed survivors liquidated in model | 9 | 3 | 14 | 4 |
| Liquidated only with policy vs no action | 1 | 0 | 1 | 0 |
| Real penalties, protocol formula | $6,150,644.20 | $11,428,042.13 | $6,158,936.80 | $11,455,890.01 |
| Policy penalties | $218,201.92 | $25.97 | $218,205.51 | $33.38 |
| Median extra collateral, % of start | +20.54% | +26.83% | +22.28% | +27.92% |
| Net equity vs actual at bottom | $19,262,678.84 | $36,116,212.35 | $19,281,131.17 | $36,174,246.42 |
| Net equity vs actual at episode end | $15,782,291.46 | $42,770,136.60 | $15,803,679.10 | $42,844,121.12 |
| Managed equity at cutoff | $50,692,669.42 | $123,307,648.39 | $50,736,518.78 | $123,531,869.38 |
| Actual leftover held passively at cutoff | $23,245,632.73 | $64,273,393.38 | $23,269,287.00 | $64,401,578.93 |
| Slippage | $169,569.10 | $186,193.14 | $169,665.25 | $186,462.63 |
| Gas | $51,482.64 | $31,141.12 | $52,417.32 | $32,946.85 |
| Sales | 33,667 | 65,094 | 40,885 | 89,535 |
| Dust-limited losses | 0 | 0 | 1 | 1 |
| Above-act-under-1h losses | 0 | 0 | 0 | 0 |

Saved versus observed includes cases where the no-action model also avoids liquidation; the supported count removes those. Equity uplift includes leverage and price-path effects, not only avoided penalties. The passive/managed cutoff columns retain the hypothetical hourly-price continuation and no-later-owner-flow assumptions from Step 2. These are simulations of sampled positions, not prospective protection guarantees.

### Per-asset view, venue-feed episodes only

| cut_usd | split | asset_group | cases | observed_liquidated | saved_with_noaction_model_support | policy_liquidated | equity_vs_actual_end | slippage | gas | dust_limited_losses | above_act_under_1h_losses |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1000 | train | cbBTC | 2028 | 1667 | 1664 | 6 | 13576062.01 | 76178.48 | 708.15 | 0 | 0 |
| 1000 | train | ETH-type | 440 | 228 | 220 | 8 | 2206229.45 | 93390.62 | 50774.49 | 0 | 0 |
| 1000 | holdout | cbBTC | 4212 | 3835 | 3835 | 1 | 37506934.64 | 159117.82 | 1433.58 | 0 | 0 |
| 1000 | holdout | ETH-type | 1685 | 1176 | 1173 | 4 | 5263201.96 | 27075.32 | 29707.54 | 0 | 0 |
| 100 | train | cbBTC | 2494 | 2070 | 2066 | 8 | 13591481.99 | 76252.11 | 872.70 | 0 | 0 |
| 100 | train | ETH-type | 680 | 405 | 396 | 12 | 2212197.11 | 93413.14 | 51544.62 | 1 | 0 |
| 100 | holdout | cbBTC | 5737 | 5250 | 5249 | 3 | 37558465.93 | 159300.21 | 1962.27 | 0 | 0 |
| 100 | holdout | ETH-type | 2437 | 1788 | 1784 | 5 | 5285655.18 | 27162.42 | 30984.58 | 1 | 0 |

### What the size/source restrictions remove

Rows below partition all eligible DEFAULT episodes for each cutoff, split and asset group. Loss diagnostics count episodes that liquidated under DEFAULT. Dust-limited means no order was queued and attempts passing the price-gap check were below the simulator’s $0.01 sale minimum. Above-act-under-1h means the first debt-bearing decision was already above act and liquidation followed in less than one hour. These two loss flags can overlap.

| cut_usd | split | asset_group | status | cases | policy_liquidated | dust_limited_losses | above_act_under_1h_losses |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1000 | holdout | ETH-type | kept venue-only | 1685 | 4 | 0 | 0 |
| 1000 | holdout | ETH-type | excluded by size | 2543 | 259 | 244 | 8 |
| 1000 | holdout | ETH-type | fallback sensitivity only | 32 | 0 | 0 | 0 |
| 1000 | holdout | cbBTC | kept venue-only | 4212 | 1 | 0 | 0 |
| 1000 | holdout | cbBTC | excluded by size | 5360 | 32 | 18 | 2 |
| 1000 | holdout | cbBTC | fallback sensitivity only | 26 | 0 | 0 | 0 |
| 1000 | train | ETH-type | kept venue-only | 440 | 8 | 0 | 0 |
| 1000 | train | ETH-type | excluded by size | 1917 | 751 | 630 | 97 |
| 1000 | train | ETH-type | fallback sensitivity only | 46 | 1 | 0 | 0 |
| 1000 | train | cbBTC | kept venue-only | 2028 | 6 | 0 | 0 |
| 1000 | train | cbBTC | excluded by size | 1505 | 35 | 7 | 21 |
| 1000 | train | cbBTC | fallback sensitivity only | 0 | 0 | 0 | 0 |
| 100 | holdout | ETH-type | kept venue-only | 2437 | 5 | 1 | 0 |
| 100 | holdout | ETH-type | excluded by size | 1770 | 258 | 243 | 8 |
| 100 | holdout | ETH-type | fallback sensitivity only | 53 | 0 | 0 | 0 |
| 100 | holdout | cbBTC | kept venue-only | 5737 | 3 | 0 | 0 |
| 100 | holdout | cbBTC | excluded by size | 3823 | 30 | 18 | 2 |
| 100 | holdout | cbBTC | fallback sensitivity only | 38 | 0 | 0 | 0 |
| 100 | train | ETH-type | kept venue-only | 680 | 12 | 1 | 0 |
| 100 | train | ETH-type | excluded by size | 1676 | 747 | 629 | 97 |
| 100 | train | ETH-type | fallback sensitivity only | 47 | 1 | 0 | 0 |
| 100 | train | cbBTC | kept venue-only | 2494 | 8 | 0 | 0 |
| 100 | train | cbBTC | excluded by size | 1039 | 33 | 7 | 21 |
| 100 | train | cbBTC | fallback sensitivity only | 0 | 0 | 0 | 0 |

### Fallback-only sensitivity

| cut_usd | split | cases | observed_liquidated | policy_liquidated | saved_with_noaction_model_support | equity_vs_actual_end | policy_penalties | dust_limited_losses | above_act_under_1h_losses |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1000 | train | 46 | 19 | 1 | 19 | 4013973.15 | 0.11 | 0 | 0 |
| 1000 | holdout | 58 | 9 | 0 | 9 | 8134.00 | 0.00 | 0 | 0 |
| 100 | train | 47 | 20 | 1 | 20 | 4014015.51 | 0.11 | 0 | 0 |
| 100 | holdout | 91 | 19 | 0 | 18 | 8099.29 | 0.00 | 0 | 0 |

The full headline and per-asset metrics for fallback-only and all-prices sensitivities are retained in the same CSVs; the judge-facing main tables above use venue feeds only.

Reproduce from this folder: `scripts/.venv/bin/python3 -I scripts/step4_size_tables.py`. All counts and amounts above are computed by that script.

## Step 4 — risk model: drop

**Recommendation: DROP this fitted risk model.** On the 2026 primary cohort, at the same 5% alert-hour budget, its recall is 23.20% versus 26.26% for the best screen, and precision is 66.10% versus 74.81%. It fails the required recall/precision improvement and the positive bootstrap-gain test. Its earlier warning at the matched budget does not override those failures. No hyperparameter or threshold retuning was performed after this comparison.

### Frozen acceptance test

| criterion | observed | result |
| --- | --- | --- |
| Recall gain >=5 pp AND no lower precision | recall -3.06 pp; precision -8.72 pp | FAIL |
| 95% position-bootstrap recall-gain interval above zero | [-3.64, -2.53] pp | FAIL |
| Median lead >=1h earlier than budget-matched act screen | +5.33h | PASS |

Primary = at least $1,000 of collateral at episode start, venue-feed episodes only. Secondary = at least $100, evaluated with the **same** primary-trained model. Binance-fallback episodes enter sensitivity analyses only. Both screens are compared at the same 5% budget for keep/drop; their original fixed thresholds are reported separately. The accepted specification is recorded in `data/step4_protocol.json`, written before fitting. The keep/drop decision is `data/step4_decision.json`.

### Primary holdout metrics

Rows: `data/step4_metrics.csv`, filtered to cut_usd=1000, split=holdout, source=venue_only. This is 853,968 eligible position-hours, 5,823 unique market/borrower positions, 5,897 replay episodes and 121,661 positive 24-hour labels. Each budget-matched method alerts on 42,698 hours. The two screen scores happen to select identical alerts in this cohort.

| method | hours | alerts | alert_rate | AUC | precision | recall |
| --- | --- | --- | --- | --- | --- | --- |
| Model, 5% budget | 853,968 | 42,698 | 5.00% | 0.9280 | 66.10% | 23.20% |
| LTV screen, 5% budget | 853,968 | 42,698 | 5.00% | 0.9268 | 74.81% | 26.26% |
| LTV-or-gap screen, 5% budget | 853,968 | 42,698 | 5.00% | 0.9268 | 74.81% | 26.26% |
| Fixed LTV >70% | 853,968 | 398,057 | 46.61% | 0.8110 | 30.55% | 99.96% |
| Fixed LTV >70% or gap >2% | 853,968 | 398,057 | 46.61% | 0.8110 | 30.55% | 99.96% |
| Fixed LTV + model alerts | 853,968 | 398,057 | 46.61% | 0.8110 | 30.55% | 99.96% |
| Fixed combined + model alerts | 853,968 | 398,057 | 46.61% | 0.8110 | 30.55% | 99.96% |

AUC for the model and budget-matched screens uses their continuous ranking scores. The fixed-screen/union AUCs use binary predictions; those are different ROC summaries and are shown for completeness, not used in model selection. A slightly higher model AUC does not satisfy the required gain at the specified operating point.

### What the model adds to the existing fixed screens

**Fixed combined screens alone: recall 99.96%, precision 30.55%; fixed combined screens plus the model’s alerts: recall 99.96%, precision 30.55%.** Added alerts: 0. The fixed act-only screen has the same result here. Every model alert is already covered by the fixed screens; the union adds no positive or negative hours. The fixed/union alert rate is 46.61%, not 5%.

### Bootstrap intervals on the primary holdout

Source: `data/step4_bootstrap_replicates.parquet` and `data/step4_bootstrap_intervals.csv`. 2,000 paired resamples draw 5,823 positions with replacement; all hours and liquidation events belonging to a sampled position move together. The intervals are two-sided 95% percentiles. In each resample the recall comparator is the better of both budget-matched screens, not a favorable choice made afterward.

| method | precision | precision_95 | recall | recall_95 |
| --- | --- | --- | --- | --- |
| Model, 5% budget | 66.10% | [65.13, 67.08] % | 23.20% | [22.73, 23.65] % |
| LTV screen, 5% budget | 74.81% | [73.38, 76.28] % | 26.26% | [25.58, 26.95] % |
| LTV-or-gap screen, 5% budget | 74.81% | [73.38, 76.28] % | 26.26% | [25.58, 26.95] % |
| Fixed LTV >70% | 30.55% | [30.30, 30.79] % | 99.96% | [99.91, 99.99] % |
| Fixed LTV >70% or gap >2% | 30.55% | [30.30, 30.79] % | 99.96% | [99.91, 99.99] % |
| Fixed LTV + model alerts | 30.55% | [30.30, 30.79] % | 99.96% | [99.91, 99.99] % |
| Fixed combined + model alerts | 30.55% | [30.30, 30.79] % | 99.96% | [99.91, 99.99] % |

| difference | estimate | interval_95 |
| --- | --- | --- |
| Recall: model minus best budget screen | -3.06 pp | [-3.64, -2.53] pp |
| Precision: model minus best budget screen | -8.72 pp | [-9.93, -7.43] pp |
| Median lead: model minus budget act screen | +5.33h | [5.14, 5.70] h |

[ASSUMPTION] Bootstrap intervals are conditional on the fitted model and the original evaluation-set operating points: alert flags stay fixed while positions are resampled, so the alert fraction can vary within a replicate. No models are refitted or cutoffs reselected inside the bootstrap. This preserves whole-position dependence but does not capture common crash-day/market dependence or uncertainty from model selection. AUC is reported as a point estimate; the acceptance-test confidence interval is for recall gain, with precision and lead-time intervals also reported.

### Lead-time comparison

Rows: `data/step4_lead_metrics.csv`, same primary holdout filter; event-level evidence is `data/step4_primary_lead_events.parquet`. Lead is measured from the earliest eligible alert in the 24 hours before each observed liquidation. Missed events receive **zero hours**, so median lead uses the same event census for every method. The paired-detection result is reported separately. These are observed event warnings, not simulated policy saves.

| method | events | positions | detected_events | event_recall | median_lead_all_events_h | median_lead_detected_h |
| --- | --- | --- | --- | --- | --- | --- |
| Model, 5% budget | 5216 | 5011 | 5126 | 98.27% | 10.08 | 10.25 |
| LTV screen, 5% budget | 5216 | 5011 | 4884 | 93.63% | 4.75 | 5.89 |
| LTV-or-gap screen, 5% budget | 5216 | 5011 | 4884 | 93.63% | 4.75 | 5.89 |
| Fixed LTV >70% | 5216 | 5011 | 5216 | 100.00% | 23.39 | 23.39 |
| Fixed LTV >70% or gap >2% | 5216 | 5011 | 5216 | 100.00% | 23.39 | 23.39 |
| Fixed LTV + model alerts | 5216 | 5011 | 5216 | 100.00% | 23.39 | 23.39 |
| Fixed combined + model alerts | 5216 | 5011 | 5216 | 100.00% | 23.39 | 23.39 |

The model’s median lead is 10.08h versus 4.75h for the budget-matched act screen: a 5.33h improvement. Across 4,813 events detected by both, median paired improvement is 2.00h. The original fixed 70% act screen warns at a median 23.39h within this capped window, earlier than the model. The model spreads its limited alerts across more liquidation events, but covers fewer positive hours and has lower precision; its alerts add no coverage to the fixed screens already running.

[ASSUMPTION] Lead-time eligibility requires at least one valid, labeled pre-event hour within 24 hours. Repeated liquidations are separate events but are clustered by position in the bootstrap. Observation gaps and episode edges can shorten the available warning window; maximum observed lead and eligible-hour count are stored per event. Lead time is capped at 24 hours, so the fixed screens may actually have fired earlier than shown.

### Secondary size cut and price-source sensitivities

Rows: `data/step4_metrics.csv`; 2026 results below reuse the primary-trained model. Each displayed cohort/source subset gets its own 5% score-rank operating point. These are descriptive sensitivities and cannot overturn the primary keep/drop test. The source subsets overlap only in the explicitly labeled all-prices sensitivity. No fallback episode was used for training.

| cut_usd | source | method | hours | positions | positive_hours | auc | precision | recall |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1000 | fallback_only | Model, 5% budget | 8399 | 58 | 216 | 0.99 | 47.02% | 91.20% |
| 1000 | fallback_only | LTV screen, 5% budget | 8399 | 58 | 216 | 0.98 | 51.31% | 99.54% |
| 1000 | fallback_only | LTV-or-gap screen, 5% budget | 8399 | 58 | 216 | 0.98 | 51.31% | 99.54% |
| 1000 | all_prices_sensitivity | Model, 5% budget | 862367 | 5861 | 121877 | 0.93 | 65.97% | 23.34% |
| 1000 | all_prices_sensitivity | LTV screen, 5% budget | 862367 | 5861 | 121877 | 0.93 | 74.59% | 26.39% |
| 1000 | all_prices_sensitivity | LTV-or-gap screen, 5% budget | 862367 | 5861 | 121877 | 0.93 | 74.59% | 26.39% |
| 100 | venue_only | Model, 5% budget | 1183493 | 8064 | 170463 | 0.92 | 63.90% | 22.18% |
| 100 | venue_only | LTV screen, 5% budget | 1183493 | 8064 | 170463 | 0.93 | 75.78% | 26.31% |
| 100 | venue_only | LTV-or-gap screen, 5% budget | 1183493 | 8064 | 170463 | 0.93 | 75.78% | 26.31% |
| 100 | fallback_only | Model, 5% budget | 13150 | 91 | 456 | 0.97 | 45.05% | 64.91% |
| 100 | fallback_only | LTV screen, 5% budget | 13150 | 91 | 456 | 0.97 | 40.94% | 58.99% |
| 100 | fallback_only | LTV-or-gap screen, 5% budget | 13150 | 91 | 456 | 0.97 | 40.94% | 58.99% |
| 100 | all_prices_sensitivity | Model, 5% budget | 1196643 | 8124 | 170919 | 0.92 | 63.76% | 22.32% |
| 100 | all_prices_sensitivity | LTV screen, 5% budget | 1196643 | 8124 | 170919 | 0.93 | 75.40% | 26.40% |
| 100 | all_prices_sensitivity | LTV-or-gap screen, 5% budget | 1196643 | 8124 | 170919 | 0.93 | 75.40% | 26.40% |

### Per-asset primary holdout view

Rows: `data/step4_per_asset_metrics.csv`; 2026, >=$1,000, venue-only. These use the **global primary-cohort** 5% budget and then split its results by asset; the threshold is not retuned independently per asset.

| method | asset_group | hours | positive_hours | alerts | alert_fraction | precision | recall |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Model, 5% budget | ETH-type | 243228 | 28484 | 9458 | 3.89% | 75.33% | 25.01% |
| Model, 5% budget | cbBTC | 610740 | 93177 | 33240 | 5.44% | 63.47% | 22.64% |
| LTV screen, 5% budget | ETH-type | 243228 | 28484 | 8377 | 3.44% | 77.64% | 22.83% |
| LTV screen, 5% budget | cbBTC | 610740 | 93177 | 34321 | 5.62% | 74.12% | 27.30% |
| LTV-or-gap screen, 5% budget | ETH-type | 243228 | 28484 | 8377 | 3.44% | 77.64% | 22.83% |
| LTV-or-gap screen, 5% budget | cbBTC | 610740 | 93177 | 34321 | 5.62% | 74.12% | 27.30% |

### Training, features and operating points

One `HistGradientBoostingClassifier` (1.9.1) was fitted to 357,860 pre-2026 primary/venue-only hours from 2,468 episodes and 2,398 positions, including 45,720 positive hours. It uses 200 boosting iterations, learning rate 0.05, at most 15 leaves per tree, depth 5, minimum leaf size 50, L2 regularization 1.0, 127 histogram bins, and seed 20261027. Early stopping and class reweighting are disabled. There is no hyperparameter search, random train/holdout mixing, or holdout-based refit. Each hour has equal training weight.

Feature order, unchanged from Step 2: `ltv`, `dist_to_line`, `price_change_1h`, `price_change_6h`, `price_change_24h`, `realized_volatility_24h`, `oracle_vs_binance_pct`, `utilization`, `borrow_rate`, `hours_since_owner_action`, `position_size_usd`. Neither outcomes, borrower IDs, future owner actions, episode size membership nor oracle-source flags are model inputs. Training labels and their full 24-hour windows end before 1 January 2026; holdout rows start in 2026. The model predicts observed borrower liquidation, not the residual liquidation risk after DEFAULT policy intervention.

[ASSUMPTION] Cohort membership uses the episode’s observed start collateral value; only hours from that start through the existing episode end are used. This avoids applying a future start balance to earlier pulled history. All eligible hours are considered, including hours where no screen fires and hours after a position closes; the screen-filtered retrieval table is not the training data. The eligible Step 2 episode definition retains its full-window missing-state/reconstruction exclusions. The same position may appear in disjoint episodes on both sides of the calendar split, but identifiers are not predictors.

No state or feature values are imputed. Unknown 24-hour labels exclude 702 otherwise in-window hours. Among the 1,663,688 retained >=$100 hours, 199,880 have undefined feature values (for example, LTV after collateral becomes zero); the estimator’s native missing-value routing handles them. Missing position-state hours are never supplied to fitting/evaluation. See the [official estimator documentation](https://scikit-learn.org/stable/modules/generated/sklearn.ensemble.HistGradientBoostingClassifier.html) for native missing-value support.

[ASSUMPTION] A replay episode containing any Binance-fallback span is assigned wholly to sensitivity, even if a particular hour has a valid venue feed. This conservatively keeps the source convention consistent with the size-stratified Step 2 tables. The hourly feature oracle remains the pulled Morpho value, unchanged. Source flags indicate replay-reconstruction coverage; they are not a substitute price inserted into features. The small Ethereum cohorts and accepted wstETH parity/exchange-rate assumptions remain labeled; Aave and all hours of the 13 failed-reconstruction positions remain excluded.

Budget matching is deterministic and uses scores, not outcome labels: rank by model probability, by LTV for the act screen, and by `max(LTV / 0.70, oracle_gap_percent / 2)` for the combined screen. Sweeping the combined score scales both original boundaries by the same factor. Select `floor(0.05 × eligible_hours)` rows; a deterministic row-key hash breaks exact cutoff ties. Null screen inputs receive the lowest ranking score and never satisfy their fixed condition. This changes no feature value used by the model.

[ASSUMPTION] These are retrospective precision/recall-at-5%-budget metrics: each evaluation set’s score distribution determines its own cutoff without looking at labels. They do not establish that a frozen deployment threshold would continue firing on exactly 5% of future hours. The model’s probability is not calibrated to all live loans: the dataset is event-enriched, uses the selected market universe, and the holdout has already been inspected for descriptive analyses in earlier steps.

Primary holdout operating points from `data/step4_operating_thresholds.csv`:

| method | score_threshold | ltv_threshold | oracle_gap_pct_threshold | alerts | hours | ties_at_threshold |
| --- | --- | --- | --- | --- | --- | --- |
| model_5pct | 0.61 | unknown | unknown | 42698 | 853968 | 13 |
| act_5pct | 0.83 | 0.83 | unknown | 42698 | 853968 | 1 |
| combined_5pct | 1.19 | 0.83 | 2.38 | 42698 | 853968 | 1 |

Pre-2026 primary-cohort metrics below are **in-sample diagnostics**, not an additional test set. They were not used to choose another model:

| method | hours | alerts | alert_rate | AUC | precision | recall |
| --- | --- | --- | --- | --- | --- | --- |
| Model, 5% budget | 357,860 | 17,893 | 5.00% | 0.9900 | 99.00% | 38.74% |
| LTV screen, 5% budget | 357,860 | 17,893 | 5.00% | 0.9058 | 73.03% | 28.58% |
| LTV-or-gap screen, 5% budget | 357,860 | 17,893 | 5.00% | 0.9059 | 73.02% | 28.58% |

### Counts, validation, timing and failures

Counts from `data/step4_prepare_status.json`, `data/step4_hour_counts.csv` and `data/step4_validation.json`:

| measure | count |
| --- | --- |
| input_hours | 11123057 |
| within_cohort_episode_hours | 1664390 |
| missing_state_excluded | 0 |
| unknown_label_excluded | 702 |
| training_horizon_crossing_excluded | 0 |
| infinite_feature_excluded | 0 |
| retained_hours | 1663688 |
| rows_with_native_nan_features | 199880 |

| cut_usd | split | source_group | asset_group | hours | positive_hours |
| --- | --- | --- | --- | --- | --- |
| 100 | holdout | fallback_only | ETH-type | 7656 | 240 |
| 100 | holdout | fallback_only | cbBTC | 5494 | 216 |
| 100 | holdout | venue_only | ETH-type | 351628 | 43248 |
| 100 | holdout | venue_only | cbBTC | 831865 | 127215 |
| 100 | train | fallback_only | ETH-type | 6815 | 487 |
| 100 | train | venue_only | ETH-type | 98600 | 9864 |
| 100 | train | venue_only | cbBTC | 361630 | 49798 |
| 1000 | holdout | fallback_only | ETH-type | 4640 | 168 |
| 1000 | holdout | fallback_only | cbBTC | 3759 | 48 |
| 1000 | holdout | venue_only | ETH-type | 243228 | 28484 |
| 1000 | holdout | venue_only | cbBTC | 610740 | 93177 |
| 1000 | train | fallback_only | ETH-type | 6670 | 463 |
| 1000 | train | venue_only | ETH-type | 63800 | 5594 |
| 1000 | train | venue_only | cbBTC | 294060 | 40126 |

Validation passed on all 1,663,688 retained hours: unique keys, episode/cohort bounds, temporal split, label masks, failed-position exclusions and source flags. All 1,663,688 labels were independently recomputed from observed events. 1,001 sampled predictions matched the serialized candidate. Full primary rankings and confusion counts matched independent calculations; the first 5 position-bootstrap draws were explicitly expanded to reproduce hour metrics and event medians. All 12 size/source headline rows reconcile to the original DEFAULT replay results. All bootstrap intervals and the keep/drop rules were recomputed. Records: `data/step4_validation.json`.

| task | seconds |
| --- | --- |
| Step 2 size tables | 0.29 |
| Episode-hour preparation and frozen protocol | 24.62 |
| Classifier fit plus scoring | 10.48 |
| Evaluation including all sensitivities and bootstrap | 122.03 |
| Independent validation | 9.20 |

Within those totals, fitting took 3.324s, scoring 6.313s and the 2,000-replicate bootstrap 2.102s. Recorded wall span from the successful size-table run to this report is 1082.393s (18.04 minutes), including implementation/reporting gaps; initial reading and package installation preceded that timer. No liquidation or price source was re-downloaded. Free package wheels are cached in `data/raw/step4_wheels/`; dependency versions are pinned in `scripts/requirements_step4.txt`.

Execution failures: the first size-exclusion partition check exposed the 151 zero-balance episodes with undefined oracle-derived value; assigning their observed zero collateral its known $0 value resolved it without imputing a price. No fitting, scoring or final validation failure remains. The **statistical acceptance failure is the result**: recall and precision are lower at the matched budget, and the recall-gain confidence interval is negative. Earlier budget-matched warning passes its gate but does not rescue the model.

Report generation initially received an unrendered metric table; rendering it as Markdown resolved the formatting error without changing any results.

### Model retention and exact reproduction

**No trained model is retained.** The evaluated compressed candidate was 146,272 bytes; `scripts/step4_finalize.py` removed it after validation because it failed the acceptance test. Predictions, operating points, bootstrap draws, manifests and all reports remain reproducible. `data/step4_retention.json` records this decision.

Exact training command, from this folder, using the existing local environment:

```sh
scripts/.venv/bin/python3 -I scripts/step4_train.py
```

The fit command consumes `data/step4_hours.parquet` and the frozen protocol and creates an evaluation candidate. The full sequence below reproduces the size tables, preparation, fit, evaluation, validation and conditional retention. The report commands replace their generated sections, so run them in this order. All work stays in this research folder; Python uses `-I`.

```sh
# Only if the local environment needs the already-cached classifier dependency:
scripts/.venv/bin/python3 -I -m pip install --no-index --find-links data/raw/step4_wheels scikit-learn==1.9.1
scripts/.venv/bin/python3 -I scripts/step4_size_tables.py
scripts/.venv/bin/python3 -I scripts/step4_prepare.py
scripts/.venv/bin/python3 -I scripts/step4_train.py
scripts/.venv/bin/python3 -I scripts/step4_evaluate.py
scripts/.venv/bin/python3 -I scripts/validate_step4.py
scripts/.venv/bin/python3 -I scripts/step4_finalize.py
scripts/.venv/bin/python3 -I scripts/report_step4.py
```

The judge-facing evidence to cite is **“Step 2 by position size”** above for corridor replay performance and this section for the model rejection. This task does not edit the judges’ README, change product decisions, train an LLM, or run Step 5. **Stopped after Step 4. No commits.**
