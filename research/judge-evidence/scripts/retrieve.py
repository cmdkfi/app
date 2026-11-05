"""Copied retrieval harness, adapted for Judge evidence Step 2 (observed query dimensions and earlier scalers).

Examples (run with the local virtual environment's python3 -I):
  scripts/retrieve.py --row-id 12345 --k 3
  scripts/retrieve.py --features-file data/query.json --as-of 2026-10-26T00:00:00Z
Default candidates are mature pre-2026 rows; --pool past explicitly permits
mature holdout observations. No outcome field participates in the distance.
"""
from pathlib import Path
import argparse,json,time,datetime
import numpy as np
import pyarrow.parquet as pq
D=Path(__file__).resolve().parents[2]/'Liquidation dataset/data';SPLIT=1767225600;END=1792972800

def scaler():return json.loads((D/'retrieval_scaler.json').read_text())
def row_by_id(i):
    t=pq.read_table(D/'retrieval.parquet',filters=[('retrieval_id','=',int(i))]);assert t.num_rows==1,f'No unique retrieval_id {i}';return t.to_pylist()[0]
def as_ts(x):
    if isinstance(x,(int,float)):return int(x)
    if str(x).isdigit():return int(x)
    t=datetime.datetime.fromisoformat(x.replace('Z','+00:00'))
    if t.tzinfo is None:raise ValueError('as-of requires timezone, e.g. 2026-02-01T00:00:00Z')
    return int(t.timestamp())
def row_query(i,k=3,pool='train'):
    row=row_by_id(i);return dict(features={x:row[x] for x in scaler()['features']},as_of=row['ts'],k=k,pool=pool,exclude_position=[row['market'],row['borrower']],query_row_id=int(i))

def search_many(queries):
    """Step 2 adaptation: exact kNN in observed dimensions; as-of-safe scaling.

    Original candidate eligibility, metric and deterministic tie-breaking retained.
    Complete library vectors stay required, but query dimensions may be omitted.
    No outcomes enter distance. Earlier scalers use only already mature train rows.
    """
    start=time.time();sc=scaler();features=sc['features']
    cols=['retrieval_id','market','borrower','ts','outcome_matures_ts']+features
    f=pq.read_table(D/'retrieval.parquet',columns=cols,
        filters=[('scaler_train_row','=',True),('index_eligible','=',True)]).to_pandas()
    f=f.sort_values(['outcome_matures_ts','retrieval_id']).reset_index(drop=True)
    maturity=f.outcome_matures_ts.to_numpy();ids=f.retrieval_id.to_numpy(dtype='int64')
    x=f[features].to_numpy(float);assert np.isfinite(x).all()
    best=[];metadata=[];scalers={};cache={}
    for q0 in queries:
        q=dict(q0);q['as_of']=as_ts(q['as_of']);assert q.get('pool','train')=='train'
        assert 1<=q.get('k',3)<=100
        # Later replay times are valid: the fixed training library never grows.
        used=[v for v in features if v in q['features']]
        assert set(q['features']).issubset(features)
        assert all(np.isfinite(float(v)) for v in q['features'].values())
        cutoff=min(q['as_of'],SPLIT-1)
        count=int(np.searchsorted(maturity,cutoff,side='right'))
        key=(cutoff,tuple((v,q['features'][v]) for v in used),q.get('k',3))
        if not used or not count:
            best.append([]);metadata.append(dict(query=q,eligible_candidates=count,
                used_features=used,scaler_key=None,status='no_features' if not used else 'no_mature_candidates'));continue
        if cutoff not in scalers:
            if q['as_of']>=SPLIT:
                fitted=dict(features=features,mean=sc['mean'],scale=sc['scale'],training_rows=sc['training_rows'],
                    latest_allowed_maturity_ts=SPLIT-1,method='original frozen pre-2026 scaler')
                assert count==sc['training_rows']
            else:
                mean=x[:count].mean(axis=0);scale=x[:count].std(axis=0)
                fitted=dict(features=features,mean=mean.tolist(),scale=scale.tolist(),training_rows=count,
                    latest_allowed_maturity_ts=cutoff,method='population mean/std on train rows mature at query timestamp')
            scalers[cutoff]=fitted
        fitted=scalers[cutoff]
        # Constant dimensions carry no distance information and are omitted explicitly.
        omitted=[v for v in used if fitted['scale'][features.index(v)]==0]
        used=[v for v in used if v not in omitted];ix=[features.index(v) for v in used]
        if key not in cache:
            if not used:cache[key]=[]
            else:
                a=np.array([q['features'][v] for v in used]);scale=np.array(fitted['scale'])[ix]
                delta=(x[:count,ix]-a)/scale
                dist=np.einsum('ij,ij->i',delta,delta);k=min(q.get('k',3),count)
                lim=np.partition(dist,k-1)[k-1];ii=np.flatnonzero(dist<=lim)
                ii=ii[np.lexsort((ids[ii],dist[ii]))[:k]]
                cache[key]=[(float(dist[z]),int(ids[z])) for z in ii]
        best.append(cache[key]);metadata.append(dict(query=q,eligible_candidates=count,used_features=used,
            zero_variance_omitted=omitted,scaler_key=str(cutoff),status='retrieved' if used else 'no_varying_features'))
    allids=sorted({rid for b in best for _,rid in b})
    records=pq.read_table(D/'retrieval.parquet',filters=[('retrieval_id','in',allids)]).to_pylist() if allids else []
    lookup={x['retrieval_id']:x for x in records};results=[]
    for meta,b in zip(metadata,best):
        hits=[]
        for distance,rid in b:
            row=lookup[rid]
            assert row['outcome_matures_ts']<=meta['query']['as_of'] and row['outcome_matures_ts']<SPLIT
            hits.append(dict(retrieval_id=rid,distance=float(np.sqrt(distance)),market=row['market'],borrower=row['borrower'],
                ts=row['ts'],outcome_matures_ts=row['outcome_matures_ts'],features={v:row[v] for v in features},
                summary=row['summary'],liquidated_within_24h=row['liquidated_within_24h'],
                hours_to_liquidation=row['hours_to_liquidation'],penalty_paid_24h_usd=row['penalty_paid_24h_usd'],
                owner_actions_next_24h=row['owner_actions_next_24h'],binance_fallback=row['binance_fallback'],
                episode_binance_fallback=row['episode_binance_fallback'],cohort_flag=row['cohort_flag']))
        results.append(dict(meta,neighbors=hits))
    return dict(metric='Exact standardized Euclidean kNN in observed dimensions, deterministic retrieval_id ties',
        elapsed_s=time.time()-start,scalers=scalers,results=results)

def main():
    p=argparse.ArgumentParser(description=__doc__);g=p.add_mutually_exclusive_group(required=True);g.add_argument('--row-id',type=int);g.add_argument('--features',help='JSON object containing all 11 feature values');g.add_argument('--features-file',type=Path);g.add_argument('--queries-file',type=Path,help='JSON array; each item has features, as_of, k and optional exclude_position');p.add_argument('--as-of',default=None);p.add_argument('--k',type=int,default=5);p.add_argument('--pool',choices=['train','past'],default='train');p.add_argument('--output',type=Path);a=p.parse_args()
    if a.queries_file:queries=json.loads(a.queries_file.read_text())
    elif a.row_id is not None:
        q=row_query(a.row_id,a.k,a.pool)
        if a.as_of:q['as_of']=as_ts(a.as_of)
        queries=[q]
    else:queries=[dict(features=json.loads(a.features_file.read_text() if a.features_file else a.features),as_of=as_ts(a.as_of) if a.as_of else END,k=a.k,pool=a.pool)]
    result=search_many(queries);s=json.dumps(result,indent=2,allow_nan=False)+'\n'
    if a.output:
        # All task artifacts stay within this dataset folder.
        if not a.output.resolve().is_relative_to(Path(__file__).resolve().parents[1]):raise ValueError('Output must be inside the dataset folder')
        a.output.write_text(s)
    else:print(s)
if __name__=='__main__':main()
