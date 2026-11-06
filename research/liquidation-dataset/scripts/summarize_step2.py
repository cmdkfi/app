"""Post-selection evaluation and headline tables. Never chooses a policy."""
import ctypes
import importlib.util
import json
import math
from pathlib import Path
import subprocess
import sys
import time
import concurrent.futures as cf
import numpy as np
import pandas as pd
spec=importlib.util.spec_from_file_location('replay',Path(__file__).with_name('replay.py'));r=importlib.util.module_from_spec(spec);spec.loader.exec_module(r)
D=r.D;S=r.S

def selected_results():
    frozen=r.read(D/'selected_policy.json');chosen=frozen['policy']['name']
    train=pd.read_parquet(D/'calibration_train_results.parquet',filters=[('policy','in',[chosen,'DEFAULT'])]);train.loc[train.policy==chosen,'policy']='SELECTED'
    rows=[]
    for path in sorted((D/'holdout_results').glob('*.json')):rows.extend(r.read(path))
    holdout=pd.DataFrame(rows);out=pd.concat([train,holdout],ignore_index=True)
    out.to_parquet(D/'selected_and_default_results.parquet',index=False);return out

def continuation():
    assert (D/'selected_policy.json').exists()
    results=pd.read_parquet(D/'selected_and_default_results.parquet');base=pd.read_parquet(D/'replay_baselines.parquet')
    base=base[(base.policy=='ACTUAL')&base.case_id.isin(results.case_id.unique())]
    jobs=pd.concat([results,base],ignore_index=True)
    plan=pd.read_parquet(D/'replay_case_plan.parquet').set_index('case_id');frozen=r.read(D/'selected_policy.json')['policy']
    h=pd.read_parquet(D/'binance_1h_ext.parquet');ratio=pd.read_csv(D/'eth_ratios_ext.csv').sort_values('ts');prices={}
    for asset in ['cbBTC','WETH','wstETH']:
        bars=h[h.symbol==('BTCUSDT' if asset=='cbBTC' else 'ETHUSDT')].sort_values('open_time')
        t=(bars.open_time.to_numpy()//1000).astype('int64');cl=bars.close.to_numpy().copy();first=float(bars.open.iloc[0]);ot=t+3600
        if asset=='wstETH':
            ix=np.searchsorted(ratio.ts.to_numpy(),ot,side='right')-1;cl*=ratio.wsteth_steth.to_numpy()[np.maximum(ix,0)];first*=ratio.wsteth_steth.iloc[0]
        prices[asset]=(ot,np.ascontiguousarray(cl),np.ascontiguousarray(np.repeat(np.r_[first,cl[:-1]],60)),int(t[0]),float(cl[-1]))
    rates={}
    for m in r.read(D/'markets.json'):
        maps={}
        for page in r.read(D/'raw/market_history'/f'{m["chain"]["id"]}-{m["marketId"]}.json'):maps.update({int(x['x']):None if x['y'] is None else math.log1p(float(x['y'])) for x in page['borrowApy']})
        rates[m['marketId']]=maps
    source=S/'sim_continuation.cpp';libpath=S/'sim_continuation.dylib'
    subprocess.run(['c++','-std=c++17','-O3','-dynamiclib',str(source),'-o',str(libpath)],check=True)
    lib=ctypes.CDLL(str(libpath));dp=np.ctypeslib.ndpointer(dtype=np.float64,flags='C_CONTIGUOUS');ip=np.ctypeslib.ndpointer(dtype=np.int64,flags='C_CONTIGUOUS')
    lib.continue_replay.argtypes=[ip,dp,ctypes.c_int,dp,ctypes.c_int,ctypes.c_int64,dp,dp];lib.continue_replay.restype=None
    checks=[]
    def one(x,validate=False):
        row=plan.loc[x['case_id']];ot,op,px,mt,today=prices[x['asset']];policy=frozen if x['policy']=='SELECTED' else r.DEFAULT
        cfg=np.array([x['C_end'],x['D_end'],row.t_end,r.END-60,rates[row.market][row.t0],row.lltv,1/(.3*row.lltv+.7)-1,.03 if row.chain==8453 else 5.,policy['act'],policy['target'],policy['budget'],policy['slice'],policy['cooldown'],x['policy']!='ACTUAL',False,r.END-60,60,60],dtype=float)
        adj=x['owner_cash']-x['borrow_clamped']-x['gas']-x['withdraw_short']*today
        out=np.zeros(20)
        if x['D_end']<=1e-9 or x['C_end']<=1e-12:out[0]=x['C_end'];out[1]=x['D_end']
        else:lib.continue_replay(ot,op,len(ot),px,len(px),mt,cfg,out)
        if validate and x['D_end']>1e-9 and x['C_end']>1e-12:
            # Check the full continuation against the original Python simulator.
            v=r.ref.Venue('morpho',row.lltv,cfg[6],cfg[7],1200)
            case=r.ref.Case(x['case_id'],v,x['C_end'],x['D_end'],int(row.t_end),r.END-60,cfg[4],[],[])
            pol=r.ref.Policy('continuation',act=policy['act'],target=policy['target'],floor=.30,disc_budget=policy['budget'],slice_max=policy['slice'],cooldown=policy['cooldown'],delay=120,enabled=x['policy']!='ACTUAL')
            market=r.ref.Market(np.arange(len(px))*60+mt,px,px,px,px);oracle=r.ref.Oracle(ot,op)
            st,_=r.ref.run(case,pol,market,oracle,record_every=3600)
            for k,idx in [('C_end',0),('D_end',1),('penalty',2),('gas',4)]:assert math.isclose(st[k],out[idx],rel_tol=1e-8,abs_tol=1e-7),(k,st[k],out[idx])
            checks.append({'case_id':x['case_id'],'policy':x['policy'],'passed':True})
        return dict(case_id=x['case_id'],policy=x['policy'],split=x['split'],asset_group=x['asset_group'],asset=x['asset'],equity_today_managed=out[0]*today-out[1]+adj-out[4],C_today=out[0],D_today=out[1],liquidated_after=bool(out[9]>0),penalty_after=out[2],sales_after=out[10],gas_after=out[4],slippage_after=out[5],p_today=today)
    # Long, debt-bearing continuations exercise rate accrual and later crashes.
    eligible=jobs[(jobs.D_end>1e-6)&(jobs.C_end>1e-12)&(jobs.policy=='DEFAULT')].copy()
    eligible['end']=eligible.case_id.map(plan.t_end)
    if (D/'continuation_parity.json').exists():checks=r.read(D/'continuation_parity.json')
    else:
        for _,g in eligible.groupby('asset'):one(g.sort_values('end').iloc[0].to_dict(),validate=True)
    r.save(D/'continuation_parity.json',checks)
    directory=D/'continuation_cache';directory.mkdir(exist_ok=True)
    def cached_case(group):
        cid,frame=group;path=directory/(r.hashlib.sha256(cid.encode()).hexdigest()+'.json')
        if path.exists():return r.read(path)
        result=[one(x) for x in frame.to_dict('records')];r.save(path,result);return result
    rows=[]
    with cf.ThreadPoolExecutor(max_workers=4) as pool:
        for i,batch in enumerate(pool.map(cached_case,list(jobs.groupby('case_id',sort=False)),buffersize=8)):
            rows.extend(batch)
            if i%500==0:print('continuation',i,flush=True)
    pd.DataFrame(rows).to_parquet(D/'continuation_results.parquet',index=False)
    return {'rows':len(rows),'parity_checks':len(checks)}

def tables():
    results=pd.read_parquet(D/'selected_and_default_results.parquet');base=pd.read_parquet(D/'replay_baselines.parquet')
    actual=base[base.policy=='ACTUAL'].set_index('case_id');noact=base[base.policy=='NOACTION'].set_index('case_id')
    continuation=pd.read_parquet(D/'continuation_results.parquet');today_actual=continuation[continuation.policy=='ACTUAL'].set_index('case_id');today=continuation.set_index(['case_id','policy'])
    for col in ['n_liqs','penalty','C_end','equity_end','equity_bottom']:results['actual_'+col]=results.case_id.map(actual[col])
    results['noaction_liqs']=results.case_id.map(noact.n_liqs)
    results['saved_from_observed_liquidation']=(results.actual_n_liqs>0)&(results.n_liqs==0)
    results['saved_with_model_support']=results.saved_from_observed_liquidation&(results.noaction_liqs>0)
    results['liquidated_only_with_policy']=(results.noaction_liqs==0)&(results.n_liqs>0)
    results['extra_collateral_pct']=np.where(results.C0>0,100*(results.C_end-results.actual_C_end)/results.C0,np.nan)
    results['equity_vs_actual_end']=results.equity_end-results.actual_equity_end
    results['equity_vs_actual_bottom']=results.equity_bottom-results.actual_equity_bottom
    results['equity_today_managed']=[today.loc[(x.case_id,x.policy),'equity_today_managed'] for x in results.itertuples()]
    results['actual_today_passive']=results.case_id.map(today_actual.equity_today_managed)
    def summarize(g):
        real=g.actual_n_liqs>0;surv=~real
        return dict(cases=len(g),observed_liquidated=int(real.sum()),observed_survived=int(surv.sum()),saved=int(g.saved_from_observed_liquidation.sum()),saved_with_noaction_model_support=int(g.saved_with_model_support.sum()),policy_liquidated=int((g.n_liqs>0).sum()),survivor_policy_liquidations=int(((g.n_liqs>0)&surv).sum()),liquidated_only_with_policy=int(g.liquidated_only_with_policy.sum()),real_penalties=float(g.actual_penalty.sum()),policy_penalties=float(g.penalty.sum()),median_extra_collateral_pct=float(g.extra_collateral_pct.median()),equity_vs_actual_bottom=float(g.equity_vs_actual_bottom.sum()),equity_vs_actual_end=float(g.equity_vs_actual_end.sum()),policy_equity_today=float(g.equity_today_managed.sum()),actual_equity_today=float(g.actual_today_passive.sum()),slippage=float(g.slip_cost.sum()),gas=float(g.gas.sum()),sales=int(g.n_sales.sum()),fallback_cases=int(g.binance_fallback.sum()),bad_debt=float(g.bad_debt.sum()))
    headline=[];assets=[]
    for (split,policy),g in results.groupby(['split','policy']):headline.append(dict(split=split,policy=policy,**summarize(g)))
    for (split,policy,asset),g in results.groupby(['split','policy','asset_group']):assets.append(dict(split=split,policy=policy,asset_group=asset,**summarize(g)))
    pd.DataFrame(headline).to_csv(D/'headline.csv',index=False);pd.DataFrame(assets).to_csv(D/'per_asset.csv',index=False)
    sensitivity=[dict(split=split,policy=policy,binance_fallback=bool(fallback),**summarize(g)) for (split,policy,fallback),g in results.groupby(['split','policy','binance_fallback'])]
    pd.DataFrame(sensitivity).to_csv(D/'oracle_source_sensitivity.csv',index=False)
    results.to_parquet(D/'evaluation_results.parquet',index=False)
    failures=results[results.n_liqs>0].copy()
    def reason(row):
        reasons=[]
        if row.reverts>0:reasons.append('fills reverted at oracle ceiling')
        if row.blocked>0:reasons.append('oracle/market gap blocked orders')
        if row.binance_fallback:reasons.append('includes unresolved-oracle fallback span')
        if row.n_sales==0:reasons.append('no sale landed before liquidation')
        return '; '.join(reasons) or 'buffer/slice/cooldown insufficient or owner flow raised LTV'
    failures['failure_reason']=failures.apply(reason,axis=1);failures.to_parquet(D/'unsaved_cases.parquet',index=False)
    print(pd.DataFrame(headline).to_string(index=False),flush=True);return {'headline_rows':len(headline),'per_asset_rows':len(assets),'unsaved_case_policy_rows':len(failures)}

if __name__=='__main__':
    name=sys.argv[1];start=time.time();result={'selected_results':lambda:{'rows':len(selected_results())},'continuation':continuation,'tables':tables}[name]()
    r.save(D/f'step2_{name}_status.json',dict(source='step2_'+name,status='complete',started_at=start,finished_at=time.time(),elapsed_s=time.time()-start,result=result))
    print(json.dumps(result),flush=True)
