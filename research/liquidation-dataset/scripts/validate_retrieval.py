"""Independent whole-table invariants and sampled source/outcome audits for Step 3."""
from pathlib import Path
import json,time
import numpy as np
import pandas as pd
import pyarrow.parquet as pq
D=Path(__file__).resolve().parents[1]/'data'
def main():
    started=time.time();sc=json.loads((D/'retrieval_scaler.json').read_text());features=sc['features'];bad=pd.read_csv(D/'retrieval_reconstruction_exclusions.csv');badkeys=set(zip(bad.market,bad.borrower));hashes=[];samples=[];n=0;trainn=0;mean=np.zeros(len(features));m2=mean.copy();lastid=-1
    sumscreen=np.zeros(4,dtype='int64');known=0;unknown=0
    for batch in pq.ParquetFile(D/'retrieval.parquet').iter_batches(batch_size=100000):
        f=batch.to_pandas();n+=len(f);assert np.array_equal(f.retrieval_id.to_numpy(),np.arange(lastid+1,lastid+1+len(f)));lastid=int(f.retrieval_id.iloc[-1]);assert ~pd.MultiIndex.from_frame(f[['market','borrower']]).isin(badkeys).any()
        assert f[['screen_ltv','screen_oracle_gap','screen_utilization','screen_feed_stale']].any(axis=1).all();assert np.array_equal(f.screen_ltv.to_numpy(),(f.ltv>.70).to_numpy());assert np.array_equal(f.screen_oracle_gap.to_numpy(),(f.oracle_vs_binance_pct>2).to_numpy());assert np.array_equal(f.screen_utilization.to_numpy(),(f.utilization>.95).to_numpy());assert np.array_equal(f.screen_feed_stale.to_numpy(),f.feed_stale.fillna(False).to_numpy(bool))
        good=f.liquidated_within_24h.notna();known+=good.sum();unknown+=(~good).sum();assert np.array_equal(good.to_numpy(),f.outcome_observed_24h.to_numpy());assert not (f.missing_position_hour&good).any();assert not (f.label_window_missing_24h&good).any();assert f.loc[~good,['hours_to_liquidation','penalty_paid_24h_usd','collateral_seized_24h','owner_Borrow_24h']].isna().all().all()
        pos=f.liquidated_within_24h.fillna(False).astype(bool);assert f.loc[pos,'hours_to_liquidation'].between(0,24,inclusive='right').all();assert f.loc[~pos,'hours_to_liquidation'].isna().all();assert np.array_equal(pos[good].to_numpy(),(f.loc[good,'liquidation_events_24h']>0).to_numpy());assert (f.loc[good,'penalty_paid_24h_usd']>=-1e-7).all()
        complete=np.isfinite(f[features].to_numpy(float)).all(axis=1);assert np.array_equal(complete,f.feature_complete.to_numpy());assert np.array_equal(complete&good.to_numpy()&~f.missing_position_hour.to_numpy(),f.index_eligible.to_numpy());assert np.array_equal((f.index_eligible&(f.outcome_matures_ts<1767225600)).to_numpy(),f.scaler_train_row.to_numpy());assert np.array_equal(f.small_cohort.to_numpy(),(f.chain==1).to_numpy())
        finite=np.isfinite(f.hours_since_owner_action)&~f.missing_position_hour;expected=(f.ts-f.last_owner_action_before_ts)/3600;assert np.allclose(f.loc[finite,'hours_since_owner_action'],expected[finite],equal_nan=False)
        hashes.append(pd.util.hash_pandas_object(f[['chain','market','borrower','ts']],index=False).to_numpy());samples.append(f.iloc[::9973].copy())
        a=f.loc[f.scaler_train_row,features].to_numpy(float)
        if len(a):
            # Merge centered variances, independently of builder's sum-of-squares.
            bn=len(a);bm=a.mean(axis=0);b2=((a-bm)**2).sum(axis=0);delta=bm-mean;total=trainn+bn;m2+=b2+delta**2*trainn*bn/total;mean+=delta*bn/total;trainn=total
    h=np.concatenate(hashes);assert len(np.unique(h))==n;assert trainn==sc['training_rows'];assert np.allclose(mean,sc['mean'],rtol=1e-10,atol=1e-12);scale=np.sqrt(m2/trainn);scale[scale==0]=1;assert np.allclose(scale,sc['scale'],rtol=1e-8,atol=1e-12)
    sample=pd.concat(samples);liqs=pd.read_parquet(D/'liquidations.parquet');liqs=liqs[liqs.included_steps_2_4];lg={k:g for k,g in liqs.groupby(['market','borrower'])};o=pd.read_parquet(D/'owner_transactions.parquet',columns=['market','borrower','ts','type']);og={k:g for k,g in o.groupby(['market','borrower'])};markets={m['marketId']:m for m in json.loads((D/'markets.json').read_text())}
    sampled=0
    for row in sample[sample.outcome_observed_24h].itertuples():
        key=(row.market,row.borrower);g=lg.get(key,liqs.iloc[:0]);g=g[(g.ts>row.ts)&(g.ts<=row.ts+86400)];assert bool(len(g))==bool(row.liquidated_within_24h);assert len(g)==row.liquidation_events_24h;assert np.isclose(g.collateral_seized.sum(),row.collateral_seized_24h,atol=1e-7,rtol=1e-7);inc=1/(.3*int(markets[row.market]['lltv'])/1e18+.7)-1;assert np.isclose(g.debt_to_cover.sum()*inc,row.penalty_paid_24h_usd,atol=1e-7,rtol=1e-7)
        if len(g):assert np.isclose((g.ts.min()-row.ts)/3600,row.hours_to_liquidation)
        g=og.get(key,o.iloc[:0]);g=g[(g.ts>row.ts)&(g.ts<=row.ts+86400)]
        for kind in ['SupplyCollateral','WithdrawCollateral','Borrow','Repay']:assert int((g.type==kind).sum())==getattr(row,'owner_'+kind+'_24h')
        sampled+=1
    # Source feature equality, sampled across all output row groups, without
    # depending on parquet row ordering in either artifact.
    wanted=set(pd.util.hash_pandas_object(sample[['chain','market','borrower','ts']],index=False));src=[]
    for batch in pq.ParquetFile(D/'features_hourly.parquet').iter_batches(batch_size=200000):
        f=batch.to_pandas();hh=pd.util.hash_pandas_object(f[['chain','market','borrower','ts']],index=False);src.append(f.loc[hh.isin(wanted)])
    source=pd.concat(src).set_index(['chain','market','borrower','ts']);out=sample.set_index(['chain','market','borrower','ts']);source=source.loc[out.index];assert np.allclose(source[features].to_numpy(float),out[features].to_numpy(float),equal_nan=True);assert source.liquidated_within_24h.astype('boolean').equals(out.liquidated_within_24h.astype('boolean'))
    result=dict(all_passed=True,rows_checked=n,unique_position_hours=n,known_outcomes=int(known),unknown_outcomes=int(unknown),scaler_training_rows=trainn,source_feature_samples=len(sample),direct_outcome_samples=sampled,elapsed_s=time.time()-started)
    (D/'step3_validation.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))
if __name__=='__main__':main()
