"""Authorized Step 1 follow-up. Public read-only requests, immutable page caches."""
import concurrent.futures as cf
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import time
import urllib.request

def module(name):
    spec=importlib.util.spec_from_file_location(name,Path(__file__).with_name(name+'.py'))
    mod=importlib.util.module_from_spec(spec);sys.modules[name]=mod;spec.loader.exec_module(mod);return mod
m=module('morpho');p=m.p

def aave_retry():
    started=time.time();deadline=time.monotonic()+7200
    endpoints=['https://ethereum-rpc.publicnode.com','https://rpc.ankr.com/eth','https://eth.llamarpc.com']
    bounds=json.loads((p.DATA/'aave_bounds.json').read_text());errors=[]
    state={'started_at':started,'deadline_seconds':7200,'step':500,'endpoints':endpoints,'status':'running'}
    p.save(p.DATA/'aave_retry_status.json',dict(state,source='aave_retry'))
    def request(url,a,b):
        payload={'jsonrpc':'2.0','id':1,'method':'eth_getLogs','params':[{'address':p.POOL,'topics':[p.TOPIC],'fromBlock':hex(a),'toBlock':hex(b)}]}
        key=hashlib.sha256(json.dumps([url,payload],sort_keys=True).encode()).hexdigest()
        path=p.RAW/'aave_retry_http'/f'{key}.json'
        if path.exists():return json.loads(path.read_text())['response']['result']
        if time.monotonic()>=deadline:raise TimeoutError('Two-hour deadline')
        req=urllib.request.Request(url,data=json.dumps(payload).encode(),headers={'Content-Type':'application/json','User-Agent':'PRIME-public-research/1.0'})
        meta={'url':url,'request':payload,'retrieved_at':time.time()}
        try:
            with urllib.request.urlopen(req,timeout=min(20,max(1,deadline-time.monotonic()))) as response:body=json.load(response)
            meta['response']=body
            if 'error' in body:raise RuntimeError(str(body['error']))
            p.save(path,meta);return body['result']
        except Exception as exc:
            meta['error']=str(exc);p.save(p.RAW/'aave_retry_http/errors'/f'{key}.json',meta);raise
    missing=[(a,min(a+1999,bounds['to_block'])) for a in range(bounds['from_block'],bounds['to_block']+1,2000) if not (p.RAW/'aave_logs'/f'{a}-{min(a+1999,bounds["to_block"])}.json').exists()]
    if not missing:return dict(state,status='complete',elapsed_s=time.time()-started)
    a,b=missing[0];working=[]
    for url in endpoints:
        try:
            rows=request(url,a,min(a+499,b));p.save(p.RAW/'aave_retry_steps'/f'{a}-{min(a+499,b)}.json',rows);working.append(url)
        except Exception as exc:errors.append({'url':url,'error':str(exc)});print(url,str(exc),flush=True)
    def page(bounds):
        a,b=bounds;rows=[]
        for x in range(a,b+1,500):
            y=min(x+499,b);path=p.RAW/'aave_retry_steps'/f'{x}-{y}.json'
            if path.exists():part=json.loads(path.read_text())
            else:
                for url in working:
                    try:part=request(url,x,y);p.save(path,part);break
                    except Exception as exc:
                        errors.append({'url':url,'from_block':x,'error':str(exc)})
                else:raise RuntimeError('Every available endpoint failed this step')
            rows.extend(part)
        p.save(p.RAW/'aave_logs'/f'{a}-{b}.json',rows);return len(rows)
    completed=0
    if working:
        with cf.ThreadPoolExecutor(max_workers=2) as pool:
            try:
                for count in pool.map(page,missing,buffersize=2):
                    completed+=1
                    if completed%50==0:print('aave retry pages',completed,'/',len(missing),flush=True)
            except Exception as exc:errors.append({'error':str(exc)})
    remaining=sum(not (p.RAW/'aave_logs'/f'{a}-{b}.json').exists() for a,b in missing)
    state.update(source='aave_retry',status='complete' if remaining==0 else 'excluded_steps_2_4',completed_new_pages=completed,remaining_pages=remaining,errors=errors,finished_at=time.time(),elapsed_s=time.time()-started)
    p.save(p.DATA/'aave_retry_status.json',state);print(json.dumps(state),flush=True);return state

def candidates():
    where={'chainId_in':[1],'collateralAssetAddress_in':[m.ASSETS[1]['WETH'].lower()],'loanAssetAddress_in':[m.ASSETS[1]['USDC'].lower()]}
    result=m.gql(m.MQ,{'w':where})['markets']
    assert len(result['items'])==result['pageInfo']['countTotal']
    p.save(p.DATA/'eth_weth_candidates.json',result['items'])
    for r in sorted(result['items'],key=lambda x:int(x['creationTimestamp'])):print(r['marketId'],r['creationTimestamp'],r['state']['borrowAssets'],flush=True)

def select_market():
    items=json.loads((p.DATA/'eth_weth_candidates.json').read_text())
    def measure(r):
        path=p.RAW/'selection_borrows'/f'{r["marketId"]}.json'
        if path.exists():tx=json.loads(path.read_text())
        else:tx=m.transactions(r,'Borrow');p.save(path,tx)
        r['cumulative_borrow_usdc']=sum(int(x['data']['assets']) for x in tx)/1e6
        r['borrow_events']=len(tx)
        print('candidate',r['marketId'],r['cumulative_borrow_usdc'],len(tx),flush=True);return r
    with cf.ThreadPoolExecutor(max_workers=4) as pool:items=list(pool.map(measure,items))
    eligible=[r for r in items if r['cumulative_borrow_usdc']>=10_000_000]
    chosen=min(eligible,key=lambda r:(int(r['creationTimestamp']),-r['cumulative_borrow_usdc']))
    chosen.update(selection='oldest among >=10M USDC cumulative Borrow-event volume; ties by cumulative volume',candidate_count=len(items))
    markets=json.loads((p.DATA/'markets.json').read_text())
    backup=p.DATA/'markets_step1_original.json'
    if not backup.exists():p.save(backup,markets)
    markets=[chosen if r['chain']['id']==1 and r['collateralAsset']['symbol']=='WETH' else r for r in markets]
    p.save(p.DATA/'eth_weth_selection.json',{'candidates':items,'chosen':chosen,'large_threshold_usdc':10_000_000,'assumption':'Large means at least 10M USDC gross cumulative borrowing through fixed cutoff; repeat borrowing counts.'})
    p.save(p.DATA/'markets.json',markets)
    return chosen

def phase_bounds():
    groups={}
    for f in (p.RAW/'chainlink').rglob('bounds.json'):
        b=json.loads(f.read_text());chain=f.relative_to(p.RAW/'chainlink').parts[0]
        groups.setdefault((chain,b['proxy']),{})[b['phase']]=b
    results=[]
    for (chain,proxy),bounds in groups.items():
        meta={}
        for phase in range(max(1,min(bounds)-1),max(bounds)+1):
            r={'chain':chain,'proxy':proxy,'phase':phase}
            try:
                agg='0x'+p.call(chain,proxy,'0xc1597304'+p.word(phase).hex())[-40:]
                last=p.uint(p.call(chain,agg,'0x668a0f02'))
                first_ts=p.uint(p.call(chain,agg,'0xb633620c'+p.word(1).hex()))
                last_ts=p.uint(p.call(chain,agg,'0xb633620c'+p.word(last).hex()))
                assert first_ts>0 and last_ts>=first_ts
                r.update(aggregator=agg,first_timestamp=first_ts,last_timestamp=last_ts,last_round=last,mapping_verified=phase not in bounds or agg.lower()==bounds[phase]['aggregator'].lower())
            except Exception as exc:r['error']=str(exc)
            meta[phase]=r
        for phase in bounds:
            r=meta[phase];previous=meta.get(phase-1);following=meta.get(phase+1)
            if 'error' not in r and r['mapping_verified']:
                # Rounds can precede proxy activation or continue after retirement.
                # Only non-overlapping intervals are usable; ambiguity falls back.
                start=max(p.START,r['first_timestamp'])
                if previous:
                    evidence_path=p.DATA/'phase_activation_evidence.json'
                    evidence=json.loads(evidence_path.read_text()) if evidence_path.exists() else []
                    known=[x['timestamp'] for x in evidence if x['chain']==chain and x['proxy'].lower()==proxy.lower() and x['phase']==phase]
                    start=max(start,previous['last_timestamp']+1 if 'last_timestamp' in previous else min(known,default=p.END))
                end=min(p.END,r['last_timestamp']+1)
                if following:end=min(end,following.get('first_timestamp',p.START))
                r.update(usable_from=start,usable_until_exclusive=end,boundary_resolved=end>start)
            else:r.update(usable_from=p.END,usable_until_exclusive=p.END,boundary_resolved=False)
            results.append(r);print('phase',chain,phase,r,flush=True)
    p.save(p.DATA/'oracle_phase_bounds.json',results)
    return {'phases':len(results),'usable_intervals':sum(r['boundary_resolved'] for r in results)}

def replacement_pull():
    results={}
    for name,fn in [('liquidations',m.liquidations),('positions',m.positions_batched),('transactions',m.transactions_scoped),('survivors',lambda:m.survivors(chain_filter=1)),('survivor_transactions',m.survivor_transactions)]:
        t=time.time();results[name]=fn();results[name]['elapsed_s']=time.time()-t
        p.save(p.DATA/'replacement_pull_progress.json',results)
    return results

def oracle_config():
    query='''query($m:String!,$c:Int!){marketById(marketId:$m,chainId:$c){oracle{address type data{... on MorphoChainlinkOracleData{baseFeedOne{address} baseFeedTwo{address} quoteFeedOne{address} quoteFeedTwo{address} baseOracleVault{address} scaleFactor} ... on MorphoChainlinkOracleV2Data{baseFeedOne{address} baseFeedTwo{address} quoteFeedOne{address} quoteFeedTwo{address} baseOracleVault{address} quoteOracleVault{address} scaleFactor}}}}}'''
    rows=[]
    for market in json.loads((p.DATA/'markets.json').read_text()):
        row=m.gql(query,{'m':market['marketId'],'c':market['chain']['id']})['marketById']
        row.update(market=market['marketId'],chain=market['chain']['id']);rows.append(row);print(row,flush=True)
    p.save(p.DATA/'market_oracle_config.json',rows);return rows

def inspect_feeds():
    rows=[]
    for item in json.loads((p.DATA/'market_oracle_config.json').read_text()):
        for field,feed in item['oracle']['data'].items():
            if 'Feed' not in field or not feed:continue
            chain='base' if item['chain']==8453 else 'eth';addr=feed['address'];r={'chain':chain,'market':item['market'],'field':field,'address':addr}
            for name,selector in [('description','0x7284e416'),('decimals','0x313ce567'),('latest','0xfeaf968c'),('aggregator','0x245a7bfc')]:
                try:
                    value=p.call(chain,addr,selector)
                    if name=='description':value=bytes.fromhex(value[130:130+p.uint(value,1)*2]).decode()
                    elif name=='decimals':value=p.uint(value)
                    r[name]=value
                except Exception as exc:r[name+'_error']=str(exc)
            rows.append(r);print(r,flush=True)
    p.save(p.DATA/'market_feed_inspection.json',rows);return rows

def extra_oracles():
    feeds=[('base','USDC/USD Morpho','0x7e860098F58bBFC8648a4311b374B1D669a2bc6B'),
           ('eth','ETH/USD Morpho','0xc0053f3FBcCD593758258334Dfce24C2A9A673aD'),
           ('eth','USDC/ETH Morpho','0x986b5E1e1755e3C2440e960477f25201B0a8bbD4')]
    results=[]
    def fetch(feed):
        phase=None
        if feed[0]=='eth' and feed[1].startswith('USDC'):
            phase=p.uint(p.call('eth',feed[2],'0xfeaf968c',p.block_at('eth',p.START)))>>64
        try:
            rows=p.oracle_feed(feed,first_phase_override=phase)
            return {'feed':feed,'rows':len(rows)}
        except Exception as exc:return {'feed':feed,'error':str(exc)}
    with cf.ThreadPoolExecutor(max_workers=3) as pool:
        for result in pool.map(fetch,feeds):results.append(result);print(result,flush=True)
    p.save(p.DATA/'extra_oracle_results.json',results)
    phase_bounds();return results

def replacement_oracle():
    market=next(x for x in json.loads((p.DATA/'markets.json').read_text()) if x['chain']['id']==1 and x['collateralAsset']['symbol']=='WETH')
    proxy='0xc0053f3FBcCD593758258334Dfce24C2A9A673aD'
    phase=p.uint(p.call('eth',proxy,'0xfeaf968c',int(market['creationBlockNumber'])))>>64
    p.save(p.DATA/'phase_activation_evidence.json',[{'chain':'eth','proxy':proxy,'phase':phase,'block':int(market['creationBlockNumber']),'timestamp':int(market['creationTimestamp'])}])
    rows=p.oracle_feed(('eth','ETH/USD Morpho',proxy),first_phase_override=phase)
    phase_bounds();return {'rounds':len(rows),'phase_at_market_creation':phase}

if __name__=='__main__':
    name=sys.argv[1];start=time.time()
    result={'aave':aave_retry,'candidates':candidates,'select_market':select_market,'phase_bounds':phase_bounds,'replacement_pull':replacement_pull,'oracle_config':oracle_config,'inspect_feeds':inspect_feeds,'extra_oracles':extra_oracles,'replacement_oracle':replacement_oracle}[name]()
    p.save(p.DATA/f'closure_{name}_status.json',{'source':'closure_'+name,'status':'complete','started_at':start,'finished_at':time.time(),'elapsed_s':time.time()-start,'result':result})
