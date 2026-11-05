#!/usr/bin/env python3
"""Independent exact-search and temporal-scaler checks. Use dataset venv python3 -I."""
import importlib.util
import json
import sys
from pathlib import Path
import numpy as np
import pyarrow.parquet as pq
assert sys.flags.isolated
ROOT=Path(__file__).resolve().parents[1]
LIB=ROOT.parent/'Liquidation dataset'
def module(name,path):
    spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
original=module('original',LIB/'scripts/retrieve.py')
copy=module('adapted',ROOT/'scripts/retrieve.py')
q=original.row_query(403770,3);q.pop('exclude_position',None)
left=original.search_many([dict(q)])['results'][0]['neighbors']
right=copy.search_many([dict(q)])['results'][0]['neighbors']
assert [n['retrieval_id'] for n in left]==[n['retrieval_id'] for n in right]
assert np.allclose([n['distance'] for n in left],[n['distance'] for n in right])
ret=json.loads((ROOT/'data/step2/retrieval.json').read_text())
selected=[];seen=set()
for r in ret['results']:
    if not r['neighbors']:continue
    key=(r['query']['as_of']<original.SPLIT,tuple(r['used_features']))
    if key not in seen:seen.add(key);selected.append(r)
for r in selected:
    q=r['query'];features=r['used_features'];sc=ret['scalers'][r['scaler_key']]
    frame=pq.read_table(LIB/'data/retrieval.parquet',columns=['retrieval_id']+features,
        filters=[('scaler_train_row','=',True),('index_eligible','=',True),
                 ('outcome_matures_ts','<=',q['as_of'])]).to_pandas()
    assert len(frame)==r['eligible_candidates']==sc['training_rows']
    scale=np.array([sc['scale'][sc['features'].index(f)] for f in features])
    if q['as_of']<original.SPLIT:
        assert np.allclose(scale,frame[features].to_numpy().std(axis=0))
        assert np.allclose([sc['mean'][sc['features'].index(f)] for f in features],frame[features].to_numpy().mean(axis=0))
    delta=(frame[features].to_numpy()-np.array([q['features'][f] for f in features]))/scale
    dist=np.sqrt(np.square(delta).sum(axis=1))
    ix=np.lexsort((frame.retrieval_id.to_numpy(),dist))[:3]
    assert list(frame.retrieval_id.to_numpy()[ix])==[n['retrieval_id'] for n in r['neighbors']]
    assert np.allclose(dist[ix],[n['distance'] for n in r['neighbors']])
print(f'PASS: original complete-vector parity plus {len(selected)} independent observed-dimension/time-pool searches and earlier scalers.')
