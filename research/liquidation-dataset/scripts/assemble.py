"""Offline Step 1 assembly and computed coverage report. Does not fit/replay anything."""
import ast
import bisect
import collections
import csv
import datetime as dt
import importlib.util
import json
from pathlib import Path
import time
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
spec=importlib.util.spec_from_file_location('pull',Path(__file__).with_name('pull.py'))
p=importlib.util.module_from_spec(spec);spec.loader.exec_module(p)

def read(path,default=None):return json.loads(path.read_text()) if path.exists() else default
def date(t):return dt.datetime.fromtimestamp(t,dt.timezone.utc).isoformat()
def table(frame):
    if frame.empty:return '(No rows.)\n'
    def text(v):return str(v).replace('|','\\|').replace('\n',' ')
    return '| '+' | '.join(map(text,frame.columns))+' |\n| '+' | '.join(['---']*len(frame.columns))+' |\n'+''.join('| '+' | '.join(map(text,row))+' |\n' for row in frame.itertuples(index=False,name=None))

def main():
    markets=read(p.DATA/'markets.json',[]);bykey={f'{m["chain"]["id"]}-{m["marketId"]}':m for m in markets}
    rows=[]
    for key,m in bykey.items():
        for r in read(p.RAW/'morpho_liquidations'/f'{key}.json',[]):
            d=r['data'];cd=m['collateralAsset']['decimals'];dd=m['loanAsset']['decimals']
            rows.append(dict(venue='Morpho Blue',chain=m['chain']['id'],market=m['marketId'],
                collateral_asset=m['collateralAsset']['symbol'],debt_asset=m['loanAsset']['symbol'],
                collateral_address=m['collateralAsset']['address'].lower(),debt_address=m['loanAsset']['address'].lower(),
                ts=int(r['timestamp']),block=int(r['blockNumber']),tx=r['txHash'],log=r['logIndex'],borrower=r['user']['address'].lower(),
                liquidator=d['liquidator'].lower(),debt_to_cover=int(d['repaidAssets'])/10**dd,
                collateral_seized=int(d['seizedAssets'])/10**cd,repaid_raw=str(d['repaidAssets']),seized_raw=str(d['seizedAssets']),
                repaid_shares=str(d['repaidShares']),bad_debt=int(d['badDebtAssets'])/10**dd,
                is_target_subset=True,decimals_known=True,decimals_source='Morpho API asset metadata'))
    original=p.ROOT.parent/'Voices on AI managed crypto loans/backtest_scripts/fetch_aave_liqs.py'
    tok={}
    for node in ast.parse(original.read_text()).body:
        if isinstance(node,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='TOK' for t in node.targets):tok=ast.literal_eval(node.value)
    unknown=set();aave_pages=sorted((p.RAW/'aave_logs').glob('*.json'))
    for file in aave_pages:
        for r in read(file,[]):
            c='0x'+r['topics'][1][-40:];d='0x'+r['topics'][2][-40:];data=r['data']
            cs,cd=tok.get(c,(c,None));ds,dd=tok.get(d,(d,None))
            if cd is None:unknown.add(c)
            if dd is None:unknown.add(d)
            stamp=int(r['blockTimestamp'],16) if r.get('blockTimestamp') else None
            rows.append(dict(venue='Aave V3',chain=1,market=p.POOL.lower(),collateral_asset=cs,debt_asset=ds,
                collateral_address=c,debt_address=d,ts=stamp,block=int(r['blockNumber'],16),tx=r['transactionHash'],log=int(r['logIndex'],16),
                borrower='0x'+r['topics'][3][-40:],liquidator='0x'+data[154:194],
                debt_to_cover=p.uint(data)/10**dd if dd is not None else None,
                collateral_seized=p.uint(data,1)/10**cd if cd is not None else None,
                repaid_raw=str(p.uint(data)),seized_raw=str(p.uint(data,1)),repaid_shares=None,bad_debt=None,
                is_target_subset=cs in ['WETH','wstETH','WBTC','cbBTC'] and ds in ['USDC','USDT'],decimals_known=cd is not None and dd is not None,decimals_source='Reference fetcher token map'))
    liqs=pd.DataFrame(rows).drop_duplicates(['chain','tx','log']).sort_values(['chain','ts','block','log'])
    # Preserve reference-table names where they have the same token-unit meaning.
    liqs['collateral']=liqs.collateral_asset
    liqs['debt']=liqs.debt_asset
    morpho_usdc=(liqs.venue=='Morpho Blue')&(liqs.debt_asset=='USDC')
    liqs['repaid_usdc']=liqs.debt_to_cover.where(morpho_usdc)
    liqs['bad_debt_usdc']=liqs.bad_debt.where(morpho_usdc)
    liqs['seized_cbbtc']=liqs.collateral_seized.where((liqs.venue=='Morpho Blue')&(liqs.collateral_asset=='cbBTC'))
    if (p.DATA/'aave_retry_status.json').exists():liqs['included_steps_2_4']=liqs.venue=='Morpho Blue'
    liqs.to_parquet(p.DATA/'liquidations.parquet',index=False)
    if (p.DATA/'aave_retry_status.json').exists():
        liqs[(liqs.venue=='Aave V3')&liqs.is_target_subset].to_parquet(p.DATA/'aave_target_subset_side_table.parquet',index=False)
    liqtimes=collections.defaultdict(list)
    for row in liqs.itertuples():
        if pd.notna(row.ts):liqtimes[(row.chain,row.market,row.borrower)].append(int(row.ts))
    for key in liqtimes:liqtimes[key].sort()
    prices=pd.read_parquet(p.DATA/'binance_1h_ext.parquet')
    # Hour-open price is observable at the position snapshot, unlike that hour's close.
    price_map={(r.symbol,int(r.open_time)//1000):float(r.open) for r in prices.itertuples()}
    ratios=pd.read_csv(p.DATA/'eth_ratios_ext.csv').sort_values('ts') if (p.DATA/'eth_ratios_ext.csv').exists() else pd.DataFrame(columns=['ts','wsteth_steth'])
    ratio_times=list(ratios.ts);ratio_values=list(ratios.wsteth_steth)
    hourly_schema=pa.schema([('venue',pa.string()),('chain',pa.int64()),('market',pa.string()),('borrower',pa.string()),
        ('ts',pa.int64()),('collateral_asset',pa.string()),('debt_asset',pa.string()),('collateral',pa.float64()),('debt',pa.float64()),
        ('ltv',pa.float64()),('oracle_px',pa.float64()),('binance_px',pa.float64()),('binance_underlying_px',pa.float64()),
        ('liquidated_within_6h',pa.bool_()),('liquidated_within_24h',pa.bool_()),('is_survivor_sample',pa.bool_()),
        ('followup_6h_observed',pa.bool_()),('followup_24h_observed',pa.bool_()),('missing_position_hour',pa.bool_()),
        ('label_window_missing_6h',pa.bool_()),('label_window_missing_24h',pa.bool_())])
    borrowers=[];quality=collections.Counter();hourcounts=collections.Counter();survivorcounts=collections.Counter();buffer=[]
    out=p.DATA/'positions_hourly.parquet';tmp=p.DATA/'positions_hourly.parquet.tmp'
    with pq.ParquetWriter(tmp,hourly_schema,compression='zstd') as writer:
        for key,m in bykey.items():
            selected=set(read(p.DATA/'survivor_selection'/f'{key}.json',{}).get('selected_borrowers',[]))
            paths={f.stem:f for f in (p.RAW/'morpho_positions'/key).glob('*.json')}
            for user in sorted(set(paths)|selected):
                contexts=[]
                if user in paths:contexts.append((read(paths[user]),False))
                if user in selected:contexts.append((read(p.RAW/'morpho_survivors'/key/f'{user}.json'),True))
                times=liqtimes.get((m['chain']['id'],m['marketId'],user),[])
                hourly={}
                for context,is_survivor in contexts:
                    for page in context['history']:
                        series={name:{int(x['x']):x['y'] for x in values or []} for name,values in page.items()}
                        stamps=sorted(set().union(*(set(x) for x in series.values())))
                        for t in stamps:
                            if not context['start']<=t<context['end_exclusive']:continue
                            c=series.get('collateral',{}).get(t);debt=series.get('borrowAssets',{}).get(t);cv=series.get('collateralValue',{}).get(t)
                            if any(v is None for v in [c,debt,cv]):quality['missing_state_observations']+=1;continue
                            c=int(c)/10**m['collateralAsset']['decimals'];debt=int(debt)/10**m['loanAsset']['decimals'];cv=int(cv)/10**m['loanAsset']['decimals']
                            ltv=debt/cv if cv>0 else None;oracle=cv/c if c>0 else None
                            asset=m['collateralAsset']['symbol'];symbol='BTCUSDT' if asset=='cbBTC' else 'ETHUSDT'
                            price=price_map.get((symbol,t));bp=price
                            if asset=='wstETH':
                                ri=bisect.bisect_right(ratio_times,t)-1
                                bp=price*ratio_values[ri] if price is not None and ri>=0 else None
                            if bp is None:quality['missing_binance_px_observations']+=1
                            if oracle is None:quality['zero_collateral_oracle_px_observations']+=1
                            idx=bisect.bisect_right(times,t);nextliq=times[idx] if idx<len(times) else None
                            six=nextliq is not None and nextliq<=t+6*3600;day=nextliq is not None and nextliq<=t+24*3600
                            sixobs=t+6*3600<p.END;dayobs=t+24*3600<p.END
                            hourly[t]=dict(venue='Morpho Blue',chain=m['chain']['id'],market=m['marketId'],borrower=user,ts=t,
                                collateral_asset=asset,debt_asset=m['loanAsset']['symbol'],collateral=c,debt=debt,ltv=ltv,
                                oracle_px=oracle,binance_px=bp,binance_underlying_px=price,
                                liquidated_within_6h=six if six or sixobs else None,liquidated_within_24h=day if day or dayobs else None,
                                is_survivor_sample=is_survivor or hourly.get(t,{}).get('is_survivor_sample',False),
                                followup_6h_observed=sixobs,followup_24h_observed=dayobs)
                expected=set()
                for context,_ in contexts:expected.update(range((context['start']+3599)//3600*3600,context['end_exclusive'],3600))
                missing=expected-set(hourly);valid_count=len(hourly)
                quality['expected_hourly_rows']+=len(expected)
                quality['missing_hourly_rows']+=len(missing)
                for t in missing:
                    hourly[t]=dict(venue='Morpho Blue',chain=m['chain']['id'],market=m['marketId'],borrower=user,ts=t,
                        collateral_asset=m['collateralAsset']['symbol'],debt_asset=m['loanAsset']['symbol'],
                        is_survivor_sample=any(s and c['start']<=t<c['end_exclusive'] for c,s in contexts),
                        followup_6h_observed=t+6*3600<p.END,followup_24h_observed=t+24*3600<p.END)
                for t,row in hourly.items():
                    row['missing_position_hour']=t in missing
                    for h in (6,24):
                        gap=any(s in missing for s in range(t,t+h*3600+1,3600))
                        row[f'label_window_missing_{h}h']=gap
                        if gap:row[f'liquidated_within_{h}h']=None
                selection='liquidated_and_survivor' if len(contexts)==2 else contexts[0][0]['selection']
                anchor=contexts[-1][0]['anchor_ts'] if user in selected else None
                if anchor is not None:survivorcounts[(m['chain']['id'],m['marketId'],dt.datetime.fromtimestamp(anchor,dt.timezone.utc).year)]+=1
                borrowers.append(dict(venue='Morpho Blue',chain=m['chain']['id'],market=m['marketId'],borrower=user,
                    collateral_asset=m['collateralAsset']['symbol'],debt_asset=m['loanAsset']['symbol'],selection=selection,
                    first_liq_ts=min(times) if times else None,is_survivor_sample=user in selected,seed=p.SEED if user in selected else None,survivor_anchor_ts=anchor,
                    hourly_rows=len(hourly),valid_hourly_rows=valid_count,expected_hourly_rows=len(expected),missing_hourly_rows=len(missing)))
                for t,row in hourly.items():hourcounts[(m['chain']['id'],m['marketId'],dt.datetime.fromtimestamp(t,dt.timezone.utc).year)]+=1
                buffer.extend(hourly.values())
                if len(buffer)>=50000:
                    writer.write_table(pa.Table.from_pylist(buffer,schema=hourly_schema));buffer=[]
        if buffer:writer.write_table(pa.Table.from_pylist(buffer,schema=hourly_schema))
    tmp.replace(out)
    borrower_cols=['venue','chain','market','borrower','collateral_asset','debt_asset','selection','first_liq_ts','is_survivor_sample','seed','survivor_anchor_ts','hourly_rows','valid_hourly_rows','expected_hourly_rows','missing_hourly_rows']
    bd=pd.DataFrame(borrowers,columns=borrower_cols)
    for col in ['first_liq_ts','seed','survivor_anchor_ts']:bd[col]=pd.array(bd[col],dtype='Int64')
    bd.to_parquet(p.DATA/'borrowers.parquet',index=False)
    owner_count=0
    if (p.RAW/'morpho_transactions.sqlite').exists():
        import sqlite3
        db=sqlite3.connect(p.RAW/'morpho_transactions.sqlite',timeout=120)
        db.execute('PRAGMA temp_store=MEMORY')
        db.execute('CREATE TEMP TABLE selected_borrowers(chain INTEGER,market TEXT,borrower TEXT,PRIMARY KEY(chain,market,borrower))')
        db.executemany('INSERT INTO selected_borrowers VALUES(?,?,?)',[(int(r.chain),r.market,r.borrower) for r in bd.itertuples()])
        schema=pa.schema([('venue',pa.string()),('chain',pa.int64()),('market',pa.string()),('borrower',pa.string()),('ts',pa.int64()),('tx',pa.string()),('log',pa.int64()),('type',pa.string()),('payload',pa.string())])
        with pq.ParquetWriter(p.DATA/'owner_transactions.parquet',schema,compression='zstd') as writer:
            cursor=db.execute('SELECT e.chain,e.market,e.borrower,e.ts,e.tx,e.log,e.kind,e.payload FROM events e JOIN selected_borrowers b ON e.chain=b.chain AND e.market=b.market AND e.borrower=b.borrower ORDER BY e.chain,e.market,e.borrower,e.ts,e.log')
            while chunk:=cursor.fetchmany(10000):
                records=[dict(zip(['venue','chain','market','borrower','ts','tx','log','type','payload'],['Morpho Blue',*row])) for row in chunk]
                writer.write_table(pa.Table.from_pylist(records,schema=schema));owner_count+=len(records)
        db.close()
    oracle=[]
    for f in (p.RAW/'chainlink').rglob('*.json'):
        if f.name!='bounds.json':oracle.extend(read(f,[]))
    oracle.extend(read(p.DATA/'eth_oracle_direct.json',[]))
    oracle.extend(read(p.DATA/'eth_oracle_standard.json',[]))
    od=pd.DataFrame(oracle)
    if len(od):od=od.drop_duplicates(['chain','aggregator','round_id']).sort_values(['chain','feed','updated_at','round_id'])
    standards={'0x5f4ec3df9cbd43714fe2740f5e3616155c5b8419','0xf4030086522a5beea4988f8ca5b36dbc97bee88c'}
    if len(od):od['source_kind']=['standard' if str(x).lower() in standards else ('Aave source' if chain=='eth' else 'Base proxy') for chain,x in zip(od.chain,od.proxy)]
    if len(od):od.loc[od.feed.str.contains('Morpho'),'source_kind']='Morpho market feed'
    phase_bounds=read(p.DATA/'oracle_phase_bounds.json',[])
    phase_lookup={(r['chain'],r['proxy'].lower(),r['phase']):r for r in phase_bounds}
    if phase_bounds:
        od['usable_for_replay']=[bool((b:=phase_lookup.get((r.chain,str(r.proxy).lower(),r.phase))) and b['usable_from']<=r.updated_at<b['usable_until_exclusive']) for r in od.itertuples()]
        od['unusable_reason']=['' if ok else 'Unresolved phase activation, overlap, independent Aave source, or outside window' for ok in od.usable_for_replay]
    od.to_csv(p.DATA/'oracle_rounds_ext.csv',index=False)
    counts=[]
    for key,m in bykey.items():
        subset=liqs[(liqs.chain==m['chain']['id'])&(liqs.market==m['marketId'])]
        for year in range(2024,2027):
            yr=pd.to_datetime(subset.ts,unit='s',utc=True).dt.year
            events=subset[yr==year]
            counts.append({'venue':'Morpho','chain':m['chain']['id'],'market':m['marketId'],'pair':m['collateralAsset']['symbol']+'/USDC','year':year,
                'liquidations':len(events),'liquidated_borrowers':events.borrower.nunique(),'survivor_anchors':survivorcounts[(m['chain']['id'],m['marketId'],year)],'hourly_rows':hourcounts[(m['chain']['id'],m['marketId'],year)],
                'coverage':'before creation' if year<dt.datetime.fromtimestamp(int(m['creationTimestamp']),dt.timezone.utc).year else 'liquidations complete; histories see coverage'})
    av=liqs[liqs.venue=='Aave V3']
    for year in [2025,2026]:
        events=av[pd.to_datetime(av.ts,unit='s',utc=True).dt.year==year]
        for label,df in [('all assets',events),('target subset',events[events.is_target_subset])]:
            counts.append({'venue':'Aave','chain':1,'market':p.POOL.lower(),'pair':label,'year':year,'liquidations':len(df),'liquidated_borrowers':df.borrower.nunique(),'survivor_anchors':0,'hourly_rows':0,'coverage':'INCOMPLETE LOG WINDOW'})
    counts=pd.DataFrame(counts);counts.to_csv(p.DATA/'counts_market_year.csv',index=False)
    oracle_counts=od.groupby(['chain','feed','source_kind']).agg(rounds=('round_id','size'),first_timestamp=('updated_at','min'),last_timestamp=('updated_at','max')).reset_index() if len(od) else pd.DataFrame()
    oracle_counts.to_csv(p.DATA/'counts_oracles.csv',index=False)
    states=[read(f) for f in sorted(p.DATA.glob('*status.json'))]
    timing=pd.DataFrame([{'source':s['source'],'status':s['status'],'last_run_seconds':round(s.get('elapsed_s',time.time()-s['started_at']),2),'error':s.get('error','')} for s in states])
    timing.to_csv(p.DATA/'source_times.csv',index=False)
    # The last invocation overwrites its status; cached log-request timestamps
    # recover the earlier Aave observation interval without claiming CPU time.
    aave_observations=[]
    for f in (p.RAW/'rpc_eth').rglob('*.json'):
        meta=read(f,{})
        if meta.get('request',{}).get('method')=='eth_getLogs':
            stamp=meta.get('retrieved_at',meta.get('at'))
            if stamp is not None:aave_observations.extend([stamp-meta.get('elapsed_s',0),stamp])
    source_spans=[]
    for source,predicate in [('Morpho',lambda n:n.startswith('morpho_')),('Chainlink',lambda n:n in ['oracles','eth_oracles','legacy_oracles','phase_intervals']),('Aave logs',lambda n:n=='aave'),('Binance',lambda n:n=='binance'),('Wrapper ratios',lambda n:n=='ratios')]:
        jobs=[s for s in states if predicate(s['source'])]
        if jobs:
            began=min(s['started_at'] for s in jobs);finished=max(s.get('finished_at',time.time()) for s in jobs)
            if source=='Aave logs' and aave_observations:
                began=min(began,min(aave_observations));finished=max(finished,max(aave_observations))
            source_spans.append({'source':source,'wall_span_seconds':round(finished-began,2),'active_workers':sum(s['status']=='running' for s in jobs)})
    spans=pd.DataFrame(source_spans);spans.to_csv(p.DATA/'source_wall_spans.csv',index=False)
    bounds=read(p.DATA/'aave_bounds.json',{});expected=((bounds.get('to_block',-1)-bounds.get('from_block',0))//2000+1) if bounds else 0
    missing_ranges=[]
    if bounds:
        for a in range(bounds['from_block'],bounds['to_block']+1,2000):
            b=min(a+1999,bounds['to_block'])
            if not (p.RAW/'aave_logs'/f'{a}-{b}.json').exists():missing_ranges.append({'from_block':a,'to_block':b})
    pd.DataFrame(missing_ranges,columns=['from_block','to_block']).to_csv(p.DATA/'aave_missing_ranges.csv',index=False)
    quality.update(hourly_rows=sum(hourcounts.values()),borrowers=len(bd),liquidations=len(liqs),aave_cached_pages=len(aave_pages),aave_expected_pages=expected,owner_transactions=owner_count)
    p.save(p.DATA/'quality.json',dict(quality,unknown_aave_token_addresses=sorted(unknown)))
    import platform
    runtime={'python':platform.python_version(),'pandas':pd.__version__,'pyarrow':pa.__version__}
    p.save(p.DATA/'runtime.json',runtime)
    market_table=pd.DataFrame([{'chain':m['chain']['id'],'pair':m['collateralAsset']['symbol']+'/USDC','market':m['marketId'],'created_utc':date(int(m['creationTimestamp'])),'borrow_USDC_at_selection':int(m['state']['borrowAssets'])/10**6,'candidate_markets':m['candidate_count']} for m in markets])
    sampled=bd.groupby(['chain','market','selection']).agg(borrowers=('borrower','size'),hourly_rows=('hourly_rows','sum')).reset_index() if len(bd) else pd.DataFrame()
    if len(sampled):
        sampled['pair']=[bykey[f'{c}-{mk}']['collateralAsset']['symbol']+'/USDC' for c,mk in zip(sampled.chain,sampled.market)]
    sampled.to_csv(p.DATA/'counts_samples.csv',index=False)
    sample_display=sampled.drop(columns=['market']) if len(sampled) else sampled
    survivor_summary=pd.DataFrame([dict(chain=m['chain']['id'],pair=m['collateralAsset']['symbol']+'/USDC',**{k:v for k,v in read(p.DATA/'survivor_selection'/f'{key}.json',{}).items() if k in ['population','screened','selected','seed']}) for key,m in bykey.items()])
    oracle_display=oracle_counts.copy()
    if len(oracle_display):
        oracle_display['first_utc']=oracle_display.first_timestamp.map(date)
        oracle_display['last_utc']=oracle_display.last_timestamp.map(date)
        oracle_display=oracle_display.drop(columns=['first_timestamp','last_timestamp'])
    phase_status='Recorded in `data/oracle_phase_intervals.json`.' if (p.DATA/'oracle_phase_intervals.json').exists() else 'Phase activation intervals are still missing: Base archive resolution hit HTTP 429 on drpc and HTTP 403 on PublicNode. This metadata pull stopped after both endpoints failed.'
    status='INCOMPLETE — Aave source blocked; not ready for Step 2'
    if any(s['status']=='running' for s in states):status+='; other pulls still running'
    text=f'''# Liquidation dataset — Step 1 pull

Status: **{status}**. Generated offline at {date(time.time())}. No features, calibration, retrieval, model, or minute-price pulls have run.

## Requested coverage and selected markets

The event cutoff is {date(p.END)} exclusive. Aave starts {date(p.AAVE_START)}. Morpho starts at each market's recorded creation. Price pulls start {date(p.START)}.

Rows: `data/markets.json`; snapshots and schema are cached under `data/raw/morpho/` and `data/raw/morpho_schema/`.

{table(market_table)}
The Ethereum WETH market has no pre-2026 history. Selection follows the brief's highest-current-borrow rule; it is not a historical market-universe selection.

## Counts by market and year

Rows: `data/liquidations.parquet`, grouped by event timestamp year; `data/positions_hourly.parquet`, grouped by snapshot year; survivor anchors from `data/borrowers.parquet`, grouped by the qualifying-hour year. Liquidated-borrower counts are unique within each market-year, not additive across years. Zero counts in incomplete windows do not establish absence of events.

{table(counts.drop(columns=['market']))}
## Sample and hourly coverage

Rows: `data/borrowers.parquet`, grouped by market and selection. Morpho liquidation histories cover every liquidated borrower, with the requested first-liquidation window; missing API hours are counted below. Aave sampling is blocked by the incomplete event population. No Aave survivor claims are made from partial logs.

{table(sample_display)}
Survivor draw results from `data/survivor_selection/*.json`; population is the complete historical Borrow-event borrower census, including closed accounts. Some selected survivors also have a liquidation outside their qualifying 30-day episode.

{table(survivor_summary)}
Total hourly rows: {quality['hourly_rows']:,}; sampled borrowers: {len(bd):,}. Requested hours missing usable API state: {quality['missing_hourly_rows']:,} of {quality['expected_hourly_rows']:,}. Aave log pages cached: {len(aave_pages):,} of {expected:,}. Unknown Aave asset decimals: {len(unknown)} assets; amounts in native units remain null where decimals are unverified, with raw integers preserved.

## Prices

Rows: `data/binance_1h_ext.parquet`: {len(prices):,} candles, BTCUSDT and ETHUSDT, interval HOUR. Existing rows were copied to `data/raw/binance_existing.parquet`; every new HTTP page is cached.

Rows: `data/oracle_rounds_ext.csv`, grouped below. Boundary rounds can precede the start or follow the exclusive cutoff so an as-of consumer has a bracket. RPC responses are cached individually; decoded groups are cached per requested round chunk.

{table(oracle_display)}
The newer Aave-source aggregators begin in March 2025. The standard Ethereum proxies provide the earlier history. Keep independent proxies separate; `source_kind` identifies standard feeds, newer Aave sources, and Base proxies. Raw round histories may include rounds published before a phase was activated. Do not treat those as active proxy observations before their phase interval. {phase_status} Historical wrapper-ratio samples: {len(ratios):,}, in `data/eth_ratios_ext.csv`.

## Time and failures

Rows: `data/*_status.json` (last invocation per source, including network waits; sources overlap in wall time, so do not add them). Original cached response timestamps preserve provenance across restarts. Timing table: `data/source_times.csv`.

{table(timing.drop(columns=['error']))}
Recorded wall spans by source, including handoff/setup gaps and overlapping jobs (not summed request or CPU time): rows from `data/source_wall_spans.csv`. These span retained mode statuses; Aave also uses cached request/error timestamps to recover its earlier work. Earlier overwritten invocations may not be fully represented, so these are timing lower bounds rather than an exact total across every restart.

{table(spans)}
Aave: MEV Blocker exhausted bounded retries after HTTP 429; the Flashbots fallback reported `pruned history unavailable`. Stopped that source after both configured endpoints failed. Complete log coverage, account population, hourly samples, and owner-event reconstruction remain outstanding. `data/aave_missing_ranges.csv` lists {len(missing_ranges):,} missing block steps, including any internal gaps; minimum/maximum event timestamps do not imply uninterrupted coverage.

Morpho: the inspected schema exposes `marketId` instead of the old `uniqueKey` output field. A mixed-case address was rejected and normalized. Offset pagination rejected `skip=11000`; the collector now continues with the schema's transaction cursor and checks returned unique event counts against the reported total. The API's address-list limit required owner groups of at most 100. A batch-query formatting error was corrected, and the server's alias-limit response capped batches at 30 selections as well as the advertised time-series point budget. Concurrent local cache indexing initially hit a SQLite reader lock; WAL mode and a longer busy timeout resolved it. Any HTTP rate-limit responses are preserved under `data/raw/morpho/errors/`. Successful pages are never refreshed during a resume.

Chainlink: the first Ethereum phase lookup returned addresses that yielded empty round data. The original direct-aggregator approach works for the newer aggregators. Starting standard-proxy enumeration at an ancient pre-window phase also failed; resolving the phase active at the window start fixed that reader. The standard proxies' cached phases supply the older observations.

Environment: the initial Python lacked pandas/pyarrow, and sandboxed network calls could not resolve hosts. A local environment under `scripts/.venv/` supplies the table libraries; the public-network commands then ran with runtime network permission. No credential files were read.

## Assumptions and interpretation

- [ASSUMPTION] “To 25 Oct 2026” includes the whole UTC day, represented by the exclusive cutoff above.
- [ASSUMPTION] A common price window starts at the beginning of 2024, before every selected market's creation. Extra prefix prices are retained.
- [ASSUMPTION] Highest borrow means current `borrowAssets` among the exact collateral/USDC token addresses, sorted descending at the cached selection snapshot. All loan assets have the same decimals; listed-only filtering is not imposed.
- [ASSUMPTION] Stablecoin loan units stand in for USD when deriving `oracle_px = collateralValue / collateral` and LTV from Morpho's hourly API state. The raw response retains integer amounts.
- [ASSUMPTION] Binance BTCUSDT represents cbBTC's underlying BTC; ETHUSDT represents WETH. Use the candle open at the exact snapshot hour, avoiding that hour's future close.
- [ASSUMPTION] wstETH Binance prices equal ETHUSDT times the most recent historical stETH share ratio, sampled with the original reader's block spacing and held until the next sample. This assumes stETH/ETH parity and omits any secondary-market discount. `binance_underlying_px` preserves unconverted ETHUSDT. No future ratio sample is used.
- [ASSUMPTION] Liquidated borrower windows are clipped to market creation and the dataset cutoff. No state is invented for missing API hours. Zero-collateral rows have null LTV/oracle unit price.
- [ASSUMPTION] API time-series timestamps denote the hourly state used for labels; no interpolation or future-hour close is substituted. Survivor eligibility is based on observed API hours and requires positive collateral value; missing hours and zero-value collateral do not establish eligibility.
- [ASSUMPTION] Survivor sampling uses a sorted historical Borrow-event population shuffled with seed {p.SEED}; it accepts borrowers with a >70% hourly LTV and no liquidation in the following complete 30 days, stopping at 2,000 eligible borrowers. Closed accounts remain in the population. The survivor history window is 14 days before through 7 days after the qualifying hour. A later liquidation beyond those 30 days does not disqualify that earlier survival episode.
- [ASSUMPTION] A sample is a borrower within a venue/chain/market; one address on different markets is a different position. Overlapping liquidated/survivor hours are merged by position and timestamp.
- [ASSUMPTION] Liquidation labels refer to events strictly after the snapshot and within the inclusive horizon. Right-censored negatives are null; follow-up-observed flags distinguish them.
- [ASSUMPTION] Aave token decimals are taken only from the earlier fetcher's explicit token map. Unrecognized assets retain addresses and raw amounts instead of assuming decimals.

## Outputs and reproducibility

All writes stay in this folder. Existing backtest files are read-only. Network data are anonymous public reads; no paid source or agentcash is used. Runtime: Python {runtime['python']}, pandas {runtime['pandas']}, PyArrow {runtime['pyarrow']}; Python's bounded executor-map API is required. Dependency versions are pinned in `scripts/requirements.txt`. Run from the repository root, with Python isolation enabled:

```sh
python3 -I 'research_notes/Liquidation dataset/scripts/pull.py' oracles > 'research_notes/Liquidation dataset/data/oracles.log' 2>&1 &
python3 -I 'research_notes/Liquidation dataset/scripts/pull.py' aave > 'research_notes/Liquidation dataset/data/aave.log' 2>&1 &
python3 -I 'research_notes/Liquidation dataset/scripts/morpho.py' discover
python3 -I 'research_notes/Liquidation dataset/scripts/morpho.py' liquidations
python3 -I 'research_notes/Liquidation dataset/scripts/morpho.py' positions_batched
python3 -I 'research_notes/Liquidation dataset/scripts/morpho.py' transactions_scoped
python3 -I 'research_notes/Liquidation dataset/scripts/morpho.py' survivors_fast
python3 -I 'research_notes/Liquidation dataset/scripts/morpho.py' survivor_transactions
python3 -I 'research_notes/Liquidation dataset/scripts/pull.py' eth_oracles
python3 -I 'research_notes/Liquidation dataset/scripts/pull.py' legacy_oracles
python3 -I 'research_notes/Liquidation dataset/scripts/pull.py' phase_intervals
python3 -I 'research_notes/Liquidation dataset/scripts/pull.py' ratios
'research_notes/Liquidation dataset/scripts/.venv/bin/python3' -I 'research_notes/Liquidation dataset/scripts/binance.py'
'research_notes/Liquidation dataset/scripts/.venv/bin/python3' -I 'research_notes/Liquidation dataset/scripts/assemble.py'
'research_notes/Liquidation dataset/scripts/.venv/bin/python3' -I 'research_notes/Liquidation dataset/scripts/validate.py'
```

Launch the first two commands as separate background jobs. Run only one worker per source/mode; finish pull jobs before the final assembly. A completed request cache is keyed by URL and exact JSON payload. HTTP/GraphQL failures are stored separately and are not treated as successful empty pages. Atomic file replacement protects completed pages. For rate limits, resume the same command after the provider's cooldown. Do not keep trying alternate sources after the brief's stopping limit.

`liquidations.parquet` has one row per `(chain, tx, log)`, including the Aave target-subset flag and unscaled raw values. `debt_to_cover` and `collateral_seized` are the common token-unit amounts; reference names `repaid_usdc`, `bad_debt_usdc`, and `seized_cbbtc` are retained where applicable. `collateral`/`debt` are legacy symbol aliases in this event table. Chain identifiers are EVM chain IDs; timestamps are Unix seconds UTC; raw integer amounts/shares are strings to avoid precision loss. `positions_hourly.parquet` has one row per `(chain, market, borrower, ts)`, with numeric collateral/debt balances. `borrowers.parquet` records selected history coverage; it is not the full liquidation population. `owner_transactions.parquet` contains {owner_count:,} cached events for the selected Morpho borrowers, retaining source JSON and raw token units; completeness depends on the scoped owner-pull statuses. The SQLite index under `data/raw/` preserves reused full-market pages plus subsequent borrower-census and sampled-owner pages. The raw liquidation extracts are complete for the four Morpho markets, independent of hourly-history completeness. `quality.json` records gaps. Processed artifacts may be regenerated entirely offline.

## Decisions before Step 2

- Do not start calibration until the incomplete pulls and population/label checks have been resolved, or explicitly approve a reduced scope with the coverage limitations above.
- Decide whether to retain the newly created Ethereum WETH market as holdout-only, omit it, or authorize an older market in addition to the required highest-current-borrow choice.
- Decide how to handle Aave's blocked full-window logs and therefore unavailable unbiased samples; a partial early-window account sample cannot substitute for the requested population.
- Decide whether the stETH/ETH parity assumption is acceptable for the wstETH price comparison; market discounts need a separate price series if they matter to the replay.
- Resolve missing proxy phase-activation boundaries before treating stored aggregator rounds as the live oracle path.
- Decide whether to accept the smaller Ethereum wstETH survivor cohort shown above; its complete borrower census is exhausted. The new Ethereum WETH market cannot supply a full 30-day survivor window by this cutoff.
- Decide whether to exclude affected position windows or backfill the missing API hours listed in `data/borrowers.parquet`; they were not imputed.

The selected feeds can be compared with Chainlink's [ETH/USD directory](https://data.chain.link/feeds/ethereum/mainnet/eth-usd). On-chain response provenance, not that live directory, determines the cached observations.
'''
    (p.ROOT/'README.md').write_text(text)
    print(json.dumps(dict(quality),indent=2));print(counts.to_string(index=False))

if __name__=='__main__':main()
