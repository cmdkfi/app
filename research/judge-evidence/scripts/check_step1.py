#!/usr/bin/env python3
"""Offline invariants for Step 1 scoring, time visibility, corpus and accounting."""
import importlib.util
import json
import sys
from pathlib import Path

assert sys.flags.isolated
spec=importlib.util.spec_from_file_location('analysis',Path(__file__).with_name('analyze_step1.py'))
a=importlib.util.module_from_spec(spec)
spec.loader.exec_module(a)
s,h=a.s,a.h
s.verify()

valid=dict(risk='high',action='exit',reasons=['concern'],source_ids=['O01'])
assert s.strict(json.dumps(valid),{'O01':'item'})[1]==[]
bad=[dict(valid,action='transfer'),dict(valid,recipient='attacker'),dict(valid,reasons=[1]),
     dict(valid,source_ids=['O99'])]
assert all(s.strict(json.dumps(x),{'O01':'item'})[1] for x in bad)
assert s.strict('prefix '+json.dumps(valid),{'O01':'item'})[1]
assert s.strict('{"risk":"low","risk":"high"}',{})[1]

now=h.P('2026-01-01T00:01:00Z')
def item(i,source,support=True,src='news',when=now,retracts=None):
    return dict(id=i,source=source,support=support,src=src,t=when,retracts=retracts or [])
t=dict(t=h.F(now),shown=['a','b'],oid_map={'O01':'a','O02':'b'})
v=dict(source_ids=['O01','O02'])
same=[item('a','publisher'),item('b','publisher')]
assert not a.gate(t,v,same,[])['passes'], 'two posts by one source are not independent'
different=[item('a','publisher-a'),item('b','publisher-b')]
assert a.gate(t,v,different,[])['passes']
assert not a.gate(t,dict(source_ids=['O01']),different,[])['passes'], 'uncited support not enough'
different[1]['support']=False
assert not a.gate(t,v,different,[])['passes'], 'a reassuring citation is not corroboration'
different[1]['support']=True
different.append(item('c','publisher-a',False,retracts=['a']))
assert not a.gate(t,v,different,[])['passes'], 'retraction removes earlier support'
future=h.P('2026-01-01T00:02:00Z')
chain=item('chain','chain-monitor',False,'onchain',future)
chain_task=dict(t=h.F(now),shown=[],oid_map={})
assert not a.gate(chain_task,dict(source_ids=[]),[chain], [dict(t=now,item='chain')])['passes'], 'future item cannot fire'
chain['t']=now
assert a.gate(chain_task,dict(source_ids=[]),[chain], [dict(t=now,item='chain')])['passes']

corpus=s.read(s.ROOT/'data/injections.json')
assert len({i['id'] for i in corpus})==len(corpus)
assert len({i['campaign'] for i in corpus if i['type']=='f'})==len([i for i in corpus if i['type']=='f'])//2
for name in s.NAMES:
    tasks=s.read(s.ROOT/'runs'/name/'tasks.json')
    task_map={t['req_sha256']:t for t in tasks}
    cp=s.ROOT/'runs'/name/'calls.jsonl'
    if cp.exists():
        recs=[json.loads(line) for line in cp.read_text().splitlines()]
        assert len({rec['req_sha256'] for rec in recs})==len(recs)
        for rec in recs:
            assert rec['req_sha256'] in task_map
            if rec.get('response_storage')=='exact provider bytes':
                assert s.sha(s.ROOT/'runs'/name/'responses'/(rec['req_sha256']+'.json'))==rec['resp_sha256']
            usage=rec['usage']
            assert usage['total_tokens']==usage['prompt_tokens']+usage['completion_tokens']
    applicable=[i for i in corpus if i['type']==name[-1]]
    for i in applicable:
        visible=[t for t in tasks if i['id'] in t['shown']]
        assert visible, i['id']
        assert all(t['t']>=i['effective_time_utc'] for t in visible), i['id']
        assert any(i['replay_text'] in t['user'] for t in visible), i['id']
    for t in tasks:
        assert 'supports_exit' not in t['user'] and 'synthetic hostile' not in t['user']

summary=s.ROOT/'runs/summary.json'
if summary.exists():
    r=s.read(summary)
    assert r['complete'] and r['total']['cost_usd']<=s.CAP
    for exp in r['experiments'].values():
        assert exp['stats']['calls']==exp['planned_calls']
        for w in exp['windows']:
            for row in w['timeline']:
                assert row['measured']
                assert row['raw']['risk']==row['after_agreement']['risk']
                if row['after_agreement']['action']=='exit':
                    assert row['raw']['action']=='exit' and row['gate']['passes']
partial=s.ROOT/'runs/incomplete_summary.json'
if partial.exists():
    r=s.read(partial)
    assert not r['complete'] and 'per_type' not in r
    assert r['budget_reserved_or_charged_usd']<=s.CAP
    assert abs(r['total']['cost_usd']+r['unknown_cost_upper_bound_usd']-r['budget_reserved_or_charged_usd'])<1e-9
    for exp in r['experiments'].values():
        for w in exp['windows']:
            for row in w['timeline']:
                if not row['measured']: continue
                assert row['raw']['risk']==row['after_agreement']['risk']
                if row['after_agreement']['action']=='exit':
                    assert row['raw']['action']=='exit' and row['gate']['passes']
    for w in r['incidents']:
        assert all(v['status']=='incomplete_not_scored' for k,v in w.items() if k.startswith('delay_'))
print('PASS: strict rejection, source independence/support, retractions, no future screens, corpus visibility and completed-run invariants.')
