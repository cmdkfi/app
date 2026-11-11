# Copy of the planning note of 11 Nov 2026 (`research/README.md` says how these copies are kept); paths point at this repository.

# Venue split and gap risk: what an oracle's design does to a protective agent in fast moves

Started 2026-11-11 11:59 UTC. Ended 2026-11-11 12:14 UTC (write-up finished after the scripts; see the end of the file).
Scripts and their outputs: `scripts/` beside this note (`part_a_gaps.py`, `part_b_oracles.py`, `part_b_validate.py`, `part_c_split.py`, `make_tables.py`; outputs in `scripts/out/`, the tables below are copied from `out/tables.md`). Run with the Liquidation-dataset venv: `".../Liquidation dataset/scripts/.venv/bin/python3" -I <script>`. Nothing was committed. No network transactions; WebFetch was used only for documentation.

Tags: **[SOURCED]** parameter with a reference; **[ASSUMPTION]** a modelling choice of mine; **[MODEL]** a script output.

## 1. The question

The founder's hypothesis: "one loan of 1 ETH on one venue liquidated in the same block is worse than the same ETH split into several loans on venues with different oracle designs, because the agent can act before liquidation on some of them." The existing note `protocol_risk_diversification.md` (KQ2 inferences) says diversification reduces variance, not expected loss, and adds venue risk. This note measures, on real minute price history, (A) how large fast moves actually are, (B) how five oracle designs translate a move into the time between the act line and the liquidation line, (C) what a three-venue split does to the liquidated fraction and the penalty against a single venue, and (D) what a venue split adds on the other side of the ledger. It measures; it does not advocate.

## 2. Data and parameters

**Prices.** `research/liquidation-dataset/data/binance_1m_ext.parquet` (release asset `research-data-2026-11-06`, SHA-256 `3b411dbd…`, see `research/liquidation-dataset/DATA.md`): Binance spot 1-minute klines, columns `open_time` (ms), `open, high, low, close, volume, quote_volume, trades, symbol, interval`; symbols `ETHUSDT`, `BTCUSDT`; 2,076,410 rows. The file was pulled in windows around liquidation events, so it is not continuous: a gap of more than 60 s between rows starts a new segment and no computation below spans a gap. Coverage **[MODEL]**:

| symbol | first | last | minutes | segments | days_covered | span_days |
|---|---|---|---|---|---|---|
| ETHUSDT | 2024-01-07 12:59 | 2026-10-25 23:00 | 1205112 | 36 | 836.90 | 1022 |
| BTCUSDT | 2024-09-28 19:59 | 2026-10-25 23:00 | 871298 | 19 | 605.10 | 757 |

Because the sample is built around liquidation events, it over-represents volatile periods. That is the right sample for extremes (part A's maxima) but the counts in A2 are not rates per year. The 10 Oct 2025 crash and 31 Jan–6 Feb 2026 are inside contiguous segments (ETH segments 2025-10-06 16:59 to 2025-10-23 00:00 and 2026-01-01 22:59 to 2026-02-09 07:00).

**Real Chainlink rounds used for validation.** `research/liquidation-dataset/data/oracle_rounds_ext.csv` (release asset, the same rounds), read on-chain for the 22 Oct 2026 backtest: the standard Chainlink ETH/USD feed on Ethereum mainnet (proxy `0x5f4e…8419`, aggregator `0x7d4e…6fb5`, rows with `usable_for_replay` true; the file also holds Aave's own ETH/USD source for the same pair, `0x7c7f…e3f8`, which the first run of this validation read by mistake and reported as 62 rounds; corrected 11 Nov 2026) and Base BTC/USD aggregator `0x852a…6ca8` (10,021 rounds, Oct 2025–Feb 2026).

**Corridor** (brief): target LTV 50 %, act line 70 %, liquidation at 86 % LTV (Morpho market LLTV). Thin-buffer starts at 60 % and 70 %. Agent latency (decision to landed transaction) 30 s, 2 min, 5 min. A **thin-corridor variant** (start 80 %, act line 83 %, liquidation 86 %) was added because at the default corridor no window in the data reaches the liquidation line within the agent's latency from any act-line crossing (B1), so the hypothesis can only be tested where the buffer is thin; the variant mirrors the "speed without a buffer" case in `cmdk/app/research/liquidation-backtest/backtest.md` §1 (act line 3 pp under the liquidation line) **[ASSUMPTION]**.

**Liquidation penalty.** Morpho liquidation incentive factor for LLTV 86 %: LIF = 1/(1 − 0.3·(1 − 0.86)) = 1/0.958 = 1.04384, so the penalty is 4.384 % of the repaid debt (`backtest.md` §2.1 gives the same formula and value 1.0438) **[SOURCED]**; the whole venue position is liquidated at once **[ASSUMPTION]**.

**Oracle designs** (values at minute-bar closes, warmed up from 6 h before each window):

| id | design | parameters | source |
|---|---|---|---|
| (1) `cl_base` | Chainlink push feed, Base ETH/USD | post the minute close when it deviates ≥ 0.15 % from the last post or 1200 s have passed | 0.15 % / 1200 s from the Chainlink reference-data JSON for Base, cited in `near_prices_and_foreign_state.md` §1 **[SOURCED]**; minute-close sampling **[ASSUMPTION]** |
| (2) `cl_main` | Chainlink push feed, Ethereum mainnet ETH/USD | ≥ 0.5 % or 3600 s | verified 11 Nov 2026 from [reference-data-directory feeds-mainnet.json](https://reference-data-directory.vercel.app/feeds-mainnet.json): "ETH / USD", proxy `0x5f4eC3Df9cbd43714FE2740f5E3616155c5b8419`, `threshold: 0.5`, `heartbeat: 3600`, `decimals: 8`, `feedCategory: "low"` (BTC / USD `0xF403…E88c` has the same values) **[SOURCED]** |
| (3) `pull_1m` | pull oracle refreshed every minute (Pyth-like) | the minute close itself | Pyth docs: "Pull-based oracle providing 500+ price feeds with 400ms updates across 100+ chains"; "Applications fetch signed price data from the off-chain Hermes service and submit it on-chain for verification" ([docs.pyth.network/llms-price-feeds.txt](https://docs.pyth.network/llms-price-feeds.txt)) **[SOURCED]**; a venue that refreshes once a minute **[ASSUMPTION]** |
| (4) `twap_30` | 30-minute TWAP | mean of the last 30 minute closes | window **[ASSUMPTION]**. Fluid's vault oracle (`UniV3CheckCLRSOracle`) is not a 30-minute TWAP: it checks a Uniswap V3 TWAP over `secondsAgos` such as `[240, 60, 15, 1, 0]` against Chainlink/Redstone with a maximum delta (test value 3 %) and returns one of them by a rate-source mode (1 = UniV3 only, 2 = UniV3 checked by Chainlink/Redstone, 3 = Chainlink/Redstone checked by UniV3); the docs pages ([docs.fluid.instadapp.io](https://docs.fluid.instadapp.io/autogenerated-docs/oracle/oracles/uniV3CheckCLRSOracle.html)) and the git mirror returned 404/521 on 11 Nov 2026, so this description comes from the search-result summary of those pages (secondary) |
| (5) `twap_60` | 60-minute TWAP | mean of the last 60 minute closes | Kamino Scope's `ScopeTwap` is a 1-hour EMA, not a simple mean: `EMA_1H_DURATION_SECONDS = 60 * 60`, smoothing factor `2 / (1 + N')` with `N' = T / delta t`, samples closer than 30 s rejected, `MIN_SAMPLES_IN_PERIOD_1H = 10`, three 20-minute sub-periods ([Kamino-Finance/scope twap.rs](https://github.com/Kamino-Finance/scope/blob/master/programs/scope/src/oracles/twap.rs), fetched 11 Nov 2026) **[SOURCED]**; the simple 60-minute mean stands in for it **[ASSUMPTION]** |

**Agent and venue rules** (`part_b_oracles.py` docstring) **[ASSUMPTION]**: bar i's close is known at T_i = open_time + 60 s; every oracle value, LTV, decision, landing and liquidation is evaluated at bar closes. A decision at T_i with latency L lands at bar i + ceil(L/60), so 30 s is modelled as 60 s (pessimistic by 30 s). The sale fills at 0.99 × the close of the landing bar (the brief's "1 % worse"), sells enough ETH to bring the venue's oracle LTV back to 50 %, and repeats if the act line is crossed again. Liquidation happens at the first bar close with oracle LTV ≥ 86 %; if the sale lands in the same bar, the liquidator wins. A second fill rule, `revert_1pct`, applies the build executor's rule from `backtest.md` §2.2 ("Each fill must be within 1% of the oracle, or the sale reverts"): a fill more than 1 % under the venue's oracle reverts and the agent re-sends with the same latency.

**Windows for B and C** (ETH, t0 = start of the worst 60-minute close-to-low window from A3; position opened at t0 on the close at t0): 2024-03-05 19:03, 2024-08-05 00:17, 2025-02-03 01:12, 2025-10-10 20:29, 2026-02-05 14:39 (the worst window inside 31 Jan–6 Feb 2026; the 10-minute list also has 2026-01-31 18:34). Each frame runs 12 h after t0.

## 3. Part A: how large are real gaps? **[MODEL]**

Definitions, within one contiguous segment: close-to-low drop over h minutes = 1 − min(low[i+1..i+h]) / close[i]; close-to-close = 1 − close[i+h]/close[i]; LTV reached = L0 / (1 − drop). Lines: from 50 % the act line needs a 28.57 % drop and the liquidation line 41.86 %; from 60 %, 14.29 % and 30.23 %; from 70 % the position starts on the act line and liquidation needs 18.60 %.

### A1 ETHUSDT: largest drop per horizon

| horizon (min) | windows | max close-to-low drop % | at (UTC, window start) | max close-to-close drop % | at (cc) | LTV from 50 % (cl) | from 60 % | from 70 % |
|---|---|---|---|---|---|---|---|---|
| 1 | 1205076 | 9.94 | 2025-02-03 02:06 | 9.59 | 2025-02-03 02:06 | 55.52 | 66.62 | 77.73 |
| 2 | 1205040 | 12.34 | 2025-02-03 02:05 | 11.99 | 2025-02-03 02:05 | 57.04 | 68.44 | 79.85 |
| 5 | 1204932 | 16.02 | 2024-08-05 01:05 | 14.84 | 2024-08-05 01:05 | 59.54 | 71.45 | 83.35 |
| 10 | 1204752 | 16.84 | 2025-02-03 02:00 | 15.46 | 2024-08-05 01:00 | 60.12 | 72.15 | 84.17 |
| 30 | 1204032 | 23.62 | 2025-02-03 01:40 | 21.40 | 2025-02-03 01:37 | 65.46 | 78.55 | 91.64 |
| 60 | 1202952 | 24.96 | 2025-02-03 01:12 | 22.93 | 2025-02-03 01:07 | 66.63 | 79.96 | 93.29 |

### A1 BTCUSDT: largest drop per horizon

| horizon (min) | windows | max close-to-low drop % | at (UTC, window start) | max close-to-close drop % | at (cc) | LTV from 50 % (cl) | from 60 % | from 70 % |
|---|---|---|---|---|---|---|---|---|
| 1 | 871279 | 5.92 | 2024-12-05 22:27 | 4.29 | 2025-10-10 21:18 | 53.14 | 63.77 | 74.40 |
| 2 | 871260 | 6.83 | 2024-12-05 22:26 | 4.61 | 2024-12-05 22:26 | 53.67 | 64.40 | 75.14 |
| 5 | 871203 | 8.19 | 2025-10-10 21:15 | 7.34 | 2025-10-10 21:14 | 54.46 | 65.35 | 76.24 |
| 10 | 871108 | 10.79 | 2025-10-10 21:10 | 9.14 | 2025-10-10 21:09 | 56.05 | 67.26 | 78.47 |
| 30 | 870728 | 12.40 | 2025-10-10 20:50 | 11.05 | 2025-10-10 20:49 | 57.08 | 68.49 | 79.91 |
| 60 | 870158 | 12.91 | 2025-10-10 20:27 | 10.96 | 2025-10-10 20:19 | 57.41 | 68.89 | 80.38 |

### A2 Number of minute starts whose close-to-low drop crossed a line, by starting LTV

| symbol | horizon_min | act line, start 50 % | act line, start 60 % | act line, start 70 % | liq line, start 50 % | liq line, start 60 % | liq line, start 70 % |
|---|---|---|---|---|---|---|---|
| BTCUSDT | 1 | 0 | 0 | starts at line | 0 | 0 | 0 |
| BTCUSDT | 2 | 0 | 0 | starts at line | 0 | 0 | 0 |
| BTCUSDT | 5 | 0 | 0 | starts at line | 0 | 0 | 0 |
| BTCUSDT | 10 | 0 | 0 | starts at line | 0 | 0 | 0 |
| BTCUSDT | 30 | 0 | 0 | starts at line | 0 | 0 | 0 |
| BTCUSDT | 60 | 0 | 0 | starts at line | 0 | 0 | 0 |
| ETHUSDT | 1 | 0 | 0 | starts at line | 0 | 0 | 0 |
| ETHUSDT | 2 | 0 | 0 | starts at line | 0 | 0 | 0 |
| ETHUSDT | 5 | 0 | 3 | starts at line | 0 | 0 | 0 |
| ETHUSDT | 10 | 0 | 13 | starts at line | 0 | 0 | 0 |
| ETHUSDT | 30 | 0 | 53 | starts at line | 0 | 0 | 31 |
| ETHUSDT | 60 | 0 | 153 | starts at line | 0 | 0 | 91 |

Reading: within the agent's latency horizons (1, 2 and 5 minutes; 30 s is below the data's resolution and is bounded by the 1-minute row) no window ever crossed the act line from 50 % or the liquidation line from 50 %, 60 % or 70 %. From 60 %, three 5-minute windows (all on 5 Aug 2024 and 3 Feb 2025, see A3) crossed the act line. From 70 % the liquidation line was crossed only at the 30- and 60-minute horizons (31 and 91 minute-starts, all in the 5 Aug 2024 and 3 Feb 2025 clusters). BTC never crossed any line at any horizon from any of the three starts.

### A3 Ten worst windows (one per 6-hour cluster; the remaining horizons are in `out/A_top10_*.csv`)

ETHUSDT, 60 minutes:

| start | close at start | drop % | LTV from 50 % | from 60 % | from 70 % |
|---|---|---|---|---|---|
| 2025-02-03 01:12 | 2831.90 | 24.96 | 66.63 | 79.96 | 93.29 |
| 2024-08-05 00:17 | 2690.38 | 21.54 | 63.72 | 76.47 | 89.21 |
| 2025-10-10 20:29 | 4024.40 | 14.65 | 58.58 | 70.30 | 82.01 |
| 2024-03-05 19:03 | 3625.00 | 11.72 | 56.64 | 67.97 | 79.30 |
| 2024-04-13 19:09 | 3224.59 | 11.55 | 56.53 | 67.84 | 79.14 |
| 2025-04-07 06:07 | 1550.99 | 9.03 | 54.96 | 65.95 | 76.94 |
| 2026-02-05 14:39 | 2097.90 | 8.13 | 54.43 | 65.31 | 76.20 |
| 2026-02-05 23:15 | 1902.29 | 7.90 | 54.29 | 65.15 | 76.00 |
| 2024-12-09 20:05 | 3798.38 | 7.62 | 54.12 | 64.95 | 75.77 |
| 2025-05-09 08:36 | 2479.34 | 7.55 | 54.08 | 64.90 | 75.72 |

ETHUSDT, 10 minutes:

| start | close at start | drop % | LTV from 50 % | from 60 % | from 70 % |
|---|---|---|---|---|---|
| 2025-02-03 02:00 | 2555.30 | 16.84 | 60.12 | 72.15 | 84.17 |
| 2024-08-05 01:00 | 2532.09 | 16.63 | 59.97 | 71.97 | 83.96 |
| 2025-10-10 21:10 | 3884.82 | 11.58 | 56.55 | 67.86 | 79.17 |
| 2024-03-05 19:50 | 3489.20 | 8.29 | 54.52 | 65.42 | 76.33 |
| 2024-04-13 20:00 | 3090.00 | 7.70 | 54.17 | 65.01 | 75.84 |
| 2024-12-09 20:55 | 3747.49 | 6.36 | 53.40 | 64.08 | 74.76 |
| 2024-04-12 18:25 | 3308.20 | 6.29 | 53.36 | 64.03 | 74.70 |
| 2025-06-21 21:21 | 2391.93 | 6.14 | 53.27 | 63.93 | 74.58 |
| 2026-01-31 18:34 | 2392.26 | 5.95 | 53.16 | 63.79 | 74.43 |
| 2026-08-22 05:00 | 2517.71 | 5.27 | 52.78 | 63.34 | 73.90 |

ETHUSDT, 5 minutes:

| start | close at start | drop % | LTV from 50 % | from 60 % | from 70 % |
|---|---|---|---|---|---|
| 2024-08-05 01:05 | 2513.68 | 16.02 | 59.54 | 71.45 | 83.35 |
| 2025-02-03 02:03 | 2512.19 | 15.41 | 59.11 | 70.93 | 82.75 |
| 2025-10-10 21:15 | 3730.71 | 7.93 | 54.30 | 65.17 | 76.03 |
| 2024-03-05 19:52 | 3448.00 | 7.19 | 53.88 | 64.65 | 75.42 |
| 2024-04-13 20:04 | 3066.02 | 6.98 | 53.75 | 64.50 | 75.25 |
| 2024-12-09 21:00 | 3744.41 | 6.29 | 53.35 | 64.03 | 74.70 |
| 2024-04-12 18:30 | 3301.59 | 6.11 | 53.25 | 63.90 | 74.55 |
| 2026-08-22 05:05 | 2517.11 | 5.25 | 52.77 | 63.32 | 73.88 |
| 2024-08-27 21:44 | 2545.37 | 5.00 | 52.63 | 63.16 | 73.69 |
| 2025-02-25 07:20 | 2433.22 | 4.92 | 52.59 | 63.11 | 73.62 |

ETHUSDT, 1 minute:

| start | close at start | drop % | LTV from 50 % | from 60 % | from 70 % |
|---|---|---|---|---|---|
| 2025-02-03 02:06 | 2410.68 | 9.94 | 55.52 | 66.62 | 77.73 |
| 2024-08-05 01:09 | 2337.01 | 9.67 | 55.35 | 66.42 | 77.49 |
| 2024-04-12 18:34 | 3225.32 | 3.89 | 52.02 | 62.43 | 72.83 |
| 2025-10-10 21:15 | 3730.71 | 3.77 | 51.96 | 62.35 | 72.74 |
| 2025-06-21 21:30 | 2332.90 | 3.77 | 51.96 | 62.35 | 72.74 |
| 2026-01-31 18:42 | 2338.17 | 3.75 | 51.95 | 62.34 | 72.73 |
| 2025-05-18 19:59 | 2408.20 | 3.53 | 51.83 | 62.19 | 72.56 |
| 2024-04-13 20:06 | 3025.36 | 3.38 | 51.75 | 62.10 | 72.45 |
| 2024-03-05 19:55 | 3354.08 | 3.34 | 51.73 | 62.07 | 72.42 |
| 2025-02-25 07:24 | 2389.07 | 3.16 | 51.63 | 61.96 | 72.29 |

BTCUSDT, 60 minutes:

| start | close at start | drop % | LTV from 50 % | from 60 % | from 70 % |
|---|---|---|---|---|---|
| 2025-10-10 20:27 | 117118.89 | 12.91 | 57.41 | 68.89 | 80.38 |
| 2024-12-05 21:28 | 99272.30 | 8.84 | 54.85 | 65.82 | 76.79 |
| 2026-02-05 23:15 | 64701.66 | 7.17 | 53.86 | 64.64 | 75.41 |
| 2025-03-07 00:13 | 91123.95 | 7.09 | 53.81 | 64.58 | 75.34 |
| 2026-02-05 14:39 | 70711.30 | 5.64 | 52.99 | 63.59 | 74.19 |
| 2026-06-25 13:16 | 61349.99 | 5.27 | 52.78 | 63.34 | 73.90 |
| 2025-02-03 01:07 | 96278.97 | 5.23 | 52.76 | 63.31 | 73.86 |
| 2025-03-03 14:11 | 93669.99 | 4.86 | 52.55 | 63.07 | 73.58 |
| 2025-04-07 14:17 | 81230.14 | 4.56 | 52.39 | 62.86 | 73.34 |
| 2025-04-02 20:16 | 88439.41 | 4.48 | 52.34 | 62.81 | 73.28 |

BTCUSDT, 5 minutes:

| start | close at start | drop % | LTV from 50 % | from 60 % | from 70 % |
|---|---|---|---|---|---|
| 2025-10-10 21:15 | 111096.85 | 8.19 | 54.46 | 65.35 | 76.24 |
| 2024-12-05 22:23 | 98250.32 | 7.89 | 54.28 | 65.14 | 75.99 |
| 2025-11-21 07:30 | 85239.78 | 3.80 | 51.98 | 62.37 | 72.77 |
| 2025-08-24 19:32 | 114446.93 | 3.29 | 51.70 | 62.04 | 72.38 |
| 2025-03-07 00:13 | 91123.95 | 3.17 | 51.64 | 61.96 | 72.29 |
| 2025-01-23 15:04 | 105720.52 | 2.86 | 51.47 | 61.77 | 72.06 |
| 2025-04-07 14:18 | 80670.94 | 2.75 | 51.41 | 61.70 | 71.98 |
| 2026-01-30 01:38 | 83299.24 | 2.62 | 51.34 | 61.61 | 71.88 |
| 2026-01-31 18:38 | 77813.02 | 2.62 | 51.34 | 61.61 | 71.88 |
| 2026-08-22 05:06 | 78528.13 | 2.58 | 51.33 | 61.59 | 71.86 |

## 4. Part B: oracle designs and time-to-line **[MODEL]**

### B0 Validation of the simulated push feeds against real Chainlink rounds (10 Oct 2025, 20:00–23:00 UTC)

| field | ETH/USD Ethereum | BTC/USD Base |
|---|---|---|
| feed | 0.5 % / 3600 s | 0.1 % / 1200 s |
| real rounds | 55 | 150 |
| simulated posts | 47 | 102 |
| real minimum | 3464.40 at 21:20:47 | 105107.25 at 21:20:31 |
| simulated minimum | 3500.60 at 21:20 bar | 103975.26 at 21:20 bar |
| Binance minimum low / close | 3435.00 / 3488.88 | 102000.00 / 103975.26 |
| real median / max gap between rounds (s) | 102 / 600 | 32 / 390 |
| real / simulated largest single step % | 4.06 / 4.27 | 2.50 / 4.29 |

The minute-close simulation posts about a quarter fewer rounds than the real feeds (47 vs 55; 102 vs 150) because it cannot see intra-minute moves, and its largest single step is larger than the real feed's. The real mainnet ETH feed printed 3464.40, below the simulated 3500.60 and below the Binance minute close of 3488.88 (the real aggregate includes other venues), so the simulation is somewhat kinder to the borrower than the real feed at the bottom. All designs below are derived from the same Binance series, so oracle-versus-market divergence other than lag is not in the model; `backtest.md` §1 reports the real oracle up to 5.3 % below Binance for ETH at 21:21 on 10 Oct.

### B1 Default corridor (act 70 %, liquidation 86 %): oracle-LTV crossings without an agent

Positions starting at 50 % never reached the act line under any design in any window; peak oracle LTVs were 55.2 % (5 Mar 2024), 62.8 % (5 Aug 2024), 65.0 % (3 Feb 2025), 57.7 % (10 Oct 2025) and 59.9 % (5 Feb 2026) on the push and pull designs, lower on the TWAPs (`out/B_crossings.csv`). For 60 % and 70 % starts:

| window | start_ltv | design | oracle LTV at t0 % | act line crossed | liquidation line crossed | gap s | peak oracle LTV % | peak at |
|---|---|---|---|---|---|---|---|---|
| 2024-03-05 | 0.6 | cl_base | 60.0 | never | never |  | 66.3 | 2024-03-05 19:57:00 |
| 2024-03-05 | 0.6 | cl_main | 60.2 | never | never |  | 66.3 | 2024-03-05 19:57:00 |
| 2024-03-05 | 0.6 | pull_1m | 60.0 | never | never |  | 66.3 | 2024-03-05 19:57:00 |
| 2024-03-05 | 0.6 | twap_30 | 59.9 | never | never |  | 64.1 | 2024-03-05 21:04:00 |
| 2024-03-05 | 0.6 | twap_60 | 59.7 | never | never |  | 63.8 | 2024-03-05 20:53:00 |
| 2024-03-05 | 0.7 | cl_base | 70.0 | 2024-03-05 19:04:00 | never |  | 77.3 | 2024-03-05 19:57:00 |
| 2024-03-05 | 0.7 | cl_main | 70.2 | 2024-03-05 19:04:00 | never |  | 77.3 | 2024-03-05 19:57:00 |
| 2024-03-05 | 0.7 | pull_1m | 70.0 | 2024-03-05 19:04:00 | never |  | 77.3 | 2024-03-05 19:57:00 |
| 2024-03-05 | 0.7 | twap_30 | 69.9 | 2024-03-05 19:08:00 | never |  | 74.7 | 2024-03-05 21:04:00 |
| 2024-03-05 | 0.7 | twap_60 | 69.6 | 2024-03-05 19:20:00 | never |  | 74.4 | 2024-03-05 20:53:00 |
| 2024-08-05 | 0.6 | cl_base | 60.0 | 2024-08-05 01:11:00 | never |  | 75.4 | 2024-08-05 01:11:00 |
| 2024-08-05 | 0.6 | cl_main | 60.0 | 2024-08-05 01:11:00 | never |  | 75.4 | 2024-08-05 01:11:00 |
| 2024-08-05 | 0.6 | pull_1m | 60.0 | 2024-08-05 01:11:00 | never |  | 75.4 | 2024-08-05 01:11:00 |
| 2024-08-05 | 0.6 | twap_30 | 60.2 | 2024-08-05 02:19:00 | never |  | 72.8 | 2024-08-05 06:52:00 |
| 2024-08-05 | 0.6 | twap_60 | 59.9 | 2024-08-05 02:38:00 | never |  | 72.0 | 2024-08-05 07:14:00 |
| 2024-08-05 | 0.7 | cl_base | 70.0 | 2024-08-05 00:18:00 | 2024-08-05 01:11:00 | 3180.0 | 88.0 | 2024-08-05 01:11:00 |
| 2024-08-05 | 0.7 | cl_main | 70.0 | 2024-08-05 00:18:00 | 2024-08-05 01:11:00 | 3180.0 | 88.0 | 2024-08-05 01:11:00 |
| 2024-08-05 | 0.7 | pull_1m | 70.0 | 2024-08-05 00:18:00 | 2024-08-05 01:11:00 | 3180.0 | 88.0 | 2024-08-05 01:11:00 |
| 2024-08-05 | 0.7 | twap_30 | 70.2 | 2024-08-05 00:18:00 | never |  | 85.0 | 2024-08-05 06:52:00 |
| 2024-08-05 | 0.7 | twap_60 | 69.9 | 2024-08-05 00:26:00 | never |  | 84.0 | 2024-08-05 07:14:00 |
| 2025-02-03 | 0.6 | cl_base | 60.0 | 2025-02-03 01:56:00 | never |  | 78.0 | 2025-02-03 02:08:00 |
| 2025-02-03 | 0.6 | cl_main | 60.2 | 2025-02-03 01:56:00 | never |  | 78.0 | 2025-02-03 02:08:00 |
| 2025-02-03 | 0.6 | pull_1m | 60.0 | 2025-02-03 01:56:00 | never |  | 78.0 | 2025-02-03 02:08:00 |
| 2025-02-03 | 0.6 | twap_30 | 60.2 | never | never |  | 69.5 | 2025-02-03 02:36:00 |
| 2025-02-03 | 0.6 | twap_60 | 60.1 | never | never |  | 68.9 | 2025-02-03 03:04:00 |
| 2025-02-03 | 0.7 | cl_base | 70.0 | 2025-02-03 01:13:00 | 2025-02-03 02:08:00 | 3300.0 | 91.0 | 2025-02-03 02:08:00 |
| 2025-02-03 | 0.7 | cl_main | 70.3 | 2025-02-03 01:13:00 | 2025-02-03 02:08:00 | 3300.0 | 91.0 | 2025-02-03 02:08:00 |
| 2025-02-03 | 0.7 | pull_1m | 70.0 | 2025-02-03 01:13:00 | 2025-02-03 02:08:00 | 3300.0 | 91.0 | 2025-02-03 02:08:00 |
| 2025-02-03 | 0.7 | twap_30 | 70.2 | 2025-02-03 01:13:00 | never |  | 81.1 | 2025-02-03 02:36:00 |
| 2025-02-03 | 0.7 | twap_60 | 70.1 | 2025-02-03 01:13:00 | never |  | 80.4 | 2025-02-03 03:04:00 |
| 2025-10-10 | 0.6 | cl_base | 60.0 | never | never |  | 69.2 | 2025-10-10 21:21:00 |
| 2025-10-10 | 0.6 | cl_main | 60.3 | never | never |  | 69.0 | 2025-10-10 21:20:00 |
| 2025-10-10 | 0.6 | pull_1m | 60.0 | never | never |  | 69.2 | 2025-10-10 21:21:00 |
| 2025-10-10 | 0.6 | twap_30 | 60.2 | never | never |  | 66.0 | 2025-10-10 21:44:00 |
| 2025-10-10 | 0.6 | twap_60 | 60.2 | never | never |  | 64.2 | 2025-10-11 08:15:00 |
| 2025-10-10 | 0.7 | cl_base | 70.0 | 2025-10-10 20:30:00 | never |  | 80.7 | 2025-10-10 21:21:00 |
| 2025-10-10 | 0.7 | cl_main | 70.3 | 2025-10-10 20:30:00 | never |  | 80.5 | 2025-10-10 21:20:00 |
| 2025-10-10 | 0.7 | pull_1m | 70.0 | 2025-10-10 20:30:00 | never |  | 80.7 | 2025-10-10 21:21:00 |
| 2025-10-10 | 0.7 | twap_30 | 70.2 | 2025-10-10 20:30:00 | never |  | 77.0 | 2025-10-10 21:44:00 |
| 2025-10-10 | 0.7 | twap_60 | 70.3 | 2025-10-10 20:30:00 | never |  | 74.9 | 2025-10-11 08:15:00 |
| 2026-02-05 | 0.6 | cl_base | 60.1 | 2026-02-06 00:14:00 | never |  | 71.8 | 2026-02-06 00:20:00 |
| 2026-02-05 | 0.6 | cl_main | 60.1 | 2026-02-06 00:14:00 | never |  | 71.8 | 2026-02-06 00:20:00 |
| 2026-02-05 | 0.6 | pull_1m | 60.0 | 2026-02-06 00:14:00 | never |  | 71.8 | 2026-02-06 00:20:00 |
| 2026-02-05 | 0.6 | twap_30 | 60.7 | 2026-02-06 00:27:00 | never |  | 70.3 | 2026-02-06 00:34:00 |
| 2026-02-05 | 0.6 | twap_60 | 60.9 | never | never |  | 69.4 | 2026-02-06 00:38:00 |
| 2026-02-05 | 0.7 | cl_base | 70.1 | 2026-02-05 14:40:00 | never |  | 83.8 | 2026-02-06 00:20:00 |
| 2026-02-05 | 0.7 | cl_main | 70.1 | 2026-02-05 14:40:00 | never |  | 83.8 | 2026-02-06 00:20:00 |
| 2026-02-05 | 0.7 | pull_1m | 70.0 | 2026-02-05 14:40:00 | never |  | 83.8 | 2026-02-06 00:20:00 |
| 2026-02-05 | 0.7 | twap_30 | 70.9 | 2026-02-05 14:40:00 | never |  | 82.0 | 2026-02-06 00:34:00 |
| 2026-02-05 | 0.7 | twap_60 | 71.0 | 2026-02-05 14:40:00 | never |  | 81.0 | 2026-02-06 00:38:00 |

Reading: a 70 % start is on the act line, so the act crossing is the first bar after t0 on the push and pull designs (the TWAPs cross 4–17 minutes later on 5 Mar 2024 and 8 minutes later on 5 Aug 2024). The liquidation line was reached from 70 % only in the two deepest windows and only on the push and pull designs: 5 Aug 2024 at 01:11 (gap 3,180 s = 53 min after the act crossing) and 3 Feb 2025 at 02:08 (gap 3,300 s = 55 min). The 30- and 60-minute TWAPs never reached 86 % from 70 % (peaks 85.0 % and 84.0 % on 5 Aug 2024; 81.1 % and 80.4 % on 3 Feb 2025). From 60 %, the liquidation line was never reached under any design.

### B2 Default corridor: agent reacting at the act line

111 (window, start, design, latency) rows crossed the act line; in all 111 the sale landed before the liquidation crossing; 0 were liquidated; in 31 rows the first fill was more than 1 % under the venue's oracle (which the build executor's rule would reject). Fill price relative to the venue oracle at landing and ETH sold:

| window | start_ltv | design | fill vs oracle %, 30 s | 120 s | 300 s | ETH sold, 30 s | 120 s | 300 s |
|---|---|---|---|---|---|---|---|---|
| 2024-03-05 | 0.70 | cl_base | -0.99 | -1.04 | -1.12 | 0.41 | 0.41 | 0.42 |
| 2024-03-05 | 0.70 | cl_main | -0.79 | -0.83 | -1.31 | 0.41 | 0.41 | 0.41 |
| 2024-03-05 | 0.70 | pull_1m | -1.00 | -1.00 | -1.00 | 0.41 | 0.41 | 0.42 |
| 2024-03-05 | 0.70 | twap_30 | -1.51 | -1.75 | -1.37 | 0.41 | 0.42 | 0.41 |
| 2024-03-05 | 0.70 | twap_60 | -2.54 | -2.77 | -2.48 | 0.42 | 0.43 | 0.42 |
| 2024-08-05 | 0.60 | cl_base | -1.00 | -1.00 | -1.00 | 0.45 | 0.43 | 0.39 |
| 2024-08-05 | 0.60 | cl_main | -1.00 | -1.00 | -1.00 | 0.45 | 0.43 | 0.39 |
| 2024-08-05 | 0.60 | pull_1m | -1.00 | -1.00 | -1.00 | 0.45 | 0.43 | 0.39 |
| 2024-08-05 | 0.60 | twap_30 | -3.66 | -3.11 | -1.01 | 0.44 | 0.43 | 0.42 |
| 2024-08-05 | 0.60 | twap_60 | -2.06 | -2.32 | -2.27 | 0.42 | 0.42 | 0.42 |
| 2024-08-05 | 0.70 | cl_base | -1.00 | -1.00 | -1.00 | 0.42 | 0.41 | 0.42 |
| 2024-08-05 | 0.70 | cl_main | -1.00 | -0.52 | -1.30 | 0.42 | 0.42 | 0.42 |
| 2024-08-05 | 0.70 | pull_1m | -1.00 | -1.00 | -1.00 | 0.42 | 0.41 | 0.42 |
| 2024-08-05 | 0.70 | twap_30 | -1.46 | -0.97 | -1.67 | 0.42 | 0.41 | 0.42 |
| 2024-08-05 | 0.70 | twap_60 | -2.32 | -2.11 | -2.14 | 0.42 | 0.42 | 0.42 |
| 2025-02-03 | 0.60 | cl_base | -1.00 | -1.00 | -1.00 | 0.35 | 0.37 | 0.34 |
| 2025-02-03 | 0.60 | cl_main | -1.00 | -1.00 | -1.00 | 0.35 | 0.37 | 0.34 |
| 2025-02-03 | 0.60 | pull_1m | -1.00 | -1.00 | -1.00 | 0.35 | 0.37 | 0.34 |
| 2025-02-03 | 0.70 | cl_base | -1.00 | -1.00 | -1.00 | 0.41 | 0.42 | 0.42 |
| 2025-02-03 | 0.70 | cl_main | -0.88 | -1.24 | -0.78 | 0.41 | 0.42 | 0.42 |
| 2025-02-03 | 0.70 | pull_1m | -1.00 | -1.00 | -1.00 | 0.41 | 0.42 | 0.42 |
| 2025-02-03 | 0.70 | twap_30 | -1.05 | -1.45 | -1.62 | 0.41 | 0.41 | 0.42 |
| 2025-02-03 | 0.70 | twap_60 | -1.07 | -1.41 | -1.52 | 0.41 | 0.41 | 0.42 |
| 2025-10-10 | 0.70 | cl_base | -1.11 | -1.00 | -0.96 | 0.41 | 0.41 | 0.41 |
| 2025-10-10 | 0.70 | cl_main | -0.70 | -0.85 | -0.81 | 0.41 | 0.41 | 0.41 |
| 2025-10-10 | 0.70 | pull_1m | -1.00 | -1.00 | -1.00 | 0.41 | 0.41 | 0.41 |
| 2025-10-10 | 0.70 | twap_30 | -0.79 | -0.94 | -0.91 | 0.41 | 0.41 | 0.41 |
| 2025-10-10 | 0.70 | twap_60 | -0.71 | -0.88 | -0.89 | 0.41 | 0.41 | 0.41 |
| 2026-02-05 | 0.60 | cl_base | -1.00 | -1.00 | -1.00 | 0.43 | 0.43 | 0.42 |
| 2026-02-05 | 0.60 | cl_main | -1.00 | -0.60 | -0.97 | 0.43 | 0.43 | 0.42 |
| 2026-02-05 | 0.60 | pull_1m | -1.00 | -1.00 | -1.00 | 0.43 | 0.43 | 0.42 |
| 2026-02-05 | 0.60 | twap_30 | -1.09 | -1.33 | -0.16 | 0.41 | 0.41 | 0.41 |
| 2026-02-05 | 0.70 | cl_base | -1.00 | -1.00 | -1.00 | 0.41 | 0.42 | 0.42 |
| 2026-02-05 | 0.70 | cl_main | -1.00 | -1.00 | -0.88 | 0.41 | 0.42 | 0.42 |
| 2026-02-05 | 0.70 | pull_1m | -1.00 | -1.00 | -1.00 | 0.41 | 0.42 | 0.42 |
| 2026-02-05 | 0.70 | twap_30 | 0.06 | -0.54 | -0.51 | 0.42 | 0.42 | 0.42 |
| 2026-02-05 | 0.70 | twap_60 | 0.32 | -0.27 | -0.23 | 0.42 | 0.42 | 0.42 |

(The pull design shows exactly −1.00 % because its oracle is the market close; the push feeds show −0.5 % to −1.3 % depending on where the last round sits; the TWAPs show −1.0 % to −3.7 % on the way down and slightly positive values on 5 Feb 2026, where the TWAP still carried the earlier, lower prices.)

### B3 Whipsaw: minutes the oracle LTV stayed above the act line (no agent, default corridor)

| window | start_ltv | design | crossed up | back below | minutes above | peak LTV % |
|---|---|---|---|---|---|---|
| 2024-03-05 | 0.7 | cl_base | 2024-03-05 19:04:00 | 2024-03-06 04:54:00 | 590.0 | 77.3 |
| 2024-03-05 | 0.7 | twap_30 | 2024-03-05 19:08:00 | 2024-03-06 05:10:00 | 602.0 | 74.7 |
| 2024-03-05 | 0.7 | twap_60 | 2024-03-05 19:20:00 | 2024-03-06 05:24:00 | 604.0 | 74.4 |
| 2024-08-05 | 0.6 | cl_base | 2024-08-05 01:11:00 | 2024-08-05 01:15:00 | 4.0 | 75.4 |
| 2024-08-05 | 0.6 | pull_1m | 2024-08-05 01:11:00 | 2024-08-05 01:15:00 | 4.0 | 75.4 |
| 2024-08-05 | 0.6 | twap_30 | 2024-08-05 02:19:00 | 2024-08-05 03:15:00 | 56.0 | 72.8 |
| 2024-08-05 | 0.6 | twap_60 | 2024-08-05 02:38:00 | 2024-08-05 03:41:00 | 63.0 | 72.0 |
| 2025-02-03 | 0.6 | cl_base | 2025-02-03 01:56:00 | 2025-02-03 01:57:00 | 1.0 | 78.0 |
| 2025-02-03 | 0.6 | twap_30 | never crossed |  |  | 69.5 |
| 2025-10-10 | 0.7 | cl_base | 2025-10-10 20:30:00 | 2025-10-10 21:57:00 | 87.0 | 80.7 |
| 2025-10-10 | 0.7 | twap_30 | 2025-10-10 20:30:00 | not within 12 h frame |  | 77.0 |
| 2025-10-10 | 0.7 | twap_60 | 2025-10-10 20:30:00 | not within 12 h frame |  | 74.9 |
| 2026-02-05 | 0.6 | cl_base | 2026-02-06 00:14:00 | 2026-02-06 00:32:00 | 18.0 | 71.8 |
| 2026-02-05 | 0.6 | twap_30 | 2026-02-06 00:27:00 | 2026-02-06 00:41:00 | 14.0 | 70.3 |

(`cl_main` and `pull_1m` rows equal `cl_base` to within a minute; full table in `out/B_whipsaw.csv`.) Example of the whipsaw cost, 5 Aug 2024, 60 % start: the push and pull oracles put the position above the act line for 4 minutes (01:11–01:15, peak 75.4 %); the 30-minute TWAP crossed up 68 minutes later (02:19) and stayed above for 56 minutes, the 60-minute TWAP crossed 87 minutes later (02:38) and stayed above for 63 minutes, both after the market had already bounced. A smoothed oracle delays the crossing on the way down and delays the recovery on the way up; an agent keyed to it sells after the bottom, at 2.1–3.7 % under the oracle it is being judged by (B2 rows 2024-08-05 / 0.60 / twap_30, twap_60).

### B4 Thin corridor (start 80 %, act line 83 %, liquidation 86 %)

Crossings without an agent:

| window | design | act line crossed | liquidation line crossed | gap s | peak oracle LTV % |
|---|---|---|---|---|---|
| 2024-03-05 | cl_base / cl_main / pull_1m | 19:38 | 19:56 | 1080 | 88.4 |
| 2024-03-05 | twap_30 | 19:57 | never | | 85.4 |
| 2024-03-05 | twap_60 | 20:13 | never | | 85.1 |
| 2024-08-05 | cl_base / cl_main / pull_1m | 00:55 | 01:07 | 720 | 100.5 |
| 2024-08-05 | twap_30 | 01:04 | 01:14 | 600 | 97.1 |
| 2024-08-05 | twap_60 | 01:12 | 01:27 | 900 | 96.0 |
| 2025-02-03 | cl_base / cl_main / pull_1m | 01:46 | 01:55 | 540 | 103.9 |
| 2025-02-03 | twap_30 | 01:56 | 02:06 | 600 | 92.7 |
| 2025-02-03 | twap_60 | 02:04 | 02:18 | 840 | 91.9 |
| 2025-10-10 | cl_base / cl_main / pull_1m | 21:00 | 21:15 | 900 | 92.3 (cl_main 92.0) |
| 2025-10-10 | twap_30 | 21:18 | 21:29 | 660 | 87.9 |
| 2025-10-10 | twap_60 | 21:28 | never | | 85.6 |
| 2026-02-05 | cl_base / cl_main / pull_1m | 15:06 | 15:29 | 1380 | 95.8 |
| 2026-02-05 | twap_30 | 15:23 | 18:32 | 11340 | 93.7 |
| 2026-02-05 | twap_60 | 15:37 | 18:44 | 11220 | 92.5 |

The shortest gap between the act line and the liquidation line in the five worst windows is 540 s (9 minutes, 3 Feb 2025, push and pull designs); the TWAPs give 600–900 s where they reach the line at all. Agent with the brief's fill rule: 75 rows crossed the act line; in all 75 the sale landed before the liquidation crossing; 0 liquidated; 39 first fills were more than 1 % under the venue oracle. On the push and pull designs the agent sold 0.66–0.72 ETH at 0.6–1.4 % under the oracle. On the TWAP venues it sold later and far under the oracle: 3.3–13.1 % under on 5 Aug 2024, 5.6–15.9 % under on 3 Feb 2025, 3.8–9.7 % under on 10 Oct 2025 (table in `out/tables.md`, B4). In one case (3 Feb 2025, `twap_60`, 300 s) restoring 50 % at the fill price required the entire 1 ETH: the sale at 2,263.05 left 2.47 USD of debt and no collateral.

## 5. Part C: split versus single **[MODEL]**

Single: 1 ETH on design (1). Split: 1/3 ETH on each of (1) Chainlink Base, (3) pull 1-minute, (4) 30-minute TWAP, same corridor, same latency, same debt per ETH. The asset is the same on all venues, so a price move reaches all of them; only the oracle timing differs. Fraction = ETH liquidated as a share of the 1 ETH; penalty = 4.384 % of the liquidated venue's debt, in USD per 1 ETH position.

Default corridor, all starts (50/60/70 %), all latencies, both fill rules: single 0 ETH liquidated, penalty 0; split 0 ETH liquidated, penalty 0 (30 window-start combinations × 3 latencies, `out/C_aggregate.csv`). Under the executor's 1 % rule the split's TWAP third reverted up to 64 times (3 Feb 2025, 30 s) before filling, and the split sold more ETH at a lower average price than the single venue (e.g. 3 Feb 2025, 70 % start, 30 s: single 0.412 ETH at 2,795.95; split 0.477 ETH at 2,653.97).

Thin corridor (start 80 %, act 83 %):

| window | fill rule | latency s | debt USD | single: ETH liquidated | penalty USD | ETH sold | avg fill | split: ETH liquidated | penalty USD | ETH sold | avg fill | reverts | TWAP third liquidated at |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 2024-03-05 | always | 30 / 120 / 300 | 2900.00 | 0 | 0 | 0.684 / 0.685 / 0.670 | 3437.86 / 3435.50 / 3467.56 | 0 | 0 | 0.703 / 0.703 / 0.682 | 3391.39 / 3392.24 / 3439.05 | 0 | |
| 2024-03-05 | revert_1pct | 30 / 120 / 300 | 2900.00 | 0 | 0 | 0.685 / 0.685 / 0.670 | 3435.50 / 3435.50 / 3467.56 | 0 | 0 | 0.688 / 0.689 / 0.678 | 3429.01 / 3428.23 / 3450.40 | 9 / 5 / 2 | |
| 2024-08-05 | always | 30 / 120 / 300 | 2152.30 | 0 | 0 | 0.693 / 0.697 / 0.718 | 2538.16 / 2531.44 / 2501.54 | 0 | 0 | 0.701 / 0.705 / 0.765 | 2522.41 / 2516.85 / 2424.23 | 0 | |
| 2024-08-05 | revert_1pct | 30 / 120 / 300 | 2152.30 | 0 | 0 | 0.693 / 0.697 / 0.718 | 2538.16 / 2531.44 / 2501.54 | 0.333 | 31.45 | 0.462 / 0.465 / 0.479 | 2538.16 / 2531.44 / 2501.54 | 9 / 4 / 1 | 01:14 |
| 2025-02-03 | always | 30 / 120 / 300 | 2265.52 | 0 | 0 | 0.687 / 0.715 / 0.713 | 2681.46 / 2637.01 / 2640.73 | 0 | 0 | 0.723 / 0.747 / 0.735 | 2614.43 / 2579.22 / 2601.55 | 0 | |
| 2025-02-03 | revert_1pct | 30 / 120 / 300 | 2265.52 | 0 | 0 | 0.687 / 0.715 / 0.713 | 2681.46 / 2637.01 / 2640.73 | 0.333 | 33.11 | 0.458 / 0.477 / 0.475 | 2681.46 / 2637.01 / 2640.73 | 9 / 4 / 1 | 02:06 |
| 2025-10-10 | always | 30 / 120 / 300 | 3219.52 | 0 | 0 | 0.679 / 0.666 / 0.660 | 3827.59 / 3856.95 / 3871.88 | 0 | 0 | 0.724 / 0.724 / 0.695 | 3710.02 / 3705.52 / 3779.87 | 0 | |
| 2025-10-10 | revert_1pct | 30 / 120 / 300 | 3219.52 | 0 | 0 | 0.666 / 0.666 / 0.660 | 3856.95 / 3856.95 / 3871.88 | 0.333 | 47.05 | 0.448 / 0.444 / 0.440 | 3842.13 / 3856.95 / 3871.88 | 11 / 5 / 2 | 21:29 |
| 2026-02-05 | always | 30 / 120 / 300 | 1678.32 | 0 | 0 | 0.675 / 0.681 / 0.683 | 2000.01 / 1992.47 / 1990.10 | 0 | 0 | 0.690 / 0.693 / 0.697 | 1978.83 / 1976.25 / 1972.01 | 0 | |
| 2026-02-05 | revert_1pct | 30 / 120 / 300 | 1678.32 | 0 | 0 | 0.675 / 0.681 / 0.683 | 2000.01 / 1992.47 / 1990.10 | 0 | 0 | 0.691 / 0.695 / 0.696 | 1981.63 / 1976.81 / 1975.28 | 19 / 9 / 3 | |

Aggregate over the five windows (`out/C_aggregate.csv`):

| corridor | fill rule | latency s | windows | single: ETH liquidated (sum) | split: ETH liquidated (sum) | single penalty USD | split penalty USD |
|---|---|---|---|---|---|---|---|
| default | always / revert_1pct | 30 / 120 / 300 | 15 (5 windows × 3 starts) | 0.00 | 0.00 | 0.00 | 0.00 |
| thin | always | 30 / 120 / 300 | 5 | 0.00 | 0.00 | 0.00 | 0.00 |
| thin | revert_1pct | 30 / 120 / 300 | 5 | 0.00 | 1.00 | 0.00 | 111.61 |

Reading: in no configuration did the split liquidate less than the single venue. With the brief's fill rule neither is ever liquidated; the split sells 0.01–0.05 ETH more at a lower average price because its TWAP third acts later. With the executor's 1 % rule the single Chainlink-Base venue is still never liquidated (its fills sit at −0.6 % to −1.4 % and at most one revert), while the split's TWAP third cannot fill within 1 % of its oracle during the fall and is liquidated in three of five windows (5 Aug 2024 01:14, 3 Feb 2025 02:06, 10 Oct 2025 21:29): one third of the ETH in each, 31.45 + 33.11 + 47.05 = 111.61 USD of penalty per 1 ETH position, against 0 for the single venue. The "different oracle" that is slower to see the move is also slower to let the agent out.

## 6. Part D: the other side of the ledger (from `protocol_risk_diversification.md` only)

Splitting a position across venues adds venue exposure. The existing note's KQ1 lists, for 2024–2026, venue-class incidents with realized losses: lending-contract and oracle events of single-digit to low-double-digit millions at mid-tier venues (Sonne $20M, UwU $20M, Rho $7.6M, Polter $8.7M, zkLend $9.55M, Abracadabra $13M, Loopscale $5.8M, Resupply $9.6M, Venus 2026 $2.15M bad debt, Moonwell $1.78M and $8.7M, Blend $10.97M, Bonzo $9.05M, Tectonic $75–124M; TRM counted 32 price-manipulation exploits in 2026 alone against 12 in 2025); the two largest 2026 lender losses came through keys and a bridge rather than lending logic (Drift ~$285–295M via a multisig takeover; Kelp rsETH ~$292M unbacked mint leaving Aave $124–230M of bad debt), while Morpho's isolated markets held a comparable oracle failure (Resolv, March 2026) to ~$3.8M and Kamino reports zero bad debt; the NEAR-specific stack had Rhea Lend ($18.4M, April 2026) and NEAR Intents ($3.8M, October 2026); chain-level incidents on Base and Solana were liveness outages (Base 33 min in Aug 2025, 116 and 20 min in June 2026; Solana none since Feb 2024) with no reported loss of funds. KQ2 gives analyst loss-rate estimates of ~0.5–0.7 % of TVL per year for 2024–2025, ~2–2.5 % annualized in H1 2026, and Nexus Mutual premiums of 0.1–0.5 % per year for blue-chip protocols up to 1.8–5 % for riskier listings. KQ4 adds that a NEAR-controlled multi-venue position makes the MPC a shared dependency of every non-NEAR venue (5-of-8 in May 2026; reportedly 11-of-17 by Oct 2026) and that Base's upgrade path has no exit window. The existing note's inference stands: per-venue splitting bounds the frequent mid-size venue-level losses, does not reduce expected loss when venues are of similar quality, and adds a venue and a controller surface for each venue added.

## 7. What the numbers support

1. On the covered minutes of 2024-01-07 to 2026-10-25 (837 days for ETH, 605 for BTC, sampled around liquidation events), the largest ETH drop was 9.94 % in one minute (3 Feb 2025 02:06), 16.02 % in five minutes (5 Aug 2024 01:05) and 24.96 % in sixty minutes (3 Feb 2025 01:12); BTC's were 5.92 %, 8.19 % and 12.91 %.
2. From 50 % LTV, no window at any horizon up to 60 minutes reached the 70 % act line (which needs a 28.57 % drop) or the 86 % liquidation line (41.86 %), for ETH or BTC.
3. From 60 % LTV, the act line was crossed in 3 five-minute and 13 ten-minute ETH windows, all in the 5 Aug 2024 and 3 Feb 2025 clusters, and the liquidation line (30.23 %) was never crossed within 60 minutes.
4. From 70 % LTV, the liquidation line (18.60 %) was crossed only at the 30- and 60-minute horizons, never within 10 minutes; in the replay the gap between the act-line crossing and the liquidation crossing on the push and pull designs was 3,180 s (5 Aug 2024) and 3,300 s (3 Feb 2025), and the 30- and 60-minute TWAPs never reached the liquidation line from 70 %.
5. With the brief's fill rule, the agent's sale landed before the liquidation crossing in every one of the 111 default-corridor and 75 thin-corridor cases, at every latency from 30 s to 5 min, on every oracle design; no position was liquidated.
6. Even with a 3 pp corridor (act 83 %, liquidation 86 %), the shortest act-to-liquidation gap in the five worst ETH windows was 540 s on the push and pull designs; the TWAPs gave 600–900 s where they reached the line.
7. A smoothed oracle delays both the crossing on the way down and the return on the way up (5 Aug 2024, 60 % start: push and pull oracles above the act line for 4 minutes from 01:11; the 30-minute TWAP crossed at 02:19 and stayed 56 minutes; the 60-minute TWAP crossed at 02:38 and stayed 63 minutes), and the agent keyed to it sold after the bottom at 2.1–3.7 % under its oracle in that case, up to 15.9 % under in the thin-corridor cases.
8. In the single-versus-split comparison, the split never liquidated less than the single venue in any of the 120 configurations; with the executor's 1 % fill rule the split's TWAP third was liquidated in 3 of 5 thin-corridor windows (111.61 USD of penalty per 1 ETH position) while the single Chainlink-Base venue was never liquidated.
9. The simulated push feeds post about a quarter fewer rounds than the real Chainlink feeds did on 10 Oct 2025 (47 vs 55 on mainnet ETH/USD; 102 vs 150 on Base BTC/USD), and the real mainnet ETH feed printed 3464.40 against the simulated 3500.60; the model is slightly kinder to the borrower than the real feeds at the bottom.
10. The price series is the same on every venue in the replay; only the oracle's timing differs. Splitting changes when each venue sees a move, not whether the move arrives.

## 8. What they do not support

- That a venue split reduces liquidation or penalty in fast moves: in this data it never did, and under the build's 1 % fill rule it added liquidations on the slow-oracle venue.
- That "a same-block liquidation" occurred or could have occurred at the default corridor in 2024–2026 on these series: from 50 % no window came within 11 pp of the act line; the brief's premise of a one-block jump past both lines is outside the measured range (largest one-minute drop 9.94 %, against the 41.86 % needed from 50 %).
- Rates or probabilities: the minute file is sampled around liquidation events, so the counts in A2 are not per-year frequencies.
- Sub-minute latency: 30 s is modelled as 60 s; the real Chainlink feeds post more often than the minute closes (B0), and `backtest.md` found the real oracle up to 5.3 % below Binance on 10 Oct, with single rounds moving LTV by 3–9 pp, and a thin-buffer keeper saving only 3/10 positions at 30 s. The replay here, built from one exchange's minute closes with a fill that always lands, is more favourable to the agent than that backtest; the two agree that the buffer, not the speed, is what saves the position.
- Oracle-versus-market divergence, exchange outages, reverted or stuck transactions, partial liquidations, Morpho's pre-liquidation, Aave's close factor, interest accrual, gas, or any venue other than a Morpho-style 86 % LLTV market.
- The TWAP designs stand in for Fluid and Scope only loosely (Fluid uses a short UniV3 TWAP check against Chainlink/Redstone; Scope uses a 1-hour EMA with its own sample rules).
- Anything about the expected-loss side of a split: part D is the existing note's material, not new measurement.

## 9. Draft for the product's sandbox (under "A one-block jump past the line cannot be answered by any agent"; 79 words)

On 2024–2026 minute data the largest one-minute ETH drop was 9.9 % and the largest five-minute drop 16.0 %; from a 50 % loan neither comes near the 70 % act line, and from 70 % the act line and the liquidation line were never less than nine minutes apart. Splitting one loan over venues with different oracles (production release) changes when each venue sees a move, not whether it arrives; in our replay it never prevented a liquidation.

## 10. Files

- Note: `research/venue-split/venue_split_gap_risk.md` (this file).
- Scripts: `scripts/venue_split_gap_risk/common.py`, `part_a_gaps.py`, `part_b_oracles.py` (`thin` argument for the thin corridor), `part_b_validate.py`, `part_c_split.py`, `make_tables.py`.
- Outputs: `scripts/venue_split_gap_risk/out/A_summary.csv`, `A_threshold_counts.csv`, `A_top10_{symbol}_{h}m_{measure}.csv` (24 files), `A_run.txt`; `B_crossings.csv`, `B_agent_latency.csv`, `B_whipsaw.csv`, `B_series_{window}.csv`, `B_raw.pkl`, `B_run.txt`, the same with prefix `Bthin_`; `B_validation_real_rounds.csv`, `B_validation_run.txt`; `C_single_vs_split.csv`, `C_aggregate.csv`, `C_run.txt`; `tables.md`.

Ended 2026-11-11 12:14 UTC.
