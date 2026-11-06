"""Morpho public API extraction, schema inspected before constructing queries."""
import importlib.util
from pathlib import Path
import sys
import json
import time
import random
import concurrent.futures as futures
spec=importlib.util.spec_from_file_location('pull',Path(__file__).with_name('pull.py'))
p=importlib.util.module_from_spec(spec); spec.loader.exec_module(p)

def gql(query,variables=None):
    return p.request('morpho','https://api.morpho.org/graphql',{'query':query,'variables':variables or {}})['data']

FIELDS='marketId creationTimestamp creationBlockNumber lltv chain{id} collateralAsset{address symbol decimals} loanAsset{address symbol decimals} state{borrowAssets borrowAssetsUsd timestamp} oracle{address}'
MQ='query($w:MarketFilters){markets(first:100,orderBy:BorrowAssets,orderDirection:Desc,where:$w){pageInfo{countTotal} items{'+FIELDS+'}}}'
FIXED='0x9103c3b4e834476c9a62ea009ba2c884ee42e94e6e314a26f04d312434191836'
ASSETS={8453:{'WETH':'0x4200000000000000000000000000000000000006','USDC':'0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913'},
        1:{'WETH':'0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2','wstETH':'0x7f39C581F595B53c5cb19bD0b3f8dA6c935E2Ca0','USDC':'0xA0b86991c6218b36c1d19d4a2e9eb0ce3606eb48'}}

def discover():
    markets=[]
    for chain,asset in [(8453,'cbBTC'),(8453,'WETH'),(1,'WETH'),(1,'wstETH')]:
        where={'chainId_in':[chain]}
        if asset=='cbBTC':where['uniqueKey_in']=[FIXED]
        else:where.update(collateralAssetAddress_in=[ASSETS[chain][asset].lower()],loanAssetAddress_in=[ASSETS[chain]['USDC'].lower()])
        result=gql(MQ,{'w':where})['markets']
        if not result['items']:raise RuntimeError(f'No market found {chain} {asset}')
        market=result['items'][0]
        market.update(selection='fixed in brief' if asset=='cbBTC' else 'highest current borrowAssets',candidate_count=result['pageInfo']['countTotal'])
        markets.append(market)
        print(chain,asset,market['marketId'],'borrow',market['state']['borrowAssets'],'created',market['creationTimestamp'],flush=True)
    p.save(p.DATA/'markets.json',markets)
    return {'markets':len(markets)}

TXQ='''query($w:MarketTransactionFilters,$skip:Int){marketTransactions(first:1000,skip:$skip,orderBy:Timestamp,orderDirection:Asc,where:$w){pageInfo{countTotal} items{txHash timestamp blockNumber logIndex type user{address} data{... on MarketTransactionTransferData{assets shares} ... on MarketTransactionCollateralTransferData{assets} ... on MarketTransactionLiquidationData{liquidator repaidAssets repaidShares seizedAssets badDebtAssets}}}}}'''

def transactions(market,kind='Liquidation',user=None):
    mk=market['marketId'];chain=market['chain']['id']
    where={'marketUniqueKey_in':[mk],'chainId_in':[chain],'timestamp_gte':int(market['creationTimestamp']),'timestamp_lte':p.END-1}
    if kind:where['type_in']=[kind]
    if user:where['userAddress_in']=[user]
    # Monthly windows keep offset pagination bounded and every page immutable.
    rows=[]
    start=where['timestamp_gte']
    while start<p.END:
        end=min(p.END,start+28*86400)
        w=dict(where,timestamp_gte=start,timestamp_lte=end-1)
        skip=0
        while True:
            if skip>10000:
                last=page['items'][-1]
                w=dict(w,cursor={'txHash':last['txHash'],'logIndex':last['logIndex']})
                skip=0
            page=gql(TXQ,{'w':w,'skip':skip})['marketTransactions']
            rows.extend(page['items']);skip+=len(page['items'])
            if skip>=page['pageInfo']['countTotal']:break
            if not page['items']:raise RuntimeError('Empty page before countTotal')
        start=end
    ids={(r['txHash'],r['logIndex']) for r in rows}
    if len(ids)!=len(rows):raise RuntimeError('Duplicate transaction IDs in bounded pagination')
    return rows

def liquidations():
    markets=json.loads((p.DATA/'markets.json').read_text());total=0
    for market in markets:
        rows=transactions(market)
        p.save(p.RAW/'morpho_liquidations'/f'{market["chain"]["id"]}-{market["marketId"]}.json',rows)
        total+=len(rows)
        print('liquidations',market['chain']['id'],market['collateralAsset']['symbol'],len(rows),flush=True)
    return {'events':total}

HQ='''query($u:String!,$m:String!,$c:Int!,$o:TimeseriesOptions){marketPosition(userAddress:$u,marketUniqueKey:$m,chainId:$c){historicalState{collateral(options:$o){x y} collateralValue(options:$o){x y} borrowAssets(options:$o){x y} borrowShares(options:$o){x y}}}}'''
HISTORY_BATCHER=None

class HistoryBatcher:
    """Coalesce independent survivor histories below the advertised point limit.
    Derived per-position pages retain the raw batch-cache reference for resumptions.
    """
    def __init__(self):
        import queue,threading
        self.queue=queue.Queue()
        for _ in range(4):threading.Thread(target=self.run,daemon=True).start()
    def get(self,variables,screening=False):
        import hashlib
        key=hashlib.sha256(json.dumps(variables,sort_keys=True).encode()).hexdigest()
        path=p.RAW/'morpho_history_pages'/f'{key}.json'
        if path.exists():return json.loads(path.read_text())['historicalState']
        request={'query':HQ,'variables':variables}
        oldkey=hashlib.sha256(json.dumps(['https://api.morpho.org/graphql',request],sort_keys=True).encode()).hexdigest()
        old=p.RAW/'morpho'/f'{oldkey}.json'
        if old.exists():return json.loads(old.read_text())['response']['data']['marketPosition']['historicalState']
        if screening:
            path=p.RAW/'morpho_history_pages'/f'{key}-screen.json'
            if path.exists():return json.loads(path.read_text())['historicalState']
        job=futures.Future();self.queue.put((variables,path,job,screening));return job.result()
    def run(self):
        import queue,hashlib
        pending=None
        def points(job):
            o=job[0]['o'];return ((o['endTimestamp']//3600)-(o['startTimestamp']//3600)+1)*(2 if job[3] else 4)
        while True:
            jobs=[pending or self.queue.get()];pending=None;budget=points(jobs[0]);deadline=time.monotonic()+.1
            while len(jobs)<30:
                try:next_job=self.queue.get(timeout=max(.001,deadline-time.monotonic()))
                except queue.Empty:break
                if budget+points(next_job)>48000:pending=next_job;break
                jobs.append(next_job);budget+=points(next_job)
                if time.monotonic()>=deadline:break
            try:
                declarations=[];fields=[];variables={}
                for i,(v,_,_,screening) in enumerate(jobs):
                    for name,typ in [('u','String!'),('m','String!'),('c','Int!'),('o','TimeseriesOptions')]:
                        declarations.append(f'${name}{i}:{typ}');variables[f'{name}{i}']=v[name]
                    series=['collateralValue','borrowAssets'] if screening else ['collateral','collateralValue','borrowAssets','borrowShares']
                    body=' '.join(f'{name}(options:$o{i}){{x y}}' for name in series)
                    fields.append(f'p{i}:marketPosition(userAddress:$u{i},marketUniqueKey:$m{i},chainId:$c{i}){{historicalState{{'+body+'}}')
                query='query('+','.join(declarations)+'){'+''.join(fields)+'}'
                result=gql(query,variables)
                cachekey=hashlib.sha256(json.dumps(['https://api.morpho.org/graphql',{'query':query,'variables':variables}],sort_keys=True).encode()).hexdigest()
                for i,(_,path,job,_) in enumerate(jobs):
                    h=result[f'p{i}']['historicalState'];p.save(path,{'source_batch':f'morpho/{cachekey}.json','historicalState':h});job.set_result(h)
            except Exception as exc:
                for _,_,job,_ in jobs:job.set_exception(exc)

def history(market,user,start,end,screening=False):
    result=[]
    # At most four weeks per response, below the API timeseries point limit.
    for a in range(start//3600*3600,end,28*86400):
        b=min(a+28*86400,end)
        variables={'u':user,'m':market['marketId'],'c':market['chain']['id'],
                   'o':{'startTimestamp':a,'endTimestamp':b-1,'interval':'HOUR'}}
        result.append(HISTORY_BATCHER.get(variables,screening) if HISTORY_BATCHER else gql(HQ,variables)['marketPosition']['historicalState'])
    return result

def positions():
    markets=json.loads((p.DATA/'markets.json').read_text()); counts=[]
    for market in markets:
        key=f'{market["chain"]["id"]}-{market["marketId"]}'
        liqs=json.loads((p.RAW/'morpho_liquidations'/f'{key}.json').read_text())
        first={}
        for r in liqs:
            u=r['user']['address'].lower();first[u]=min(first.get(u,p.END),int(r['timestamp']))
        def liquidated(user):
            path=p.RAW/'morpho_positions'/key/f'{user}.json'
            if path.exists():return
            t=first[user];start=max(int(market['creationTimestamp']),t-14*86400);end=min(p.END,t+7*86400)
            hist=history(market,user,start,end)
            p.save(path,{'borrower':user,'selection':'all_liquidated','first_liq_ts':t,'start':start,'end_exclusive':end,'history':hist})
        with futures.ThreadPoolExecutor(max_workers=8) as pool:
            for i,_ in enumerate(pool.map(liquidated,sorted(first),buffersize=8)):
                if i%100==0:print('positions',key,i+1,'/',len(first),flush=True)
        counts.append({'market':key,'liquidated_borrowers':len(first)})
    return {'markets':counts}

def all_transactions():
    markets=json.loads((p.DATA/'markets.json').read_text());counts=[]
    for market in markets:
        key=f'{market["chain"]["id"]}-{market["marketId"]}'
        path=p.RAW/'morpho_transactions'/f'{key}.json'
        rows=json.loads(path.read_text()) if path.exists() else transactions(market,None)
        p.save(path,rows)
        counts.append({'market':key,'transactions':len(rows)})
        print('all transactions',key,len(rows),flush=True)
    return {'markets':counts}

def survivors(chain_filter=None):
    """Seeded rejection sampling from ALL historical borrowers, including closed ones.
    A borrower qualifies at its earliest observed >70% hour with 30-day follow-up.
    Sampling a random permutation then accepting qualifiers is uniform sampling
    without replacement from the eligible population; stop after 2,000 accepts.
    """
    import collections,bisect
    global HISTORY_BATCHER
    HISTORY_BATCHER=HistoryBatcher()
    markets=json.loads((p.DATA/'markets.json').read_text());summary=[]
    if chain_filter is not None:markets=[m for m in markets if m['chain']['id']==chain_filter]
    for market in markets:
        key=f'{market["chain"]["id"]}-{market["marketId"]}'
        txpath=p.RAW/'morpho_transactions'/f'{key}.json'
        events=collections.defaultdict(list)
        if (p.DATA/'transaction_scope.json').exists():
            import sqlite3
            scope=json.loads((p.DATA/'transaction_scope.json').read_text())
            if key not in scope['completed_populations']:raise RuntimeError(f'Incomplete borrower population: {key}')
            db=sqlite3.connect(p.RAW/'morpho_transactions.sqlite',timeout=120)
            source=(json.loads(row[0]) for row in db.execute('SELECT payload FROM events WHERE chain=? AND market=? AND kind IN (?,?)',(market['chain']['id'],market['marketId'],'Borrow','Liquidation')))
        elif txpath.exists():source=json.loads(txpath.read_text())
        else:raise RuntimeError(f'Complete transaction population required: {key}')
        for r in source:
            if r['type'] in ['Borrow','Repay','Liquidation']:events[r['user']['address'].lower()].append(r)
        candidates=sorted(u for u,ev in events.items() if any(r['type']=='Borrow' for r in ev))
        random.Random(p.SEED).shuffle(candidates)
        p.save(p.DATA/'survivor_draws'/f'{key}.json',{'seed':p.SEED,'population':len(candidates),'ordered_borrowers':candidates})
        accepted=0;screened=0;eligible_end=p.END-30*86400
        def screen(user):
            path=p.RAW/'morpho_survivors'/key/f'{user}.json'
            if path.exists():return json.loads(path.read_text())
            ev=sorted(events[user],key=lambda x:(int(x['timestamp']),int(x['blockNumber']),x['logIndex']))
            start=min(int(r['timestamp']) for r in ev if r['type']=='Borrow')
            liq_times=sorted(int(r['timestamp']) for r in ev if r['type']=='Liquidation')
            # Complete own events identify debt-active intervals, including positions
            # opened and closed between daily snapshots. Interest does not change shares.
            shares=0;opened=None;periods=[];unknown_shares=False
            for r in ev:
                data=r['data'];amount=int(data.get('shares') or data.get('repaidShares') or 0)
                if r['type']=='Borrow' and amount==0:unknown_shares=True
                if r['type']=='Borrow' and shares<=0:opened=int(r['timestamp'])
                shares+=amount if r['type']=='Borrow' else -amount
                if shares<=0 and opened is not None:
                    periods.append((opened,int(r['timestamp'])+3600));opened=None
            if opened is not None:periods.append((opened,eligible_end))
            if unknown_shares:periods=[(start,eligible_end)]
            periods=[(a,min(b,eligible_end)) for a,b in periods if a<eligible_end]
            end=max((b for a,b in periods),default=min(start,eligible_end))
            anchor=None
            for opened,closed in periods:
                for a in range(opened//3600*3600,closed,28*86400):
                    b=min(a+28*86400,closed)
                    h=history(market,user,a,b,screening=True)[0]
                    coll={int(x['x']):x['y'] for x in h['collateralValue'] or []}
                    for point in sorted(h['borrowAssets'] or [],key=lambda x:x['x']):
                        t=int(point['x']);debt=point['y'];value=coll.get(t)
                        if debt is None or value is None or int(value)<=0 or int(debt)/int(value)<=.7:continue
                        j=bisect.bisect_right(liq_times,t)
                        if j<len(liq_times) and liq_times[j]<=t+30*86400:continue
                        if t+30*86400>=p.END:continue
                        anchor=t;break
                    if anchor is not None:break
                if anchor is not None:break
            result={'borrower':user,'qualified':anchor is not None,'anchor_ts':anchor,'selection':'seeded_survivor','seed':p.SEED,'screen_start':start,'screen_end':end}
            if anchor is not None:
                a=max(int(market['creationTimestamp']),anchor-14*86400);b=min(p.END,anchor+7*86400)
                result.update(start=a,end_exclusive=b,history=history(market,user,a,b),first_liq_ts=min(liq_times) if liq_times else None)
            p.save(path,result);return result
        def prepared_candidates():
            for begin in range(0,len(candidates),100):
                group=candidates[begin:begin+100]
                pending=[u for u in group if not (p.RAW/'morpho_survivors'/key/f'{u}.json').exists()]
                if pending:
                    own_marker=p.DATA/'survivor_owner_prefetch'/key/f'{begin}.json'
                    if not own_marker.exists():
                        where={'marketUniqueKey_in':[market['marketId']],'chainId_in':[market['chain']['id']],
                               'userAddress_in':group,'timestamp_gte':scope['prefix_ends'][key],'timestamp_lte':p.END-1}
                        skip=0;seen=set();expected=None;count=0
                        while True:
                            if skip>10000:
                                last=page['items'][-1];where=dict(where,cursor={'txHash':last['txHash'],'logIndex':last['logIndex']});skip=0
                            page=gql(TXQ,{'w':where,'skip':skip})['marketTransactions']
                            if expected is None:expected=page['pageInfo']['countTotal']
                            records=page['items']
                            db.executemany('INSERT OR IGNORE INTO events VALUES(?,?,?,?,?,?,?,?)',[(market['chain']['id'],market['marketId'],r['txHash'],r['logIndex'],r['user']['address'].lower(),int(r['timestamp']),r['type'],json.dumps(r,separators=(',',':'))) for r in records]);db.commit()
                            seen.update((r['txHash'],r['logIndex']) for r in records);count+=len(records);skip+=len(records)
                            if skip>=page['pageInfo']['countTotal']:break
                            if not records:raise RuntimeError('Empty candidate owner page')
                        if count!=len(seen) or count!=expected:raise RuntimeError('Candidate own-transaction pagination mismatch')
                        p.save(own_marker,{'borrowers':group,'events':count})
                    for u in pending:
                        events[u]=[json.loads(row[0]) for row in db.execute('SELECT payload FROM events WHERE chain=? AND market=? AND borrower=? AND kind IN (?,?,?)',(market['chain']['id'],market['marketId'],u,'Borrow','Repay','Liquidation'))]
                yield from group
        with futures.ThreadPoolExecutor(max_workers=64) as pool:
            for result in pool.map(screen,prepared_candidates(),buffersize=256):
                screened+=1
                if result['qualified']:accepted+=1
                if screened%50==0:print('survivors',key,'screened',screened,'accepted',accepted,flush=True)
                if accepted>=2000:break
        record={'market':key,'population':len(candidates),'screened':screened,'selected':accepted,'seed':p.SEED}
        p.save(p.DATA/'survivor_selection'/f'{key}.json',dict(record,selected_borrowers=[u for u in candidates[:screened] if json.loads((p.RAW/'morpho_survivors'/key/f'{u}.json').read_text())['qualified']]))
        summary.append(record)
    return {'markets':summary}

def survivors_fast():
    import os,signal,subprocess
    for line in subprocess.check_output(['ps','-axo','pid=,command='],text=True).splitlines():
        parts=line.strip().split(None,1)
        if len(parts)==2 and int(parts[0])!=os.getpid() and any(parts[1].endswith('research/liquidation-dataset/scripts/morpho.py '+mode) for mode in ['survivors','survivors_fast']):
            path=p.DATA/'morpho_survivors_status.json';old=json.loads(path.read_text())
            old.update(status='superseded_by_active_interval_reader',finished_at=time.time(),elapsed_s=time.time()-old['started_at']);p.save(path,old)
            os.kill(int(parts[0]),signal.SIGTERM)
    return survivors()

def transactions_scoped():
    """Finish historical borrower census, then all own events for liquidated cases.
    Cached full-market pages are indexed first and reused; avoid downloading
    unrelated lenders' transactions over the remaining window.
    """
    import os,signal,subprocess,sqlite3
    for line in subprocess.check_output(['ps','-axo','pid=,command='],text=True).splitlines():
        parts=line.strip().split(None,1)
        if len(parts)==2 and int(parts[0])!=os.getpid() and parts[1].endswith('research/liquidation-dataset/scripts/morpho.py transactions'):
            statuspath=p.DATA/'morpho_transactions_status.json'
            status=json.loads(statuspath.read_text());status.update(status='superseded_by_scoped_reader',finished_at=time.time(),elapsed_s=time.time()-status['started_at']);p.save(statuspath,status)
            os.kill(int(parts[0]),signal.SIGTERM)
    db=sqlite3.connect(p.RAW/'morpho_transactions.sqlite',timeout=120)
    db.execute('PRAGMA journal_mode=WAL')
    db.executescript('CREATE TABLE IF NOT EXISTS events(chain INTEGER, market TEXT, tx TEXT, log INTEGER, borrower TEXT, ts INTEGER, kind TEXT, payload TEXT, PRIMARY KEY(chain,tx,log)); CREATE TABLE IF NOT EXISTS indexed_files(path TEXT PRIMARY KEY); CREATE INDEX IF NOT EXISTS by_user ON events(chain,market,borrower,ts); CREATE INDEX IF NOT EXISTS by_market_time ON events(chain,market,ts); CREATE TABLE IF NOT EXISTS windows(chain INTEGER,market TEXT,start INTEGER,end INTEGER,total INTEGER, PRIMARY KEY(chain,market,start,end));')
    def insert(chain,mk,rows):
        db.executemany('INSERT OR IGNORE INTO events VALUES(?,?,?,?,?,?,?,?)',[(chain,mk,r['txHash'],r['logIndex'],r['user']['address'].lower(),int(r['timestamp']),r['type'],json.dumps(r,separators=(',',':'))) for r in rows])
    indexed={r[0] for r in db.execute('SELECT path FROM indexed_files')}
    for file in sorted((p.RAW/'morpho').glob('*.json')):
        if file.name in indexed:continue
        d=json.loads(file.read_text());variables=d['request'].get('variables',{});where=variables.get('w',{})
        if 'marketTransactions' in (d['response'].get('data') or {}):
            chain=where['chainId_in'][0];mk=where['marketUniqueKey_in'][0];page=d['response']['data']['marketTransactions'];insert(chain,mk,page['items'])
            if 'type_in' not in where and 'userAddress_in' not in where and 'cursor' not in where and variables.get('skip',0)==0:
                db.execute('INSERT OR REPLACE INTO windows VALUES(?,?,?,?,?)',(chain,mk,where['timestamp_gte'],where['timestamp_lte']+1,page['pageInfo']['countTotal']))
        db.execute('INSERT INTO indexed_files VALUES(?)',(file.name,))
        if len(indexed)%100==0:db.commit()
        indexed.add(file.name)
    db.commit();markets=json.loads((p.DATA/'markets.json').read_text())
    scopepath=p.DATA/'transaction_scope.json'
    scope=json.loads(scopepath.read_text()) if scopepath.exists() else {'completed_populations':[],'completed_liquidated_owners':[],'prefix_ends':{}}
    def fetch(where):
        skip=0;count=0;ids=set();expected=None
        while True:
            if skip>10000:
                last=page['items'][-1];where=dict(where,cursor={'txHash':last['txHash'],'logIndex':last['logIndex']});skip=0
            page=gql(TXQ,{'w':where,'skip':skip})['marketTransactions']
            if expected is None:expected=page['pageInfo']['countTotal']
            insert(where['chainId_in'][0],where['marketUniqueKey_in'][0],page['items']);db.commit()
            ids.update((r['txHash'],r['logIndex']) for r in page['items']);count+=len(page['items']);skip+=len(page['items'])
            if skip>=page['pageInfo']['countTotal']:break
            if not page['items']:raise RuntimeError('Empty cursor page')
        if len(ids)!=expected or count!=len(ids):raise RuntimeError(f'Pagination consistency failure: unique={len(ids)} rows={count} expected={expected}')
        return count
    for m in markets:
        chain=m['chain']['id'];mk=m['marketId'];key=f'{chain}-{mk}'
        if key not in scope['prefix_ends']:
            end=int(m['creationTimestamp'])
            for a,b,total in db.execute('SELECT start,end,total FROM windows WHERE chain=? AND market=? ORDER BY start',(chain,mk)):
                if a!=end:break
                actual=db.execute('SELECT count(*) FROM events WHERE chain=? AND market=? AND ts>=? AND ts<?',(chain,mk,a,b)).fetchone()[0]
                if actual!=total:break
                end=b
            scope['prefix_ends'][key]=end;p.save(scopepath,scope)
        start=scope['prefix_ends'][key]
        if key not in scope['completed_populations']:
            for a in range(start,p.END,28*86400):
                count=fetch({'marketUniqueKey_in':[mk],'chainId_in':[chain],'type_in':['Borrow'],'timestamp_gte':a,'timestamp_lte':min(a+28*86400,p.END)-1})
                print('borrower census',key,a,count,flush=True)
            scope['completed_populations'].append(key);p.save(scopepath,scope)
        if key not in scope['completed_liquidated_owners']:
            users=sorted({r['user']['address'].lower() for r in json.loads((p.RAW/'morpho_liquidations'/f'{key}.json').read_text())})
            for i in range(0,len(users),100):
                count=fetch({'marketUniqueKey_in':[mk],'chainId_in':[chain],'userAddress_in':users[i:i+100],'timestamp_gte':start,'timestamp_lte':p.END-1})
                if i%1000==0:print('owner transactions',key,i,'/',len(users),count,flush=True)
            scope['completed_liquidated_owners'].append(key);p.save(scopepath,scope)
    db.close();return scope

def survivor_transactions():
    import sqlite3
    db=sqlite3.connect(p.RAW/'morpho_transactions.sqlite',timeout=120)
    scope=json.loads((p.DATA/'transaction_scope.json').read_text());counts=[]
    for m in json.loads((p.DATA/'markets.json').read_text()):
        chain=m['chain']['id'];mk=m['marketId'];key=f'{chain}-{mk}'
        selection=json.loads((p.DATA/'survivor_selection'/f'{key}.json').read_text())
        liquidated={r['user']['address'].lower() for r in json.loads((p.RAW/'morpho_liquidations'/f'{key}.json').read_text())}
        covered=set(liquidated)
        for file in (p.DATA/'survivor_owner_prefetch'/key).glob('*.json'):covered.update(json.loads(file.read_text())['borrowers'])
        users=sorted(set(selection['selected_borrowers'])-covered)
        total=0
        for begin in range(0,len(users),100):
            where={'marketUniqueKey_in':[mk],'chainId_in':[chain],'userAddress_in':users[begin:begin+100],
                   'timestamp_gte':scope['prefix_ends'][key],'timestamp_lte':p.END-1}
            skip=0;seen=set();expected=None;count=0
            while True:
                if skip>10000:
                    last=page['items'][-1];where=dict(where,cursor={'txHash':last['txHash'],'logIndex':last['logIndex']});skip=0
                page=gql(TXQ,{'w':where,'skip':skip})['marketTransactions']
                if expected is None:expected=page['pageInfo']['countTotal']
                rows=page['items']
                db.executemany('INSERT OR IGNORE INTO events VALUES(?,?,?,?,?,?,?,?)',[(chain,mk,r['txHash'],r['logIndex'],r['user']['address'].lower(),int(r['timestamp']),r['type'],json.dumps(r,separators=(',',':'))) for r in rows]);db.commit()
                seen.update((r['txHash'],r['logIndex']) for r in rows);count+=len(rows);skip+=len(rows)
                if skip>=page['pageInfo']['countTotal']:break
                if not rows:raise RuntimeError('Empty survivor transaction page')
            if count!=len(seen) or len(seen)!=expected:raise RuntimeError(f'Survivor transaction count mismatch {key}')
            total+=count
        counts.append({'market':key,'additional_survivor_owners':len(users),'additional_transactions':total})
        print('survivor transactions',key,len(users),total,flush=True)
    db.close();return {'markets':counts}

def positions_batched():
    """Use the API's advertised 50,000 timeseries-point budget, with stable cached batches."""
    import os,signal,subprocess
    # Transfer ownership from the earlier one-position worker, preserving its completed pages.
    commands=subprocess.check_output(['ps','-axo','pid=,command='],text=True)
    for line in commands.splitlines():
        parts=line.strip().split(None,1)
        if len(parts)!=2:continue
        pid,command=parts
        if int(pid)!=os.getpid() and command.endswith('research/liquidation-dataset/scripts/morpho.py positions'):
            old=p.DATA/'morpho_positions_status.json'
            if old.exists():
                status=json.loads(old.read_text());status.update(status='superseded_by_batched_reader',finished_at=time.time(),elapsed_s=time.time()-status['started_at'])
                p.save(old,status)
            os.kill(int(pid),signal.SIGTERM)
    markets=json.loads((p.DATA/'markets.json').read_text());summary=[]
    for market in markets:
        key=f'{market["chain"]["id"]}-{market["marketId"]}'
        first={}
        for r in json.loads((p.RAW/'morpho_liquidations'/f'{key}.json').read_text()):
            u=r['user']['address'].lower();first[u]=min(first.get(u,p.END),int(r['timestamp']))
        planpath=p.DATA/'history_batch_plans'/f'{key}.json'
        if planpath.exists():groups=json.loads(planpath.read_text())
        else:
            pending=[u for u in sorted(first) if not (p.RAW/'morpho_positions'/key/f'{u}.json').exists()]
            groups=[pending[i:i+20] for i in range(0,len(pending),20)];p.save(planpath,groups)
        def batch(users):
            if all((p.RAW/'morpho_positions'/key/f'{u}.json').exists() for u in users):return len(users)
            declarations=['$m:String!','$c:Int!'];fields=[];variables={'m':market['marketId'],'c':market['chain']['id']};windows=[]
            for i,user in enumerate(users):
                start=max(int(market['creationTimestamp']),first[user]-14*86400);end=min(p.END,first[user]+7*86400);windows.append((start,end))
                declarations.extend([f'$u{i}:String!',f'$o{i}:TimeseriesOptions'])
                variables[f'u{i}']=user;variables[f'o{i}']={'startTimestamp':start//3600*3600,'endTimestamp':end-1,'interval':'HOUR'}
                fields.append(f'p{i}:marketPosition(userAddress:$u{i},marketUniqueKey:$m,chainId:$c){{historicalState{{collateral(options:$o{i}){{x y}} collateralValue(options:$o{i}){{x y}} borrowAssets(options:$o{i}){{x y}} borrowShares(options:$o{i}){{x y}}}}}}')
            response=gql('query('+','.join(declarations)+'){'+''.join(fields)+'}',variables)
            for i,user in enumerate(users):
                path=p.RAW/'morpho_positions'/key/f'{user}.json'
                if path.exists():continue
                start,end=windows[i]
                p.save(path,{'borrower':user,'selection':'all_liquidated','first_liq_ts':first[user],'start':start,'end_exclusive':end,'history':[response[f'p{i}']['historicalState']]})
            return len(users)
        done=0
        with futures.ThreadPoolExecutor(max_workers=2) as pool:
            for n in pool.map(batch,groups,buffersize=2):
                done+=n
                if done%200==0:print('batched histories',key,done,'/',sum(map(len,groups)),flush=True)
        summary.append({'market':key,'borrowers':len(first),'batched':done})
    return {'markets':summary}

def main():
    name=sys.argv[1];start=time.time();status={'source':'morpho_'+name,'started_at':start,'status':'running'}
    p.save(p.DATA/f'morpho_{name}_status.json',status)
    try:status.update(status='complete',result={'discover':discover,'liquidations':liquidations,'positions':positions,'positions_batched':positions_batched,'transactions':all_transactions,'transactions_scoped':transactions_scoped,'survivors':survivors,'survivors_fast':survivors_fast,'survivors_eth':lambda:survivors(chain_filter=1),'survivor_transactions':survivor_transactions}[name]())
    except Exception as exc:
        import traceback;traceback.print_exc();status.update(status='failed',error=str(exc))
    status.update(finished_at=time.time(),elapsed_s=time.time()-start)
    p.save(p.DATA/f'morpho_{name}_status.json',status);print(json.dumps(status),flush=True)
    if status['status']=='failed':raise SystemExit(1)

if __name__=='__main__':main()
