#!/usr/bin/env python3
"""Offline Step 3 invariants. Run python3 -I; never sends inference."""
import hashlib
import importlib.util
import json
import sys
from pathlib import Path
assert sys.flags.isolated
def module(name,path):
    spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
s=module('step3',Path(__file__).with_name('step3.py'));c=s.c
d=module('discover3',Path(__file__).with_name('discover_step3.py'))
s.verify()
arms=s.active_arms();by_id={}
base=c.read(c.ROOT/'runs/step2_clean/tasks.json')
for arm in arms:
    name=arm['name'];by_id.setdefault(arm['arm'],[]).append(arm)
    tasks=c.read(c.ROOT/'runs'/name/'tasks.json')
    assert len(tasks)==len(base)
    for t,b in zip(tasks,base):
        assert (t['run'],t['t'],t['user'],t['shown'],t['oid_map'])==(b['run'],b['t'],b['user'],b['shown'],b['oid_map'])
        body=json.loads(s.request_body(t,arm));prior=json.loads(c.h.request_body(b['user'],'deep8k'))
        body['model']=prior['model'];body['max_tokens']=prior['max_tokens'];assert body==prior
    if arm['arm']=='glm8k':assert [t['req_sha256'] for t in tasks]==[t['req_sha256'] for t in base]
    p=c.ROOT/'runs'/name/'calls.jsonl'
    if not p.exists():continue
    rs=[json.loads(l) for l in p.read_text().splitlines()];lookup={t['req_sha256']:t for t in tasks}
    assert len(rs)==len({r['req_sha256'] for r in rs})
    for r in rs:
        t=lookup[r['req_sha256']];folder=p.parent
        assert c.sha(folder/'responses'/(r['req_sha256']+'.json'))==r['resp_sha256']
        assert c.sha(folder/'requests'/(r['req_sha256']+'.json'))==r['req_sha256']
        obj,errors=c.strict(r['content'],t['oid_map']);assert (not errors)==r['valid'] and errors==r['schema_errors']
        assert r['usage']['completion_tokens']<=arm['max_tokens']
        assert r['usage']['total_tokens']==r['usage']['prompt_tokens']+r['usage']['completion_tokens']
        if r['sig']['passed']:assert r['sig']['kind']=='provider_tee' and r['sig']['eip191_verified'] and r['sig']['text_match']
for name,pair in by_id.items():
    assert len(pair)==(1 if name=='glm8k' else 2)
    if len(pair)==2:
        assert [t['req_sha256'] for t in c.read(c.ROOT/'runs'/pair[0]['name']/'tasks.json')]==[t['req_sha256'] for t in c.read(c.ROOT/'runs'/pair[1]['name']/'tasks.json')]
discovery=c.read(c.ART/'discovery.json')
for row in discovery['models']:
    if not row['eligible']:continue
    folder=c.ROOT/row['folder'];body=(folder/'request.json').read_bytes();raw=(folder/'response.json').read_bytes()
    sig=c.read(folder/'signature_1.json')
    assert d.signature_check(sig,body,raw,row['model'],row['attestation'])['passed']
    assert not d.signature_check(sig,body+b' ',raw,row['model'],row['attestation'])['passed']
    altered=dict(sig,text=sig['text']+'tamper')
    assert not d.signature_check(altered,body,raw,row['model'],row['attestation'])['eip191_verified']
budget=c.read(c.ROOT/'runs/step3_budget.json')
assert sum(e['charged_or_reserved_usd'] for e in budget['entries'].values())<=c.CAP
for e in budget['entries'].values():
    if e['status']=='completed':
        u,r=e['usage'],e['rates'];cost=(u['prompt_tokens']*r['input_per_million']+u['completion_tokens']*r['output_per_million'])/1e6
        assert abs(cost-e['charged_or_reserved_usd'])<1e-12
a=module('analysis3',Path(__file__).with_name('analyze_step3.py'))
runner=module('runner3',Path(__file__).with_name('run_step3.py'))
for code in (401,402,403,400,404):assert not runner.retryable(22,code)
for code in (0,None,408,429,500,503):assert runner.retryable(22,code)
baseline=dict(spread={w:{m:a.envelope(v) for m,v in dict(useful_exits=[6,6],control_exits=[0,1],control_high=[1,2],control_exit_or_high=[1,2]).items()} for w in ['raw','after_agreement']})
candidate=json.loads(json.dumps(baseline));candidate['stats']=dict(signatures_passed=582,calls=582)
assert a.qualifies(candidate,baseline)['qualifies']
candidate['spread']['raw']['useful_exits']=a.envelope([5,7])
assert not a.qualifies(candidate,baseline)['qualifies'],'An average match must not hide failed repeats'
candidate['spread']['raw']['useful_exits']=a.envelope([6,6])
candidate['spread']['after_agreement']['control_exits']=a.envelope([0,2])
assert not a.qualifies(candidate,baseline)['qualifies'],'Agreement-filtered regressions also block recommendation'
candidate['spread']['after_agreement']['control_exits']=a.envelope([0,1])
candidate['stats']['signatures_passed']=581
assert not a.qualifies(candidate,baseline)['qualifies'],'Unverified candidate responses block recommendation'
sp=c.ROOT/'runs/step3_summary.json'
if sp.exists():
    summary=c.read(sp);assert summary['complete'] and summary['new_calls']==summary['planned_new_calls']
    for name,exp in summary['runs'].items():
        assert exp['complete'] and exp['planned_calls']==exp['stats']['calls']
        for w in exp['windows']:
            for row in w['timeline']:
                assert row['measured'] and row['raw']['risk']==row['after_agreement']['risk']
                for fire in row['gate']['fresh_fires']:assert 0<=fire['age_s']<=1800
                if row['after_agreement']['action'] in ('exit','reduce_light','reduce_to_target'):
                    assert row['gate']['passes'] and row['after_agreement']['action']==row['raw']['action']
    chosen=summary['recommendation']['arm']
    if chosen!='glm8k':assert summary['pairs'][chosen]['qualification']['qualifies']
print('PASS: paired request identity, byte-bound signatures and tamper rejection, schema/output limits and whole-step budget.')
