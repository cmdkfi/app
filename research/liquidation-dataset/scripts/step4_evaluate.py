"""Frozen Step 4 evaluation: matched budgets, paired position bootstrap, lead time."""
import os
os.environ['OMP_NUM_THREADS']='4';os.environ['OPENBLAS_NUM_THREADS']='4'
from pathlib import Path
import json,time,hashlib
import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score
from threadpoolctl import threadpool_limits
D=Path(__file__).resolve().parents[1]/'data'
METHODS=['model_5pct','act_5pct','combined_5pct','act_fixed','combined_fixed','act_fixed_plus_model','combined_fixed_plus_model']
def save(p,x):p.write_text(json.dumps(x,indent=2,allow_nan=False)+'\n')
def top_budget(score,tie,rate=.05):
    n=len(score);k=int(np.floor(n*rate));order=np.lexsort((tie,-score));alert=np.zeros(n,bool);alert[order[:k]]=True;threshold=float(score[order[k-1]]) if k else None
    return alert,threshold

def alerts_for(f):
    a={};thresholds={}
    for name,col in [('model_5pct','model_score'),('act_5pct','act_score'),('combined_5pct','combined_score')]:a[name],thresholds[name]=top_budget(f[col].to_numpy(float),f.row_key_hash.to_numpy())
    a['act_fixed']=f.act_fixed.to_numpy(bool);a['combined_fixed']=f.combined_fixed.to_numpy(bool);a['act_fixed_plus_model']=a['act_fixed']|a['model_5pct'];a['combined_fixed_plus_model']=a['combined_fixed']|a['model_5pct'];return a,thresholds

def lead_events(f,alerts,lg):
    # An event can be preceded by more than one positive hourly observation;
    # all events are kept, and position bootstrap preserves their dependence.
    rows=[]
    for key,g in f.groupby(['market','borrower'],sort=False):
        lq=lg.get(key)
        if lq is None:continue
        g=g.sort_values('ts');idx=g.index.to_numpy();t=g.ts.to_numpy(dtype='int64');lq=lq[(lq.ts>t[0])&(lq.ts<=t[-1]+86400)]
        for event in lq.itertuples():
            lo=np.searchsorted(t,event.ts-86400,side='left');hi=np.searchsorted(t,event.ts,side='left')
            if lo==hi:continue
            assert (g.y.iloc[lo:hi]==1).all()
            row=dict(market=key[0],borrower=key[1],chain=int(g.chain.iloc[0]),asset_group=g.asset_group.iloc[0],liquidation_id=event.liquidation_id,liquidation_ts=int(event.ts),eligible_pre_event_hours=int(hi-lo),max_observed_lead_h=float((event.ts-t[lo])/3600),position_code=int(g.position_code.iloc[0]))
            for method in METHODS:
                hit=np.flatnonzero(alerts[method][idx[lo:hi]]);row[method]=float((event.ts-t[lo+hit[0]])/3600) if len(hit) else 0.
            rows.append(row)
    return pd.DataFrame(rows)

def weighted_median(values,cluster_codes,weights):
    if not len(values):return np.full(len(weights),np.nan)
    order=np.argsort(values,kind='stable');v=values[order];w=weights[:,cluster_codes[order]];c=np.cumsum(w,axis=1);tot=c[:,-1];low=(tot-1)//2+1;high=tot//2+1;il=np.argmax(c>=low[:,None],axis=1);ih=np.argmax(c>=high[:,None],axis=1);out=(v[il]+v[ih])/2;out[tot==0]=np.nan;return out

def bootstrap(f,alerts,events,protocol):
    codes=f.position_code.to_numpy();n=int(codes.max())+1;y=f.y.to_numpy(bool);ng=np.bincount(codes,minlength=n);yg=np.bincount(codes,weights=y,minlength=n);ag=np.stack([np.bincount(codes,weights=alerts[m],minlength=n) for m in METHODS],axis=1);tg=np.stack([np.bincount(codes,weights=alerts[m]&y,minlength=n) for m in METHODS],axis=1)
    rng=np.random.default_rng(protocol['bootstrap']['seed']);B=protocol['bootstrap']['replicates'];rows=[]
    with threadpool_limits(limits=4):
        for start in range(0,B,100):
            b=min(100,B-start);w=rng.multinomial(n,np.full(n,1/n),size=b);yn=w@yg;nn=w@ng;at=w@ag;tp=w@tg;rec=tp/yn[:,None];prec=np.divide(tp,at,out=np.full_like(tp,np.nan),where=at>0);lead={m:weighted_median(events[m].to_numpy(),events.position_code.to_numpy(),w) for m in METHODS};best=np.maximum(rec[:,1],rec[:,2]);best_ix=np.where(rec[:,1]>=rec[:,2],1,2)
            for i in range(b):
                z=dict(replicate=start+i,positions=n,weighted_hours=int(nn[i]),weighted_positive_hours=int(yn[i]),recall_gain_vs_best=float(rec[i,0]-best[i]),precision_gain_vs_best=float(prec[i,0]-prec[i,best_ix[i]]),median_lead_gain_vs_act=float(lead['model_5pct'][i]-lead['act_5pct'][i]))
                for j,m in enumerate(METHODS):z[m+'_recall']=float(rec[i,j]);z[m+'_precision']=float(prec[i,j]);z[m+'_alert_fraction']=float(at[i,j]/nn[i]);z[m+'_median_lead_h']=float(lead[m][i])
                rows.append(z)
    return pd.DataFrame(rows)

def main():
    started=time.time();protocol=json.loads((D/'step4_protocol.json').read_text());manifest=json.loads((D/'step4_training_manifest.json').read_text());fit=json.loads((D/'step4_fit_status.json').read_text());assert fit['fit_finished_at']<started;assert manifest['protocol_sha256']==hashlib.sha256((D/'step4_protocol.json').read_bytes()).hexdigest()
    p=pd.read_parquet(D/'step4_predictions.parquet');liq=pd.read_parquet(D/'liquidations.parquet');liq=liq[liq.included_steps_2_4].sort_values(['ts','log']);liq['liquidation_id']=liq.tx.astype(str)+':'+liq.log.astype(str);lg={k:g for k,g in liq.groupby(['market','borrower'])}
    metrics=[];leads=[];perasset=[];unions=[];threshold_rows=[];main_f=None;main_a=None;main_events=None
    for cut in [1000,100]:
        for split in ['train','holdout']:
            for source in ['venue_only','fallback_only','all_prices_sensitivity']:
                f=p[(p.start_size_usd>=cut)&(p.split==split)].copy()
                if source!='all_prices_sensitivity':f=f[f.source_group==source]
                f=f.reset_index(drop=True)
                if f.empty:continue
                f['position_code']=pd.factorize(pd.MultiIndex.from_frame(f[['chain','market','borrower']]),sort=True)[0];a,thresholds=alerts_for(f);y=f.y.to_numpy(bool);base=dict(cut_usd=cut,split=split,source=source)
                for method,threshold in thresholds.items():threshold_rows.append(dict(**base,method=method,score_threshold=threshold,ltv_threshold=(threshold if method=='act_5pct' else .7*threshold if method=='combined_5pct' else None),oracle_gap_pct_threshold=(2*threshold if method=='combined_5pct' else None),alerts=int(a[method].sum()),hours=len(f),ties_at_threshold=int((f[{'model_5pct':'model_score','act_5pct':'act_score','combined_5pct':'combined_score'}[method]]==threshold).sum())))
                for method in METHODS:
                    al=a[method];tp=int((al&y).sum());nalert=int(al.sum());col={'model_5pct':'model_score','act_5pct':'act_score','combined_5pct':'combined_score'}.get(method);score=f[col].to_numpy() if col else al.astype(float);score=np.nan_to_num(score,nan=-1e12,neginf=-1e12,posinf=1e12);auc=float(roc_auc_score(y,score)) if len(np.unique(y))==2 else None
                    metrics.append(dict(**base,method=method,hours=len(f),positions=f.position_code.nunique(),episodes=f.case_id.nunique(),positive_hours=int(y.sum()),alerts=nalert,true_positives=tp,false_positives=nalert-tp,alert_fraction=nalert/len(f),auc=auc,precision=tp/nalert if nalert else 0.,recall=tp/y.sum() if y.sum() else 0.))
                    for asset,g in f.groupby('asset_group'):
                        ix=g.index.to_numpy();aa=al[ix];yy=y[ix];at=int(aa.sum());tt=int((aa&yy).sum());perasset.append(dict(**base,method=method,asset_group=asset,hours=len(ix),positive_hours=int(yy.sum()),alerts=at,true_positives=tt,precision=tt/at if at else 0.,recall=tt/yy.sum() if yy.sum() else 0.,alert_fraction=at/len(ix)))
                for fixed in ['act_fixed','combined_fixed']:
                    new=a['model_5pct']&~a[fixed];unions.append(dict(**base,fixed_screen=fixed,new_alerts=int(new.sum()),new_positive_hours=int((new&y).sum()),new_negative_hours=int((new&~y).sum()),recall_gain=int((new&y).sum())/y.sum() if y.sum() else 0.))
                # Lead-time reporting is on holdout, not optimistic training fits.
                if split=='holdout':
                    events=lead_events(f,a,lg)
                    for method in METHODS:
                        values=events[method];det=values>0;leads.append(dict(**base,method=method,events=len(events),positions=events.position_code.nunique(),detected_events=int(det.sum()),event_recall=float(det.mean()),median_lead_all_events_h=float(values.median()),median_lead_detected_h=float(values[det].median()) if det.any() else None))
                    if cut==1000 and source=='venue_only':main_f=f;main_a=a;main_events=events;events.to_parquet(D/'step4_primary_lead_events.parquet',index=False);flags=f[['chain','market','borrower','ts','case_id','position_code','row_key_hash','y']].copy()
                    if cut==1000 and source=='venue_only':
                        for method in METHODS:flags[method]=a[method]
                        flags.to_parquet(D/'step4_primary_alerts.parquet',index=False)
                print('evaluated',cut,split,source,len(f),flush=True)
    met=pd.DataFrame(metrics);lead=pd.DataFrame(leads);met.to_csv(D/'step4_metrics.csv',index=False);lead.to_csv(D/'step4_lead_metrics.csv',index=False);pd.DataFrame(perasset).to_csv(D/'step4_per_asset_metrics.csv',index=False);pd.DataFrame(unions).to_csv(D/'step4_fixed_union_increment.csv',index=False);pd.DataFrame(threshold_rows).to_csv(D/'step4_operating_thresholds.csv',index=False)
    t=time.time();boot=bootstrap(main_f,main_a,main_events,protocol);boot.to_parquet(D/'step4_bootstrap_replicates.parquet',index=False);boot_s=time.time()-t;intervals=[]
    for col in boot.columns:
        if col in ['replicate','positions','weighted_hours','weighted_positive_hours']:continue
        values=boot[col].dropna();lo,hi=np.quantile(values,[.025,.975]);intervals.append(dict(metric=col,lower_95=float(lo),upper_95=float(hi),valid_replicates=len(values)))
    ci=pd.DataFrame(intervals);ci.to_csv(D/'step4_bootstrap_intervals.csv',index=False)
    pm=met[(met.cut_usd==1000)&(met.split=='holdout')&(met.source=='venue_only')].set_index('method');best=pm.loc[['act_5pct','combined_5pct']].sort_values(['recall','precision'],ascending=False,kind='stable').index[0];rd=float(pm.loc['model_5pct','recall']-pm.loc[best,'recall']);pdiff=float(pm.loc['model_5pct','precision']-pm.loc[best,'precision']);gain_ci=ci[ci.metric=='recall_gain_vs_best'].iloc[0];pl=lead[(lead.cut_usd==1000)&(lead.source=='venue_only')].set_index('method');lead_gain=float(pl.loc['model_5pct','median_lead_all_events_h']-pl.loc['act_5pct','median_lead_all_events_h']);paired=(main_events.model_5pct>0)&(main_events.act_5pct>0);paired_gain=float((main_events.loc[paired,'model_5pct']-main_events.loc[paired,'act_5pct']).median()) if paired.any() else None
    criteria={'recall_gain_5pp_and_no_lower_precision':bool(rd>=.05 and pdiff>=0),'positive_95pct_position_bootstrap_recall_gain':bool(gain_ci.lower_95>0),'median_lead_at_least_1h_earlier_than_act':bool(lead_gain>=1)};keep=all(criteria.values())
    decision=dict(keep=keep,recommendation='KEEP' if keep else 'DROP',criteria=criteria,failed=[k for k,v in criteria.items() if not v],best_budget_screen=best,recall_gain=rd,precision_gain=pdiff,recall_gain_95ci=[float(gain_ci.lower_95),float(gain_ci.upper_95)],median_lead_gain_h=lead_gain,paired_detected_events=int(paired.sum()),paired_detected_median_gain_h=paired_gain,holdout_primary_hours=len(main_f),holdout_primary_positions=int(main_f.position_code.nunique()),holdout_primary_events=len(main_events),candidate_bytes=fit['candidate_bytes'],protocol_sha256=manifest['protocol_sha256'],bootstrap_s=boot_s,completed_at=time.time());save(D/'step4_decision.json',decision);save(D/'step4_evaluate_status.json',dict(started_at=started,finished_at=time.time(),elapsed_s=time.time()-started,bootstrap_s=boot_s,metric_rows=len(met),bootstrap_replicates=len(boot)));print(json.dumps(decision,indent=2),flush=True)
if __name__=='__main__':main()
