#!/usr/bin/env python3
"""Offline tests and frozen-input/result invariants; python3 -I."""
import hashlib
import json
import sys
from datetime import timedelta
from pathlib import Path
assert sys.flags.isolated
import importlib.util
def module(name,path):
    spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
s=module('step2',Path(__file__).with_name('step2.py'))
g=module('agreement',Path(__file__).with_name('agreement.py'))
s.verify()
now=s.h.P('2026-01-01T01:00:00Z')
def item(i,src,when=now,retracts=None):
    return dict(id=i,source=src,src='news',support=True,t=when,retracts=retracts or [])
task=dict(t=s.h.F(now),shown=['a','b'],oid_map={'O01':'a','O02':'b'})
v=dict(source_ids=['O01','O02'])
items=[item('a','A'),item('b','B')]
assert g.gate(task,v,items,[])['passes']
assert not g.gate(task,dict(source_ids=['O01']),items,[])['passes']
items[1]['source']='A';assert not g.gate(task,v,items,[])['passes'];items[1]['source']='B'
items.append(item('r','wrong',retracts=['a']));assert g.gate(task,v,items,[])['passes']
items[-1]['source']='A';assert not g.gate(task,v,items,[])['passes']
items.append(dict(item('c','chain'),src='onchain',support=False))
fires=[dict(item='c',t=now-timedelta(minutes=30))]
assert g.gate(task,v,items,fires)['passes'], 'retraction does not cancel fresh screen'
fires[0]['t']-=timedelta(seconds=1);assert not g.gate(task,v,items,fires)['passes']
fires[0]['t']=now+timedelta(seconds=1);assert not g.gate(task,v,items,fires)['passes']
fires[0]['t']=now;items[-1]['t']=now+timedelta(seconds=1)
assert not g.gate(task,v,items,fires)['passes'],'future effective item unavailable'

queries=s.read(s.ART/'queries.json');ret=s.read(s.ART/'retrieval.json')
clean=s.read(s.ROOT/'runs'/s.NAMES[0]/'tasks.json');aug=s.read(s.ROOT/'runs'/s.NAMES[1]/'tasks.json')
assert len(clean)==len(aug)==len(queries)==len(ret['results'])
assert s.clean_tasks()[0]==s.read(s.ART/'clean_tasks.json')
assert s.clean_tasks()[1]==queries
for q,r,a,b in zip(queries,ret['results'],clean,aug):
    assert q['run']==a['run']==b['run'] and q['t']==a['t']==b['t']
    assert set(q['features']).isdisjoint(q['missing_features'])
    assert set(q['features'])|set(q['missing_features'])==set(s.SHARED)
    assert set(r['used_features'])<=set(q['features'])
    for f,p in q['provenance'].items():
        assert p['sample_time']<=q['t'] and p['effective_time']<=q['t']
    if r['scaler_key']:
        sc=ret['scalers'][r['scaler_key']]
        assert sc['latest_allowed_maturity_ts']<=q['as_of']
    for n in r['neighbors']:
        assert n['outcome_matures_ts']<=q['as_of'] and n['outcome_matures_ts']<1767225600
        assert n['ts']<1767225600
    if r['neighbors']:
        context='\n\n'+s.HEADING+'\n'+'\n'.join('- '+n['summary'] for n in r['neighbors'])
        assert b['user'].replace(context,'')==a['user']
        assert len(r['neighbors'])==3
    else:assert b['user']==a['user']

for name in s.NAMES:
    folder=s.ROOT/'runs'/name;tasks={t['req_sha256']:t for t in s.read(folder/'tasks.json')}
    cp=folder/'calls.jsonl'
    if not cp.exists():continue
    rs=[json.loads(l) for l in cp.read_text().splitlines()]
    assert len({r['req_sha256'] for r in rs})==len(rs)
    for r in rs:
        t=tasks[r['req_sha256']]
        assert s.sha(folder/'responses'/(r['req_sha256']+'.json'))==r['resp_sha256']
        assert s.sha(folder/'requests'/(r['req_sha256']+'.json'))==r['req_sha256']
        obj,errors=s.strict(r['content'],t['oid_map'])
        assert errors==r['schema_errors'] and (not errors)==r['valid']
        assert r['usage']['total_tokens']==r['usage']['prompt_tokens']+r['usage']['completion_tokens']
bp=s.ROOT/'runs/step2_budget.json'
if bp.exists():
    budget=s.read(bp)
    assert sum(e['charged_or_reserved_usd'] for e in budget['entries'].values())<=s.CAP
    for e in budget['entries'].values():
        if e['status']=='completed':
            cost=e['usage']['prompt_tokens']*s.h.PRICE_IN+e['usage']['completion_tokens']*s.h.PRICE_OUT
            assert abs(cost-e['charged_or_reserved_usd'])<1e-12
a=module('analysis_step2',Path(__file__).with_name('analyze_step2.py'))
def example(exit_time,action='exit',useful=1,alarms=0):
    row=dict(t='2026-01-01T01:00:00Z',measured=True,label=['high','exit'],raw=dict(action=action,risk='high'))
    window=dict(run='INC-01/test',raw=dict(first_exit=exit_time),timeline=[row])
    return dict(windows=[window],aggregate={'raw':dict(useful_exits=useful,control_exits=alarms,control_exit_or_high=alarms)})
ref=example('2026-01-01T01:00:00Z')
assert a.comparison(ref,example('2026-01-01T00:59:00Z'),'raw')['keep_retrieval']
assert not a.comparison(ref,example('2026-01-01T01:01:00Z'),'raw')['keep_retrieval']
assert not a.comparison(ref,example(None,useful=0),'raw')['keep_retrieval']
assert not a.comparison(ref,example('2026-01-01T00:59:00Z',alarms=1),'raw')['keep_retrieval']
assert a.matched_labels(ref,example(None,action='hold'),'raw')['right_action']==0
summary=s.ROOT/'runs/step2_summary.json'
if summary.exists():
    result=s.read(summary);assert result['complete']
    for name,exp in result['experiments'].items():
        assert exp['complete'] and exp['stats']['calls']==exp['planned_calls']
        for w in exp['windows']:
            assert all(row['measured'] for row in w['timeline'])
            for row in w['timeline']:
                assert row['raw']['risk']==row['after_agreement']['risk']
                if row['after_agreement']['action'] in ('exit','reduce_light','reduce_to_target'):
                    assert row['raw']['action']==row['after_agreement']['action'] and row['gate']['passes']
                for fire in row['gate']['fresh_fires']:
                    assert 0<=fire['age_s']<=1800
    assert abs(result['total']['cost_usd']-result['budget']['returned_token_cost_usd'])<1e-9
    reuse=s.read(s.ROOT/'runs/step2_clean/reuse_for_step3.json')
    assert reuse['calls_sha256']==s.sha(s.ROOT/'runs/step2_clean/calls.jsonl')
print('PASS: 30-minute screens, same-source retractions, query omissions, as-of-safe neighbors/scalers, arm parity, schema and cost invariants.')
