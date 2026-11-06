"""Prepare and run the existing Morpho P2 simulator, accelerated after parity checks.

The untouched Python source is sim_reference.py. sim_grid.cpp implements its
Morpho/no-signal/no-relever path. Selection reads train results only; holdout is
run only after selected_policy.json is written. No retrieval or model fitting.
"""
import concurrent.futures as cf
import ctypes
import hashlib
import importlib.util
import itertools
import json
import math
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

ROOT=Path(__file__).resolve().parents[1];D=ROOT/'data';S=ROOT/'scripts';END=1792972800;SPLIT=1767225600
def read(f):return json.loads(f.read_text())
def save(f,x):f.parent.mkdir(parents=True,exist_ok=True);tmp=f.with_suffix(f.suffix+'.tmp');tmp.write_text(json.dumps(x,indent=2)+'\n');tmp.replace(f)
def module(name):
    spec=importlib.util.spec_from_file_location(name,S/(name+'.py'));m=importlib.util.module_from_spec(spec);sys.modules[name]=m;spec.loader.exec_module(m);return m
ref=module('sim_reference')
DEFAULT=dict(name='DEFAULT',act=.70,target=.50,budget=.20,slice=.25,cooldown=120)
GRID=[dict(name=f'G{a}-{t}-{b}-{s}-{co}',act=a/100,target=t/100,budget=b/100,slice=s/100,cooldown=co) for a,t,b,s,co in itertools.product([65,70,75],[45,50,55],[10,20,30],[5,10,20],[60,300])]
OUTPUT=['C_end','D_end','penalty','bad_debt','gas','slip_cost','owner_cash','borrow_clamped','withdraw_short','n_liqs','n_sales','coll_sold','reverts','blocked','max_ltv','C_bottom','D_bottom','adj_bottom','short_bottom','first_liq_ts']
LIB=None
def library():
    global LIB
    if LIB is None:
        path=S/'sim_grid.dylib';source=S/'sim_grid.cpp'
        if not path.exists() or path.stat().st_mtime<source.stat().st_mtime:subprocess.run(['c++','-std=c++17','-O3','-dynamiclib',str(source),'-o',str(path)],check=True)
        LIB=ctypes.CDLL(str(path));dp=np.ctypeslib.ndpointer(dtype=np.float64,flags='C_CONTIGUOUS');ip=np.ctypeslib.ndpointer(dtype=np.int64,flags='C_CONTIGUOUS')
        LIB.replay.argtypes=[dp,ctypes.c_int,ip,dp,ctypes.c_int,dp,dp,dp,dp,ctypes.c_int,ctypes.c_int64,dp,dp];LIB.replay.restype=None
    return LIB

def prepare_oracles():
    rounds=pd.read_csv(D/'oracle_rounds_ext.csv',low_memory=False);bounds=read(D/'oracle_phase_bounds.json')
    minute=pd.read_parquet(D/'binance_1m_ext.parquet');ratios=pd.read_csv(D/'eth_ratios_ext.csv').sort_values('ts')
    configs=read(D/'market_oracle_config.json');markets=read(D/'markets.json');counts=[]
    for market,config in zip(markets,configs):
        key=f'{market["chain"]["id"]}-{market["marketId"]}';asset=market['collateralAsset']['symbol'];symbol='BTCUSDT' if asset=='cbBTC' else 'ETHUSDT'
        bars=minute[minute.symbol==symbol].sort_values('open_time');mt=(bars.open_time.to_numpy()//1000).astype('int64')
        numerator=config['oracle']['data']['baseFeedOne']['address'].lower();quote=config['oracle']['data']['quoteFeedOne'];denominator=quote['address'].lower() if quote else None
        needed=[numerator] if asset!='wstETH' else []
        if denominator:needed.append(denominator)
        relevant=rounds[rounds.proxy.str.lower().isin(needed)]
        boundary_times=[x[k] for x in bounds if x['proxy'].lower() in needed for k in ['usable_from','usable_until_exclusive']]
        ts=np.unique(np.r_[mt+60,relevant.updated_at.to_numpy(),ratios.ts.to_numpy() if asset=='wstETH' else [],boundary_times]).astype('int64')
        ts=ts[(ts>=mt[0]+60)&(ts<END)]
        ix=np.searchsorted(mt+60,ts,side='right')-1;fallback=bars.close.to_numpy()[np.maximum(ix,0)]
        ratio_idx=np.searchsorted(ratios.ts.to_numpy(),ts,side='right')-1;ratio=ratios.wsteth_steth.to_numpy()[np.maximum(ratio_idx,0)]
        if asset=='wstETH':fallback=fallback*ratio
        usable=np.ones(len(ts),dtype=bool);components={}
        for addr in needed:
            r=relevant[(relevant.proxy.str.lower()==addr)&relevant.usable_for_replay].sort_values(['updated_at','round_id']).drop_duplicates('updated_at',keep='last')
            if r.empty:usable[:]=False;components[addr]=np.ones(len(ts));continue
            ix=np.searchsorted(r.updated_at.to_numpy(),ts,side='right')-1
            components[addr]=r.answer.to_numpy()[np.maximum(ix,0)];valid=ix>=0;within=np.zeros(len(ts),dtype=bool)
            for b in bounds:
                if b['proxy'].lower()==addr:
                    mask=(ts>=b['usable_from'])&(ts<b['usable_until_exclusive'])
                    # An old round must not leak across an unresolved boundary.
                    mask &= r.updated_at.to_numpy()[np.maximum(ix,0)]>=b['usable_from']
                    within|=mask
            usable&=valid&within
        actual=ratio if asset=='wstETH' else components[numerator]
        if denominator:actual=actual/components[denominator]
        px=np.where(usable,actual,fallback)
        assert np.all(np.isfinite(px)&(px>0))
        keep=np.r_[True,(np.diff(px)!=0)|(np.diff(usable.astype(int))!=0)]
        path=D/'replay_oracles'/f'{key}.npz';path.parent.mkdir(exist_ok=True)
        np.savez_compressed(path,t=ts[keep],p=px[keep],binance_fallback=(~usable)[keep])
        counts.append(dict(chain=market['chain']['id'],market=market['marketId'],asset=asset,updates=int(keep.sum()),fallback_update_rows=int(((~usable)&keep).sum())))
    pd.DataFrame(counts).to_csv(D/'replay_oracle_coverage.csv',index=False);return counts

class Data:
    def __init__(self):
        self.markets={x['marketId']:x for x in read(D/'markets.json')}
        minute=pd.read_parquet(D/'binance_1m_ext.parquet');self.bars={};self.oracles={};self.rates={}
        ratios=pd.read_csv(D/'eth_ratios_ext.csv').sort_values('ts')
        for mk,m in self.markets.items():
            key=f'{m["chain"]["id"]}-{mk}';asset=m['collateralAsset']['symbol'];sym='BTCUSDT' if asset=='cbBTC' else 'ETHUSDT'
            bars=minute[minute.symbol==sym].sort_values('open_time');t=(bars.open_time.to_numpy()//1000).astype('int64')
            values=[np.ascontiguousarray(bars[n].to_numpy(dtype=float)) for n in ['open','high','low','close']]
            if asset=='wstETH':
                ix=np.searchsorted(ratios.ts.to_numpy(),t,side='right')-1;mult=ratios.wsteth_steth.to_numpy()[np.maximum(ix,0)]
                values=[np.ascontiguousarray(v*mult) for v in values]
            self.bars[mk]=(t,*values)
            oracle=np.load(D/'replay_oracles'/f'{key}.npz');self.oracles[mk]={k:oracle[k] for k in oracle.files}
            rates={}
            for page in read(D/'raw/market_history'/f'{key}.json'):rates.update({int(x['x']):None if x['y'] is None else math.log1p(float(x['y'])) for x in page['borrowApy']})
            self.rates[mk]=rates
        tx=pd.read_parquet(D/'owner_transactions.parquet',columns=['chain','market','borrower','ts','log','payload'])
        self.events={}
        for (mk,user),g in tx.groupby(['market','borrower'],sort=False):
            rows=[];dec=self.markets[mk]['collateralAsset']['decimals']
            for x in g.sort_values(['ts','log']).itertuples():
                r=json.loads(x.payload);kind=r['type'];v=r['data'];t=int(r['timestamp'])
                if kind in ['SupplyCollateral','WithdrawCollateral']:rows.append((t,1 if kind=='SupplyCollateral' else 2,int(v['assets'])/10**dec,0.))
                elif kind in ['Borrow','Repay']:rows.append((t,3 if kind=='Borrow' else 4,int(v['assets'])/1e6,0.))
                elif kind=='Liquidation':rows.append((t,5,int(v['repaidAssets'])/1e6,int(v['seizedAssets'])/10**dec))
            self.events[(mk,user)]=rows
    def inputs(self,row):
        mk=row['market'];t,o,h,l,cl=self.bars[mk];a=np.searchsorted(t,row['t0']-60);b=np.searchsorted(t,row['t_end'],side='right')
        tt=t[a:b];assert len(tt)>1 and np.all(np.diff(tt)==60),(row['case_id'],'minute gap')
        bars=[np.ascontiguousarray(x[a:b]) for x in [o,h,l,cl]]
        oracle=self.oracles[mk];ot=oracle['t'];op=oracle['p'];j=max(0,np.searchsorted(ot,row['t0'],side='right')-1);k=np.searchsorted(ot,row['t_end'],side='right')
        ot=np.ascontiguousarray(ot[j:k]);op=np.ascontiguousarray(op[j:k]);assert ot[0]<=row['t0']
        ev=[(int(t),0,0.,0.) for t in ot if row['t0']<=t<=row['t_end']]
        flows=[x for x in self.events.get((mk,row['borrower']),[]) if row['t0']<x[0]<=row['t_end']]
        ev.extend(flows);ev.extend((t,6,0.,0.) for t in range(row['t0']-row['t0']%60+60,row['t_end']+1,60))
        ev.sort(key=lambda x:(x[0],0 if x[1]==0 else (3 if x[1]==6 else 1)))
        event=np.array(ev,dtype=float)
        event=np.ascontiguousarray(np.c_[event,op[np.maximum(np.searchsorted(ot,event[:,0],side='right')-1,0)]])
        bottom_idx=int(np.argmin(bars[2]));bottom_t=int(tt[bottom_idx]);bottom_px=float(bars[2][bottom_idx])
        # Restrict bottom to the episode, excluding the causal prefix minute.
        if bottom_t<row['t0']:
            bottom_idx=int(np.argmin(bars[2][1:]))+1;bottom_t=int(tt[bottom_idx]);bottom_px=float(bars[2][bottom_idx])
        return dict(ev=event,ot=ot,op=op,bars=bars,mt=int(tt[0]),bottom_ts=bottom_t,p_bottom=bottom_px,p_end=float(bars[3][-1]),flows=flows,rate=self.rates[mk].get(row['t0']),fallback=bool(np.any(oracle['binance_fallback'][j:k])))

def fast(row,inp,policy=DEFAULT,actual=False,disabled=False):
    lib=library();cfg=np.array([row['C0'],row['D0'],row['t0'],row['t_end'],inp['rate'],row['lltv'],1/(.3*row['lltv']+.7)-1,.03 if row['chain']==8453 else 5.,policy['act'],policy['target'],policy['budget'],policy['slice'],policy['cooldown'],not(actual or disabled),actual,inp['bottom_ts'],60,60],dtype=float)
    out=np.zeros(20);o,h,l,cl=inp['bars']
    lib.replay(inp['ev'],len(inp['ev']),inp['ot'],inp['op'],len(inp['ot']),o,h,l,cl,len(cl),inp['mt'],cfg,out)
    result=dict(zip(OUTPUT,map(float,out)));result.update(case_id=row['case_id'],policy='ACTUAL' if actual else ('NOACTION' if disabled else policy['name']),asset=row['asset'],asset_group=row['asset_group'],split=row['split'],selection=row['selection'],binance_fallback=inp['fallback'])
    result['equity_end']=out[0]*inp['p_end']-out[1]+out[6]-out[7]-out[4]-out[8]*inp['p_end']
    result['equity_bottom']=out[15]*inp['p_bottom']-out[16]+out[17]-out[18]*inp['p_bottom']
    result['C0']=row['C0'];return result

def parity():
    data=Data();plan=pd.read_parquet(D/'replay_case_plan.parquet');plan=plan[plan.included].copy()
    sample=pd.concat([g.head(2) for _,g in plan.groupby(['split','chain','asset','selection'])]).drop_duplicates('case_id')
    results=[]
    for row in sample.to_dict('records'):
        inp=data.inputs(row)
        if inp['rate'] is None:continue
        v=ref.Venue('morpho',row['lltv'],1/(.3*row['lltv']+.7)-1,.03 if row['chain']==8453 else 5.,1200)
        kinds={1:'SupplyCollateral',2:'WithdrawCollateral',3:'Borrow',4:'Repay'}
        flows=[(int(t),kinds[int(k)],a) for t,k,a,b in inp['flows'] if k in kinds]
        real=[(int(t),a,b) for t,k,a,b in inp['flows'] if k==5]
        case=ref.Case(row['case_id'],v,row['C0'],row['D0'],row['t0'],row['t_end'],inp['rate'],flows,real)
        market=ref.Market(np.arange(len(inp['bars'][0]))*60+inp['mt'],*inp['bars']);oracle=ref.Oracle(inp['ot'],inp['op'])
        for policy,actual in [(DEFAULT,False),(GRID[0],False),(GRID[-1],False),(DEFAULT,True)]:
            pol=ref.Policy(policy['name'],act=policy['act'],target=policy['target'],floor=.30,disc_budget=policy['budget'],slice_max=policy['slice'],cooldown=policy['cooldown'],delay=120,enabled=not actual)
            st,se=ref.run(case,pol,market,oracle,record_every=60,actual=actual)
            got=fast(row,inp,policy,actual);expected={k:st[k] for k in ['C_end','D_end','penalty','bad_debt','gas','slip_cost','owner_cash','borrow_clamped','withdraw_short','reverts','blocked','max_ltv']}
            expected.update(n_liqs=len(st['liqs']),n_sales=len(st['sales']),coll_sold=sum(x['q'] for x in st['sales']))
            for k,value in expected.items():
                assert math.isclose(got[k],value,rel_tol=1e-8,abs_tol=1e-7),(row['case_id'],policy,actual,k,got[k],value)
            cb,db,adj,short=ref.state_at(se,inp['bottom_ts'])
            for k,value in zip(['C_bottom','D_bottom','adj_bottom','short_bottom'],[cb,db,adj,short]):assert math.isclose(got[k],value,rel_tol=1e-8,abs_tol=1e-7),(k,got[k],value)
            results.append(dict(case_id=row['case_id'],policy=policy['name'],actual=actual,passed=True))
        print('reference parity',len(results),flush=True)
    save(D/'simulator_parity.json',{'checks':len(results),'cases':len(sample),'all_passed':True,'reference_sha256':hashlib.sha256((S/'sim_reference.py').read_bytes()).hexdigest(),'cpp_sha256':hashlib.sha256((S/'sim_grid.cpp').read_bytes()).hexdigest(),'results':results})
    return {'checks':len(results),'cases':len(sample)}

def baseline():
    data=Data();plan=pd.read_parquet(D/'replay_case_plan.parquet');rows=[];validation=[]
    for i,row in enumerate(plan[plan.included].to_dict('records')):
        inp=data.inputs(row)
        if inp['rate'] is None:
            validation.append(dict(case_id=row['case_id'],eligible=False,reason='missing_start_rate'));continue
        actual=fast(row,inp,actual=True);noact=fast(row,inp,disabled=True)
        # Predeclared endpoint check: 1% of observed/start balance, dust tolerances.
        ce=abs(actual['C_end']-row['C_expected_end']);de=abs(actual['D_end']-row['D_expected_end'])
        ctol=max(1e-8,.01*max(row['C_expected_end'],row['C0']));dtol=max(1.,.01*max(row['D_expected_end'],row['D0']))
        ok=ce<=ctol and de<=dtol
        validation.append(dict(case_id=row['case_id'],eligible=ok,reason='' if ok else 'actual_replay_endpoint_mismatch',collateral_abs_error=ce,debt_abs_error=de,collateral_tolerance=ctol,debt_tolerance=dtol))
        rows.extend([actual,noact])
        if i%500==0:print('baselines',i,flush=True)
    pd.DataFrame(rows).to_parquet(D/'replay_baselines.parquet',index=False);pd.DataFrame(validation).to_parquet(D/'replay_validation.parquet',index=False)
    return {'cases':len(validation),'eligible':sum(r['eligible'] for r in validation)}

def grid(mode):
    assert read(D/'simulator_parity.json')['all_passed']
    data=Data();plan=pd.read_parquet(D/'replay_case_plan.parquet');valid=pd.read_parquet(D/'replay_validation.parquet');ids=set(valid.loc[valid.eligible,'case_id'])
    plan=plan[plan.included & plan.case_id.isin(ids) & (plan.split==('train' if mode=='train' else 'holdout'))]
    if mode=='train':policies=GRID+[DEFAULT]
    else:
        frozen=read(D/'selected_policy.json');policies=[dict(frozen['policy'],name='SELECTED'),DEFAULT]
    directory=D/('grid_train' if mode=='train' else 'holdout_results');directory.mkdir(exist_ok=True)
    manifest={'cpp_sha256':hashlib.sha256((S/'sim_grid.cpp').read_bytes()).hexdigest(),'policies':policies,
              'case_ids_sha256':hashlib.sha256('\n'.join(sorted(plan.case_id)).encode()).hexdigest(),
              'episode_definition':'three days either side, literal corridor, start APR held constant, endpoint error <=1 percent'}
    manifest_path=D/f'{mode}_run_manifest.json'
    if manifest_path.exists():assert read(manifest_path)==manifest,'Cached replay settings differ; do not reuse stale results'
    else:save(manifest_path,manifest)
    # Each completed case is its own resumable result. Workers share immutable arrays.
    def one(row):
        path=directory/(hashlib.sha256(row['case_id'].encode()).hexdigest()+'.json')
        if path.exists():return read(path)
        inp=data.inputs(row);results=[fast(row,inp,pol) for pol in policies];save(path,results);return results
    library();n=0
    with cf.ThreadPoolExecutor(max_workers=4) as pool:
        for result in pool.map(one,plan.to_dict('records'),buffersize=8):
            n+=1
            if n%100==0:print(mode,'cases',n,'/',len(plan),flush=True)
    return {'cases':n,'policies':len(policies),'case_policy_runs':n*len(policies)}

def select():
    base=pd.read_parquet(D/'replay_baselines.parquet');base=base[(base.policy=='ACTUAL')&(base.split=='train')].set_index('case_id')
    aggregates={};buffer=[];writer=None
    try:
        for path in sorted((D/'grid_train').glob('*.json')):
            rows=read(path)
            for r in rows:
                assert r['split']=='train'
                r['equity_vs_actual']=r['equity_end']-float(base.loc[r['case_id'],'equity_end']);r['cost']=r['gas']+r['slip_cost']
                agg=aggregates.setdefault(r['policy'],dict(policy=r['policy'],cases=0,liquidated=0,equity_vs_actual=0.,cost=0.,sales=0))
                agg['cases']+=1;agg['liquidated']+=r['n_liqs']>0;agg['equity_vs_actual']+=r['equity_vs_actual'];agg['cost']+=r['cost'];agg['sales']+=r['n_sales']
            buffer.extend(rows)
            if len(buffer)>=10000:
                table=pa.Table.from_pylist(buffer)
                if writer is None:writer=pq.ParquetWriter(D/'calibration_train_results.parquet',table.schema,compression='zstd')
                writer.write_table(table);buffer=[]
        if buffer:
            table=pa.Table.from_pylist(buffer)
            if writer is None:writer=pq.ParquetWriter(D/'calibration_train_results.parquet',table.schema,compression='zstd')
            writer.write_table(table)
    finally:
        if writer:writer.close()
    out=pd.DataFrame(aggregates.values())
    grid=out[out.policy!='DEFAULT'].copy();params={r['name']:r for r in GRID}
    grid['default_distance']=[sum(abs(params[x][k]-DEFAULT[k]) for k in ['act','target','budget','slice'])+abs(params[x]['cooldown']-DEFAULT['cooldown'])/1000 for x in grid.policy]
    grid=grid.sort_values(['liquidated','equity_vs_actual','cost','sales','default_distance','policy'],ascending=[True,False,True,True,True,True])
    winner=grid.iloc[0];grid.to_csv(D/'calibration_grid.csv',index=False)
    save(D/'selected_policy.json',{'selected_at':time.time(),'selection_split':'train only; all episode and survivor follow-up ends before 2026','rank_order':['fewest liquidated cases','highest total end equity vs actual','lowest slippage plus gas','fewest sales','closest to defaults','lexical name'],'policy':params[winner.policy],'train_score':winner.to_dict(),'holdout_used_for_selection':False})
    print(winner.to_string(),flush=True);return params[winner.policy]

if __name__=='__main__':
    name=sys.argv[1];start=time.time()
    result={'oracles':prepare_oracles,'parity':parity,'baseline':baseline,'train':lambda:grid('train'),'select':select,'holdout':lambda:grid('holdout')}[name]()
    save(D/f'step2_replay_{name}_status.json',dict(source='step2_replay_'+name,status='complete',started_at=start,finished_at=time.time(),elapsed_s=time.time()-start,result=result))
    print(json.dumps(result),flush=True)
