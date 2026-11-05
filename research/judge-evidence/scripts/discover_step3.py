#!/usr/bin/env python3
"""Read live attestation availability and probe provider_tee signatures; python3 -I.

One tiny completion per suitable model with a nonempty bound model report.
All inference, including probes, shares the Step 3 spend ledger.
No Intel DCAP/NVIDIA trust-chain verification is claimed by this availability test.
"""
import concurrent.futures
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import time
import urllib.parse
from pathlib import Path
assert sys.flags.isolated
spec=importlib.util.spec_from_file_location('c',Path(__file__).with_name('step3_common.py'))
c=importlib.util.module_from_spec(spec);spec.loader.exec_module(c)

def report_check(rep,nonce):
    checks=[]
    for m in rep.get('model_attestations') or []:
        raw=m.get('intel_quote','').removeprefix('0x')
        try:q=bytes.fromhex(raw)
        except ValueError:q=b''
        sa=(m.get('signing_address') or '').lower()
        version=int.from_bytes(q[:2],'little')
        check=dict(signing_address=sa,signing_algo=m.get('signing_algo'),quote_version=version,
            quote_bytes=len(q),nonce_echo=m.get('request_nonce')==nonce,
            quote_nonce=len(q)>=632 and version==4 and q[600:632].hex()==nonce,
            address_bound=bool(sa) and len(q)>=632 and sa.removeprefix('0x') in q[568:600].hex(),
            debug_bit=(q[168]&1) if len(q)>168 else None)
        check['passes']=check['nonce_echo'] and check['quote_nonce'] and check['address_bound'] and check['debug_bit']==0
        checks.append(check)
    return dict(candidates=checks,passes=bool(checks) and all(x['passes'] for x in checks))

def signature_check(sig,body,raw,model,attestation):
    expected=f'{model}:{hashlib.sha256(body).hexdigest()}:{hashlib.sha256(raw).hexdigest()}'
    address=(sig.get('signing_address') or '').lower()
    matches=[r for r in attestation['candidates'] if r['signing_address']==address and r['signing_algo']==sig.get('signing_algo')]
    verified=False
    if sig.get('signature') and sig.get('text'):
        try:
            p=subprocess.run([str(Path.home()/'.foundry/bin/cast'),'wallet','verify','--address',address,
                sig['text'],sig['signature']],capture_output=True,timeout=20)
            verified=p.returncode==0 and b'validation succeeded' in p.stdout.lower()
        except (OSError,subprocess.TimeoutExpired):pass
    return dict(kind=sig.get('signature_kind'),text_match=sig.get('text')==expected,
        matching_preflight_reports=len(matches),eip191_verified=verified,signing_address=address,
        passed=sig.get('signature_kind')=='provider_tee' and sig.get('text')==expected and len(matches)==1 and verified)

def main():
    result_path=c.ART/'discovery.json';assert not result_path.exists()
    catalog=c.read(c.ART/'models.json')['data'];http=c.HTTP();budget=c.Budget()
    def one(m):
        slug=hashlib.sha256(m['id'].encode()).hexdigest()[:16]
        folder=c.ROOT/'runs/step3_discovery'/slug;folder.mkdir(parents=True,exist_ok=True)
        row=dict(model=m['id'],owned_by=m.get('owned_by'),rates=dict(input_per_million=m['pricing']['input'],output_per_million=m['pricing']['output']),
            folder=str(folder.relative_to(c.ROOT)),features=m.get('supported_features',[]),checked_utc=c.now())
        suitable='text' in m.get('input_modalities',[]) and 'text' in m.get('output_modalities',[]) and (m.get('max_output_length') is None or m['max_output_length']>=8000)
        row['suitable_text_replay']=suitable
        if not suitable:
            row.update(eligible=False,reason='not a text chat model with 8000-token output capacity');return row
        nonce=os.urandom(32).hex()
        params=urllib.parse.urlencode(dict(model=m['id'],provider='near',signing_algo='ecdsa',nonce=nonce))
        r=http.request('GET',c.h.BASE+'/attestation/report?'+params,timeout=90)
        (folder/'attestation.raw.json').write_bytes(r['raw'])
        c.write(folder/'attestation_request.json',dict(nonce=nonce,model=m['id'],provider='near',signing_algo='ecdsa',at=c.now()))
        row['attestation_http']=r['status']
        try:rep=json.loads(r['raw'])
        except ValueError:rep={}
        row['attestation']=report_check(rep,nonce)
        if r['rc'] or r['status']!=200 or not row['attestation']['passes']:
            row.update(eligible=False,reason='no usable nonce/address-bound NEAR model attestation');return row
        body=json.dumps(dict(model=m['id'],messages=[dict(role='user',content='Reply with exactly: OK')],stream=False,
            temperature=0,seed=7,max_tokens=64),ensure_ascii=False).encode()
        bp=folder/'request.json';bp.write_bytes(body)
        key='discovery/'+slug+'/probe1';budget.reserve(key,body,row['rates'])
        rr=http.request('POST',c.h.BASE+'/chat/completions',bp,timeout=180)
        (folder/'response.json').write_bytes(rr['raw'])
        try:
            assert rr['rc']==0 and rr['status']==200
            response=json.loads(rr['raw']);budget.finish(key,response['usage'])
        except Exception:
            budget.unknown(key);row.update(eligible=False,reason='signature probe completion unavailable');return row
        row.update(usage=response['usage'],latency_s=rr['latency_s'],finish_reason=response['choices'][0].get('finish_reason'),
            req_sha256=hashlib.sha256(body).hexdigest(),resp_sha256=hashlib.sha256(rr['raw']).hexdigest())
        params=urllib.parse.urlencode(dict(model=m['id'],signing_algo='ecdsa'))
        sig={}
        for attempt in range(3):
            sr=http.request('GET',c.h.BASE+'/signature/'+response['id']+'?'+params,timeout=30)
            (folder/f'signature_{attempt+1}.json').write_bytes(sr['raw'])
            try:sig=json.loads(sr['raw'])
            except ValueError:sig={}
            check=signature_check(sig,body,rr['raw'],m['id'],row['attestation'])
            if check['passed']:break
            if attempt<2:time.sleep(2)
        row.update(signature=check,eligible=check['passed'],reason='both capabilities present' if check['passed'] else 'no verified provider_tee signature bound to preflight')
        c.write(folder/'result.json',row);return row
    rows=[]
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        for row in pool.map(one,catalog):
            rows.append(row)
            print(json.dumps({k:row[k] for k in ['model','eligible','reason']}),flush=True)
    c.write(result_path,dict(at=c.now(),catalog_sha256=c.sha(c.ART/'models.json'),models=rows,
        scope='attestation availability, nonce/address binding and response EIP-191 signature; no DCAP or GPU trust-chain verification'))
    print('eligible',sum(r['eligible'] for r in rows),flush=True)

def recheck():
    """Offline re-evaluation of stored evidence; never repeat a probe completion."""
    path=c.ART/'discovery.json';d=c.read(path)
    archive=c.ART/'discovery.initial_check.json'
    if not archive.exists():archive.write_bytes(path.read_bytes())
    for row in d['models']:
        if not row.get('signature'):continue
        folder=c.ROOT/row['folder']
        check=signature_check(c.read(folder/'signature_1.json'),(folder/'request.json').read_bytes(),
            (folder/'response.json').read_bytes(),row['model'],row['attestation'])
        row.update(signature=check,eligible=check['passed'],reason='both capabilities present' if check['passed'] else 'no verified provider_tee signature bound to preflight')
        c.write(folder/'result.json',row)
    d['offline_recheck']='Recognize cast output Validation succeeded; no extra inference.'
    c.write(path,d)
    print(json.dumps([dict(model=r['model'],eligible=r['eligible']) for r in d['models'] if r.get('signature')]))

if __name__=='__main__':
    if '--recheck' in sys.argv:recheck()
    else:main()
