"""Step 3 only: observed screen-triggered hours and outcome-aware retrieval data.
Offline; no state imputation and no risk-model fitting.
"""
from pathlib import Path
import json,time,hashlib
import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
ROOT=Path(__file__).resolve().parents[1];D=ROOT/'data';SPLIT=1767225600;END=1792972800
FEATURES=['ltv','dist_to_line','price_change_1h','price_change_6h','price_change_24h','realized_volatility_24h','oracle_vs_binance_pct','utilization','borrow_rate','hours_since_owner_action','position_size_usd']
def read(p):return json.loads(p.read_text())
def save(p,x):p.write_text(json.dumps(x,indent=2,allow_nan=False)+'\n')

def feed_metadata(markets):
    rounds=pd.read_csv(D/'oracle_rounds_ext.csv',low_memory=False);rounds['proxy']=rounds.proxy.str.lower();bounds=read(D/'oracle_phase_bounds.json');configs={c['market']:c for c in read(D/'market_oracle_config.json')};ratios=pd.read_csv(D/'eth_ratios_ext.csv').sort_values('ts');result={}
    for m in markets:
        mk=m['marketId'];chain=m['chain']['id'];asset=m['collateralAsset']['symbol'];ts=np.arange(1704067200,END,3600,dtype='int64');cfg=configs[mk]['oracle']['data'];needed=[]
        if asset!='wstETH':needed.append((cfg['baseFeedOne']['address'].lower(),1200 if chain==8453 else 3600))
        if cfg['quoteFeedOne']:needed.append((cfg['quoteFeedOne']['address'].lower(),3600 if asset=='wstETH' else 86400))
        valid=np.ones(len(ts),bool);stale=np.zeros(len(ts),bool);ages=[];component_thresholds={}
        for addr,threshold in needed:
            r=rounds[(rounds.proxy==addr)&rounds.usable_for_replay].sort_values(['updated_at','round_id']).drop_duplicates('updated_at',keep='last');rt=r.updated_at.to_numpy(dtype='int64');ix=np.searchsorted(rt,ts,side='right')-1;age=ts-rt[np.maximum(ix,0)];within=np.zeros(len(ts),bool)
            for b in bounds:
                if b['proxy'].lower()==addr:within|=(ts>=b['usable_from'])&(ts<b['usable_until_exclusive'])&(rt[np.maximum(ix,0)]>=b['usable_from'])
            known=(ix>=0)&within;valid&=known;stale|=known&(age>threshold);ages.append(np.where(known,age,np.nan));component_thresholds[addr]=threshold
        if asset=='wstETH':
            rt=ratios.ts.to_numpy(dtype='int64');ix=np.searchsorted(rt,ts,side='right')-1;age=ts-rt[np.maximum(ix,0)];known=ix>=0;valid&=known;stale|=known&(age>86400);ages.append(np.where(known,age,np.nan));component_thresholds['asof_wsteth_exchange_rate_sample']=86400
        fs=pd.array(stale,dtype='boolean');fs[(~valid)&(~stale)]=pd.NA
        maxage=np.max(np.stack(ages),axis=0)
        result[mk]=pd.DataFrame(dict(ts=ts,binance_fallback=~valid,feed_stale=fs,feed_staleness_unknown=~valid,oracle_component_max_age_s=maxage))
        save(D/'retrieval_feed_thresholds.json',dict(read(D/'retrieval_feed_thresholds.json') if (D/'retrieval_feed_thresholds.json').exists() else {},**{mk:component_thresholds}))
    return result

def main():
    started=time.time();markets=read(D/'markets.json');marketmap={m['marketId']:m for m in markets};meta=feed_metadata(markets)
    plan=pd.read_parquet(D/'replay_case_plan.parquet');val=pd.read_parquet(D/'replay_validation.parquet');bad=plan[plan.case_id.isin(val.loc[~val.eligible,'case_id'])];badkeys=set(zip(bad.market,bad.borrower));assert len(bad)==13
    bad[['case_id','chain','market','borrower','t0','t_end']].to_csv(D/'retrieval_reconstruction_exclusions.csv',index=False)
    ev=pd.read_parquet(D/'evaluation_results.parquet');ev=ev[ev.policy=='DEFAULT'];ep=plan.merge(ev[['case_id','binance_fallback']],on='case_id');epmap={k:g for k,g in ep.groupby(['market','borrower'])}
    liqs=pd.read_parquet(D/'liquidations.parquet');liqs=liqs[liqs.included_steps_2_4].sort_values(['ts','log']);lg={k:g for k,g in liqs.groupby(['market','borrower'])}
    owners=pd.read_parquet(D/'owner_transactions.parquet',columns=['market','borrower','ts','type']);owners=owners[owners.type.isin(['SupplyCollateral','WithdrawCollateral','Borrow','Repay'])].sort_values('ts');og={k:g for k,g in owners.groupby(['market','borrower'])}
    stats=dict(input_hours=0,excluded_reconstruction_hours=0,screen_hours=0,missing_state_screen_hours=0,unknown_label_hours=0,feature_incomplete_hours=0,binance_fallback_hours=0,episode_binance_fallback_hours=0,small_cohort_hours=0,staleness_unknown_hours=0,positive_24h=0,negative_24h=0,index_eligible_hours=0)
    counts=[];screen_counts=[];feature_sum=np.zeros(len(FEATURES));feature_sq=np.zeros(len(FEATURES));ntrain=0;writer=None;rid=0;positions=set();training_positions=set();pfile=pq.ParquetFile(D/'features_hourly.parquet')
    tmp=D/'retrieval.parquet.tmp'
    try:
        for batch_id,batch in enumerate(pfile.iter_batches(batch_size=100000)):
            f=batch.to_pandas();stats['input_hours']+=len(f);badmask=np.fromiter(((m,b) in badkeys for m,b in zip(f.market,f.borrower)),bool,len(f));stats['excluded_reconstruction_hours']+=int(badmask.sum());f=f.loc[~badmask].copy()
            parts=[]
            for mk,g in f.groupby('market',sort=False):parts.append(g.merge(meta[mk],on='ts',how='left',validate='many_to_one'))
            f=pd.concat(parts,ignore_index=True)
            f['screen_ltv']=f.ltv>.70;f['screen_oracle_gap']=f.oracle_vs_binance_pct>2;f['screen_utilization']=f.utilization>.95;f['screen_feed_stale']=f.feed_stale.fillna(False).astype(bool)
            screens=['screen_ltv','screen_oracle_gap','screen_utilization','screen_feed_stale'];f=f[f[screens].any(axis=1)].reset_index(drop=True)
            if f.empty:continue
            n=len(f);f['retrieval_id']=np.arange(rid,rid+n,dtype='int64');rid+=n
            f['split']=np.where(f.ts<SPLIT,'train','holdout');f['year']=pd.to_datetime(f.ts,unit='s',utc=True).dt.year
            f['asset_group']=np.where(f.collateral_asset=='cbBTC','cbBTC','ETH-type');f['market_name']=f.chain.map({8453:'Base',1:'Ethereum'})+' '+f.collateral_asset+'/USDC'
            f['cohort_flag']=np.where(f.chain==1,np.where(f.collateral_asset=='WETH','small_eth_weth_holdout_only','small_eth_wsteth_483_survivors'),'base_standard');f['small_cohort']=f.chain==1
            f['active_borrow']=f.debt.gt(0)&f.collateral.gt(0);f['dust_collateral_under_004_usd']=f.position_size_usd.lt(.04)&f.position_size_usd.gt(0)
            f['reconstruction_failure_excluded']=False;f['in_validated_replay_episode']=False;f['episode_binance_fallback']=False
            f['outcome_observed_24h']=f.liquidated_within_24h.notna();f['outcome_matures_ts']=f.ts+86400
            f['hours_to_liquidation']=np.nan;f['next_observed_liquidation_ts']=np.nan;f['liquidation_events_24h']=0.;f['collateral_seized_24h']=0.;f['debt_repaid_24h_usd']=0.;f['penalty_paid_24h_usd']=0.
            kinds=['SupplyCollateral','WithdrawCollateral','Borrow','Repay']
            for k in kinds:f['owner_'+k+'_24h']=0.
            f['last_owner_action_before_ts']=np.nan
            for key,g in f.groupby(['market','borrower'],sort=False):
                idx=g.index;t=g.ts.to_numpy();mk,b=key
                if key in epmap:
                    for row in epmap[key].itertuples():
                        mask=(t>=row.t0)&(t<=row.t_end);sel=idx[mask];f.loc[sel,'in_validated_replay_episode']=True
                        if row.binance_fallback:f.loc[sel,'episode_binance_fallback']=True
                if key in lg:
                    lq=lg[key];lt=lq.ts.to_numpy(dtype='int64');a=np.searchsorted(lt,t,side='right');z=np.searchsorted(lt,t+86400,side='right');has=a<len(lt);nextt=lt[np.minimum(a,len(lt)-1)];f.loc[idx[has],'hours_to_liquidation']=(nextt[has]-t[has])/3600;f.loc[idx[has],'next_observed_liquidation_ts']=nextt[has];f.loc[idx,'liquidation_events_24h']=z-a
                    incentive=1/(.3*(int(marketmap[mk]['lltv'])/1e18)+.7)-1
                    for col,vals in [('collateral_seized_24h',lq.collateral_seized.to_numpy(float)),('debt_repaid_24h_usd',lq.debt_to_cover.to_numpy(float)),('penalty_paid_24h_usd',lq.debt_to_cover.to_numpy(float)*incentive)]:
                        cum=np.r_[0.,np.cumsum(vals)];f.loc[idx,col]=cum[z]-cum[a]
                if key in og:
                    o=og[key];ot=o.ts.to_numpy(dtype='int64');prev=np.searchsorted(ot,t,side='right')-1;has=prev>=0;f.loc[idx[has],'last_owner_action_before_ts']=ot[prev[has]]
                    for k in kinds:
                        kt=o.loc[o.type==k,'ts'].to_numpy(dtype='int64');f.loc[idx,'owner_'+k+'_24h']=np.searchsorted(kt,t+86400,side='right')-np.searchsorted(kt,t,side='right')
            # A missing/censored label window must never become a negative or a filled outcome.
            outcols=['hours_to_liquidation','next_observed_liquidation_ts','liquidation_events_24h','collateral_seized_24h','debt_repaid_24h_usd','penalty_paid_24h_usd']+['owner_'+k+'_24h' for k in kinds]
            f.loc[~f.outcome_observed_24h,outcols]=np.nan
            # Retrieval outcomes mature after 24h. Do not expose later events
            # merely because the offline event census already contains them.
            f.loc[f.hours_to_liquidation>24,['hours_to_liquidation','next_observed_liquidation_ts']]=np.nan
            known=f.outcome_observed_24h;assert np.array_equal(f.loc[known,'liquidated_within_24h'].astype(bool).to_numpy(),(f.loc[known,'liquidation_events_24h']>0).to_numpy())
            f['seized_pct_of_current_collateral_24h']=100*f.collateral_seized_24h/f.collateral.where(f.collateral>0)
            f['feature_complete']=np.isfinite(f[FEATURES].to_numpy(float)).all(axis=1);f['index_eligible']=f.feature_complete&f.outcome_observed_24h&~f.missing_position_hour
            f['scaler_train_row']=f.index_eligible&(f.outcome_matures_ts<SPLIT)
            a=f.loc[f.scaler_train_row,FEATURES].to_numpy(float);ntrain+=len(a);feature_sum+=a.sum(axis=0);feature_sq+=np.square(a).sum(axis=0)
            dates=pd.to_datetime(f.ts,unit='s',utc=True).dt.strftime('%Y-%m-%d %H:%M UTC');ownertext=[];summaries=[]
            for row,date in zip(f.itertuples(index=False),dates):
                if not row.outcome_observed_24h:owner='unknown (missing/censored 24h window)';outcome='24h outcome unknown'
                else:
                    actions=[f'{k} ×{int(getattr(row,"owner_"+k+"_24h"))}' for k in kinds if getattr(row,'owner_'+k+'_24h')>0];owner=', '.join(actions) if actions else 'no observed owner actions in next 24h'
                    if bool(row.liquidated_within_24h):outcome=f'liquidated {row.hours_to_liquidation:.2f}h later; {row.seized_pct_of_current_collateral_24h:.2f}% of current collateral seized over 24h; estimated penalty ${row.penalty_paid_24h_usd:.6f}'
                    else:outcome='no liquidation in next 24h'
                flags=[]
                if row.binance_fallback:flags.append('Binance fallback at hour')
                if row.episode_binance_fallback:flags.append('replay episode includes Binance fallback')
                if row.small_cohort:flags.append(row.cohort_flag)
                if row.missing_position_hour:flags.append('missing position state')
                if row.feed_staleness_unknown:flags.append('feed age partly unknown')
                ownertext.append(owner);summaries.append(f'{row.market_name}, {date}, LTV {row.ltv:.1%}, 24h price {row.price_change_24h:+.1%}, oracle gap {row.oracle_vs_binance_pct:+.2f}%; {outcome}; owner: {owner}'+('; flags: '+', '.join(flags) if flags else ''))
            f['owner_actions_next_24h']=ownertext
            # Human-readable missing values; numeric columns remain null.
            f['summary']=[s.replace('LTV nan%','LTV unknown').replace('price +nan%','price unknown').replace('gap +nan%','gap unknown').replace('nan% of current collateral','unknown fraction of current collateral') for s in summaries]
            positions.update(zip(f.market,f.borrower));training_positions.update(zip(f.loc[f.scaler_train_row,'market'],f.loc[f.scaler_train_row,'borrower']))
            stats['screen_hours']+=n
            for name,col in [('missing_state_screen_hours','missing_position_hour'),('binance_fallback_hours','binance_fallback'),('episode_binance_fallback_hours','episode_binance_fallback'),('small_cohort_hours','small_cohort'),('staleness_unknown_hours','feed_staleness_unknown'),('index_eligible_hours','index_eligible')]:stats[name]+=int(f[col].sum())
            stats['unknown_label_hours']+=int((~known).sum());stats['feature_incomplete_hours']+=int((~f.feature_complete).sum());stats['positive_24h']+=int(f.liquidated_within_24h.fillna(False).sum());stats['negative_24h']+=int((f.liquidated_within_24h==False).sum())
            counts.append(f.assign(positive_flag=f.liquidated_within_24h.fillna(False).astype('int64')).groupby(['split','year','market_name','cohort_flag'],dropna=False).agg(hours=('ts','size'),known_labels=('outcome_observed_24h','sum'),positive_24h=('positive_flag','sum'),index_eligible=('index_eligible','sum'),fallback_hours=('binance_fallback','sum')).reset_index())
            screen_counts.append(f.groupby('split')[screens].sum())
            table=pa.Table.from_pandas(f,preserve_index=False)
            if writer is None:writer=pq.ParquetWriter(tmp,table.schema,compression='zstd')
            writer.write_table(table.cast(writer.schema))
            if batch_id%10==0:print('retrieval batch',batch_id,'rows',rid,flush=True)
    finally:
        if writer:writer.close()
    tmp.replace(D/'retrieval.parquet')
    mean=feature_sum/ntrain;variance=np.maximum(feature_sq/ntrain-mean**2,0);scale=np.sqrt(variance);scale[scale==0]=1
    save(D/'retrieval_scaler.json',dict(features=FEATURES,mean=mean.tolist(),scale=scale.tolist(),training_rows=ntrain,training_positions=len(training_positions),split_ts=SPLIT,latest_allowed_maturity_ts=SPLIT-1,method='population standard deviation; equal weight per raw feature; no imputation, transforms or clipping',fit_filter='index_eligible and ts+86400 < 2026-01-01 UTC'))
    pd.concat(counts).groupby(['split','year','market_name','cohort_flag']).sum(numeric_only=True).reset_index().to_csv(D/'retrieval_counts.csv',index=False)
    pd.concat(screen_counts).groupby(level=0).sum().reset_index().to_csv(D/'retrieval_screen_counts.csv',index=False)
    stats.update(positions=len(positions),excluded_failed_cases=len(bad),excluded_failed_positions=len(badkeys),scaler_training_rows=ntrain,elapsed_s=time.time()-started,started_at=started,finished_at=time.time(),bytes=(D/'retrieval.parquet').stat().st_size)
    save(D/'step3_build_status.json',stats);print(json.dumps(stats,indent=2),flush=True)
if __name__=='__main__':main()
