"""Offline diagnostics of DEFAULT, instrumenting without changing simulator math."""
from pathlib import Path
import importlib.util,sys,ctypes,subprocess,time,json,concurrent.futures as cf
import numpy as np
import pandas as pd
S=Path(__file__).resolve().parent;D=S.parent/'data'
spec=importlib.util.spec_from_file_location('replay',S/'replay.py');r=importlib.util.module_from_spec(spec);sys.modules['replay']=r;spec.loader.exec_module(r)
EXTRA=['first_decision_ts','first_decision_ltv','first_block_ts','first_block_gap_pct','first_revert_ts','first_revert_gap_pct','first_revert_fill_gap_pct','first_dust_ts','dust_attempts_before_liq','blocked_before_liq','reverts_before_liq','sales_before_liq','first_order_ts','first_sale_ts','act_attempts_before_liq','last_owner_risk_ts','last_owner_risk_ltv','liq_trigger_kind','first_liq_ltv','max_candidate_notional_before_liq']
def compile_diagnostics():
    src=(S/'sim_grid.cpp').read_text()
    src=src.replace('    auto oracle=', '''    double fd=0,fdl=0,fb=0,fbg=0,fr=0,frg=0,frfg=0,ftiny=0,ntiny=0,nbpre=0,nrpre=0,nspre=0,fo=0,fs=0,nact=0,lastflow=0,lastflowL=0,liqkind=-1,liqL=0,maxnot=0;int currentkind=-1;
    auto oracle=''',1)
    src=src.replace('        double repaid=D,', '        if(!nliq){liqkind=currentkind;liqL=ltv(px);}\n        double repaid=D,',1)
    src=src.replace('if(pf<px*.99){++reverts;return;}', 'if(pf<px*.99){if(!firstliq){++nrpre;if(!fr){fr=t;frg=100*(px/cl[std::max(mi(t)-1,0)]-1);frfg=100*(px/pf-1);}}++reverts;return;}',1)
    src=src.replace('C-=q;D-=q*pf;', 'if(!firstliq){++nspre;if(!fs)fs=t;}C-=q;D-=q*pf;',1)
    src=src.replace('        double pe=pm*.999,', '        if(!firstliq)++nact;\n        double pe=pm*.999,',1)
    src=src.replace('if(nmax<=0){++blocked;', 'if(nmax<=0){if(!firstliq){++nbpre;if(!fb){fb=t;fbg=100*gap;}}++blocked;',1)
    src=src.replace('if(q*pm<.01)return;', 'if(!firstliq)maxnot=std::max(maxnot,q*pm);if(q*pm<.01){if(!firstliq){++ntiny;if(!ftiny)ftiny=t;}return;}',1)
    src=src.replace('lastdec=t;pending.push_back', 'if(!firstliq && !fo)fo=t;lastdec=t;pending.push_back',1)
    src=src.replace('auto od=pending.front();pending.pop_front();', 'auto od=pending.front();pending.pop_front();currentkind=7;',1)
    src=src.replace('        accrue(t);double px=e[4];', '''        accrue(t);double px=e[4];currentkind=kind;
        if(!fd && (kind==0 || kind==6) && C>1e-12 && D>1e-6){fd=t;fdl=ltv(px);}''',1)
    src=src.replace('            if(!actual)liq(t,px);', '            if((kind==2 || kind==3) && !firstliq){lastflow=t;lastflowL=ltv(px);}\n            if(!actual)liq(t,px);',1)
    src=src.replace('    std::copy(vals,vals+20,out);', '''    std::copy(vals,vals+20,out);
    double extra[]={fd,fdl,fb,fbg,fr,frg,frfg,ftiny,ntiny,nbpre,nrpre,nspre,fo,fs,nact,lastflow,lastflowL,liqkind,liqL,maxnot};
    std::copy(extra,extra+20,out+20);''')
    (S/'sim_diagnostics.cpp').write_text(src)
    subprocess.run(['c++','-std=c++17','-O3','-dynamiclib',str(S/'sim_diagnostics.cpp'),'-o',str(S/'sim_diagnostics.dylib')],check=True)
    lib=ctypes.CDLL(str(S/'sim_diagnostics.dylib'));lib.replay.argtypes=r.library().replay.argtypes;lib.replay.restype=None;return lib

def run():
    start=time.time();lib=compile_diagnostics();data=r.Data()
    p=pd.read_parquet(D/'replay_case_plan.parquet').set_index('case_id');ev=pd.read_parquet(D/'evaluation_results.parquet');ev=ev[ev.policy=='DEFAULT'].copy()
    def one(e):
        row=p.loc[e['case_id']].to_dict();row['case_id']=e['case_id'];inp=data.inputs(row);pol=r.DEFAULT
        cfg=np.array([row['C0'],row['D0'],row['t0'],row['t_end'],inp['rate'],row['lltv'],1/(.3*row['lltv']+.7)-1,.03 if row['chain']==8453 else 5.,pol['act'],pol['target'],pol['budget'],pol['slice'],pol['cooldown'],1,0,inp['bottom_ts'],60,60],dtype=float)
        out=np.zeros(40);o,h,l,cl=inp['bars'];lib.replay(inp['ev'],len(inp['ev']),inp['ot'],inp['op'],len(inp['ot']),o,h,l,cl,len(cl),inp['mt'],cfg,out)
        for k,v in zip(r.OUTPUT,out[:20]):assert np.isclose(v,e[k],rtol=1e-9,atol=1e-7),(e['case_id'],k,v,e[k])
        return dict(case_id=e['case_id'],**dict(zip(EXTRA,out[20:])))
    with cf.ThreadPoolExecutor(max_workers=4) as pool:
        rows=[]
        for i,x in enumerate(pool.map(one,ev.to_dict('records'),buffersize=8)):
            rows.append(x)
            if i%2000==0:print('diagnostics',i,flush=True)
    diag=pd.DataFrame(rows);diag.to_parquet(D/'default_execution_diagnostics.parquet',index=False)
    elapsed=time.time()-start;r.save(D/'loss_diagnostics_status.json',dict(elapsed_s=elapsed,started_at=start,finished_at=time.time(),cases=len(ev),unchanged_metrics_verified=len(ev)*20));print('done',elapsed,flush=True)

if __name__=='__main__':run()
