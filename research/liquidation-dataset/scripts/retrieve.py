"""Exact standardized Euclidean kNN over Step 3 rows, without a risk model.

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
D=Path(__file__).resolve().parents[1]/'data';SPLIT=1767225600;END=1792972800

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
    start=time.time();sc=scaler();features=sc['features'];mean=np.array(sc['mean']);scale=np.array(sc['scale']);vec=[]
    for q in queries:
        a=np.array([q['features'][f] for f in features],dtype=float)
        if not np.isfinite(a).all():raise ValueError('Every listed feature must be finite; missing values are not imputed')
        q['as_of']=as_ts(q.get('as_of',END));q.setdefault('k',5);q.setdefault('pool','train')
        if q['as_of']<SPLIT:raise ValueError('The frozen scaler requires as-of >= 2026-01-01; pre-2026 backtesting requires an earlier scaler')
        if q['as_of']>END:raise ValueError('As-of exceeds the documented dataset cutoff')
        if q['pool'] not in ('train','past'):raise ValueError('pool must be train or past')
        if not 1<=q['k']<=100:raise ValueError('k must be 1..100')
        vec.append((a-mean)/scale)
    best=[[] for _ in queries];eligible_counts=np.zeros(len(queries),dtype='int64')
    cols=['retrieval_id','market','borrower','ts','outcome_matures_ts','index_eligible','scaler_train_row']+features
    for batch in pq.ParquetFile(D/'retrieval.parquet').iter_batches(batch_size=200000,columns=cols):
        f=batch.to_pandas();f=f[f.index_eligible]
        if f.empty:continue
        x=(f[features].to_numpy(float)-mean)/scale;ids=f.retrieval_id.to_numpy(dtype='int64')
        for j,q in enumerate(queries):
            mask=(f.outcome_matures_ts.to_numpy()<=q['as_of'])
            if q['pool']=='train':mask &= f.scaler_train_row.to_numpy(bool)
            if q.get('exclude_position'):
                mk,b=q['exclude_position'];mask &= ~((f.market==mk)&(f.borrower==b)).to_numpy()
            ix=np.flatnonzero(mask);eligible_counts[j]+=len(ix)
            if not len(ix):continue
            dist=np.einsum('ij,ij->i',x[ix]-vec[j],x[ix]-vec[j]);k=min(q['k'],len(ix))
            # Keep all ties at the kth distance before deterministic ID ordering.
            lim=np.partition(dist,k-1)[k-1];ii=np.flatnonzero(dist<=lim);ii=ii[np.lexsort((ids[ix[ii]],dist[ii]))[:k]]
            best[j]=sorted(best[j]+[(float(dist[z]),int(ids[ix[z]])) for z in ii])[:q['k']]
    allids=sorted({rid for b in best for _,rid in b});records=pq.read_table(D/'retrieval.parquet',filters=[('retrieval_id','in',allids)]).to_pylist() if allids else [];lookup={x['retrieval_id']:x for x in records};results=[]
    for q,b,n in zip(queries,best,eligible_counts):
        hits=[]
        for distance,rid in b:
            row=lookup[rid];hits.append(dict(retrieval_id=rid,distance=float(np.sqrt(distance)),market=row['market'],borrower=row['borrower'],ts=row['ts'],outcome_matures_ts=row['outcome_matures_ts'],features={x:row[x] for x in features},summary=row['summary'],liquidated_within_24h=row['liquidated_within_24h'],hours_to_liquidation=row['hours_to_liquidation'],penalty_paid_24h_usd=row['penalty_paid_24h_usd'],owner_actions_next_24h=row['owner_actions_next_24h'],binance_fallback=row['binance_fallback'],episode_binance_fallback=row['episode_binance_fallback'],cohort_flag=row['cohort_flag']))
        results.append(dict(query=q,eligible_candidates=int(n),neighbors=hits))
    return dict(metric='Euclidean distance after frozen pre-2026 z-score scaling; exact search',elapsed_s=time.time()-start,results=results)

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
        if not a.output.resolve().is_relative_to(D.parent):raise ValueError('Output must be inside the dataset folder')
        a.output.write_text(s)
    else:print(s)
if __name__=='__main__':main()
