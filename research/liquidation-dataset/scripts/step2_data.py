"""Step 2 public market/price pulls and causal hourly features; no Step 3 work."""
import concurrent.futures as cf
import importlib.util
import json
import math
from pathlib import Path
import sqlite3
import sys
import time
import urllib.parse
import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

spec=importlib.util.spec_from_file_location('closure',Path(__file__).with_name('close_step1.py'))
c=importlib.util.module_from_spec(spec);spec.loader.exec_module(c)
p=c.p;m=c.m
SPLIT=1767225600
def read(f):return json.loads(f.read_text())
def save(f,x):p.save(f,x)

def market_history():
    query='''query($m:String!,$c:Int!,$o:TimeseriesOptions){marketById(marketId:$m,chainId:$c){historicalState{utilization(options:$o){x y} borrowApy(options:$o){x y} borrowAssets(options:$o){x y} borrowShares(options:$o){x y}}}}'''
    counts={}
    for market in read(p.DATA/'markets.json'):
        key=f'{market["chain"]["id"]}-{market["marketId"]}';pages=[]
        for a in range(int(market['creationTimestamp'])//3600*3600,p.END,90*86400):
            b=min(a+90*86400,p.END)
            page=m.gql(query,{'m':market['marketId'],'c':market['chain']['id'],'o':{'startTimestamp':a,'endTimestamp':b-1,'interval':'HOUR'}})['marketById']['historicalState']
            pages.append(page)
        save(p.RAW/'market_history'/f'{key}.json',pages);counts[key]=sum(len(x['utilization']) for x in pages)
        print('market history',key,counts[key],flush=True)
    return counts

def case_plan():
    markets=read(p.DATA/'markets.json');plans=[]
    for market in markets:
        key=f'{market["chain"]["id"]}-{market["marketId"]}'
        selected=set(read(p.DATA/'survivor_selection'/f'{key}.json')['selected_borrowers'])
        contexts=[(f.stem,'liquidated',read(f)) for f in sorted((p.RAW/'morpho_positions'/key).glob('*.json'))]
        contexts += [(u,'survivor',read(p.RAW/'morpho_survivors'/key/f'{u}.json')) for u in sorted(selected)]
        for user,kind,context in contexts:
            anchor=context['first_liq_ts'] if kind=='liquidated' else context['anchor_ts']
            start=max((anchor-3*86400)//3600*3600,math.ceil(context['start']/3600)*3600)
            end=min((anchor+3*86400)//3600*3600,((context['end_exclusive']-1)//3600)*3600)
            states={}
            for page in context['history']:
                maps={k:{int(x['x']):x['y'] for x in v or []} for k,v in page.items()}
                for t in range(start,end+1,3600):
                    vals=[maps.get(k,{}).get(t) for k in ('collateral','borrowAssets','collateralValue')]
                    if all(x is not None for x in vals):states[t]=[int(x) for x in vals]
            missing=sum(t not in states for t in range(start,end+1,3600))
            split='train' if anchor<SPLIT else 'holdout';reason=''
            if missing:reason='missing_position_hours'
            elif start<SPLIT<=end or (split=='train' and kind=='survivor' and anchor+30*86400>=SPLIT):reason='split_boundary_or_followup_crosses_2026'
            elif end<=start:reason='empty_window'
            row=dict(case_id=f'{key}-{user}-{kind}',chain=market['chain']['id'],market=market['marketId'],borrower=user,selection=kind,asset=market['collateralAsset']['symbol'],
                     asset_group='cbBTC' if market['collateralAsset']['symbol']=='cbBTC' else 'ETH-type',split=split,anchor_ts=anchor,t0=start,t_end=end,
                     missing_hours=missing,excluded_reason=reason,included=not reason,lltv=int(market['lltv'])/1e18)
            if start in states:row.update(C0=states[start][0]/10**market['collateralAsset']['decimals'],D0=states[start][1]/1e6,oracle_start=states[start][2]/1e6/(states[start][0]/10**market['collateralAsset']['decimals']) if states[start][0] else None)
            if end in states:row.update(C_expected_end=states[end][0]/10**market['collateralAsset']['decimals'],D_expected_end=states[end][1]/1e6)
            plans.append(row)
    plan=pd.DataFrame(plans)
    # The selected cohort is retained; overlapping replay episodes are counted once.
    for _,g in plan[plan.included].groupby(['chain','market','borrower'],sort=False):
        liquidated=g[g.selection=='liquidated']
        if liquidated.empty:continue
        for idx,s in g[g.selection=='survivor'].iterrows():
            if any(max(s.t0,l.t0)<=min(s.t_end,l.t_end) for l in liquidated.itertuples()):
                plan.loc[idx,['included','excluded_reason']]=[False,'overlaps_liquidation_episode']
    plan.to_parquet(p.DATA/'replay_case_plan.parquet',index=False)
    counts=plan.groupby(['split','asset','selection','excluded_reason'],dropna=False).size().rename('cases').reset_index();counts.to_csv(p.DATA/'replay_case_counts.csv',index=False)
    print(counts.to_string(index=False),flush=True);return {'planned':len(plan),'included':int(plan.included.sum())}

def minute_prices():
    plan=pd.read_parquet(p.DATA/'replay_case_plan.parquet');plan=plan[plan.included]
    columns=['open_time','open','high','low','close','volume','close_time','quote_volume','trades','tb_base','tb_quote','ignore']
    original=p.ROOT.parent/'liquidation-backtest/backtest_data/binance_1m.parquet'
    old=pd.read_parquet(original);old=old[old.symbol.isin(['BTCUSDT','ETHUSDT'])].copy()
    if (p.DATA/'binance_1m_ext.parquet').exists():old=pd.concat([old,pd.read_parquet(p.DATA/'binance_1m_ext.parquet')]).drop_duplicates(['symbol','open_time'])
    raw=p.RAW/'binance_existing_1m.parquet'
    if not raw.exists():old.to_parquet(raw,index=False)
    tasks=[];wanted={};intervals=[]
    for symbol,assets in [('BTCUSDT',['cbBTC']),('ETHUSDT',['WETH','wstETH'])]:
        covered=set()
        # A complete previous minute is needed for the causal first decision.
        for r in plan[plan.asset.isin(assets)].itertuples():covered.update(range(r.t0-60,r.t_end+60,60))
        wanted[symbol]=covered
        known=set((old.loc[old.symbol==symbol,'open_time']//1000).astype(int))
        missing=sorted(covered-known);i=0
        while i<len(missing):
            a=missing[i];j=i+1
            while j<len(missing) and j-i<1000 and missing[j]==missing[j-1]+60:j+=1
            tasks.append((symbol,a,missing[j-1]+60));i=j
        intervals.append({'symbol':symbol,'requested_minutes':len(covered),'reused_minutes':len(covered&known)})
    def fetch(task):
        symbol,a,b=task;query=urllib.parse.urlencode({'symbol':symbol,'interval':'1m','startTime':a*1000,'endTime':b*1000-1,'limit':1000})
        values=p.request('binance_1m','https://api.binance.com/api/v3/klines?'+query)
        assert len(values)==(b-a)//60,(task,len(values))
        rows=[]
        for v in values:
            r=dict(zip(columns,v));r.update(symbol=symbol,interval='1m');rows.append(r)
        return rows
    rows=[]
    with cf.ThreadPoolExecutor(max_workers=3) as pool:
        for i,batch in enumerate(pool.map(fetch,tasks,buffersize=6)):
            rows.extend(batch)
            if i%100==0:print('minute pages',i,'/',len(tasks),flush=True)
    keep=['open_time','open','high','low','close','volume','quote_volume','trades','symbol','interval']
    new=pd.DataFrame(rows,columns=columns+['symbol','interval'])
    out=pd.concat([old[keep],new[keep]],ignore_index=True).drop_duplicates(['symbol','open_time'])
    out=out[[int(t)//1000 in wanted[s] for s,t in zip(out.symbol,out.open_time)]].copy()
    for name in ['open','high','low','close','volume','quote_volume']:out[name]=out[name].astype(float)
    out['open_time']=out.open_time.astype('int64');out['trades']=out.trades.astype('int64')
    out=out.sort_values(['symbol','open_time']);out.to_parquet(p.DATA/'binance_1m_ext.parquet',index=False)
    assert len(out)==sum(len(x) for x in wanted.values())
    save(p.DATA/'minute_price_coverage.json',intervals);return {'rows':len(out),'pages':len(tasks),'coverage':intervals}

def features():
    markets=read(p.DATA/'markets.json');lookup={(x['chain']['id'],x['marketId']):x for x in markets}
    histories={}
    for key,market in lookup.items():
        maps={}
        for page in read(p.RAW/'market_history'/f'{key[0]}-{key[1]}.json'):
            for name,series in page.items():maps.setdefault(name,{}).update({int(x['x']):x['y'] for x in series})
        histories[key]=maps
    prices=pd.read_parquet(p.DATA/'binance_1h_ext.parquet');price_maps={}
    for symbol,g in prices.groupby('symbol'):
        g=g.sort_values('open_time');t=(g.open_time//1000).to_numpy();price=g.open.to_numpy();returns=np.log(price[1:]/price[:-1])
        frame=pd.DataFrame({'ts':t,'price':price})
        for h in [1,6,24]:frame[f'price_change_{h}h']=frame.price.pct_change(h)
        frame['realized_volatility_24h']=pd.Series(np.r_[np.nan,returns]).rolling(24,min_periods=24).std(ddof=1)
        price_maps[symbol]=frame.set_index('ts')
    ratios=pd.read_csv(p.DATA/'eth_ratios_ext.csv').sort_values('ts')
    # wstETH return/volatility uses the exchange-rate-adjusted observable price.
    eth=price_maps['ETHUSDT'].copy();ri=np.searchsorted(ratios.ts.to_numpy(),eth.index.to_numpy(),side='right')-1
    wprice=eth.price.to_numpy()*ratios.wsteth_steth.to_numpy()[np.maximum(ri,0)];wprice[ri<0]=np.nan
    wf=pd.DataFrame({'price':wprice},index=eth.index)
    for h in [1,6,24]:wf[f'price_change_{h}h']=wf.price.pct_change(h,fill_method=None)
    wf['realized_volatility_24h']=np.log(wf.price/wf.price.shift()).rolling(24,min_periods=24).std(ddof=1)
    price_maps['wstETH']=wf
    tx=pd.read_parquet(p.DATA/'owner_transactions.parquet',columns=['chain','market','borrower','ts','type'])
    tx=tx[tx.type.isin(['SupplyCollateral','WithdrawCollateral','Borrow','Repay'])]
    actions={k:np.sort(g.ts.unique()) for k,g in tx.groupby(['chain','market','borrower'])}
    path=p.DATA/'features_hourly.parquet';writer=None;counts={'rows':0,'missing_position_hours':0,'labels_6h':0,'labels_24h':0,'missing_market_history_rows':0}
    try:
        for batch in pq.ParquetFile(p.DATA/'positions_hourly.parquet').iter_batches(batch_size=100000):
            df=batch.to_pandas();pieces=[]
            for key,g in df.groupby(['chain','market'],sort=False):
                market=lookup[key];h=histories[key];asset=market['collateralAsset']['symbol'];symbol='BTCUSDT' if asset=='cbBTC' else ('wstETH' if asset=='wstETH' else 'ETHUSDT')
                g=g.copy();g['dist_to_line']=int(market['lltv'])/1e18-g.ltv
                g['oracle_vs_binance_pct']=100*(g.oracle_px/g.binance_px-1)
                for col in ['price_change_1h','price_change_6h','price_change_24h','realized_volatility_24h']:g[col]=g.ts.map(price_maps[symbol][col])
                g['utilization']=pd.to_numeric(g.ts.map(h['utilization']),errors='coerce')
                # API borrowApy is effective annual yield; continuous APR is log1p(APY).
                g['borrow_rate']=np.log1p(pd.to_numeric(g.ts.map(h['borrowApy']),errors='coerce'))
                g['market_history_missing']=g.utilization.isna()|g.borrow_rate.isna()
                g['position_size_usd']=g.collateral*g.oracle_px
                g['hours_since_owner_action']=np.nan
                for user,u in g.groupby('borrower',sort=False):
                    a=actions.get((*key,user),np.array([],dtype='int64'));times=u.ts.to_numpy();idx=np.searchsorted(a,times,side='right')-1
                    if len(a):g.loc[u.index,'hours_since_owner_action']=np.where(idx>=0,(times-a[np.maximum(idx,0)])/3600,np.nan)
                # Missing requested state stays missing; no market data fills it.
                names=['dist_to_line','oracle_vs_binance_pct','position_size_usd']
                g.loc[g.missing_position_hour,names]=np.nan
                pieces.append(g)
            out=pd.concat(pieces).sort_index();table=pa.Table.from_pandas(out,preserve_index=False)
            if writer is None:writer=pq.ParquetWriter(path,table.schema,compression='zstd')
            writer.write_table(table)
            counts['rows']+=len(out);counts['missing_position_hours']+=int(out.missing_position_hour.sum())
            counts['labels_6h']+=int(out.liquidated_within_6h.notna().sum());counts['labels_24h']+=int(out.liquidated_within_24h.notna().sum())
            counts['missing_market_history_rows']+=int(out.market_history_missing.sum())
            if counts['rows']%1000000==0:print('feature rows',counts['rows'],flush=True)
    finally:
        if writer:writer.close()
    save(p.DATA/'features_quality.json',counts);return counts

if __name__=='__main__':
    name=sys.argv[1];start=time.time()
    result={'market_history':market_history,'case_plan':case_plan,'minute_prices':minute_prices,'features':features}[name]()
    save(p.DATA/f'step2_{name}_status.json',{'source':'step2_'+name,'status':'complete','started_at':start,'finished_at':time.time(),'elapsed_s':time.time()-start,'result':result})
    print(json.dumps(result),flush=True)
