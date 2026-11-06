"""Fit one frozen small classifier on pre-2026 primary venue-feed hours only."""
import os
os.environ['OMP_NUM_THREADS']='4';os.environ['OPENBLAS_NUM_THREADS']='4'
from pathlib import Path
import json,time,hashlib
import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import joblib
from sklearn.ensemble import HistGradientBoostingClassifier
from threadpoolctl import threadpool_limits
ROOT=Path(__file__).resolve().parents[1];D=ROOT/'data';S=ROOT/'scripts'
def save(p,x):p.write_text(json.dumps(x,indent=2,allow_nan=False)+'\n')
def main():
    started=time.time();protocol=json.loads((D/'step4_protocol.json').read_text());assert protocol['created_at']<started;features=protocol['features'];ph=hashlib.sha256((D/'step4_protocol.json').read_bytes()).hexdigest();train=pq.read_table(D/'step4_hours.parquet',filters=[('split','=','train'),('primary_cohort','=',True),('source_group','=','venue_only')],columns=features+['y','ts','row_key_hash','market','borrower','case_id']).to_pandas();assert (train.ts+86400<1767225600).all();assert train.y.nunique()==2
    manifest=dict(started_at=started,protocol_sha256=ph,train_rows=len(train),train_positions=len(train[['market','borrower']].drop_duplicates()),train_episodes=train.case_id.nunique(),train_positives=int(train.y.sum()),latest_train_hour=int(train.ts.max()),latest_train_horizon_end=int(train.ts.max()+86400),training_keys_sha256=hashlib.sha256(np.sort(train.row_key_hash.to_numpy()).tobytes()).hexdigest(),parameters=protocol['parameters'],features=features,train_command='scripts/.venv/bin/python3 -I scripts/step4_train.py',model_selection='one predeclared fit; no hyperparameter sweep or early stopping',holdout_used_in_fit=False)
    save(D/'step4_training_manifest.json',manifest)
    model=HistGradientBoostingClassifier(**protocol['parameters']);t=time.time()
    with threadpool_limits(limits=4):model.fit(train[features].to_numpy(float),train.y.to_numpy())
    fit_s=time.time()-t;assert model.n_iter_==protocol['parameters']['max_iter'];bundle=dict(model=model,features=features,protocol=protocol,training_manifest=manifest);candidate=D/'step4_candidate.joblib';joblib.dump(bundle,candidate,compress=3)
    fit_finished=time.time();save(D/'step4_fit_status.json',dict(started_at=started,fit_finished_at=fit_finished,fit_s=fit_s,candidate_bytes=candidate.stat().st_size,iterations=int(model.n_iter_),model_sha256=hashlib.sha256(candidate.read_bytes()).hexdigest(),training_manifest_sha256=hashlib.sha256((D/'step4_training_manifest.json').read_bytes()).hexdigest()))
    outcols=['chain','market','borrower','case_id','asset_group','collateral_asset','ts','split','source_group','episode_binance_fallback','primary_cohort','start_size_usd','y','row_key_hash','next_liquidation_ts','next_liquidation_id','ltv','oracle_vs_binance_pct','t0','t_end'];writer=None;rows=0;t=time.time()
    try:
        for batch in pq.ParquetFile(D/'step4_hours.parquet').iter_batches(batch_size=200000):
            f=batch.to_pandas()
            with threadpool_limits(limits=4):score=model.predict_proba(f[features].to_numpy(float))[:,1]
            out=f[outcols].copy();out['model_score']=score;out['act_score']=out.ltv.fillna(-np.inf);out['combined_score']=np.maximum(out.ltv.fillna(-np.inf)/.7,out.oracle_vs_binance_pct.fillna(-np.inf)/2);out['act_fixed']=(out.ltv>.7);out['combined_fixed']=(out.ltv>.7)|(out.oracle_vs_binance_pct>2);assert np.isfinite(score).all();rows+=len(out);table=pa.Table.from_pandas(out,preserve_index=False)
            if writer is None:writer=pq.ParquetWriter(D/'step4_predictions.parquet.tmp',table.schema,compression='zstd')
            writer.write_table(table.cast(writer.schema))
            print('predicted',rows,flush=True)
    finally:
        if writer:writer.close()
    (D/'step4_predictions.parquet.tmp').replace(D/'step4_predictions.parquet');save(D/'step4_train_status.json',dict(started_at=started,finished_at=time.time(),fit_s=fit_s,predict_s=time.time()-t,elapsed_s=time.time()-started,rows_predicted=rows,candidate_bytes=candidate.stat().st_size));print(json.dumps(dict(train_rows=len(train),train_positive=int(train.y.sum()),fit_s=fit_s,predicted=rows,candidate_bytes=candidate.stat().st_size),indent=2))
if __name__=='__main__':main()
