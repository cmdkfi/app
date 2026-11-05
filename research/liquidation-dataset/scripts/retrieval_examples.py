"""Five reproducible demonstration queries; examples are not an evaluation set."""
from pathlib import Path
import importlib.util,sys,json,time
import numpy as np
import pyarrow.parquet as pq
S=Path(__file__).resolve().parent;D=S.parent/'data';spec=importlib.util.spec_from_file_location('retrieve',S/'retrieve.py');r=importlib.util.module_from_spec(spec);sys.modules['retrieve']=r;spec.loader.exec_module(r)
def main():
    start=time.time();chosen=[None]*5;names=['cbBTC stress hour','Base WETH dust position','Ethereum WETH small cohort','Ethereum wstETH cohort','Binance-fallback episode']
    cols=['retrieval_id','chain','collateral_asset','market','borrower','ts','index_eligible','binance_fallback','episode_binance_fallback']+r.scaler()['features']
    for batch in pq.ParquetFile(D/'retrieval.parquet').iter_batches(batch_size=200000,columns=cols):
        f=batch.to_pandas();f=f[f.index_eligible&(f.ts>=r.SPLIT)&(f.ltv>.70)]
        if f.empty:continue
        masks=[(f.collateral_asset=='cbBTC')&(f.price_change_24h<-.05)&(f.position_size_usd>100), (f.chain==8453)&(f.collateral_asset=='WETH')&(f.position_size_usd<.04)&(f.position_size_usd>0), (f.chain==1)&(f.collateral_asset=='WETH')&(f.position_size_usd>100), (f.collateral_asset=='wstETH')&(f.position_size_usd>100), f.binance_fallback|f.episode_binance_fallback]
        for i,mask in enumerate(masks):
            g=f[mask]
            if not len(g):continue
            # Stress example near the liquidation line; others near .78.
            # Selection uses feature values only, never neighbor outcomes.
            target=.855 if i==0 else .78
            g=g.assign(score=(g.ltv-target).abs()).sort_values(['score','retrieval_id']);row=g.iloc[0];candidate=(float(row.score),int(row.retrieval_id))
            if chosen[i] is None or candidate<chosen[i]:chosen[i]=candidate
    assert all(x is not None for x in chosen),chosen
    queries=[]
    for name,(_,i) in zip(names,chosen):q=r.row_query(i,k=3);q['name']=name;queries.append(q)
    (D/'retrieval_example_queries.json').write_text(json.dumps(queries,indent=2,allow_nan=False)+'\n')
    result=r.search_many(queries);result['total_example_elapsed_s']=time.time()-start
    # Distances and maturity are checked independently of search ranking.
    sc=r.scaler();scale=np.array(sc['scale'])
    for item in result['results']:
        q=item['query'];a=np.array([q['features'][k] for k in sc['features']]);last=-1
        for hit in item['neighbors']:
            b=np.array([hit['features'][k] for k in sc['features']]);dist=np.sqrt(np.sum(((a-b)/scale)**2));assert np.isclose(dist,hit['distance'],rtol=1e-10);assert dist>=last-1e-10;last=dist;assert hit['outcome_matures_ts']<=q['as_of'];assert hit['outcome_matures_ts']<r.SPLIT;assert [hit['market'],hit['borrower']]!=q['exclude_position']
    (D/'retrieval_examples.json').write_text(json.dumps(result,indent=2,allow_nan=False)+'\n');print(json.dumps(dict(examples=len(queries),neighbors=sum(len(x['neighbors']) for x in result['results']),elapsed_s=time.time()-start,queries=[(q['name'],q['query_row_id']) for q in queries]),indent=2))
if __name__=='__main__':main()
