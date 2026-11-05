# Liquidation dataset — Step 1 pull

Status: **INCOMPLETE — Aave source blocked; not ready for Step 2**. Generated offline at 2026-10-26T22:36:23.029221+00:00. No features, calibration, retrieval, model, or minute-price pulls have run.

## Requested coverage and selected markets

The event cutoff is 2026-10-26T00:00:00+00:00 exclusive. Aave starts 2025-01-01T00:00:00+00:00. Morpho starts at each market's recorded creation. Price pulls start 2024-01-01T00:00:00+00:00.

Rows: `data/markets.json`; snapshots and schema are cached under `data/raw/morpho/` and `data/raw/morpho_schema/`.

| chain | pair | market | created_utc | borrow_USDC_at_selection | candidate_markets |
| --- | --- | --- | --- | --- | --- |
| 8453 | cbBTC/USDC | 0x9103c3b4e834476c9a62ea009ba2c884ee42e94e6e314a26f04d312434191836 | 2024-09-04T09:48:29+00:00 | 1614573992.147823 | 1 |
| 8453 | WETH/USDC | 0x8793cf302b8ffd655ab97bd1c695dbd967807e8367a65cb2f4edaf1380ba1bda | 2024-06-07T21:56:45+00:00 | 96911838.108011 | 46 |
| 1 | WETH/USDC | 0xddac43c721a67ea9f19b1c57d2de12039c99f3f271c90123f7d8fc9edb591ee6 | 2026-10-07T12:52:23+00:00 | 101982223.863124 | 12 |
| 1 | wstETH/USDC | 0xb323495f7e4148be5643a4ea4a8221eef163e4bccfdedc2a6f4696baacbc86cc | 2024-01-03T09:21:59+00:00 | 29764823.426788 | 4 |

The Ethereum WETH market has no pre-2026 history. Selection follows the brief's highest-current-borrow rule; it is not a historical market-universe selection.

## Counts by market and year

Rows: `data/liquidations.parquet`, grouped by event timestamp year; `data/positions_hourly.parquet`, grouped by snapshot year; survivor anchors from `data/borrowers.parquet`, grouped by the qualifying-hour year. Liquidated-borrower counts are unique within each market-year, not additive across years. Zero counts in incomplete windows do not establish absence of events.

| venue | chain | pair | year | liquidations | liquidated_borrowers | survivor_anchors | hourly_rows | coverage |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Morpho | 8453 | cbBTC/USDC | 2024 | 19 | 15 | 8 | 7910 | liquidations complete; histories see coverage |
| Morpho | 8453 | cbBTC/USDC | 2025 | 3451 | 3085 | 780 | 1798575 | liquidations complete; histories see coverage |
| Morpho | 8453 | cbBTC/USDC | 2026 | 10554 | 9556 | 1212 | 4898543 | liquidations complete; histories see coverage |
| Morpho | 8453 | WETH/USDC | 2024 | 319 | 299 | 135 | 146265 | liquidations complete; histories see coverage |
| Morpho | 8453 | WETH/USDC | 2025 | 1867 | 1663 | 523 | 913415 | liquidations complete; histories see coverage |
| Morpho | 8453 | WETH/USDC | 2026 | 3910 | 3369 | 1342 | 2085399 | liquidations complete; histories see coverage |
| Morpho | 1 | WETH/USDC | 2024 | 0 | 0 | 0 | 0 | before creation |
| Morpho | 1 | WETH/USDC | 2025 | 0 | 0 | 0 | 0 | before creation |
| Morpho | 1 | WETH/USDC | 2026 | 0 | 0 | 0 | 0 | liquidations complete; histories see coverage |
| Morpho | 1 | wstETH/USDC | 2024 | 113 | 32 | 132 | 54660 | liquidations complete; histories see coverage |
| Morpho | 1 | wstETH/USDC | 2025 | 104 | 93 | 228 | 126925 | liquidations complete; histories see coverage |
| Morpho | 1 | wstETH/USDC | 2026 | 106 | 95 | 123 | 92055 | liquidations complete; histories see coverage |
| Aave | 1 | all assets | 2025 | 1502 | 862 | 0 | 0 | INCOMPLETE LOG WINDOW |
| Aave | 1 | target subset | 2025 | 911 | 535 | 0 | 0 | INCOMPLETE LOG WINDOW |
| Aave | 1 | all assets | 2026 | 0 | 0 | 0 | 0 | INCOMPLETE LOG WINDOW |
| Aave | 1 | target subset | 2026 | 0 | 0 | 0 | 0 | INCOMPLETE LOG WINDOW |

## Sample and hourly coverage

Rows: `data/borrowers.parquet`, grouped by market and selection. Morpho liquidation histories cover every liquidated borrower, with the requested first-liquidation window; missing API hours are counted below. Aave sampling is blocked by the incomplete event population. No Aave survivor claims are made from partial logs.

| chain | selection | borrowers | hourly_rows | pair |
| --- | --- | --- | --- | --- |
| 1 | all_liquidated | 70 | 31824 | wstETH/USDC |
| 1 | liquidated_and_survivor | 138 | 118001 | wstETH/USDC |
| 1 | seeded_survivor | 345 | 123815 | wstETH/USDC |
| 8453 | all_liquidated | 4620 | 2111984 | WETH/USDC |
| 8453 | liquidated_and_survivor | 603 | 509167 | WETH/USDC |
| 8453 | seeded_survivor | 1397 | 523928 | WETH/USDC |
| 8453 | all_liquidated | 11640 | 5696291 | cbBTC/USDC |
| 8453 | liquidated_and_survivor | 491 | 427126 | cbBTC/USDC |
| 8453 | seeded_survivor | 1509 | 581611 | cbBTC/USDC |

Survivor draw results from `data/survivor_selection/*.json`; population is the complete historical Borrow-event borrower census, including closed accounts. Some selected survivors also have a liquidation outside their qualifying 30-day episode.

| chain | pair | population | screened | selected | seed |
| --- | --- | --- | --- | --- | --- |
| 8453 | cbBTC/USDC | 73876 | 4387 | 2000 | 20261026 |
| 8453 | WETH/USDC | 23807 | 5470 | 2000 | 20261026 |
| 1 | WETH/USDC | 1 | 1 | 0 | 20261026 |
| 1 | wstETH/USDC | 1183 | 1183 | 483 | 20261026 |

Total hourly rows: 10,123,747; sampled borrowers: 20,813. Requested hours missing usable API state: 957,644 of 11,081,391. Aave log pages cached: 126 of 2,374. Unknown Aave asset decimals: 18 assets; amounts in native units remain null where decimals are unverified, with raw integers preserved.

## Prices

Rows: `data/binance_1h_ext.parquet`: 49,392 candles, BTCUSDT and ETHUSDT, interval HOUR. Existing rows were copied to `data/raw/binance_existing.parquet`; every new HTTP page is cached.

Rows: `data/oracle_rounds_ext.csv`, grouped below. Boundary rounds can precede the start or follow the exclusive cutoff so an as-of consumer has a bracket. RPC responses are cached individually; decoded groups are cached per requested round chunk.

| chain | feed | source_kind | rounds | first_utc | last_utc |
| --- | --- | --- | --- | --- | --- |
| base | BTC/USD | Base proxy | 242227 | 2024-04-17T16:09:07+00:00 | 2026-10-26T00:03:23+00:00 |
| base | ETH/USD | Base proxy | 248101 | 2023-12-31T23:58:55+00:00 | 2026-10-26T00:02:31+00:00 |
| eth | BTC/USD | Aave source | 18775 | 2025-03-07T09:53:59+00:00 | 2026-10-26T00:28:23+00:00 |
| eth | BTC/USD | standard | 37675 | 2023-12-31T23:55:23+00:00 | 2026-10-26T00:29:11+00:00 |
| eth | ETH/USD | Aave source | 25273 | 2025-03-19T05:53:11+00:00 | 2026-10-26T00:09:59+00:00 |
| eth | ETH/USD | standard | 49010 | 2023-12-31T23:55:11+00:00 | 2026-10-26T00:33:23+00:00 |

The newer Aave-source aggregators begin in March 2025. The standard Ethereum proxies provide the earlier history. Keep independent proxies separate; `source_kind` identifies standard feeds, newer Aave sources, and Base proxies. Raw round histories may include rounds published before a phase was activated. Do not treat those as active proxy observations before their phase interval. Phase activation intervals are still missing: Base archive resolution hit HTTP 429 on drpc and HTTP 403 on PublicNode. This metadata pull stopped after both endpoints failed. Historical wrapper-ratio samples: 1,030, in `data/eth_ratios_ext.csv`.

## Time and failures

Rows: `data/*_status.json` (last invocation per source, including network waits; sources overlap in wall time, so do not add them). Original cached response timestamps preserve provenance across restarts. Timing table: `data/source_times.csv`.

| source | status | last_run_seconds |
| --- | --- | --- |
| aave | failed | 11.35 |
| binance | complete | 14.43 |
| eth_oracles | complete | 61.93 |
| legacy_oracles | complete | 69.57 |
| morpho_discover | complete | 0.73 |
| morpho_liquidations | complete | 26.54 |
| morpho_positions_batched | complete | 569.63 |
| morpho_positions | superseded_by_batched_reader | 6727.53 |
| morpho_survivor_transactions | complete | 2.28 |
| morpho_survivors_eth | complete | 423.21 |
| morpho_survivors_fast | complete | 1720.21 |
| morpho_survivors | superseded_by_active_interval_reader | 1133.8 |
| morpho_transactions_scoped | complete | 538.08 |
| morpho_transactions | superseded_by_scoped_reader | 7221.83 |
| oracles | partial | 8408.21 |
| phase_intervals | failed | 75.33 |
| ratios | complete | 67.23 |

Recorded wall spans by source, including handoff/setup gaps and overlapping jobs (not summed request or CPU time): rows from `data/source_wall_spans.csv`. These span retained mode statuses; Aave also uses cached request/error timestamps to recover its earlier work. Earlier overwritten invocations may not be fully represented, so these are timing lower bounds rather than an exact total across every restart.

| source | wall_span_seconds | active_workers |
| --- | --- | --- |
| Morpho | 11098.89 | 0 |
| Chainlink | 8408.21 | 0 |
| Aave logs | 11.35 | 0 |
| Binance | 14.43 | 0 |
| Wrapper ratios | 67.23 | 0 |

Aave: MEV Blocker exhausted bounded retries after HTTP 429; the Flashbots fallback reported `pruned history unavailable`. Stopped that source after both configured endpoints failed. Complete log coverage, account population, hourly samples, and owner-event reconstruction remain outstanding. `data/aave_missing_ranges.csv` lists 2,248 missing block steps, including any internal gaps; minimum/maximum event timestamps do not imply uninterrupted coverage.

Morpho: the inspected schema exposes `marketId` instead of the old `uniqueKey` output field. A mixed-case address was rejected and normalized. Offset pagination rejected `skip=11000`; the collector now continues with the schema's transaction cursor and checks returned unique event counts against the reported total. The API's address-list limit required owner groups of at most 100. A batch-query formatting error was corrected, and the server's alias-limit response capped batches at 30 selections as well as the advertised time-series point budget. Concurrent local cache indexing initially hit a SQLite reader lock; WAL mode and a longer busy timeout resolved it. Any HTTP rate-limit responses are preserved under `data/raw/morpho/errors/`. Successful pages are never refreshed during a resume.

Chainlink: the first Ethereum phase lookup returned addresses that yielded empty round data. The original direct-aggregator approach works for the newer aggregators. Starting standard-proxy enumeration at an ancient pre-window phase also failed; resolving the phase active at the window start fixed that reader. The standard proxies' cached phases supply the older observations.

Environment: the initial Python lacked pandas/pyarrow, and sandboxed network calls could not resolve hosts. A local environment under `scripts/.venv/` supplies the table libraries; the public-network commands then ran with runtime network permission. No credential files were read.

## Assumptions and interpretation

- [ASSUMPTION] “To 25 Oct 2026” includes the whole UTC day, represented by the exclusive cutoff above.
- [ASSUMPTION] A common price window starts at the beginning of 2024, before every selected market's creation. Extra prefix prices are retained.
- [ASSUMPTION] Highest borrow means current `borrowAssets` among the exact collateral/USDC token addresses, sorted descending at the cached selection snapshot. All loan assets have the same decimals; listed-only filtering is not imposed.
- [ASSUMPTION] Stablecoin loan units stand in for USD when deriving `oracle_px = collateralValue / collateral` and LTV from Morpho's hourly API state. The raw response retains integer amounts.
- [ASSUMPTION] Binance BTCUSDT represents cbBTC's underlying BTC; ETHUSDT represents WETH. Use the candle open at the exact snapshot hour, avoiding that hour's future close.
- [ASSUMPTION] wstETH Binance prices equal ETHUSDT times the most recent historical stETH share ratio, sampled with the original reader's block spacing and held until the next sample. This assumes stETH/ETH parity and omits any secondary-market discount. `binance_underlying_px` preserves unconverted ETHUSDT. No future ratio sample is used.
- [ASSUMPTION] Liquidated borrower windows are clipped to market creation and the dataset cutoff. No state is invented for missing API hours. Zero-collateral rows have null LTV/oracle unit price.
- [ASSUMPTION] API time-series timestamps denote the hourly state used for labels; no interpolation or future-hour close is substituted. Survivor eligibility is based on observed API hours and requires positive collateral value; missing hours and zero-value collateral do not establish eligibility.
- [ASSUMPTION] Survivor sampling uses a sorted historical Borrow-event population shuffled with seed 20261026; it accepts borrowers with a >70% hourly LTV and no liquidation in the following complete 30 days, stopping at 2,000 eligible borrowers. Closed accounts remain in the population. The survivor history window is 14 days before through 7 days after the qualifying hour. A later liquidation beyond those 30 days does not disqualify that earlier survival episode.
- [ASSUMPTION] A sample is a borrower within a venue/chain/market; one address on different markets is a different position. Overlapping liquidated/survivor hours are merged by position and timestamp.
- [ASSUMPTION] Liquidation labels refer to events strictly after the snapshot and within the inclusive horizon. Right-censored negatives are null; follow-up-observed flags distinguish them.
- [ASSUMPTION] Aave token decimals are taken only from the earlier fetcher's explicit token map. Unrecognized assets retain addresses and raw amounts instead of assuming decimals.

## Outputs and reproducibility

All writes stay in this folder. Existing backtest files are read-only. Network data are anonymous public reads; no paid source or agentcash is used. Runtime: Python 3.14.6, pandas 3.1.0, PyArrow 26.0.0; Python's bounded executor-map API is required. Dependency versions are pinned in `scripts/requirements.txt`. Run from the repository root, with Python isolation enabled:

```sh
python3 -I 'research_notes/Liquidation dataset/scripts/pull.py' oracles > 'research_notes/Liquidation dataset/data/oracles.log' 2>&1 &
python3 -I 'research_notes/Liquidation dataset/scripts/pull.py' aave > 'research_notes/Liquidation dataset/data/aave.log' 2>&1 &
python3 -I 'research_notes/Liquidation dataset/scripts/morpho.py' discover
python3 -I 'research_notes/Liquidation dataset/scripts/morpho.py' liquidations
python3 -I 'research_notes/Liquidation dataset/scripts/morpho.py' positions_batched
python3 -I 'research_notes/Liquidation dataset/scripts/morpho.py' transactions_scoped
python3 -I 'research_notes/Liquidation dataset/scripts/morpho.py' survivors_fast
python3 -I 'research_notes/Liquidation dataset/scripts/morpho.py' survivor_transactions
python3 -I 'research_notes/Liquidation dataset/scripts/pull.py' eth_oracles
python3 -I 'research_notes/Liquidation dataset/scripts/pull.py' legacy_oracles
python3 -I 'research_notes/Liquidation dataset/scripts/pull.py' phase_intervals
python3 -I 'research_notes/Liquidation dataset/scripts/pull.py' ratios
'research_notes/Liquidation dataset/scripts/.venv/bin/python3' -I 'research_notes/Liquidation dataset/scripts/binance.py'
'research_notes/Liquidation dataset/scripts/.venv/bin/python3' -I 'research_notes/Liquidation dataset/scripts/assemble.py'
'research_notes/Liquidation dataset/scripts/.venv/bin/python3' -I 'research_notes/Liquidation dataset/scripts/validate.py'
```

Launch the first two commands as separate background jobs. Run only one worker per source/mode; finish pull jobs before the final assembly. A completed request cache is keyed by URL and exact JSON payload. HTTP/GraphQL failures are stored separately and are not treated as successful empty pages. Atomic file replacement protects completed pages. For rate limits, resume the same command after the provider's cooldown. Do not keep trying alternate sources after the brief's stopping limit.

`liquidations.parquet` has one row per `(chain, tx, log)`, including the Aave target-subset flag and unscaled raw values. `debt_to_cover` and `collateral_seized` are the common token-unit amounts; reference names `repaid_usdc`, `bad_debt_usdc`, and `seized_cbbtc` are retained where applicable. `collateral`/`debt` are legacy symbol aliases in this event table. Chain identifiers are EVM chain IDs; timestamps are Unix seconds UTC; raw integer amounts/shares are strings to avoid precision loss. `positions_hourly.parquet` has one row per `(chain, market, borrower, ts)`, with numeric collateral/debt balances. `borrowers.parquet` records selected history coverage; it is not the full liquidation population. `owner_transactions.parquet` contains 646,248 cached events for the selected Morpho borrowers, retaining source JSON and raw token units; completeness depends on the scoped owner-pull statuses. The SQLite index under `data/raw/` preserves reused full-market pages plus subsequent borrower-census and sampled-owner pages. The raw liquidation extracts are complete for the four Morpho markets, independent of hourly-history completeness. `quality.json` records gaps. Processed artifacts may be regenerated entirely offline.

## Decisions before Step 2

- Do not start calibration until the incomplete pulls and population/label checks have been resolved, or explicitly approve a reduced scope with the coverage limitations above.
- Decide whether to retain the newly created Ethereum WETH market as holdout-only, omit it, or authorize an older market in addition to the required highest-current-borrow choice.
- Decide how to handle Aave's blocked full-window logs and therefore unavailable unbiased samples; a partial early-window account sample cannot substitute for the requested population.
- Decide whether the stETH/ETH parity assumption is acceptable for the wstETH price comparison; market discounts need a separate price series if they matter to the replay.
- Resolve missing proxy phase-activation boundaries before treating stored aggregator rounds as the live oracle path.
- Decide whether to accept the smaller Ethereum wstETH survivor cohort shown above; its complete borrower census is exhausted. The new Ethereum WETH market cannot supply a full 30-day survivor window by this cutoff.
- Decide whether to exclude affected position windows or backfill the missing API hours listed in `data/borrowers.parquet`; they were not imputed.

The selected feeds can be compared with Chainlink's [ETH/USD directory](https://data.chain.link/feeds/ethereum/mainnet/eth-usd). On-chain response provenance, not that live directory, determines the cached observations.

## Offline validation

Checks in `scripts/validate.py` passed for 10,123,747 hourly rows, 20,813 sampled borrower positions, 21,945 unique liquidation events, 4,483 selected survival episodes, 49,392 Binance candles, and 621,061 oracle rounds. Results: `data/validation.json`. Checks cover unique keys, LTV arithmetic, liquidation horizons and censoring, seeded survivor selection and follow-up, continuous hourly candles, and complete requested oracle round ranges. These checks establish internal consistency of the saved data; they do not resolve the documented source-coverage gaps.
