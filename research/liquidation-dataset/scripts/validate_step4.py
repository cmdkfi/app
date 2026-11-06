"""Independent data, ranking, bootstrap and model-serialization checks."""
import os
os.environ['OMP_NUM_THREADS']='4'
from pathlib import Path
import json,time,hashlib
import numpy as np
import pandas as pd
import joblib
from sklearn.metrics import confusion_matrix,roc_auc_score
from threadpoolctl import threadpool_limits
D=Path(__file__).resolve().parents[1]/'data'
def main():
    start=time.time();protocol=json.loads((D/'step4_protocol.json').read_text());manifest=json.loads((D/'step4_training_manifest.json').read_text());fit=json.loads((D/'step4_fit_status.json').read_text());decision=json.loads((D/'step4_decision.json').read_text());features=protocol['features'];f=pd.read_parquet(D/'step4_hours.parquet');p=pd.read_parquet(D/'step4_predictions.parquet');assert len(f)==len(p);assert np.array_equal(f.row_key_hash,p.row_key_hash);assert f.row_key_hash.is_unique;assert (f.ts>=f.t0).all() and (f.ts<=f.t_end).all();assert (f.start_size_usd>=100).all();assert not f.missing_position_hour.any();assert f.liquidated_within_24h.notna().all();assert not f.label_window_missing_24h.any();assert np.array_equal(f.y.to_numpy(),f.liquidated_within_24h.to_numpy(dtype='int8'));assert not np.isinf(f[features].to_numpy(float)).any();assert (f.loc[f.split=='train','ts']+86400<1767225600).all();assert (f.loc[f.split=='holdout','ts']>=1767225600).all();assert np.array_equal(f.episode_binance_fallback.to_numpy(),f.source_group.eq('fallback_only').to_numpy())
    bad=pd.read_csv(D/'retrieval_reconstruction_exclusions.csv');assert not pd.MultiIndex.from_frame(f[['market','borrower']]).isin(set(zip(bad.market,bad.borrower))).any()
    for mk,g in f.groupby('market'):
        chain=int(g.chain.iloc[0]);o=np.load(D/'replay_oracles'/f'{chain}-{mk}.npz');ix=np.searchsorted(o['t'],g.ts.to_numpy(),side='right')-1;assert (ix>=0).all();needed=o['binance_fallback'][ix];assert not (needed&~g.episode_binance_fallback.to_numpy()).any()
    train=f[(f.split=='train')&f.primary_cohort&(f.source_group=='venue_only')];assert len(train)==manifest['train_rows'];assert train.y.sum()==manifest['train_positives'];assert hashlib.sha256(np.sort(train.row_key_hash.to_numpy()).tobytes()).hexdigest()==manifest['training_keys_sha256'];assert protocol['created_at']<manifest['started_at']<fit['fit_finished_at']<decision['completed_at'];assert manifest['protocol_sha256']==hashlib.sha256((D/'step4_protocol.json').read_bytes()).hexdigest()
    modelpath=D/'step4_candidate.joblib'
    if not modelpath.exists():modelpath=D/'risk_model.joblib'
    assert hashlib.sha256(modelpath.read_bytes()).hexdigest()==fit['model_sha256'];bundle=joblib.load(modelpath);assert bundle['features']==features;assert bundle['model'].get_params()==dict(bundle['model'].get_params(),**protocol['parameters'])
    sample=np.arange(0,len(f),max(1,len(f)//1000))
    with threadpool_limits(limits=4):score=bundle['model'].predict_proba(f.iloc[sample][features].to_numpy(float))[:,1]
    assert np.allclose(score,p.model_score.iloc[sample],rtol=1e-12,atol=1e-12)
    liqs=pd.read_parquet(D/'liquidations.parquet');liqs=liqs[liqs.included_steps_2_4];lg={k:g.ts.sort_values().to_numpy() for k,g in liqs.groupby(['market','borrower'])};labels=0
    for key,g in f.groupby(['market','borrower']):
        t=lg.get(key,np.array([],dtype='int64'));a=np.searchsorted(t,g.ts.to_numpy(),side='right');b=np.searchsorted(t,g.ts.to_numpy()+86400,side='right');assert np.array_equal((b>a).astype('int8'),g.y.to_numpy());labels+=len(g)
    primary=p[(p.split=='holdout')&p.primary_cohort&(p.source_group=='venue_only')].reset_index(drop=True);alerts=pd.read_parquet(D/'step4_primary_alerts.parquet');assert np.array_equal(primary.row_key_hash,alerts.row_key_hash);methods=['model_5pct','act_5pct','combined_5pct','act_fixed','combined_fixed','act_fixed_plus_model','combined_fixed_plus_model'];met=pd.read_csv(D/'step4_metrics.csv');pm=met[(met.cut_usd==1000)&(met.split=='holdout')&(met.source=='venue_only')].set_index('method');y=primary.y.to_numpy()
    for method,col in [('model_5pct','model_score'),('act_5pct','act_score'),('combined_5pct','combined_score')]:
        order=primary.sort_values([col,'row_key_hash'],ascending=[False,True],kind='stable').index.to_numpy();expected=np.zeros(len(primary),bool);expected[order[:int(.05*len(primary))]]=True;assert np.array_equal(expected,alerts[method]);assert alerts[method].sum()==int(.05*len(primary))
    assert np.array_equal(alerts.act_fixed,primary.ltv>.7);assert np.array_equal(alerts.combined_fixed,(primary.ltv>.7)|(primary.oracle_vs_binance_pct>2));assert np.array_equal(alerts.act_fixed_plus_model,alerts.act_fixed|alerts.model_5pct);assert np.array_equal(alerts.combined_fixed_plus_model,alerts.combined_fixed|alerts.model_5pct)
    for method in methods:
        tn,fp,fn,tp=confusion_matrix(y,alerts[method],labels=[0,1]).ravel();row=pm.loc[method];assert row.true_positives==tp and row.false_positives==fp;assert np.isclose(row.recall,tp/(tp+fn));assert np.isclose(row.precision,tp/(tp+fp))
    events=pd.read_parquet(D/'step4_primary_lead_events.parquet');assert not events.duplicated(['chain','market','borrower','liquidation_id']).any()
    for event in events.iloc[::max(1,len(events)//100)].itertuples():
        g=primary[(alerts.position_code==event.position_code)&(primary.ts>=event.liquidation_ts-86400)&(primary.ts<event.liquidation_ts)];assert len(g)==event.eligible_pre_event_hours
        for method in methods:
            hit=g[alerts.loc[g.index,method].to_numpy()];expected=(event.liquidation_ts-hit.ts.min())/3600 if len(hit) else 0.;assert np.isclose(expected,getattr(event,method))
    # Independently expand the first five cluster bootstrap draws into hour/event
    # weights rather than using the evaluator's pre-aggregated matrix products.
    boot=pd.read_parquet(D/'step4_bootstrap_replicates.parquet');n=alerts.position_code.nunique();rng=np.random.default_rng(protocol['bootstrap']['seed']);weights=rng.multinomial(n,np.full(n,1/n),size=5)
    for i,w in enumerate(weights):
        wh=w[alerts.position_code.to_numpy()];we=w[events.position_code.to_numpy()];rec={};prec={};lead={}
        for method in methods:
            al=alerts[method].to_numpy();tp=np.sum(wh*al*y);rec[method]=tp/np.sum(wh*y);prec[method]=tp/np.sum(wh*al);lead[method]=float(np.median(np.repeat(events[method].to_numpy(),we)));assert np.isclose(rec[method],boot.iloc[i][method+'_recall']);assert np.isclose(prec[method],boot.iloc[i][method+'_precision']);assert np.isclose(lead[method],boot.iloc[i][method+'_median_lead_h'])
        assert np.isclose(rec['model_5pct']-max(rec['act_5pct'],rec['combined_5pct']),boot.iloc[i].recall_gain_vs_best)
    ci=pd.read_csv(D/'step4_bootstrap_intervals.csv').set_index('metric')
    for col in ci.index:assert np.allclose(np.quantile(boot[col].dropna(),[.025,.975]),ci.loc[col,['lower_95','upper_95']].to_numpy(float))
    best=pm.loc[['act_5pct','combined_5pct']].sort_values(['recall','precision'],ascending=False,kind='stable').index[0];rd=pm.loc['model_5pct','recall']-pm.loc[best,'recall'];pdiff=pm.loc['model_5pct','precision']-pm.loc[best,'precision'];ld=events.model_5pct.median()-events.act_5pct.median();rules=[rd>=.05 and pdiff>=0,ci.loc['recall_gain_vs_best','lower_95']>0,ld>=1];assert bool(all(rules))==decision['keep'];assert np.isclose(rd,decision['recall_gain']);assert np.isclose(ld,decision['median_lead_gain_h'])
    size=pd.read_csv(D/'step2_size_headline.csv');plan=pd.read_parquet(D/'step4_episode_cohorts.parquet');base=pd.read_parquet(D/'evaluation_results.parquet');base=base[base.policy=='DEFAULT'].merge(plan[['case_id','start_size_usd']],on='case_id',validate='one_to_one')
    for row in size.itertuples():
        g=base[(base.start_size_usd>=row.cut_usd)&(base.split==row.split)]
        if row.source=='venue_only':g=g[~g.binance_fallback]
        elif row.source=='fallback_only':g=g[g.binance_fallback]
        assert len(g)==row.cases;assert (g.n_liqs>0).sum()==row.policy_liquidated;assert np.isclose(g.equity_vs_actual_end.sum(),row.equity_vs_actual_end);assert np.isclose(g.penalty.sum(),row.policy_penalties)
    status=dict(all_passed=True,hours_checked=len(f),observed_labels_recomputed=labels,serialized_model_predictions_checked=len(sample),primary_ranking_rows=len(primary),bootstrap_draws_expanded=5,bootstrap_replicates=len(boot),size_headlines_reconciled=len(size),elapsed_s=time.time()-start)
    (D/'step4_validation.json').write_text(json.dumps(status,indent=2)+'\n');print(json.dumps(status,indent=2))
if __name__=='__main__':main()
