"""Prepare all eligible episode hours and freeze Step 4 before classifier fitting."""
from pathlib import Path
import json,time,hashlib,sys,importlib.metadata
import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
ROOT=Path(__file__).resolve().parents[1];D=ROOT/'data';S=ROOT/'scripts';SPLIT=1767225600
FEATURES=['ltv','dist_to_line','price_change_1h','price_change_6h','price_change_24h','realized_volatility_24h','oracle_vs_binance_pct','utilization','borrow_rate','hours_since_owner_action','position_size_usd']
def save(p,x):p.write_text(json.dumps(x,indent=2,allow_nan=False)+'\n')
def main():
    started=time.time();assert '## Step 2 by position size' in (ROOT/'README.md').read_text()
    params=dict(loss='log_loss',learning_rate=.05,max_iter=200,max_leaf_nodes=15,max_depth=5,min_samples_leaf=50,l2_regularization=1.,max_features=1.,max_bins=127,categorical_features=None,early_stopping=False,random_state=20261027,class_weight=None)
    protocol=dict(version=1,created_at=started,features=FEATURES,estimator='sklearn.ensemble.HistGradientBoostingClassifier',parameters=params,primary_min_start_collateral_usd=1000,secondary_min_start_collateral_usd=100,train='pre-2026 eligible episode hours; >=1000 start collateral; no episode fallback; full 24h outcome before 2026',test='2026 eligible episode hours; no fitting or tuning on holdout',secondary='same primary-trained model, no refitting',source_policy='exclude any episode with replay Binance fallback from primary fitting/evaluation; fallback-only and all-source sensitivity',alert_budget=.05,budget_method='rank scores, select floor(0.05*N); deterministic pandas row-key hash breaks exact score ties without labels',act_score='ltv',combined_score='maximum(ltv / 0.70, oracle_vs_binance_pct / 2.0)',fixed_act='ltv > 0.70',fixed_combined='ltv > 0.70 or oracle_vs_binance_pct > 2.0',best_screen='higher holdout recall at matched 5% budget; ties higher precision then act screen; paired bootstrap takes best of both in each replicate',bootstrap=dict(replicates=2000,seed=20261028,cluster='chain, market, borrower; all episode hours and liquidation events resampled together',interval='two-sided 95% percentile',operating_point='alerts fixed at original evaluation-set 5% ranking; no classifier refit in resamples'),keep_rules=dict(recall_gain_at_least=.05,precision_gain_at_least=0,recall_gain_bootstrap_lower_gt=0,median_lead_gain_hours_at_least=1),lead_definition='first alert in preceding 24h for each observed liquidation event with an eligible labeled pre-event hour; missed events receive 0h; compare medians over identical event census, and report paired detected-event lead separately',lead_comparator='budget-matched act-line screen; original fixed act-line reported separately',nan_policy='no imputation; observed state and known label required; native HGB missing-feature routing; inf rejected',hour_domain='all hours within non-overlapping eligible Step 2 episodes, not retrieval-screen-selected hours',versions={p:importlib.metadata.version(p) for p in ['scikit-learn','numpy','pandas','pyarrow','scipy','joblib','threadpoolctl']},n_threads=4,sklearn_documentation='https://scikit-learn.org/stable/modules/generated/sklearn.ensemble.HistGradientBoostingClassifier.html')
    frozen=D/'step4_protocol.json'
    if frozen.exists():
        previous=json.loads(frozen.read_text());protocol['created_at']=previous['created_at'];assert previous==protocol,'Protocol already frozen differently'
    else:save(frozen,protocol)
    (S/'requirements_step4.txt').write_text('\n'.join(f'{k}=={v}' for k,v in protocol['versions'].items())+'\n')
    p=pd.read_parquet(D/'step4_episode_cohorts.parquet');p=p[p.start_size_usd>=100].copy();bad=pd.read_csv(D/'retrieval_reconstruction_exclusions.csv');badkeys=set(zip(bad.market,bad.borrower));p=p[~pd.MultiIndex.from_frame(p[['market','borrower']]).isin(badkeys)]
    for _,g in p.groupby(['market','borrower']):
        g=g.sort_values('t0');assert np.all(g.t0.to_numpy()[1:]>g.t_end.to_numpy()[:-1]),'Overlapping episode windows'
    p=p.rename(columns={'split':'episode_split','binance_fallback':'episode_binance_fallback'});p['primary_cohort']=p.start_size_usd>=1000
    # Label truth and lead-time identities from observed events, not replay liquidations.
    liq=pd.read_parquet(D/'liquidations.parquet');liq=liq[liq.included_steps_2_4].sort_values(['ts','log']);liq['liquidation_id']=liq.tx.astype(str)+':'+liq.log.astype(str);lg={k:g for k,g in liq.groupby(['market','borrower'])}
    pf=pq.ParquetFile(D/'features_hourly.parquet');writer=None;counts=[];stats={'input_hours':0,'within_cohort_episode_hours':0,'missing_state_excluded':0,'unknown_label_excluded':0,'training_horizon_crossing_excluded':0,'infinite_feature_excluded':0,'retained_hours':0,'rows_with_native_nan_features':0};hashes=[]
    tmp=D/'step4_hours.parquet.tmp'
    try:
        for batchid,batch in enumerate(pf.iter_batches(batch_size=200000)):
            f=batch.to_pandas();stats['input_hours']+=len(f);f=f.merge(p,on=['chain','market','borrower'],how='inner',validate='many_to_many');f=f[(f.ts>=f.t0)&(f.ts<=f.t_end)].copy();stats['within_cohort_episode_hours']+=len(f)
            stats['missing_state_excluded']+=int(f.missing_position_hour.sum());f=f[~f.missing_position_hour];stats['unknown_label_excluded']+=int(f.liquidated_within_24h.isna().sum());f=f[f.liquidated_within_24h.notna()];cross=(f.episode_split=='train')&(f.ts+86400>=SPLIT);stats['training_horizon_crossing_excluded']+=int(cross.sum());f=f[~cross];inf=np.isinf(f[FEATURES].to_numpy(float)).any(axis=1);stats['infinite_feature_excluded']+=int(inf.sum());f=f[~inf].reset_index(drop=True)
            if f.empty:continue
            assert (f.loc[f.episode_split=='train','ts']<SPLIT).all();assert (f.loc[f.episode_split=='holdout','ts']>=SPLIT).all();assert not f.label_window_missing_24h.any()
            f['y']=f.liquidated_within_24h.astype('int8');f['split']=f.episode_split;f['row_key_hash']=pd.util.hash_pandas_object(f[['chain','market','borrower','ts']],index=False).to_numpy(dtype='uint64');hashes.append(f.row_key_hash.to_numpy())
            f['source_group']=np.where(f.episode_binance_fallback,'fallback_only','venue_only');f['next_liquidation_ts']=np.nan;f['next_liquidation_id']=pd.Series([None]*len(f),dtype='string');f['owner_action_missing_feature']=f.hours_since_owner_action.isna()
            for key,g in f[f.y==1].groupby(['market','borrower'],sort=False):
                lq=lg[key];lt=lq.ts.to_numpy(dtype='int64');a=np.searchsorted(lt,g.ts.to_numpy(),side='right');assert (a<len(lt)).all();t=lt[a];assert ((t-g.ts.to_numpy()>0)&(t-g.ts.to_numpy()<=86400)).all();f.loc[g.index,'next_liquidation_ts']=t;f.loc[g.index,'next_liquidation_id']=lq.liquidation_id.to_numpy()[a]
            stats['retained_hours']+=len(f);stats['rows_with_native_nan_features']+=int(f[FEATURES].isna().any(axis=1).sum())
            for cut in [1000,100]:
                q=f[f.start_size_usd>=cut];c=q.groupby(['split','source_group','asset_group']).agg(hours=('ts','size'),positive_hours=('y','sum'),missing_owner_action_feature_hours=('owner_action_missing_feature','sum')).reset_index();c['cut_usd']=cut;counts.append(c)
            table=pa.Table.from_pandas(f,preserve_index=False)
            if writer is None:writer=pq.ParquetWriter(tmp,table.schema,compression='zstd')
            writer.write_table(table.cast(writer.schema))
            if batchid%10==0:print('prepare',batchid,stats['retained_hours'],flush=True)
    finally:
        if writer:writer.close()
    hh=np.concatenate(hashes);assert len(np.unique(hh))==len(hh),'Duplicate position-hour across episodes'
    tmp.replace(D/'step4_hours.parquet');pd.concat(counts).groupby(['cut_usd','split','source_group','asset_group']).sum(numeric_only=True).reset_index().to_csv(D/'step4_hour_counts.csv',index=False)
    stats.update(started_at=started,finished_at=time.time(),elapsed_s=time.time()-started,protocol_sha256=hashlib.sha256(frozen.read_bytes()).hexdigest());save(D/'step4_prepare_status.json',stats);print(json.dumps(stats,indent=2))
if __name__=='__main__':main()
