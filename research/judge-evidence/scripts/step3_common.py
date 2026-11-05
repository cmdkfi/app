#!/usr/bin/env python3
"""Step 3 shared HTTP, token accounting and frozen request handling. python3 -I.

Credentials are read only here and enter curl via stdin, never argv/logs/files.
"""
import hashlib
import importlib.util
import json
import re
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
assert sys.flags.isolated
def module(name,path):
    spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
s=module('step2',Path(__file__).with_name('step2.py'))
ROOT,REPO,h=s.ROOT,s.REPO,s.h
ART=ROOT/'data/step3'
CAP=6.0
read,write,sha,strict=s.read,s.write,s.sha,s.strict
def now():return datetime.now(timezone.utc).isoformat()

class HTTP:
    def __init__(self):
        key=(Path.home()/'.config/prime/nearai_key').read_text().strip()
        assert key and '\n' not in key and '\r' not in key
        self.auth=('header = '+json.dumps('Authorization: Bearer '+key)+'\n').encode()
    def request(self,method,url,body_path=None,timeout=120):
        args=['curl','--silent','--show-error','--http2','--connect-timeout','30',
            '--max-time',str(timeout),'--fail-with-body','--config','-',
            '--header','Accept-Encoding: identity','--write-out','%{stderr}\nHTTP_STATUS:%{http_code}',
            '--request',method,url]
        if body_path:args+=['--header','Content-Type: application/json','--header','x-no-aliasing: true','--data-binary','@'+str(body_path)]
        start=time.monotonic()
        try:
            p=subprocess.run(args,input=self.auth,capture_output=True,timeout=timeout+30)
            match=re.search(rb'HTTP_STATUS:(\d+)',p.stderr)
            return dict(rc=p.returncode,status=int(match.group(1)) if match else None,
                raw=p.stdout,latency_s=time.monotonic()-start)
        except subprocess.TimeoutExpired:
            return dict(rc=-1,status=None,raw=b'',latency_s=time.monotonic()-start)

class Budget:
    def __init__(self):
        self.path=ROOT/'runs/step3_budget.json';self.lock=threading.Lock()
        self.state=read(self.path) if self.path.exists() else dict(cap_usd=CAP,entries={})
    def reserve(self,key,body,rates):
        d=json.loads(body);bound=(len(body)+8192)*rates['input_per_million']/1e6+d['max_tokens']*rates['output_per_million']/1e6
        with self.lock:
            assert key not in self.state['entries'],key
            if sum(e['charged_or_reserved_usd'] for e in self.state['entries'].values())+bound>CAP:
                raise RuntimeError('Spend cap: cannot reserve next request')
            self.state['entries'][key]=dict(status='reserved',model=d['model'],rates=rates,
                charged_or_reserved_usd=bound,reserved_usd=bound,started_utc=now())
            write(self.path,self.state)
        return key
    def finish(self,key,usage):
        assert all(isinstance(usage.get(k),int) and usage[k]>=0 for k in ['prompt_tokens','completion_tokens'])
        with self.lock:
            e=self.state['entries'][key];r=e['rates']
            cost=(usage['prompt_tokens']*r['input_per_million']+usage['completion_tokens']*r['output_per_million'])/1e6
            assert cost<=e['reserved_usd'],'Conservative token bound exceeded'
            e.update(status='completed',usage=usage,charged_or_reserved_usd=cost,finished_utc=now())
            write(self.path,self.state)
    def unknown(self,key):
        with self.lock:
            if self.state['entries'][key]['status']!='completed':self.state['entries'][key]['status']='unknown_after_error'
            write(self.path,self.state)

if __name__=='__main__':
    ART.mkdir(exist_ok=True)
    p=ART/'models.json'
    assert not p.exists(),'Use saved catalog; do not overwrite discovery evidence'
    r=HTTP().request('GET',h.BASE+'/models')
    assert r['status']==200 and r['rc']==0,f"Model catalog failed: HTTP {r['status']}, curl {r['rc']}"
    p.write_bytes(r['raw']);write(ART/'catalog_fetch.json',dict(at=now(),url=h.BASE+'/models',latency_s=r['latency_s'],sha256=sha(p)))
    d=json.loads(r['raw']);print(json.dumps(dict(top_keys=list(d),count=len(d.get('data',[])),sample=d.get('data',[])[:1]),indent=2))
