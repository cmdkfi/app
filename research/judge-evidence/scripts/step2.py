#!/usr/bin/env python3
"""Frozen Step 2 setup and budget. Use python3 -I; no inference in prepare/verify.

The copied baseline prompt is unchanged. Evaluate all primary J2 timeline steps
to allow matched labels and uncensored offline agreement evaluation.
"""
import argparse
import hashlib
import importlib.util
import math
import shutil
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

assert sys.flags.isolated
def module(name, path):
    spec=importlib.util.spec_from_file_location(name,path)
    mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod);return mod

old=module('step1',Path(__file__).with_name('step1.py'))
ROOT,REPO,DATA,BASELINE,h=old.ROOT,old.REPO,old.DATA,old.BASELINE,old.h
PRIMARY=old.PRIMARY
read,write,sha,strict=old.read,old.write,old.sha,old.strict
NAMES=['step2_clean','step2_retrieval']
CAP=1.50
ART=ROOT/'data/step2'
LIB=REPO/'research_notes/Liquidation dataset'
SHARED=['utilization','borrow_rate','oracle_vs_binance_pct','price_change_1h',
        'price_change_6h','price_change_24h','realized_volatility_24h']
HEADING='Similar past cases (Morpho borrowers)'

# Explicit position relevance. Borrowers query debt-market utilization; the
# legacy J3 collateral-market rule is retained separately for comparability.
UTIL={r['run']:h.J3_MAP.get(r['run'],{}).get('util',[]) for r in PRIMARY}
UTIL.update({'INC-04/cbeth-borrower':['usdc_util_pct'],
             'CTL-03/cbeth-borrower':['usdc_util_pct'],
             'INC-05/wsteth-looper':['weth_util_pct']})
RATE={'INC-01/weth':['weth_var_borrow_apr_pct'],
      'CTL-06/weth':['weth_var_borrow_apr_pct'],
      'CTL-01/usdc':['usdc_var_borrow_apr_pct'],
      'INC-08/usdc':['usdc_borrow_apr_pct']}

def query_for(run, task, items):
    """Latest visible chain observation; omit absent fields, never forward-fill them.

    Its age is recorded; retaining a visible observation does not claim a new
    reading. Within timestamped series use only the latest sample <= replay t.
    Across contemporaneous vault markets take max utilization (stress proxy).
    """
    now=h.P(task['t']); byid={i['id']:i for i in items}
    chain=[byid[i] for i in task['shown'] if byid[i]['src']=='onchain']
    q=dict(run=run['run'],t=task['t'],as_of=int(now.timestamp()),k=3,pool='train',
           features={},provenance={},missing_features=[],onchain_item=None)
    if not chain:
        q.update(status='no_visible_onchain',missing_features=SHARED[:]);return q
    it=chain[-1];q.update(onchain_item=it['id'],onchain_effective_time=h.F(it['t']),
                         observation_age_s=(now-it['t']).total_seconds())
    def add(feature,keys,factor):
        vals=[]
        for key in keys:
            samples=[(st,v) for st,v in h.metric_samples(it,key) if st<=now]
            if not samples:continue
            st,v=max(samples,key=lambda x:x[0])
            if math.isfinite(v):vals.append((v*factor,st,key))
        if vals:
            v,st,key=max(vals,key=lambda x:x[0])
            q['features'][feature]=v
            q['provenance'][feature]=dict(item=it['id'],metric=key,sample_time=h.F(st),
                effective_time=h.F(it['t']),conversion_factor=factor,
                candidate_metrics=[x[2] for x in vals])
    util=UTIL[run['run']]
    if run['run']=='INC-02/relend' and it['id'] not in ['INC02-008','INC02-011','INC02-013']:
        util=[]  # xUSD Arbitrum readings are not the Relend sdeUSD market.
    add('utilization',util,.01)
    add('borrow_rate',RATE.get(run['run'],[]),.01)
    add('oracle_vs_binance_pct',h.J3_MAP.get(run['run'],{}).get('dev',[]),1.)
    # Exact horizons only: neither six-minute jumps nor vault share/TVL changes
    # substitute for collateral price returns; no sparse-price interpolation.
    for feature in SHARED[3:]:
        add(feature,[feature,feature+'_pct'],1. if feature in it['om'] else .01)
    q['missing_features']=[f for f in SHARED if f not in q['features']]
    q['status']='query' if q['features'] else 'no_shared_features'
    return q

def clean_tasks():
    tasks=[];queries=[]
    for run in PRIMARY:
        d,items=h.load_window(str(DATA),run['file'])
        for t,shown,total in h.build_steps(d,items,'J2'):
            task=dict(run=run['run'],judge='J2',t=h.F(t),
                user=h.render(run,'J2',t,shown,total),shown=[x['id'] for x in shown],
                oid_map={x['oid']['J2']:x['id'] for x in shown})
            tasks.append(task);queries.append(query_for(run,task,items))
    return tasks,queries

def prepare_queries():
    old.verify()
    assert not ART.exists(),'Step 2 inputs already exist'
    ART.mkdir()
    # Preserve the completed Step 1 report; subsequent README holds both stages.
    shutil.copyfile(ROOT/'README.md',ART/'step1_report.md')
    tasks,queries=clean_tasks()
    write(ART/'clean_tasks.json',tasks);write(ART/'queries.json',queries)
    write(ART/'policy.json',dict(screen_freshness_seconds=1800,
        corroboration='two supporting visible cited items with different source fields OR one fresh fired screen',
        retraction='same-source retraction cancels that source text support, never a screen',
        training_pool='scaler_train_row and outcome_matures_ts <= replay timestamp',
        scaling='original frozen scaler for 2026; earlier mature-only scaler for 2025',
        missing='omit absent features; no imputation or filling from older chain items',
        prompt='unchanged baseline prompt, deep8k request body, thinking every step',
        budget_usd=CAP,baseline_reusable_for_step3=True,
        decision_rule='keep only if lead improves or control false alarms fall without worsening the other; useful exits must not fall',
        lead_rule='paired per-incident first-exit leads: no later or lost exit, at least one earlier (or fewer control alarms with nonworse leads)',
        control_rule='both exit and exit-or-high counts must not worsen',
        scoring='raw primary per brief; agreement and historical comparisons reported separately'))
    print(dict(tasks=len(tasks),queries=sum(q['status']=='query' for q in queries),cap=CAP))

def freeze():
    assert not (ART/'manifest.json').exists()
    tasks=read(ART/'clean_tasks.json');queries=read(ART/'queries.json')
    retrieval=read(ART/'retrieval.json')['results'];assert len(retrieval)==len(tasks)
    for name in NAMES:
        folder=ROOT/'runs'/name;assert not folder.exists()
        for sub in ['data','responses','requests','errors']: (folder/sub).mkdir(parents=True,exist_ok=True)
        for run in PRIMARY:shutil.copyfile(DATA/run['file'],folder/'data'/run['file'])
        out=[]
        for t,q,result in zip(tasks,queries,retrieval):
            x=dict(t,experiment=name)
            if name=='step2_retrieval' and result['neighbors']:
                context='\n\n'+HEADING+'\n'+'\n'.join('- '+n['summary'] for n in result['neighbors'])
                marker='\n\nAssess the risk to this position now'
                assert marker in x['user']
                x['user']=x['user'].replace(marker,context+marker)
            x['req_sha256']=hashlib.sha256(h.request_body(x['user'],'deep8k')).hexdigest()
            x['retrieval_neighbors']=len(result['neighbors']) if name=='step2_retrieval' else 0
            out.append(x)
        write(folder/'tasks.json',out)
    inputs=list(ART.glob('*.json'))+[ROOT/'runs'/n/'tasks.json' for n in NAMES]
    inputs += [p for n in NAMES for p in (ROOT/'runs'/n/'data').glob('*.json')]
    library=[LIB/'data/retrieval.parquet',LIB/'data/retrieval_scaler.json',LIB/'scripts/retrieve.py']
    write(ART/'manifest.json',dict(protected=old.protected(),
        inputs={str(p.relative_to(ROOT)):sha(p) for p in inputs},
        library={str(p.relative_to(REPO)):sha(p) for p in library},
        script_hashes={p.name:sha(p) for p in [Path(__file__),Path(__file__).with_name('retrieve.py')]},
        model=h.MODEL,mode='deep8k',cap_usd=CAP))
    verify()

def verify():
    m=read(ART/'manifest.json');assert old.protected()==m['protected']
    for p,digest in m['inputs'].items():assert sha(ROOT/p)==digest,p
    for p,digest in m['library'].items():assert sha(REPO/p)==digest,p
    for name in NAMES:
        for t in read(ROOT/'runs'/name/'tasks.json'):
            assert hashlib.sha256(h.request_body(t['user'],'deep8k')).hexdigest()==t['req_sha256']
    print('PASS: Step 2 frozen prompts, retrieval inputs, original harness and cited baseline unchanged.')

class Budget(old.Budget):
    def __init__(self):
        self.path=ROOT/'runs/step2_budget.json';self.lock=threading.Lock()
        self.state=read(self.path) if self.path.exists() else dict(cap_usd=CAP,entries={})
    def reserve(self,task,body,attempt):
        bound=(len(body)+8192)*h.PRICE_IN+8000*h.PRICE_OUT
        key=task['experiment']+'/'+task['req_sha256']+'/attempt'+str(attempt)
        with self.lock:
            assert key not in self.state['entries']
            total=sum(e['charged_or_reserved_usd'] for e in self.state['entries'].values())
            if total+bound>CAP:raise RuntimeError('Spend cap: cannot reserve next request')
            self.state['entries'][key]=dict(status='reserved',charged_or_reserved_usd=bound,
                reserved_usd=bound,started_utc=datetime.now(timezone.utc).isoformat())
            write(self.path,self.state)
        return key

if __name__=='__main__':
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('command',choices=['queries','freeze','verify'])
    {'queries':prepare_queries,'freeze':freeze,'verify':verify}[ap.parse_args().command]()
