"""Reproducible DEFAULT-only episode loss breakdowns, no new simulation decisions."""
from pathlib import Path
import pandas as pd,numpy as np,json,time
D=Path(__file__).resolve().parents[1]/'data';ROOT=D.parent

def md(df):
    def f(x):
        if isinstance(x,(float,np.floating)):return f'{x:.2f}' if pd.notna(x) else 'unknown'
        return str(x)
    return '| '+' | '.join(map(str,df.columns))+' |\n| '+' | '.join(['---']*len(df.columns))+' |\n'+'\n'.join('| '+' | '.join(f(x) for x in row)+' |' for row in df.itertuples(index=False,name=None))

def main():
    start=time.time();e=pd.read_parquet(D/'evaluation_results.parquet');e=e[e.policy=='DEFAULT'].copy();p=pd.read_parquet(D/'replay_case_plan.parquet');d=pd.read_parquet(D/'default_execution_diagnostics.parquet')
    e=e.merge(p[['case_id','chain','market','t0','t_end','anchor_ts','D0','oracle_start']],on='case_id',validate='one_to_one').merge(d,on='case_id',validate='one_to_one')
    u=pd.read_parquet(D/'unsaved_cases.parquet');assert set(u.loc[u.policy=='DEFAULT','case_id'])==set(e.loc[e.n_liqs>0,'case_id'])
    e['lost']=e.n_liqs>0;e['start_ltv']=e.D0/(e.C0*e.oracle_start);e['start_size_usd']=e.C0*e.oracle_start
    e['market_name']=e.chain.map({8453:'Base',1:'Ethereum'})+' '+e.asset+'/USDC'
    e['ltv_band']=pd.cut(e.start_ltv,[-np.inf,.5,.7,.8,np.inf],labels=['below 50%','50–70%','70–80%','above 80%'],right=True).astype('str').fillna('undefined (zero collateral)')
    # Exact 50% belongs to 50–70%; exact70% to50–70%;exact80%to70–80%.
    e.loc[e.start_ltv==.5,'ltv_band']='50–70%'
    e['above_act_at_first_decision']=np.where(e.first_decision_ts==0,'no debt-bearing decision',np.where(e.first_decision_ltv>.70,'yes','no'))
    e['size_band_usd']=pd.cut(e.start_size_usd,[-np.inf,.04,1,100,1000,10000,100000,np.inf],labels=['< $0.04','$0.04–<1','$1–<100','$100–<1k','$1k–<10k','$10k–<100k','>= $100k'],right=False).astype('str')
    t=pd.to_datetime(e.t_end,unit='s',utc=True);e['end_year']=t.dt.year;e['end_iso_week']=t.dt.strftime('%G-W%V')
    e['order_blocking']=e.blocked_before_liq>0;e['revert']=e.reverts_before_liq>0;e['no_landed_sale']=e.sales_before_liq==0;e['fallback_price']=e.binance_fallback
    e['primary_reason']=np.select([e.order_blocking,e.revert,e.no_landed_sale,e.fallback_price],['order blocking','revert','no landed sale','fallback price'],default='other')
    e['other']=~(e.order_blocking|e.revert|e.no_landed_sale|e.fallback_price)
    e['dust_limited']=e.no_landed_sale & (e.first_order_ts==0) & (e.dust_attempts_before_liq>0)
    e['hours_decision_to_liq']=np.where(e.lost & (e.first_decision_ts>0),(e.first_liq_ts-e.first_decision_ts)/3600,np.nan)
    e['above_act_under_1h']=e.lost & (e.above_act_at_first_decision=='yes') & (e.hours_decision_to_liq>=0)&(e.hours_decision_to_liq<1)
    e['below_120s_opportunity']=e.lost & (e.first_decision_ts>0)&(e.first_liq_ts-e.first_decision_ts<120)
    e['first_failed_sale_ts']=np.where(e.first_block_ts==0,e.first_revert_ts,np.where(e.first_revert_ts==0,e.first_block_ts,np.minimum(e.first_block_ts,e.first_revert_ts)))
    e['first_failed_sale_gap_pct']=np.where(e.first_failed_sale_ts==0,np.nan,np.where(e.first_failed_sale_ts==e.first_block_ts,e.first_block_gap_pct,e.first_revert_gap_pct))
    e['gap_band_pct']=pd.cut(e.first_failed_sale_gap_pct,[-np.inf,0,.5,.85,1,2,5,np.inf],labels=['<= 0%','0–0.5%','0.5–0.85%','0.85–1%','1–2%','2–5%','>5%']).astype('str').fillna('not applicable')
    e.to_parquet(D/'default_loss_analysis.parquet',index=False)
    rows=[]
    def group(kind,cols):
        g=e.groupby(['split']+cols,dropna=False).agg(episodes=('case_id','size'),lost=('lost','sum')).reset_index();g['loss_pct_of_group']=100*g.lost/g.episodes;tot=e.groupby('split').lost.sum();g['pct_of_split_losses']=[100*x/tot[s] for x,s in zip(g.lost,g.split)]
        for z in g.to_dict('records'):rows.append(dict(breakdown=kind,group=' / '.join(str(z[k]) for k in cols),**{k:z[k] for k in ['split','episodes','lost','loss_pct_of_group','pct_of_split_losses']}))
        return g
    definitions=[('primary reason',['primary_reason']),('asset group',['asset_group']),('market',['market_name']),('end year',['end_year']),('end ISO week',['end_iso_week']),('start LTV',['ltv_band']),('first decision above act',['above_act_at_first_decision']),('LTV and first decision',['ltv_band','above_act_at_first_decision']),('start collateral size USD',['size_band_usd']),('first blocked/reverted sale oracle gap',['gap_band_pct'])]
    tables={name:group(name,cols) for name,cols in definitions}
    tags=[]
    for tag in ['order_blocking','revert','no_landed_sale','fallback_price','other','dust_limited','above_act_under_1h','below_120s_opportunity']:
        for split,g in e.groupby('split'):
            a=g[g[tag]];tags.append(dict(split=split,flag=tag,episodes=int(len(a)),lost=int(a.lost.sum()),loss_pct_of_group=100*a.lost.mean() if len(a) else 0,pct_of_split_losses=100*a.lost.sum()/g.lost.sum()))
    pd.DataFrame(rows).to_csv(D/'default_loss_breakdowns.csv',index=False);pd.DataFrame(tags).to_csv(D/'default_loss_reason_flags.csv',index=False)
    stat=[]
    for split,g in e[e.asset_group=='ETH-type'].groupby('split'):
        a=g[g.lost];dust=a[a.dust_limited];stat.append(dict(split=split,episodes=len(g),losses=len(a),dust_limited=int(a.dust_limited.sum()),dust_share_pct=100*a.dust_limited.mean(),dust_start_under_4c=int((dust.start_size_usd<.04).sum()),dust_max_candidate_notional=float(dust.max_candidate_notional_before_liq.max()),dust_median_lead_hours=float(dust.hours_decision_to_liq.median()),above_act_under_1h=int(a.above_act_under_1h.sum()),under_120s=int(a.below_120s_opportunity.sum()),order_blocks=int(a.order_blocking.sum()),reverts=int(a.revert.sum()),fallback=int(a.fallback_price.sum())))
    pd.DataFrame(stat).to_csv(D/'default_loss_eth_factors.csv',index=False)
    text=['## Why episodes are lost under the defaults',
    'Source: DEFAULT rows of `data/unsaved_cases.parquet`, reconciled exactly to DEFAULT in `data/evaluation_results.parquet`. Denominators are **all 19,794 eligible DEFAULT episodes**, including saved and survivor episodes; the 13 reconstruction failures are excluded. These are simulated policy liquidations, not a claim that every loss occurred in the observed borrower history. Instrumented replays in `data/default_execution_diagnostics.parquet` reproduce every original output metric without changing policy math. `data/default_loss_analysis.parquet` joins episode definitions, execution diagnostics and loss flags; CSV breakdowns retain all denominators.',
    '“Lost / episodes” and “loss_pct_of_group” mean losses divided by every eligible episode in that row’s group. “pct_of_split_losses” divides by all losses in the same train/holdout split. Split follows Step 2; year and ISO calendar week use the UTC **episode end**, not its liquidation time. Start size means collateral × replay oracle USD price, not debt or equity.',
    '[ASSUMPTION] Primary reason is a mutually exclusive diagnostic hierarchy: an order block before first liquidation, then a revert, then no sale landed before first liquidation, then any fallback-price span, otherwise other. It is not a causal attribution. The overlapping flag table also shows every applicable condition; fallback coverage alone does not prove that fallback caused liquidation. For surviving episodes, execution flags cover the full episode. A landed sale after first liquidation cannot count as saving that episode.',
    '[ASSUMPTION] The first decision is the first debt-bearing oracle update/minute tick, measured **before** the simulator’s liquidation check. This records opportunities lost to immediate liquidation rather than reporting the cleared post-liquidation balance. The gap is 100 × (oracle / last completed Binance minute close − 1) at the first blocked or reverted attempt before first liquidation; reverted fill gaps are also stored separately. Gaps are venue-equivalent for wstETH. LTV bands assign exactly 50% to 50–70%, exactly 70% to 50–70%, exactly 80% to 70–80%; zero-collateral starts are undefined. Size-band edges are lower-inclusive.',
    '### The dominant ETH-type factor']
    for z in stat:
        text.append(f"**{z['split']}: {z['dust_limited']:,} of {z['losses']:,} ETH-type losses ({z['dust_share_pct']:.2f}%) never queued a sale because every size-eligible attempt was below the simulator’s $0.01 minimum order.** {z['dust_start_under_4c']:,} of these started with less than $0.04 of collateral; with a 25% maximum slice, such a position normally cannot reach the minimum. The largest candidate sale among these failed attempts was ${z['dust_max_candidate_notional']:.8f}. Median time from first debt-bearing decision to liquidation was {z['dust_median_lead_hours']:.2f} hours. Under the frozen defaults these dust positions had no executable sale opportunity, even when they had time; earlier triggering alone does not remove the minimum-size constraint. Separately, {z['above_act_under_1h']:,} ETH-type losses were already above act at the first decision and had under one hour to liquidation; {z['under_120s']:,} had less than the 120-second fill delay (including a liquidation before the first decision). These categories can overlap.")
    text+=['This isolates the dominant **mechanical** factor in the training result; it does not establish that a different live executor could profitably rescue cents-sized debt. The default simulation’s sale-size floor is economically material to event-count survival. No dust filter, policy retuning, or Step 4 model has been introduced.','### Overlapping execution and opportunity flags',md(pd.DataFrame(tags))]
    for name,g in tables.items():text+=['### '+name,md(g)]
    gap=e[e.lost & e.first_failed_sale_gap_pct.notna()].groupby('split').first_failed_sale_gap_pct.agg(['count','min','median','max']).reset_index();text+=['### Gap magnitudes among losses with a blocked or reverted sale',md(gap),'Full numeric first-block, first-revert and fill-gap values, timing, owner-risk actions, and liquidation trigger kind are in `data/default_loss_analysis.parquet`. Dust attempts are distinguished from explicit price blocks. Other includes insufficient protection despite landed sales or subsequent owner flows; the data do not justify assigning every residual loss to one cause.']
    status=json.loads((D/'loss_diagnostics_status.json').read_text());text+= [f"Diagnostics took {status['elapsed_s']:.3f}s; {status['unchanged_metrics_verified']:,} original simulator metrics were checked. No data source was queried. Reproduce with `python3 -I scripts/loss_analysis.py` followed by `python3 -I scripts/report_losses.py` from this folder using the local virtual environment."]
    section='\n\n'.join(text)+'\n';section=section.replace('because every size-eligible attempt was below','because every attempt passing the price-gap check was below');readme=ROOT/'README.md';s=readme.read_text();s=s.split('\n## Why episodes are lost under the defaults')[0];s=s.replace('Stopped after Step 2. No retrieval table, risk model, LLM calls, secrets, or commits were produced.','Step 2 completed. The decisions above were accepted before the Step 3 work below.');readme.write_text(s.rstrip()+'\n\n'+section)
    (D/'loss_report_status.json').write_text(json.dumps(dict(elapsed_s=time.time()-start,eth_factors=stat),indent=2)+'\n');print(json.dumps(stat,indent=2))
if __name__=='__main__':main()
