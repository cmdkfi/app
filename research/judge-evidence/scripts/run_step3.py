#!/usr/bin/env python3
"""Repeated Step 3 J2 inference, bounded retries and one $6 ledger. python3 -I.

Separate per-replicate caches, unchanged frozen request bytes. Only absent
transport responses may retry; schema failures and token-limit outputs never do.
All responses get provider_tee exact-byte/EIP-191 checks against preflight.
"""
import argparse
import concurrent.futures
import hashlib
import importlib.util
import json
import sys
import threading
import time
import urllib.parse
from collections import defaultdict,deque
from pathlib import Path
assert sys.flags.isolated
def module(name,path):
    spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
s=module('step3',Path(__file__).with_name('step3.py'));c=s.c
d=module('discovery3',Path(__file__).with_name('discover_step3.py'))
TIMEOUT=1200
MAX_TRIES=3
FATAL_HTTP={401,402,403}

def retryable(rc,status):
    return status not in FATAL_HTTP and (status in (None,0,408,429) or status>=500 or (status==200 and rc!=0))

def main(parallel):
    s.verify();http=c.HTTP();budget=c.Budget()
    arms={a['name']:a for a in s.active_arms()};groups=defaultdict(list)
    for name,arm in arms.items():
        folder=c.ROOT/'runs'/name;cp=folder/'calls.jsonl'
        cache={json.loads(l)['req_sha256'] for l in cp.read_text().splitlines()} if cp.exists() else set()
        for t in c.read(folder/'tasks.json'):
            if t['req_sha256'] not in cache:groups[(t['run'],name)].append(t)
    todo=[]
    for rank in range(max(map(len,groups.values()),default=0)):
        for key in sorted(groups):
            if rank<len(groups[key]):todo.append((groups[key][rank],1))
    queue=deque(todo);ep=c.ROOT/'runs/step3_execution.json'
    execution=c.read(ep) if ep.exists() else dict(sessions=[]);session_num=len(execution['sessions'])+1
    session=dict(started_utc=c.now(),parallel=parallel,scheduled=len(todo),session=session_num,
        transport='curl HTTP/2, credential via stdin',max_tries=MAX_TRIES,timeout_s=TIMEOUT,
        authorization='User authorized repeated Step 3; whole-step inference cap $6')
    execution['sessions'].append(session);c.write(ep,execution)
    locks={n:threading.Lock() for n in arms};start=time.monotonic();received=errors=0;terminal=[];exhausted=False
    stop_dispatch=threading.Event()
    print(json.dumps(dict(scheduled=len(todo),parallel=parallel,cap_usd=c.CAP)),flush=True)
    def work(t,n,key,body):
        arm=arms[t['experiment']];folder=c.ROOT/'runs'/arm['name'];model=arm['model']
        bp=folder/'requests'/(t['req_sha256']+'.json');bp.write_bytes(body);begin=time.monotonic()
        try:
            r=http.request('POST',c.h.BASE+'/chat/completions',bp,timeout=TIMEOUT)
            if r['rc'] or r['status']!=200:
                if r['status'] in FATAL_HTTP:stop_dispatch.set()
                # Never persist a credential even if an upstream error echoes it.
                secret=(Path.home()/'.config/prime/nearai_key').read_bytes().strip()
                if secret not in r['raw']:
                    (folder/'errors'/(t['req_sha256']+f'.s{session_num}.try{n}.body')).write_bytes(r['raw'])
                raise Transport(r['rc'],r['status'])
            response=json.loads(r['raw']);budget.finish(key,response['usage'])
            (folder/'responses'/(t['req_sha256']+'.json')).write_bytes(r['raw'])
            choice=response['choices'][0];msg=choice['message'];content=msg.get('content') or ''
            obj,issues=c.strict(content,t['oid_map'])
            params=urllib.parse.urlencode(dict(model=model,signing_algo='ecdsa'))
            checks=[]
            for sig_try in range(3):
                sr=http.request('GET',c.h.BASE+'/signature/'+response['id']+'?'+params,timeout=30)
                (folder/'signatures'/(t['req_sha256']+f'.{sig_try+1}.json')).write_bytes(sr['raw'])
                try:sig=json.loads(sr['raw'])
                except ValueError:sig={}
                check=d.signature_check(sig,body,r['raw'],model,arm['attestation'])
                checks.append(dict(check,http_status=sr['status']))
                if check['passed']:break
                if sig_try<2:time.sleep(2)
            rec=dict(req_sha256=t['req_sha256'],resp_sha256=hashlib.sha256(r['raw']).hexdigest(),
                ok=True,model=model,max_tokens=arm['max_tokens'],repeat=arm['repeat'],mode='step3',
                tag=f'{t["run"]}|J2|{t["t"]}',experiment=t['experiment'],session=session_num,transport_try=n,
                budget_key=key,id=response.get('id'),latency_s=round(r['latency_s'],3),usage=response['usage'],
                finish_reason=choice.get('finish_reason'),content=content,parsed=obj,valid=not issues,schema_errors=issues,
                reasoning_chars=len(msg.get('reasoning_content') or msg.get('reasoning') or ''),
                sig=checks[-1],signature_checks=checks,response_storage='exact provider bytes')
            with locks[t['experiment']]:
                with (folder/'calls.jsonl').open('a') as f:f.write(json.dumps(rec,ensure_ascii=False)+'\n')
            return True,rec
        except Exception as e:
            budget.unknown(key)
            err=dict(error_type=type(e).__name__,experiment=t['experiment'],req_sha256=t['req_sha256'],
                session=session_num,transport_try=n,budget_key=key,elapsed_s=time.monotonic()-begin,
                retry=n<MAX_TRIES and isinstance(e,Transport) and retryable(e.rc,e.status),recorded_utc=c.now())
            if isinstance(e,Transport):err.update(curl_exit=e.rc,http_status=e.status)
            c.write(folder/'errors'/(t['req_sha256']+f'.s{session_num}.try{n}.json'),err)
            return False,err
    futures={}
    with concurrent.futures.ThreadPoolExecutor(max_workers=parallel) as pool:
        while queue or futures:
            if stop_dispatch.is_set() and not futures:break
            while queue and len(futures)<parallel and not stop_dispatch.is_set():
                t,n=queue[0];arm=arms[t['experiment']];body=s.request_body(t,arm)
                assert hashlib.sha256(body).hexdigest()==t['req_sha256']
                key=t['experiment']+'/'+t['req_sha256']+f'/session{session_num}/try{n}'
                try:budget.reserve(key,body,arm['rates'])
                except RuntimeError as e:
                    if 'Spend cap' not in str(e):raise
                    if not futures:exhausted=True
                    break
                queue.popleft();futures[pool.submit(work,t,n,key,body)]=(t,n)
            if exhausted:break
            done,_=concurrent.futures.wait(futures,timeout=20,return_when=concurrent.futures.FIRST_COMPLETED)
            for future in done:
                t,n=futures.pop(future);ok,r=future.result()
                if ok:received+=1
                else:
                    errors+=1
                    if r['retry'] and not stop_dispatch.is_set():queue.append((t,n+1))
                    else:terminal.append(dict(experiment=t['experiment'],req_sha256=t['req_sha256']))
                print(json.dumps(dict(received=received,remaining=len(queue)+len(futures),errors=errors,
                    experiment=t['experiment'],window=t['run'],ok=ok,latency_s=r.get('latency_s',r.get('elapsed_s')))),flush=True)
            with budget.lock:
                entries=list(budget.state['entries'].values())
                state=dict(received=received,remaining=len(queue)+len(futures),in_flight=len(futures),errors=errors,
                    terminal_failures=len(terminal),elapsed_s=time.monotonic()-start,updated_utc=c.now(),
                    returned_token_cost_usd=sum(e['charged_or_reserved_usd'] for e in entries if e['status']=='completed'),
                    charged_plus_reserved_usd=sum(e['charged_or_reserved_usd'] for e in entries))
            c.write(c.ROOT/'runs/step3_progress.json',state)
    session.update(finished_utc=c.now(),elapsed_s=time.monotonic()-start,returned_this_session=received,
        request_errors=errors,terminal_failures=terminal,budget_exhausted=exhausted,
        fatal_http_stop=stop_dispatch.is_set(),unsubmitted=len(queue),stopped=bool(terminal or exhausted or stop_dispatch.is_set()))
    c.write(ep,execution);print(json.dumps(session),flush=True)
    if terminal or exhausted or stop_dispatch.is_set():raise SystemExit('Incomplete: inspect recorded failures and budget; never exceed cap.')

class Transport(Exception):
    def __init__(self,rc,status):self.rc,self.status=rc,status

if __name__=='__main__':
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--parallel',type=int,default=48)
    main(ap.parse_args().parallel)
