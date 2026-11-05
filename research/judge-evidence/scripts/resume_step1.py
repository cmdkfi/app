#!/usr/bin/env python3
"""User-authorized Step 1 resumption; frozen inference bodies, HTTP/2 transport.

python3 -I 'research_notes/Judge evidence/scripts/resume_step1.py' --parallel 24
Only missing responses are retried, at most three attempts per task this session.
Invalid/truncated model answers remain scored and are never retried.
All attempts reserve cost; unknown charges from earlier sessions remain reserved.
"""
import argparse
import concurrent.futures
import hashlib
import importlib.util
import json
import re
import subprocess
import sys
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path

assert sys.flags.isolated
spec=importlib.util.spec_from_file_location('step1',Path(__file__).with_name('step1.py'))
s=importlib.util.module_from_spec(spec)
spec.loader.exec_module(s)
h=s.h
TIMEOUT_S=1200
MAX_TRIES=3


def now():
    return datetime.now(timezone.utc).isoformat()


def main(parallel):
    s.verify()
    key=(Path.home()/'.config/prime/nearai_key').read_text().strip()
    assert key and '\n' not in key and '\r' not in key
    # Secret enters curl through stdin only, never argv, a file, stdout or logs.
    auth_config=('header = '+json.dumps('Authorization: Bearer '+key)+'\n').encode()
    budget=s.Budget()
    cached={}
    groups=defaultdict(list)
    for name in s.NAMES:
        path=s.ROOT/'runs'/name/'calls.jsonl'
        cached[name]={r['req_sha256']:r for r in
            [json.loads(line) for line in path.read_text().splitlines()]} if path.exists() else {}
        for t in s.read(s.ROOT/'runs'/name/'tasks.json'):
            if t['req_sha256'] not in cached[name]:
                groups[(name,t['run'])].append(t)
    todo=[]
    for rank in range(max(map(len,groups.values()),default=0)):
        for group in sorted(groups):
            if rank<len(groups[group]): todo.append((groups[group][rank],1))
    queue=deque(todo)
    ep=s.ROOT/'runs/execution.json'
    execution=s.read(ep)
    session_num=len(execution['sessions'])+1
    assert session_num>=3
    session=dict(started_utc=now(),parallel=parallel,scheduled=len(todo),attempt=session_num,
        transport='curl HTTP/2, secret via stdin config',timeout_s=TIMEOUT_S,max_tries=MAX_TRIES,
        authorization='User: retry step 1 to make it complete',
        resumed_from_returned_calls=sum(len(c) for c in cached.values()))
    execution['sessions'].append(session)
    s.write(ep,execution)
    # Retain prior report as an explicitly dated checkpoint, then mark current work.
    s.write(s.ROOT/'runs/retry_authorization.json',dict(at=now(),instruction=session['authorization'],
        cap_usd=s.CAP,prompts_unchanged=True,earlier_unknown_charges_retained=True))
    locks={n:threading.Lock() for n in s.NAMES}
    started=time.monotonic()
    responses=errors=0
    exhausted=False
    terminal=[]
    progress=s.ROOT/'runs/progress.json'
    print(json.dumps(dict(session=session_num,missing=len(todo),parallel=parallel,cap_usd=s.CAP)),flush=True)

    def http(method,url,body_path=None,timeout=TIMEOUT_S):
        args=['curl','--silent','--show-error','--http2','--connect-timeout','30',
            '--max-time',str(timeout),'--fail-with-body','--config','-',
            '--header','Accept-Encoding: identity','--write-out','%{stderr}\nHTTP_STATUS:%{http_code}',
            '--request',method,url]
        if body_path:
            args += ['--header','Content-Type: application/json','--header','x-no-aliasing: true',
                     '--data-binary','@'+str(body_path)]
        p=subprocess.run(args,input=auth_config,capture_output=True,timeout=timeout+30)
        match=re.search(rb'HTTP_STATUS:(\d+)',p.stderr)
        status=int(match.group(1)) if match else None
        return p.returncode,status,p.stdout

    def work(t,n,bkey,body):
        folder=s.ROOT/'runs'/t['experiment']
        body_path=folder/'requests'/(t['req_sha256']+'.json')
        body_path.parent.mkdir(exist_ok=True)
        body_path.write_bytes(body)
        begin=time.monotonic()
        error=None
        try:
            rc,status,raw=http('POST',h.BASE+'/chat/completions',body_path)
            dt=time.monotonic()-begin
            if rc or status!=200:
                error=dict(error_type='CurlTransportError',curl_exit=rc,http_status=status)
            else:
                response=json.loads(raw)
                usage=response['usage']
                assert all(isinstance(usage[k],int) and usage[k]>=0 for k in ('prompt_tokens','completion_tokens'))
                budget.finish(bkey,usage)
                choice=response['choices'][0]
                msg=choice['message']
                content=msg.get('content') or ''
                obj,issues=s.strict(content,t['oid_map'])
                legacy,_=h.extract_json(content)
                rec=dict(req_sha256=t['req_sha256'],resp_sha256=hashlib.sha256(raw).hexdigest(),
                    ok=True,mode='deep8k',tag=f'{t["run"]}|J2|{t["t"]}',experiment=t['experiment'],
                    attempt=session_num,transport_try=n,budget_key=bkey,id=response.get('id'),
                    latency_s=round(dt,3),usage=usage,finish_reason=choice.get('finish_reason'),
                    content=content,parsed=obj,valid=not issues,schema_errors=issues,
                    legacy_valid=h.validate(legacy),legacy_parsed=legacy,
                    reasoning_chars=len(msg.get('reasoning_content') or msg.get('reasoning') or ''),
                    production_schema_valid=not issues and obj['action'] in ('hold','reduce_light','reduce_to_target','leave_market'),
                    response_storage='exact provider bytes')
                rp=folder/'responses'/(t['req_sha256']+'.json')
                rp.parent.mkdir(exist_ok=True)
                rp.write_bytes(raw)
                if int(t['req_sha256'][:8],16)%10==0 and response.get('id'):
                    expect=f'{h.MODEL}:{t["req_sha256"]}:{rec["resp_sha256"]}'
                    try:
                        sr,ss,sraw=http('GET',f'{h.BASE}/signature/{response["id"]}?model={h.MODEL}&signing_algo=ecdsa',timeout=30)
                        sig=json.loads(sraw) if sr==0 and ss==200 else {}
                        rec['sig']=dict(kind=sig.get('signature_kind'),text_match=sig.get('text')==expect,
                            passed=sig.get('signature_kind')=='provider_tee' and sig.get('text')==expect,
                            http_status=ss)
                    except Exception as e:
                        rec['sig']=dict(passed=False,error_type=type(e).__name__)
                with locks[t['experiment']]:
                    with (folder/'calls.jsonl').open('a') as f:
                        f.write(json.dumps(rec,ensure_ascii=False)+'\n')
                return True,rec
        except Exception as e:
            error=dict(error_type=type(e).__name__)
        error.update(experiment=t['experiment'],req_sha256=t['req_sha256'],attempt=session_num,
            transport_try=n,budget_key=bkey,elapsed_s=round(time.monotonic()-begin,3),
            retry=n<MAX_TRIES,recorded_utc=now())
        with budget.lock:
            entry=budget.state['entries'][bkey]
            if entry['status']!='completed': entry['status']='unknown_after_transport_error'
            s.write(budget.path,budget.state)
        s.write(folder/'errors'/(t['req_sha256']+f'.session{session_num}.try{n}.json'),error)
        return False,error

    futures={}
    with concurrent.futures.ThreadPoolExecutor(max_workers=parallel) as pool:
        while queue or futures:
            while queue and len(futures)<parallel:
                t,n=queue[0]
                body=h.request_body(t['user'],'deep8k')
                assert hashlib.sha256(body).hexdigest()==t['req_sha256']
                try:
                    bkey=budget.reserve(t,body,session_num*100+n)
                except RuntimeError as e:
                    if 'Spend cap' not in str(e): raise
                    if not futures: exhausted=True
                    break
                queue.popleft()
                futures[pool.submit(work,t,n,bkey,body)]=(t,n)
            if exhausted: break
            done,_=concurrent.futures.wait(futures,timeout=20,return_when=concurrent.futures.FIRST_COMPLETED)
            for fut in done:
                t,n=futures.pop(fut)
                ok,rec=fut.result()
                if ok:
                    responses+=1
                else:
                    errors+=1
                    if n<MAX_TRIES:
                        # Retry later after other queued prompts, not in a tight loop.
                        queue.append((t,n+1))
                    else: terminal.append(dict(experiment=t['experiment'],req_sha256=t['req_sha256']))
                print(json.dumps(dict(received=responses,remaining=len(queue)+len(futures),errors=errors,
                    experiment=t['experiment'],window=t['run'],ok=ok,latency_s=rec.get('latency_s',rec.get('elapsed_s')))),flush=True)
            with budget.lock:
                entries=list(budget.state['entries'].values())
                state=dict(session=session_num,received=responses,remaining=len(queue)+len(futures),
                    in_flight=len(futures),errors=errors,terminal_failures=len(terminal),
                    elapsed_s=round(time.monotonic()-started,3),updated_utc=now(),
                    returned_token_cost_usd=sum(e['charged_or_reserved_usd'] for e in entries if e['status']=='completed'),
                    charged_plus_reserved_usd=sum(e['charged_or_reserved_usd'] for e in entries))
            s.write(progress,state)
    session.update(finished_utc=now(),elapsed_s=time.monotonic()-started,stopped=bool(terminal or exhausted),
        returned_this_session=responses,request_errors=errors,terminal_failures=terminal,budget_exhausted=exhausted)
    s.write(ep,execution)
    print(json.dumps(session),flush=True)
    if terminal or exhausted: raise SystemExit('Missing responses remain; inspect the failure and budget records.')


if __name__=='__main__':
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--parallel',type=int,default=24)
    args=ap.parse_args()
    main(args.parallel)
