# Data files not included here

This folder holds the note (`README.md`), its brief and the original data-source readme (`data/raw/step1_readme_original.md`). The scripts and every data file, large or small, are internal (see `../README.md`, "What stays internal, and what we will do with it"); the list below is what we hold, with sizes. Every number in `README.md` is computed from them by the internal scripts; the small result tables they produce are not included either. Judges who want to check a number can ask for the relevant extract.

| File or folder in `data/` | Size |
|---|---|
| `raw/` (cached HTTP pages and RPC responses of every pull; only `raw/step1_readme_original.md` is included) | 10 GB |
| `grid_train/` | 908 MB |
| `features_hourly.parquet` | 672 MB |
| `retrieval.parquet` | 414 MB |
| `positions_hourly.parquet` | 274 MB |
| `step4_hours.parquet` | 116 MB |
| `oracle_rounds_ext.csv` | 99 MB |
| `binance_1m_ext.parquet` | 81 MB |
| `continuation_cache/` | 77 MB |
| `step4_predictions.parquet` | 74 MB |
| `owner_transactions.parquet` | 59 MB |
| `holdout_results/` | 54 MB |
| `calibration_train_results.parquet` | 22 MB |
| `eth_oracle_standard.json` | 18 MB |
| `eth_oracle_direct.json` | 9.2 MB |
| `step4_primary_alerts.parquet` | 8.3 MB |
| `replay_oracles/` | 8.0 MB |
| `evaluation_results.parquet` | 7.2 MB |
| `default_loss_analysis.parquet` | 6.3 MB |
| `survivor_draws/`, `survivor_owner_prefetch/`, `history_batch_plans/` (intermediate pull state) | 5.7 MB together |

To regenerate them with the internal scripts, run the commands in `README.md` in this order: "Reproduction" (Step 1 pulls from the public Morpho API, public RPCs and Binance; free, no keys), "Step 2 reproduction", "Reproduce this analysis and Step 3", then "Model retention and exact reproduction" (Step 4). The commands are written for the planning repository's layout (`research_notes/Liquidation dataset/` is this folder); `scripts/binance.py` and `scripts/assemble.py` also read files of the earlier backtest from the sibling folder `Voices on AI managed crypto loans/`, which is `../liquidation-backtest/` here. Python versions are pinned in `scripts/requirements.txt` and `scripts/requirements_step4.txt`; the C++ simulators (`scripts/*.cpp`) are compiled with the local C++17 toolchain.
