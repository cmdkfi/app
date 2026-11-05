"""Independent checks of saved features and train/holdout separation."""
import hashlib
import json
import math
from pathlib import Path
import numpy as np
import pandas as pd
import pyarrow.parquet as pq
D=Path(__file__).resolve().parents[1]/'data';S=D.parent/'scripts'
SPLIT=1767225600
def read(name):return json.loads((D/name).read_text())
prices=pd.read_parquet(D/'binance_1h_ext.parquet');ratios=pd.read_csv(D/'eth_ratios_ext.csv').sort_values('ts');price={}
for asset in ['cbBTC','WETH','wstETH']:
    g=prices[prices.symbol==('BTCUSDT' if asset=='cbBTC' else 'ETHUSDT')].sort_values('open_time')
    t=(g.open_time.to_numpy()//1000).astype('int64');v=g.open.to_numpy().copy()
    if asset=='wstETH':v*=ratios.wsteth_steth.to_numpy()[np.maximum(np.searchsorted(ratios.ts.to_numpy(),t,side='right')-1,0)]
    price[asset]=dict(zip(t,v))
lltv={x['marketId']:int(x['lltv'])/1e18 for x in read('markets.json')}
rows=missing=labels6=labels24=samples=0
for batch in pq.ParquetFile(D/'features_hourly.parquet').iter_batches(batch_size=100000):
    g=batch.to_pandas();rows+=len(g);missing+=int(g.missing_position_hour.sum())
    assert g.loc[g.missing_position_hour,['collateral','debt','ltv','oracle_px','position_size_usd']].isna().all().all()
    for h in [6,24]:assert g.loc[g[f'label_window_missing_{h}h'],f'liquidated_within_{h}h'].isna().all()
    labels6+=int(g.liquidated_within_6h.notna().sum());labels24+=int(g.liquidated_within_24h.notna().sum())
    for r in g.iloc[::997].itertuples():
        px=price[r.collateral_asset]
        for h in [1,6,24]:
            if r.ts-h*3600 in px:assert math.isclose(getattr(r,f'price_change_{h}h'),px[r.ts]/px[r.ts-h*3600]-1,rel_tol=1e-8,abs_tol=1e-10)
        times=list(range(r.ts-24*3600,r.ts+1,3600))
        if all(t in px for t in times):
            expected=float(np.std(np.diff(np.log([px[t] for t in times])),ddof=1))
            assert math.isclose(r.realized_volatility_24h,expected,rel_tol=1e-8,abs_tol=1e-10)
        if not r.missing_position_hour and not pd.isna(r.ltv):
            assert math.isclose(r.dist_to_line,lltv[r.market]-r.ltv,abs_tol=1e-10)
            assert math.isclose(r.position_size_usd,r.collateral*r.oracle_px,rel_tol=1e-10,abs_tol=1e-9)
            assert math.isclose(r.oracle_vs_binance_pct,100*(r.oracle_px/r.binance_px-1),rel_tol=1e-10,abs_tol=1e-9)
        samples+=1
assert rows==read('quality.json')['hourly_rows'] and missing==read('quality.json')['missing_hourly_rows']
plan=pd.read_parquet(D/'replay_case_plan.parquet');included=plan[plan.included]
assert (included.missing_hours==0).all()
train=included[included.split=='train'];holdout=included[included.split=='holdout']
assert (train.t_end<SPLIT).all() and (holdout.t0>=SPLIT).all()
assert (train.loc[train.selection=='survivor','anchor_ts']+30*86400<SPLIT).all()
frozen=read('selected_policy.json');assert frozen['holdout_used_for_selection'] is False
parity=read('simulator_parity.json');assert parity['all_passed'] and parity['cpp_sha256']==hashlib.sha256((S/'sim_grid.cpp').read_bytes()).hexdigest()
grid=pd.read_csv(D/'calibration_grid.csv');assert len(grid)==162 and grid.cases.nunique()==1
checks=dict(feature_rows=rows,missing_position_hours=missing,labelled_6h=labels6,labelled_24h=labels24,independent_feature_samples=samples,
            train_cases_before_endpoint_check=len(train),holdout_cases_before_endpoint_check=len(holdout),grid_settings=len(grid),simulator_parity_checks=parity['checks'],training_frozen_before_holdout=True)
if (D/'step2_replay_holdout_status.json').exists():assert read('step2_replay_holdout_status.json')['started_at']>=frozen['selected_at']
(D/'step2_validation.json').write_text(json.dumps(checks,indent=2)+'\n');print(json.dumps(checks,indent=2))
