"""Independent full-sort ranking verification and public CLI output parity."""
from pathlib import Path
import json,time,subprocess
import numpy as np
import pyarrow.parquet as pq
D=Path(__file__).resolve().parents[1]/'data';S=D.parent/'scripts'
def main():
    start=time.time();sc=json.loads((D/'retrieval_scaler.json').read_text());examples=json.loads((D/'retrieval_examples.json').read_text());cols=['retrieval_id','market','borrower','outcome_matures_ts']+sc['features'];f=pq.read_table(D/'retrieval.parquet',filters=[('scaler_train_row','=',True)],columns=cols).to_pandas();a=f[sc['features']].to_numpy(float);scale=np.array(sc['scale']);ids=f.retrieval_id.to_numpy();checked=0
    for item in examples['results']:
        q=item['query'];v=np.array([q['features'][k] for k in sc['features']]);mask=(f.outcome_matures_ts<=q['as_of']).to_numpy(copy=True);mk,b=q['exclude_position'];mask &= ~((f.market==mk)&(f.borrower==b)).to_numpy();ix=np.flatnonzero(mask);dist=np.linalg.norm((a[ix]-v)/scale,axis=1);order=np.lexsort((ids[ix],dist))[:q['k']];assert ids[ix[order]].tolist()==[h['retrieval_id'] for h in item['neighbors']];assert len(ix)==item['eligible_candidates'];checked+=len(order)
    subprocess.run([str(S/'.venv/bin/python3'),'-I',str(S/'retrieve.py'),'--queries-file',str(D/'retrieval_example_queries.json'),'--output',str(D/'retrieval_cli_check.json')],check=True)
    cli=json.loads((D/'retrieval_cli_check.json').read_text());assert cli['results']==examples['results']
    result=dict(all_passed=True,full_sort_queries=len(examples['results']),neighbors_checked=checked,candidate_training_rows=len(f),public_cli_matches=True,elapsed_s=time.time()-start)
    (D/'step3_neighbor_validation.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))
if __name__=='__main__':main()
