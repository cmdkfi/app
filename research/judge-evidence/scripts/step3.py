#!/usr/bin/env python3
"""Freeze Step 3 repeated arms and baseline-spread criterion; python3 -I."""
import argparse
import hashlib
import importlib.util
import json
import shutil
import sys
from pathlib import Path
assert sys.flags.isolated
spec=importlib.util.spec_from_file_location('common3',Path(__file__).with_name('step3_common.py'))
c=importlib.util.module_from_spec(spec);spec.loader.exec_module(c)
ROOT,ART,h=c.ROOT,c.ART,c.h
read,write,sha=c.read,c.write,c.sha
SELECTED=['Qwen/Qwen3-VL-30B-A3B-Instruct','Qwen/Qwen3.6-35B-A3B-FP8']

def active_arms():
    arms=read(ART/'arms.json')
    extra=ART/'extra_arms.json'
    return arms+(read(extra) if extra.exists() else [])

def prepare_extra():
    """Cost-only second phase: no outcome-based candidate selection."""
    verify();assert not (ART/'extra_selection.json').exists()
    rule=read(ART/'extra_admission_policy.json');policy=read(ART/'policy.json')
    for arm in read(ART/'arms.json'):
        path=ROOT/'runs'/arm['name']/'calls.jsonl'
        assert path.exists() and len(path.read_text().splitlines())==len(read(path.parent/'tasks.json')),'First phase incomplete'
    state=read(ROOT/'runs/step3_budget.json')
    committed=sum(e['charged_or_reserved_usd'] for e in state['entries'].values())
    option=policy['options'][2]
    admit=committed+option['forecast_pair_usd']+rule['retry_margin_usd']<=c.CAP
    selection=dict(at=c.now(),model=option['model'],admitted=admit,committed_usd=committed,
        forecast_pair_usd=option['forecast_pair_usd'],retry_margin_usd=rule['retry_margin_usd'],cap_usd=c.CAP,
        reason='Cost-only admission after first phase; no behavior/outcome data used.')
    if admit:
        row=next(r for r in read(ART/'discovery.json')['models'] if r['model']==option['model'])
        assert row['eligible'];tasks=read(ROOT/'runs/step2_clean/tasks.json');arms=[];frozen=[]
        for repeat in [1,2]:
            name=f'step3_qwen388k_r{repeat}'
            arm=dict(name=name,arm='qwen388k',model=option['model'],max_tokens=8000,repeat=repeat,
                rates=row['rates'],discovery_folder=row['folder'],attestation=row['attestation'])
            folder=ROOT/'runs'/name;assert not folder.exists()
            for sub in ['requests','responses','signatures','errors','data']:(folder/sub).mkdir(parents=True,exist_ok=True)
            for run in c.s.PRIMARY:shutil.copyfile(ROOT/'runs/step2_clean/data'/run['file'],folder/'data'/run['file'])
            out=[]
            for task in tasks:
                t=dict(task,experiment=name);t['req_sha256']=hashlib.sha256(request_body(t,arm)).hexdigest();out.append(t)
            write(folder/'tasks.json',out);write(folder/'arm.json',arm);arms.append(arm)
            frozen += list((folder/'data').glob('*.json'))+[folder/'tasks.json',folder/'arm.json']
        write(ART/'extra_arms.json',arms);frozen += [ART/'extra_arms.json',ART/'extra_admission_policy.json']
        write(ART/'extra_manifest.json',dict(frozen={str(p.relative_to(ROOT)):sha(p) for p in frozen}))
    write(ART/'extra_selection.json',selection);print(json.dumps(selection))

def request_body(task,arm):
    # Preserve JSON serialization/order and every other field from pinned GLM.
    obj=json.loads(h.request_body(task['user'],'deep8k'))
    obj['model']=arm['model'];obj['max_tokens']=arm['max_tokens']
    return json.dumps(obj,ensure_ascii=False).encode()

def prepare():
    assert not (ART/'manifest.json').exists(),'Already frozen'
    c.s.verify()
    discovery=read(ART/'discovery.json');by_model={r['model']:r for r in discovery['models']}
    assert by_model[h.MODEL]['eligible'] and all(by_model[m]['eligible'] for m in SELECTED)
    tasks=read(ROOT/'runs/step2_clean/tasks.json')
    pinned=read(ROOT/'runs/step2_clean/reuse_for_step3.json')
    assert pinned['calls_sha256']==sha(ROOT/'runs/step2_clean/calls.jsonl')
    old=[json.loads(l) for l in (ROOT/'runs/step2_clean/calls.jsonl').read_text().splitlines()]
    def forecast(model,cap,repeats):
        rates=by_model[model]['rates']
        return repeats*sum((r['usage']['prompt_tokens']*rates['input_per_million']+
                           min(r['usage']['completion_tokens'],cap)*rates['output_per_million'])/1e6 for r in old)
    core=forecast(h.MODEL,8000,1)+forecast(h.MODEL,2000,2)+forecast(h.MODEL,4000,2)
    options=sorted([dict(model=r['model'],forecast_pair_usd=forecast(r['model'],8000,2),rates=r['rates'])
        for r in discovery['models'] if r['eligible'] and r['model']!=h.MODEL],key=lambda r:r['forecast_pair_usd'])
    assert [r['model'] for r in options[:2]]==SELECTED
    projected=core+sum(r['forecast_pair_usd'] for r in options[:2])
    all_projected=core+sum(r['forecast_pair_usd'] for r in options[:3])
    assert projected<c.CAP and all_projected>c.CAP
    policy=dict(cap_usd=c.CAP,baseline=['step2_clean','step3_glm8k_r2'],
        instruction='Run each candidate twice; pinned clean GLM once more; stop after Step 3.',
        agreement_policy_sha256=sha(ROOT/'data/step2/policy.json'),agreement_code_sha256=sha(ROOT/'scripts/agreement.py'),
        qualification='Both candidate replicates must fall inside the two baseline runs min/max for useful incident exits, control exits, control high flags and control exit-or-high, for both raw and agreement-filtered scores.',
        baseline_spread='Observed range of two runs, not a confidence interval. No pooling that hides a bad repeat.',
        recommendation='Lowest mean token cost per full replay among qualifying candidates; ties broken by pooled median latency. If none qualifies retain GLM 8000.',
        lead_policy='Report pooled latency and per-run median lead plus their observed ranges; lead is diagnostic, not an extra acceptance threshold beyond the user rule.',
        settings='Pinned baseline prompt; no retrieval; temperature 0; seed 7; nonstreaming; only model/max_tokens vary.',
        thinking='GLM and Qwen3.6 default thinking remains on; Qwen3-VL Instruct is non-reasoning. max_tokens includes reasoning and final output, not a separate reasoning-only cap.',
        forecast_method='Pinned clean prompt/completion counts at live prices; completion counts capped at each requested token budget. Cost forecast only, not a guarantee.',
        core_forecast_usd=core,options=options,selected_forecast_usd=projected,three_alternatives_forecast_usd=all_projected,
        selected_alternatives=SELECTED,signature_policy='Require model preflight and verified provider_tee availability; fetch exact-byte EIP-191 signatures for every new response. Unverified signatures block recommendation, not behavioral schema scoring.',
        reuse_policy='Pinned Step 2 baseline is behavioral evidence with sampled metadata checks, not retroactively claimed to have full preflight verification.')
    write(ART/'policy.json',policy)
    shutil.copyfile(ROOT/'README.md',ART/'step2_report.md')
    arms=[]
    definitions=[('glm8k',h.MODEL,8000,[2]),('glm2k',h.MODEL,2000,[1,2]),('glm4k',h.MODEL,4000,[1,2]),
        ('qwen_vl8k',SELECTED[0],8000,[1,2]),('qwen368k',SELECTED[1],8000,[1,2])]
    for armid,model,tokens,repeats in definitions:
        for repeat in repeats:
            name=f'step3_{armid}_r{repeat}'
            arm=dict(name=name,arm=armid,model=model,max_tokens=tokens,repeat=repeat,rates=by_model[model]['rates'],
                discovery_folder=by_model[model]['folder'],attestation=by_model[model]['attestation'])
            folder=ROOT/'runs'/name;assert not folder.exists()
            for sub in ['requests','responses','signatures','errors','data']:(folder/sub).mkdir(parents=True,exist_ok=True)
            for run in c.s.PRIMARY:shutil.copyfile(ROOT/'runs/step2_clean/data'/run['file'],folder/'data'/run['file'])
            out=[]
            for task in tasks:
                t=dict(task,experiment=name)
                t['req_sha256']=hashlib.sha256(request_body(t,arm)).hexdigest()
                if armid=='glm8k':assert t['req_sha256']==task['req_sha256']
                out.append(t)
            write(folder/'tasks.json',out);write(folder/'arm.json',arm);arms.append(arm)
    write(ART/'arms.json',arms)
    frozen=[ART/'policy.json',ART/'models.json',ART/'discovery.json',ART/'arms.json',ROOT/'data/step2/policy.json',ROOT/'scripts/agreement.py',
        ROOT/'runs/step2_clean/calls.jsonl',ROOT/'runs/step2_clean/tasks.json',ROOT/'runs/step2_clean/reuse_for_step3.json']
    for arm in arms:frozen += list((ROOT/'runs'/arm['name']/'data').glob('*.json'))+[ROOT/'runs'/arm['name']/'tasks.json',ROOT/'runs'/arm['name']/'arm.json']
    write(ART/'manifest.json',dict(protected=c.s.old.protected(),frozen={str(p.relative_to(ROOT)):sha(p) for p in frozen},
        prepared_utc=c.now(),new_runs=len(arms),new_calls=len(arms)*len(tasks),pinned_baseline_calls=len(tasks)))
    verify()
    print(json.dumps(dict(new_runs=len(arms),new_calls=len(arms)*len(tasks),forecast_usd=projected,
        forecast_all_three_usd=all_projected,cap_usd=c.CAP)))

def verify():
    m=read(ART/'manifest.json')
    assert m['protected']==c.s.old.protected(),'Protected original changed'
    for p,digest in m['frozen'].items():assert sha(ROOT/p)==digest,p
    extra=ART/'extra_manifest.json'
    if extra.exists():
        for p,digest in read(extra)['frozen'].items():assert sha(ROOT/p)==digest,p
    for arm in active_arms():
        for task in read(ROOT/'runs'/arm['name']/'tasks.json'):
            assert hashlib.sha256(request_body(task,arm)).hexdigest()==task['req_sha256']
    print('PASS: Step 3 arms, baseline, agreement policy, original harness and cited cache are frozen.')

if __name__=='__main__':
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('command',choices=['prepare','verify','extra'])
    {'prepare':prepare,'verify':verify,'extra':prepare_extra}[ap.parse_args().command]()
