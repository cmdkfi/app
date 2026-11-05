"""Generate the research README from saved measurements; never network."""
import json
from pathlib import Path
import sys
import datetime as dt
import pandas as pd
ROOT=Path(__file__).resolve().parents[1];D=ROOT/'data'
def read(name):return json.loads((D/name).read_text())
def table(df):
    return '| '+' | '.join(df.columns)+' |\n| '+' | '.join(['---']*len(df.columns))+' |\n'+''.join('| '+' | '.join(str(x).replace('|','/').replace('\n',' ') for x in row)+' |\n' for row in df.itertuples(index=False,name=None))
def closure():
    quality=read('quality.json');validation=read('validation.json');selected=read('eth_weth_selection.json');aave=read('aave_retry_status.json')
    markets=read('markets.json');active={x['marketId'] for x in markets}
    counts=pd.read_csv(D/'counts_market_year.csv');counts=counts[counts.venue=='Morpho'].drop(columns=['market','coverage'])
    samples=[]
    for x in markets:
        s=read(f"survivor_selection/{x['chain']['id']}-{x['marketId']}.json")
        samples.append(dict(chain=x['chain']['id'],pair=x['collateralAsset']['symbol']+'/USDC',population=s['population'],screened=s['screened'],survivors=s['selected']))
    candidates=pd.DataFrame([dict(market=x['marketId'],created_utc=pd.Timestamp(int(x['creationTimestamp']),unit='s',tz='UTC').isoformat(),cumulative_borrow_usdc=x['cumulative_borrow_usdc'],borrow_events=x['borrow_events']) for x in selected['candidates']]).sort_values('created_utc')
    rounds=pd.read_csv(D/'oracle_rounds_ext.csv',low_memory=False)
    oracle_counts=rounds.groupby(['chain','feed']).agg(rounds=('round_id','size'),usable_rounds=('usable_for_replay','sum')).reset_index()
    old=(D/'raw/step1_readme_original.md').read_text();assumptions=old.split('## Assumptions and interpretation\n')[1].split('\n## Outputs')[0]
    assumptions=assumptions.replace('Highest borrow means current','For the unchanged markets, highest borrow means current')
    chosen=selected['chosen']
    text=f'''# Liquidation dataset — Steps 1 and 2

Step 1 is closed under the founder's reduced scope. Aave is excluded from Steps 2–4. Step 2 has not yet run. Generated {dt.datetime.now(dt.timezone.utc).isoformat()}.

All writes are confined to this folder. All Python commands use `-I`; sources are public and free. No secrets, mainnet transactions, commits, or changes to the original backtest files.

## Step 1 closure

The fixed cutoff remains 26 October 2026 00:00 UTC exclusive. Morpho event history begins at each market's creation.

Counts below come from `data/counts_market_year.csv`; hourly rows now include explicitly flagged missing-hour rows. Borrower counts are unique within market-year and are not additive across years.

{table(counts)}
Survivor populations and selections come from `data/survivor_selection/*.json`. The original wstETH selection is unchanged; seed {read('survivor_selection/'+str(markets[0]['chain']['id'])+'-'+markets[0]['marketId']+'.json')['seed']}.

{table(pd.DataFrame(samples))}
Saved {quality['hourly_rows']:,} requested position-hours for {quality['borrowers']:,} borrower positions, including {quality['missing_hourly_rows']:,} hours with `missing_position_hour=true` and null balances. The original missing hours are retained; additional gaps belong to the replacement market. No position state is imputed. Labels are null when the snapshot or any requested hour through the label horizon is missing (`label_window_missing_6h` / `label_window_missing_24h`), or when negative follow-up is right-censored. Owner-event rows: {quality['owner_transactions']:,}.

### Aave disposition

The authorized additional attempt used 500-block steps and a hard two-hour deadline. It ended after {aave['elapsed_s']:.3f} seconds: PublicNode returned HTTP 403, Ankr required authentication, and LlamaRPC returned HTTP 525. No further endpoint was tried. {aave['remaining_pages']:,} original 2,000-block pages remain missing. Aave is excluded from Steps 2–4; the {len(pd.read_parquet(D/'aave_target_subset_side_table.parquet')):,} previously collected target-subset events are preserved in `data/aave_target_subset_side_table.parquet`. Historical Aave rows retained in `liquidations.parquet` have `included_steps_2_4=false`. Raw all-asset logs remain cached; observed zeros outside these pages mean unknown coverage.

### Replacement Ethereum WETH market

Selected `{chosen['marketId']}`, created {pd.Timestamp(int(chosen['creationTimestamp']),unit='s',tz='UTC').isoformat()}, with {chosen['cumulative_borrow_usdc']:,.6f} USDC cumulative borrowing. Selection used creation date first among markets passing the large-market threshold, then cumulative volume for ties. It still has no pre-2026 history and is holdout-only. The original highest-current-borrow selection is retained in `data/markets_step1_original.json`.

[ASSUMPTION] “Large” means at least {selected['large_threshold_usdc']:,} USDC gross cumulative Borrow-event volume through the fixed cutoff, including repeat borrowing. The oldest markets are tiny by that measure. The threshold and all candidates are recorded in `data/eth_weth_selection.json`; selection is not based on replay outcomes.

Candidate rows come from the cached markets list and complete Borrow-event pages, summarized in `data/eth_weth_selection.json`:

{table(candidates)}
### Oracle handling

`data/oracle_phase_bounds.json` verifies proxy-to-aggregator mappings with `phaseAggregators(phaseId)` and first/last round timestamps. An overlap between consecutive aggregators does not prove the exact activation time: every round in the ambiguous interval is unusable. `usable_for_replay` is persisted in `data/oracle_rounds_ext.csv`; replay uses Binance in unresolved spans. The replacement ETH feed's first phase was unreadable; phase 2 was verified as active at the market's creation block, then fetched successfully.

[ASSUMPTION] Non-overlapping phase interiors are treated as usable under the requested timestamp-based boundary inference. Overlaps and gaps use Binance. Exact activation transactions were not fetched; the replacement market's creation-block phase check is separate historical evidence.

The actual market configurations are cached in `data/market_oracle_config.json`: Base WETH has a USDC/USD denominator; Ethereum WETH has its own ETH/USD feed; wstETH has an exchange-rate numerator and USDC/ETH denominator. Supplemental quote-feed rounds were pulled rather than silently substituting a standard ETH/USD stream.

[ASSUMPTION] stETH/ETH parity is accepted. Morpho's selected wstETH oracle prices through the wstETH/ETH exchange rate, so parity matches that venue's exchange-rate pricing rather than a secondary-market stETH discount. Historical share-ratio samples are held as of their observation; no future sample is interpolated.

Oracle counts come from `data/oracle_rounds_ext.csv`:

{table(oracle_counts)}
## Assumptions retained from the initial pull
{assumptions}
## Validation, failures and timings

`data/validation.json` records passed key, arithmetic, label, price continuity, requested-round and survivor checks: {validation['hourly_rows']:,} hourly rows and {validation['verified_survivor_samples']:,} survivor episodes. These validate internal consistency, not missing-source completeness. Missing balances remain null.

Initial failure/recovery details are archived in `data/raw/step1_readme_original.md`: Morpho schema and pagination adaptations, rate limits, and the earlier Aave/phase-archive failures. New cached HTTP errors remain in `data/raw/`. Status files preserve measured elapsed time per command; jobs overlap and earlier overwritten invocations are timing lower bounds. See `data/source_times.csv` and the additional `data/closure_*_status.json` files.

## Reproduction

Use Python 3.14 and the versions pinned in `scripts/requirements.txt`. From the repository root:

```sh
python3 -I 'research_notes/Liquidation dataset/scripts/close_step1.py' candidates
python3 -I 'research_notes/Liquidation dataset/scripts/close_step1.py' select_market
python3 -I 'research_notes/Liquidation dataset/scripts/close_step1.py' replacement_pull
python3 -I 'research_notes/Liquidation dataset/scripts/close_step1.py' oracle_config
python3 -I 'research_notes/Liquidation dataset/scripts/close_step1.py' extra_oracles
python3 -I 'research_notes/Liquidation dataset/scripts/close_step1.py' replacement_oracle
'research_notes/Liquidation dataset/scripts/.venv/bin/python3' -I 'research_notes/Liquidation dataset/scripts/assemble.py'
'research_notes/Liquidation dataset/scripts/.venv/bin/python3' -I 'research_notes/Liquidation dataset/scripts/validate.py'
'research_notes/Liquidation dataset/scripts/.venv/bin/python3' -I 'research_notes/Liquidation dataset/scripts/report.py'
```

Successful exact HTTP pages are immutable and reused on restart. Do not restart the failed Aave attempt without a new instruction; `close_step1.py aave` is the recorded attempt command. All table assembly and validation are offline. No Step 3 or Step 4 code has run.
'''
    (ROOT/'README.md').write_text(text)
    if not (D/'step1_closure.json').exists():
        (D/'step1_closure.json').write_text(json.dumps({'status':'closed_reduced_scope','aave_excluded_steps':[2,3,4],'validation':validation,'closed_at':dt.datetime.now(dt.timezone.utc).isoformat()},indent=2)+'\n')

def completed():
    closure()
    text=(ROOT/'README.md').read_text().replace('Step 2 has not yet run.','Step 2 is complete; stop here before Step 3.')
    headline=pd.read_csv(D/'headline.csv');assets=pd.read_csv(D/'per_asset.csv');features=read('features_quality.json');check=read('step2_validation.json')
    plan=pd.read_parquet(D/'replay_case_plan.parquet');validation=pd.read_parquet(D/'replay_validation.parquet');grid=pd.read_csv(D/'calibration_grid.csv');frozen=read('selected_policy.json');policy=frozen['policy']
    baseline=pd.read_parquet(D/'replay_baselines.parquet');eligible=set(validation.loc[validation.eligible,'case_id']);baseline=baseline[baseline.case_id.isin(eligible)]
    actual=baseline[baseline.policy=='ACTUAL'].set_index('case_id');noaction=baseline[baseline.policy=='NOACTION'].set_index('case_id')
    actual_only=int(((actual.n_liqs>0)&(noaction.n_liqs==0)).sum());model_only=int(((actual.n_liqs==0)&(noaction.n_liqs>0)).sum())
    joined=actual[['first_liq_ts']].rename(columns={'first_liq_ts':'first_liq_ts_actual'}).join(noaction[['first_liq_ts']].rename(columns={'first_liq_ts':'first_liq_ts_model'}));joined=joined[(joined.first_liq_ts_actual>0)&(joined.first_liq_ts_model>0)]
    differences=joined.first_liq_ts_model-joined.first_liq_ts_actual
    money=lambda x:f'${x:,.2f}'
    metrics=[('Episodes','cases',lambda x:f'{int(x):,}'),('Observed liquidated episodes','observed_liquidated',lambda x:f'{int(x):,}'),
             ('Saved vs observed liquidation','saved',lambda x:f'{int(x):,}'),('Saved with no-action model support','saved_with_noaction_model_support',lambda x:f'{int(x):,}'),
             ('Liquidated under policy, all episodes','policy_liquidated',lambda x:f'{int(x):,}'),('Observed survivors liquidated in policy model','survivor_policy_liquidations',lambda x:f'{int(x):,}'),
             ('Liquidated only with policy vs no-action model','liquidated_only_with_policy',lambda x:f'{int(x):,}'),
             ('Real penalties, protocol formula','real_penalties',money),('Policy penalties','policy_penalties',money),
             ('Median extra collateral, % of start','median_extra_collateral_pct',lambda x:f'{x:+.2f}%'),
             ('Net equity vs actual at bottom','equity_vs_actual_bottom',money),('Net equity vs actual at episode end','equity_vs_actual_end',money),
             ('Managed equity at cutoff','policy_equity_today',money),('Actual leftover held passively at cutoff','actual_equity_today',money),
             ('Slippage','slippage',money),('Gas','gas',money),('Sales','sales',lambda x:f'{int(x):,}')]
    display=[]
    for label,col,fmt in metrics:
        row={'metric':label}
        for split in ['train','holdout']:
            for name in ['DEFAULT','SELECTED']:
                value=headline[(headline.split==split)&(headline.policy==name)].iloc[0][col];row[f'{split} {name.lower()}']=fmt(value)
        display.append(row)
    asset_display=assets[['split','policy','asset_group','cases','observed_liquidated','saved_with_noaction_model_support','policy_liquidated','equity_vs_actual_end','slippage','gas']].copy()
    for col in ['equity_vs_actual_end','slippage','gas']:asset_display[col]=asset_display[col].map(money)
    reasons=plan[~plan.included].groupby(['split','excluded_reason']).size().rename('episodes').reset_index()
    mismatch=validation[~validation.eligible].merge(plan[['case_id','split']],on='case_id').groupby(['split','reason']).size().rename('episodes').reset_index().rename(columns={'reason':'excluded_reason'})
    exclusions=pd.concat([reasons,mismatch],ignore_index=True);exclusions.to_csv(D/'step2_exclusions.csv',index=False)
    states=[read(f.name) for f in D.glob('step2_*_status.json')];timing=pd.DataFrame([{'command':x['source'],'seconds':round(x['elapsed_s'],3)} for x in states]).sort_values('command')
    timing.to_csv(D/'step2_source_times.csv',index=False)
    span=max(x['finished_at'] for x in states)-min(x['started_at'] for x in states)
    followup=[read(f.name) for f in D.glob('closure_*_status.json')]
    followup_timing=pd.DataFrame([{'command':x['source'],'seconds':round(x['elapsed_s'],3)} for x in followup]).sort_values('command')
    supplementary=read('closure_replacement_pull_status.json')['result']
    train=headline[headline.split=='train'].set_index('policy');hold=headline[headline.split=='holdout'].set_index('policy')
    comparison={'grid_winner':policy,'train_default_liquidated':int(train.loc['DEFAULT','policy_liquidated']),'train_selected_liquidated':int(train.loc['SELECTED','policy_liquidated']),
                'holdout_default_liquidated':int(hold.loc['DEFAULT','policy_liquidated']),'holdout_selected_liquidated':int(hold.loc['SELECTED','policy_liquidated']),
                'recommendation':'retain defaults; the requested grid winner did not improve survival','step2_wall_span_seconds':span,'noaction_missed_observed_liquidations':actual_only,'noaction_model_only_liquidations':model_only}
    (D/'step2_summary.json').write_text(json.dumps(comparison,indent=2)+'\n')
    text+='''
## Step 2 result

'''+f'''**Keep the existing defaults on this evidence.** The frozen grid winner caused {int(hold.loc['SELECTED','policy_liquidated']):,} holdout episodes to be liquidated, versus {int(hold.loc['DEFAULT','policy_liquidated']):,} under the defaults. It also had more training liquidations ({int(train.loc['SELECTED','policy_liquidated']):,} versus {int(train.loc['DEFAULT','policy_liquidated']):,}). No product settings were changed.

The best setting *inside the requested grid* is act **{policy['act']:.0%}**, target **{policy['target']:.0%}**, discretionary sell budget **{policy['budget']:.0%}**, maximum slice **{policy['slice']:.0%}**, cooldown **{policy['cooldown']} seconds**. The execution delay remains two minutes. It was frozen in `data/selected_policy.json` before the holdout command started. The defaults benchmark is 70% / 50% / 20%, with the original simulator's 25% slice and 120-second cooldown; those latter values lie outside the requested grid. All {len(grid)} grid settings and their training scores are in `data/calibration_grid.csv`.

### Hourly features and causal labels

`data/features_hourly.parquet` contains {features['rows']:,} rows. It has LTV, distance to LLTV, 1h/6h/24h price changes, 24h realized volatility, oracle-versus-Binance percentage, utilization, continuous borrow rate, hours since owner action, collateral-value position size, the two liquidation labels, and missing-state/window flags. There are {features['labels_6h']:,} usable 6h labels and {features['labels_24h']:,} usable 24h labels. Missing market-history rows: {features['missing_market_history_rows']:,}.

- [ASSUMPTION] Hourly price changes are decimal returns between observable hour-open prices. Realized volatility is the sample standard deviation of the preceding 24 hourly log returns, unannualized. wstETH uses the exchange-rate-adjusted price with only ratio samples available by that hour.
- [ASSUMPTION] `borrow_rate = log1p(borrowApy)` is continuous annual APR; utilization is a fraction. `position_size_usd` means collateral value at the venue oracle. `oracle_vs_binance_pct` is 100 times `(oracle / Binance - 1)`.
- Owner-action time uses only SupplyCollateral, WithdrawCollateral, Borrow and Repay events at or before the snapshot. Liquidator actions do not reset it. Missing balances and derived position features stay null; market-wide observations can still be present on flagged hours.

### Replay construction and selection

The plan covers {len(plan):,} first-liquidation and selected-survivor episodes. {int(plan.included.sum()):,} pass the missing-state/split/overlap screen; {int(validation.eligible.sum()):,} also pass the real-state reconstruction check. The scored cohorts contain {int(train.loc['DEFAULT','cases']):,} training and {int(hold.loc['DEFAULT','cases']):,} holdout episodes. These are episodes, not the full number of liquidation events or unique wallets.

- [ASSUMPTION] Replay windows are three days either side of the first liquidation or qualifying survivor hour, clipped to the available Step 1 context. They start/end on observed hourly boundaries. The minute-price pull covers those windows plus one completed minute for the first decision; overlapping windows share cached pages. This uses the brief's ±3-day replay-cluster span within the larger Step 1 histories.
- Whole replay episodes with a missing requested hour are excluded. Overlapping survivor/liquidation episodes on the same position are counted once, with liquidation episodes taking precedence. The original survivor cohorts remain in the dataset.
- Training episode ends must be before 1 January 2026; training survivor qualification also requires the full 30-day follow-up to end before that date. Holdout starts must be in 2026. No straddling episode informs selection.
- [ASSUMPTION] Endpoint reproduction requires collateral error no greater than max(1e-8 token, 1% of max(start, observed end)), and debt error no greater than max($1, 1% of max(start, observed end)). {int((~validation.eligible).sum())} cases failed this fixed check and were excluded. Their residuals are recorded in `data/replay_validation.parquet`; many are dust residuals on positions with zero start/end balances.
- [ASSUMPTION] The literal corridor is used, matching the grid's percentages; floor is 30%, no exchange signal, no buy-back. Interest uses the market's observed APR at episode start, held constant as in the reference simulator, without fitting it to later state. Gas is $0.03 per Base attempt and $5 per Ethereum attempt. Slippage, owner-flow clamping, protective sessions, the one-percent oracle execution ceiling, two-minute landing delay, and minute OHLC4 fills follow the original simulator. Protective sessions are not limited by the discretionary sell budget.
- Grid ranking is survival first (fewest liquidated episodes), then total episode-end equity versus actual, then slippage plus gas, then sales. Remaining exact ties favor settings closest to defaults, then their names. Ranking reads only training results. The headline continuation below is post-selection and never enters the ranking.
- The sample includes every liquidated borrower but only a seeded subset of qualifying survivors. It is enriched for stressful episodes and does not estimate unconditional liquidation probabilities across all loans. Market selection uses the specified present-day/historical-volume universe, not a point-in-time investment universe.

Exclusions below come from `data/replay_case_plan.parquet` and `data/replay_validation.parquet`; full case identities and reasons are retained.

{table(exclusions)}
### Headline table

Rows come from `data/evaluation_results.parquet`, aggregated in `data/headline.csv`. “Saved” uses observed liquidations as the denominator; the stricter supported count additionally requires the no-action model to liquidate. Positive equity differences are not pure penalty savings: leverage reduction can benefit from falling prices and lag during rallies.

{table(pd.DataFrame(display))}
“Who could not be saved” is retained case by case in `data/unsaved_cases.parquet`, including order blocking, reverts, lack of landed sales, and fallback-price flags. The no-action model misses {actual_only} observed liquidation episodes and liquidates {model_only} episodes that had no real liquidation in the replay window. Those discrepancies are reported rather than credited as certain policy saves. Among episodes liquidated in both, model-minus-real first-liquidation timing has median {differences.median():,.0f} seconds; the model acts at the earliest allowed crossing, so timing can precede actual keepers.

- [ASSUMPTION] “Bottom” is the episode's lowest Binance minute low, marked at that bar's opening timestamp with the last recorded state; “end” uses the episode-end minute close, consistently with the original backtest.
- [ASSUMPTION] “At cutoff” continues each endpoint to 26 October 2026 00:00 UTC using completed hourly Binance closes as proxy oracle/market prices, minute ticks, the same fixed APR, and no later owner flows. Policy remains active; actual leftover is passive under the same liquidation model. wstETH uses fetched as-of ratios, not an extrapolated yield. This is a hypothetical continuation, not the borrowers' observed later equity. It is evaluated only after policy selection and can use 2026 prices for training-origin episodes without feeding them into calibration.

### Per-asset view

Rows from `data/per_asset.csv`, grouping the same eligible case-policy rows into cbBTC and ETH-type collateral. `saved_with_noaction_model_support` excludes cases the model would already save without intervention. The replacement Ethereum WETH market contributes only to holdout; ETH-type training comes from Base WETH and Ethereum wstETH.

{table(asset_display)}
`data/oracle_source_sensitivity.csv` separates episodes with any Binance fallback from those wholly on reconstructed venue feeds, using the same frozen policies. This is a coverage sensitivity report, not another fit.

### Validation and timing

The unchanged simulator is copied to `scripts/sim_reference.py`; its SHA-256 is recorded in `data/simulator_parity.json`. The compiled P2 path in `scripts/sim_grid.cpp` matched it in {check['simulator_parity_checks']} comparisons across positions, policies and actual-event replays, including balances, liquidations, costs, sales and bottom marks. Full continuation paths also matched in {len(read('continuation_parity.json'))} asset-specific checks. C++ only accelerates the research computation; Python entry points run with `-I`.

Independent feature checks passed on {check['independent_feature_samples']:,} sampled rows, while all {check['feature_rows']:,} rows were checked for missing-state and label-mask consistency. Split and freeze-order assertions passed. Raw quotes and events remain cached. A continuation cache initially rejected a NumPy boolean; converting it to a built-in boolean fixed serialization, and completed parity checks were reused. No provider failure was hidden as an empty successful page.

`data/final_validation.json` additionally verifies unique case-policy keys, finite outputs, aggregate reconciliation, Step 1 closure before Step 2, and selection frozen before holdout execution.

Step 1 follow-up command timings (seconds, overlapping; source: `data/closure_*_status.json`):

{table(followup_timing)}
Replacement-market components inside its total: liquidation pull {supplementary['liquidations']['elapsed_s']:.3f}s, histories {supplementary['positions']['elapsed_s']:.3f}s, transaction census/owners {supplementary['transactions']['elapsed_s']:.3f}s, survivors {supplementary['survivors']['elapsed_s']:.3f}s, remaining survivor transactions {supplementary['survivor_transactions']['elapsed_s']:.3f}s. Cached existing-market pages were reused.

Step 2 measured command timings (seconds; `data/step2_source_times.csv`). Recorded wall span is {span:,.3f}s including setup gaps and overlapping work, not the sum of command times. Last-invocation timers omit the earlier failed serialization attempt.

{table(timing)}
### Step 2 reproduction

After Step 1 closure, run these from the repository root using the local environment. Every successful request and completed case is reused on restart; `train_run_manifest.json` prevents reuse across incompatible grid settings.

```sh
'research_notes/Liquidation dataset/scripts/.venv/bin/python3' -I 'research_notes/Liquidation dataset/scripts/step2_data.py' market_history
'research_notes/Liquidation dataset/scripts/.venv/bin/python3' -I 'research_notes/Liquidation dataset/scripts/step2_data.py' case_plan
'research_notes/Liquidation dataset/scripts/.venv/bin/python3' -I 'research_notes/Liquidation dataset/scripts/step2_data.py' minute_prices
'research_notes/Liquidation dataset/scripts/.venv/bin/python3' -I 'research_notes/Liquidation dataset/scripts/step2_data.py' features
'research_notes/Liquidation dataset/scripts/.venv/bin/python3' -I 'research_notes/Liquidation dataset/scripts/replay.py' oracles
'research_notes/Liquidation dataset/scripts/.venv/bin/python3' -I 'research_notes/Liquidation dataset/scripts/replay.py' parity
'research_notes/Liquidation dataset/scripts/.venv/bin/python3' -I 'research_notes/Liquidation dataset/scripts/replay.py' baseline
'research_notes/Liquidation dataset/scripts/.venv/bin/python3' -I 'research_notes/Liquidation dataset/scripts/replay.py' train
'research_notes/Liquidation dataset/scripts/.venv/bin/python3' -I 'research_notes/Liquidation dataset/scripts/replay.py' select
'research_notes/Liquidation dataset/scripts/.venv/bin/python3' -I 'research_notes/Liquidation dataset/scripts/replay.py' holdout
'research_notes/Liquidation dataset/scripts/.venv/bin/python3' -I 'research_notes/Liquidation dataset/scripts/summarize_step2.py' selected_results
'research_notes/Liquidation dataset/scripts/.venv/bin/python3' -I 'research_notes/Liquidation dataset/scripts/summarize_step2.py' continuation
'research_notes/Liquidation dataset/scripts/.venv/bin/python3' -I 'research_notes/Liquidation dataset/scripts/summarize_step2.py' tables
'research_notes/Liquidation dataset/scripts/.venv/bin/python3' -I 'research_notes/Liquidation dataset/scripts/validate_step2.py'
'research_notes/Liquidation dataset/scripts/.venv/bin/python3' -I 'research_notes/Liquidation dataset/scripts/validate_results.py'
'research_notes/Liquidation dataset/scripts/.venv/bin/python3' -I 'research_notes/Liquidation dataset/scripts/report.py' complete
```

The compiler is the local C++17 toolchain (`c++`); no external simulation package or paid service is required. See `scripts/requirements.txt` for Python versions.

## Decisions before Step 3

1. Retain the 70/50/20 defaults, as these results support, or commission a new calibration study. The grid winner is recorded for comparison and has not been adopted. Any redesign after viewing this holdout needs a fresh evaluation period for a new untouched test.
2. Decide whether retrieval should include flagged Binance-fallback episodes or only reconstructed venue-feed episodes. Keep source and model-mismatch flags visible; hypothetical policy outcomes must be distinguished from observed borrower events.
3. Accept the small, holdout-only Ethereum WETH cohort and the unchanged wstETH survivor census, or broaden the market universe in a separately specified pull. Aave remains excluded from Steps 2–4.
4. Decide how to handle flagged missing hours and the excluded reconstruction cases in retrieval. Do not manufacture state or present their missing labels as negatives.

Stopped after Step 2. No retrieval table, risk model, LLM calls, secrets, or commits were produced.
'''
    (ROOT/'README.md').write_text(text)

if __name__=='__main__':completed() if len(sys.argv)>1 and sys.argv[1]=='complete' else closure()
