"""Step 1 only. Public reads; immutable success cache; Python standard library.
Run with python3 -I scripts/pull.py SOURCE. See generated README for provenance.
"""
import concurrent.futures as futures
import csv
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / 'data/raw'
DATA = ROOT / 'data'
START = int(dt.datetime(2024, 1, 1, tzinfo=dt.timezone.utc).timestamp())
END = int(dt.datetime(2026, 10, 26, tzinfo=dt.timezone.utc).timestamp())
AAVE_START = int(dt.datetime(2025, 1, 1, tzinfo=dt.timezone.utc).timestamp())
SEED = 20261026
POOL = '0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2'
TOPIC = '0xe413a321e8681d831f4dbccbca790d2952b56f977908e45be37335533e005286'
RPCS = {'eth': ['https://eth.drpc.org', 'https://ethereum-rpc.publicnode.com'],
        'base': ['https://base.drpc.org', 'https://base-rpc.publicnode.com']}
LOG_RPCS = ['https://rpc.mevblocker.io', 'https://rpc.flashbots.net']

def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.' + str(os.getpid()) + '.tmp')
    tmp.write_text(json.dumps(value, separators=(',', ':')))
    tmp.replace(path)

def request(source, url, payload=None):
    key = hashlib.sha256(json.dumps([url, payload], sort_keys=True).encode()).hexdigest()
    path = RAW / source / (key + '.json')
    if path.exists():
        return json.loads(path.read_text())['response']
    req = urllib.request.Request(url, data=None if payload is None else json.dumps(payload).encode(),
                                 headers={'Content-Type': 'application/json', 'User-Agent': 'PRIME-research-public/1.0'})
    for attempt in range(5):
        began = time.time()
        try:
            with urllib.request.urlopen(req, timeout=90) as response:
                body = response.read().decode()
                value = json.loads(body)
            if isinstance(value, dict) and (value.get('error') or value.get('errors')):
                save(RAW / source / 'errors' / (key + '.json'),
                     {'url': url, 'request': payload, 'response': value, 'at': time.time()})
                raise RuntimeError(str(value)[:2000])
            save(path, {'url': url, 'request': payload, 'response': value,
                        'retrieved_at': time.time(), 'elapsed_s': time.time() - began})
            return value
        except urllib.error.HTTPError as exc:
            body = exc.read().decode(errors='replace')
            save(RAW / source / 'errors' / (key + '.json'), {'url':url,'request':payload,'http_status':exc.code,'body':body,'at':time.time()})
            if exc.code not in (429, 500, 502, 503, 504) or attempt == 4:
                raise
            time.sleep(min(60, int(exc.headers.get('Retry-After', 2 ** (attempt + 1)))))
        except (TimeoutError, urllib.error.URLError):
            if attempt == 4:
                raise
            time.sleep(2 ** attempt)

def rpc(chain, method, params, urls=None):
    errors = []
    for url in urls or RPCS[chain]:
        try:
            return request('rpc_' + chain, url, {'jsonrpc':'2.0','id':1,'method':method,'params':params})['result']
        except Exception as exc:
            errors.append(f'{url}: {exc}')
    raise RuntimeError('Two public endpoints failed: ' + '; '.join(errors))

def call(chain, target, data, block='latest'):
    return rpc(chain, 'eth_call', [{'to':target,'data':data}, hex(block) if isinstance(block,int) else block])

def word(n): return int(n).to_bytes(32, 'big')
def uint(h, i=0): return int(h[2+64*i:2+64*(i+1)],16)

def multicall(chain, calls):
    # aggregate3((address,bool,bytes)[]) ABI; offsets are relative to array's first offset.
    entries = []
    for target, calldata in calls:
        raw = bytes.fromhex(calldata[2:])
        entries.append(bytes.fromhex(target[2:]).rjust(32,b'\0') + word(1) + word(96) + word(len(raw)) + raw.ljust((len(raw)+31)//32*32,b'\0'))
    off = len(entries)*32
    offsets = []
    for ent in entries:
        offsets.append(word(off)); off += len(ent)
    payload = word(32) + word(len(entries)) + b''.join(offsets) + b''.join(entries)
    raw = bytes.fromhex(call(chain,'0xcA11bde05977b3631167028862bE2a173976CA11','0x82ad56cb'+payload.hex())[2:])
    def n(at): return int.from_bytes(raw[at:at+32],'big')
    arr = n(0); size = n(arr); base = arr+32
    assert size == len(calls), (size, len(calls))
    out = []
    for i in range(size):
        ent = base+n(base+i*32)
        pos = ent+n(ent+32)
        out.append('0x'+raw[pos+32:pos+32+n(pos)].hex() if n(ent) else None)
    return out

def block(chain, height):
    return rpc(chain,'eth_getBlockByNumber',[hex(height),False])

def block_at(chain, timestamp):
    latest = int(rpc(chain,'eth_blockNumber',[]),16)
    top = block(chain, latest)
    if int(top['timestamp'],16) < timestamp:
        raise RuntimeError(f'Requested cutoff {timestamp} is later than public chain head {int(top["timestamp"],16)}')
    lo, hi = 0, latest
    while lo < hi:
        mid = (lo+hi)//2
        if int(block(chain,mid)['timestamp'],16) < timestamp: lo=mid+1
        else: hi=mid
    return lo

def aave():
    b0, b1 = block_at('eth',AAVE_START), block_at('eth',END)-1
    save(DATA/'aave_bounds.json', {'start':AAVE_START,'end_exclusive':END,'from_block':b0,'to_block':b1,'step':2000})
    jobs = [(b,min(b+1999,b1)) for b in range(b0,b1+1,2000)]
    def page(bounds):
        a,b=bounds; path=RAW/'aave_logs'/f'{a}-{b}.json'
        if path.exists(): return len(json.loads(path.read_text()))
        rows=rpc('eth','eth_getLogs',[{'address':POOL,'topics':[TOPIC],'fromBlock':hex(a),'toBlock':hex(b)}], LOG_RPCS)
        save(path,rows)
        return len(rows)
    count=0
    with futures.ThreadPoolExecutor(max_workers=3) as pool:
        for i,n in enumerate(pool.map(page,jobs)):
            count+=n
            if i%25==0: print('aave',i+1,'/',len(jobs),'pages',count,'events',flush=True)
    return {'pages':len(jobs),'events':count}

FEEDS = [('base','BTC/USD','0x64c911996D3c6aC71f9b455B1E8E7266BcbD848F'),
         ('base','ETH/USD','0x71041dddad3595F9CEd3DcCFBe3D1F4b0a16Bb70'),
         ('eth','ETH/USD','0x5424384b256154046e9667ddfaaa5e550145215e'),
         ('eth','BTC/USD','0xb41e773f507f7a7ea890b1afb7d2b660c30c8b0a')]

def oracle_feed(feed, first_phase_override=None):
    chain,name,proxy=feed
    latest=call(chain,proxy,'0xfeaf968c')
    current_phase=uint(latest)>>64
    decimals=uint(call(chain,proxy,'0x313ce567'))
    first_phase=1
    if first_phase_override is not None:first_phase=first_phase_override
    if chain=='eth' and proxy.lower() in ['0x5f4ec3df9cbd43714fe2740f5e3616155c5b8419','0xf4030086522a5beea4988f8ca5b36dbc97bee88c']:
        # Ancient pre-window phases may no longer implement the current round interface.
        # Resolve the phase actually active at the start, rather than querying those phases.
        first_phase=uint(call(chain,proxy,'0xfeaf968c',block_at(chain,START)))>>64
    phases=range(first_phase,current_phase+1) if current_phase else [0]
    results=[]
    for phase in phases:
        agg=('0x'+call(chain,proxy,'0xc1597304'+word(phase).hex())[-40:]) if phase else proxy
        last=uint(call(chain,agg,'0x668a0f02'))
        def ts(r):
            # getTimestamp is readable on Base; getRoundData via the proxy bypasses aggregator access control.
            return uint(call(chain,agg,'0xb633620c'+word(r).hex()))
        if ts(last)<START: continue
        def lower(t):
            lo,hi=1,last+1
            while lo<hi:
                mid=(lo+hi)//2
                if ts(mid)<t: lo=mid+1
                else: hi=mid
            return lo
        r0=max(1,lower(START)-1); r1=min(last,lower(END))
        directory=RAW/'chainlink'/chain/name.replace('/','_')/str(phase)
        save(directory/'bounds.json',{'proxy':proxy,'aggregator':agg,'phase':phase,'first':r0,'last':r1,'decimals':decimals})
        for begin in range(r0,r1+1,10000):
            finish=min(begin+9999,r1); path=directory/f'{begin}-{finish}.json'
            if path.exists():
                rows=json.loads(path.read_text()); results.extend(rows); continue
            rows=[]
            for start in range(begin,finish+1,250):
                ids=list(range(start,min(start+249,finish)+1))
                calls=[(proxy,'0x9a6fc8f5'+word((phase<<64)|r).hex()) for r in ids]
                values=multicall(chain,calls)
                for r,value in zip(ids,values):
                    if value is None: raise RuntimeError(f'Missing round {chain} {name} {phase} {r}')
                    answer=uint(value,1)
                    if answer>=2**255: answer-=2**256
                    rows.append({'chain':chain,'feed':name,'proxy':proxy,'aggregator':agg,'phase':phase,
                                 'round_id':r,'updated_at':uint(value,3),'answer':answer/10**decimals})
            save(path,rows); results.extend(rows)
            print('chainlink',chain,name,'phase',phase,finish,'/',r1,flush=True)
    return results

def oracles():
    rows=[]; failures=[]
    with futures.ThreadPoolExecutor(max_workers=4) as pool:
        jobs={pool.submit(oracle_feed,f):f for f in FEEDS}
        for job in futures.as_completed(jobs):
            try: rows.extend(job.result())
            except Exception as exc: failures.append({'feed':jobs[job],'error':str(exc)})
    rows.sort(key=lambda r:(r['chain'],r['feed'],r['phase'],r['round_id']))
    columns=['chain','feed','proxy','aggregator','phase','round_id','updated_at','answer']
    with (DATA/'oracle_rounds_ext.csv').open('w') as stream:
        w=csv.DictWriter(stream,fieldnames=columns); w.writeheader(); w.writerows(rows)
    if failures: save(DATA/'oracle_failures.json',failures)
    return {'rows':len(rows),'failures':failures}

def eth_oracles():
    # Second approach: use aggregator() as in the original fetcher, then direct
    # getTimestamp/getAnswer reads. Do not assume phaseAggregators is supported.
    rows=[];failures=[]
    for chain,name,source in FEEDS[2:]:
        try:
            raw=call(chain,source,'0x245a7bfc')
            if len(raw)!=66:raise RuntimeError(f'aggregator() returned {raw}')
            agg='0x'+raw[-40:]
            latest=uint(call(chain,agg,'0x668a0f02'))
            def timestamp(r):return uint(call(chain,agg,'0xb633620c'+word(r).hex()))
            def lower(t):
                lo,hi=1,latest+1
                while lo<hi:
                    mid=(lo+hi)//2
                    if timestamp(mid)<t:lo=mid+1
                    else:hi=mid
                return lo
            first=max(1,lower(START)-1);last=min(latest,lower(END))
            directory=RAW/'chainlink_direct'/name.replace('/','_')
            save(directory/'bounds.json',{'aggregator':agg,'source':source,'first':first,'last':last,'first_available_timestamp':timestamp(1),'last_available_timestamp':timestamp(latest)})
            for begin in range(first,last+1,10000):
                end=min(begin+9999,last);path=directory/f'{begin}-{end}.json'
                if path.exists():rows.extend(json.loads(path.read_text()));continue
                batch=[]
                for a in range(begin,end+1,125):
                    ids=list(range(a,min(a+124,end)+1));calls=[]
                    for r in ids:calls.extend([(agg,'0xb633620c'+word(r).hex()),(agg,'0xb5ab58dc'+word(r).hex())])
                    result=multicall('eth',calls)
                    for i,r in enumerate(ids):
                        t,answer=result[2*i:2*i+2]
                        if t is None or answer is None:raise RuntimeError(f'Missing round {r}')
                        value=uint(answer)
                        if value>=2**255:value-=2**256
                        batch.append({'chain':'eth','feed':name,'proxy':source,'aggregator':agg,'phase':None,'round_id':r,'updated_at':uint(t),'answer':value/10**8})
                save(path,batch);rows.extend(batch);print('eth direct',name,end,'/',last,flush=True)
        except Exception as exc:failures.append({'feed':name,'error':str(exc)})
    save(DATA/'eth_oracle_direct.json',rows)
    return {'rows':len(rows),'failures':failures}

def legacy_oracles():
    # Standard Chainlink proxies, including the phases before the newer Aave sources.
    feeds=[('eth','ETH/USD','0x5f4ec3df9cbd43714fe2740f5e3616155c5b8419'),
           ('eth','BTC/USD','0xf4030086522a5beea4988f8ca5b36dbc97bee88c')]
    rows=[];failures=[]
    for feed in feeds:
        try:rows.extend(oracle_feed(feed))
        except Exception as exc:failures.append({'feed':feed,'error':str(exc)})
    save(DATA/'eth_oracle_standard.json',rows)
    return {'rows':len(rows),'failures':failures}

def ratios():
    """Historical wrapper conversion using the original fetcher's 7,150-block spacing."""
    b0=block_at('eth',START)-1;b1=block_at('eth',END)-1
    def sample(b):
        stamp=int(block('eth',b)['timestamp'],16)
        ratio=uint(call('eth','0xae7ab96520de3a18e5e111b5eaab095312d7fe84','0x7a28fb88'+word(10**18).hex(),b))/10**18
        if not 1<ratio<2:raise RuntimeError(f'Unexpected stETH share ratio {ratio} at {b}')
        return {'block':b,'ts':stamp,'wsteth_steth':ratio}
    rows=[]
    with futures.ThreadPoolExecutor(max_workers=3) as pool:
        for row in pool.map(sample,range(b0,b1+1,7150),buffersize=3):
            rows.append(row)
            if len(rows)%100==0:print('wrapper ratios',len(rows),flush=True)
    with (DATA/'eth_ratios_ext.csv').open('w') as stream:
        writer=csv.DictWriter(stream,fieldnames=['block','ts','wsteth_steth']);writer.writeheader();writer.writerows(rows)
    return {'rows':len(rows),'first_timestamp':rows[0]['ts'],'last_timestamp':rows[-1]['ts']}

def phase_intervals():
    """Locate actual proxy phase switches, preserving pre-activation round history separately."""
    groups={}
    for f in (RAW/'chainlink').rglob('bounds.json'):
        b=json.loads(f.read_text());chain=f.relative_to(RAW/'chainlink').parts[0]
        groups.setdefault((chain,b['proxy']),[]).append(b['phase'])
    rows=[]
    for (chain,proxy),phases in groups.items():
        phases=sorted(set(phases));b0=block_at(chain,START);b1=block_at(chain,END)-1
        boundaries={phases[0]:START}
        for phase in phases[1:]:
            lo,hi=b0,b1
            while lo<hi:
                mid=(lo+hi)//2;value=call(chain,proxy,'0xfeaf968c',mid)
                observed=uint(value)>>64 if len(value)>=66 else 0
                if observed<phase:lo=mid+1
                else:hi=mid
            boundaries[phase]=int(block(chain,lo)['timestamp'],16)
        for i,phase in enumerate(phases):
            rows.append({'chain':chain,'proxy':proxy,'phase':phase,'active_from':boundaries[phase],
                         'active_until_exclusive':boundaries[phases[i+1]] if i+1<len(phases) else END,
                         'start_clipped_to_window':i==0})
    save(DATA/'oracle_phase_intervals.json',rows)
    return {'intervals':len(rows)}

def main():
    name=sys.argv[1]; start=time.time()
    status={'source':name,'started_at':start,'status':'running','pid':os.getpid()}
    save(DATA/(name+'_status.json'),status)
    try:
        result={'aave':aave,'oracles':oracles,'eth_oracles':eth_oracles,'legacy_oracles':legacy_oracles,'ratios':ratios,'phase_intervals':phase_intervals}[name]()
        status.update(status='complete' if not result.get('failures') else 'partial',result=result)
    except Exception as exc:
        import traceback
        traceback.print_exc()
        status.update(status='failed',error=str(exc))
    finally:
        status.update(finished_at=time.time(),elapsed_s=time.time()-start)
        save(DATA/(name+'_status.json'),status)
        print(json.dumps(status),flush=True)
    if status['status'] in ('failed','partial'):raise SystemExit(1)

if __name__=='__main__': main()
