"""Extend existing hourly candles; never request an already covered hour."""
import importlib.util
from pathlib import Path
import json
import time
import urllib.parse
import pandas as pd
spec=importlib.util.spec_from_file_location('pull',Path(__file__).with_name('pull.py'))
p=importlib.util.module_from_spec(spec);spec.loader.exec_module(p)
start=time.time();status={'source':'binance','started_at':start,'status':'running'}
p.save(p.DATA/'binance_status.json',status)
try:
    original=p.ROOT.parent/'Voices on AI managed crypto loans/backtest_data/binance_1h.parquet'
    old=pd.read_parquet(original)
    old=old[old.symbol.isin(['BTCUSDT','ETHUSDT']) & (old.open_time>=p.START*1000) & (old.open_time<p.END*1000)].copy()
    raw_copy=p.RAW/'binance_existing.parquet'
    if not raw_copy.exists():old.to_parquet(raw_copy,index=False)
    columns=['open_time','open','high','low','close','volume','close_time','quote_volume','trades','tb_base','tb_quote','ignore']
    rows=[]
    for symbol in ['BTCUSDT','ETHUSDT']:
        known=set(old.loc[old.symbol==symbol,'open_time'].astype(int))
        t=p.START*1000
        while t<p.END*1000:
            if t in known:t+=3600000;continue
            end=t
            while end<p.END*1000 and end not in known and end<t+1000*3600000:end+=3600000
            query=urllib.parse.urlencode({'symbol':symbol,'interval':'1h','startTime':t,'endTime':end-1,'limit':1000})
            batch=p.request('binance','https://api.binance.com/api/v3/klines?'+query)
            if not batch:raise RuntimeError(f'Empty klines for {symbol} at {t}')
            for row in batch:
                item=dict(zip(columns,row));item.update(symbol=symbol,interval='1h');rows.append(item)
            t=int(batch[-1][0])+3600000
        print(symbol,'reused',len(known),'new cumulative',len(rows),flush=True)
    new=pd.DataFrame(rows)
    keep=['open_time','open','high','low','close','volume','quote_volume','trades','symbol','interval']
    df=pd.concat([old[keep],new[keep]],ignore_index=True)
    for col in ['open','high','low','close','volume','quote_volume']:df[col]=df[col].astype(float)
    df['open_time']=df.open_time.astype('int64');df['trades']=df.trades.astype('int64')
    df=df.drop_duplicates(['symbol','open_time']).sort_values(['symbol','open_time'])
    df.to_parquet(p.DATA/'binance_1h_ext.parquet',index=False)
    expected=(p.END-p.START)//3600*2
    assert len(df)==expected,(len(df),expected)
    status.update(status='complete',rows=len(df),reused_rows=len(old),new_rows=len(new))
except Exception as exc:
    import traceback;traceback.print_exc();status.update(status='failed',error=str(exc))
status.update(finished_at=time.time(),elapsed_s=time.time()-start)
p.save(p.DATA/'binance_status.json',status);print(json.dumps(status),flush=True)
if status['status']=='failed':raise SystemExit(1)
