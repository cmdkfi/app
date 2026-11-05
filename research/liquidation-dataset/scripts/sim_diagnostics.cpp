// Acceleration of sim_reference.py's Morpho P2 path, without signals or relever.
// All calibration runs are checked against the unchanged Python reference first.
#include <algorithm>
#include <cmath>
#include <cstdint>
#include <deque>
#include <vector>
struct Order {double t,q;};
extern "C" void replay(const double* ev,int ne,const int64_t* ot,const double* op,int no,
    const double* o,const double* h,const double* l,const double* cl,int nm,int64_t mt,
    const double* cfg,double* out) {
    // cfg: C0,D0,t0,end,rate,lltv,incentive,gas,act,target,budget,slice,cooldown,
    // enabled,actual,bottom_ts,decision_every,record_every
    double C=cfg[0],D=cfg[1],tlast=cfg[2],end=cfg[3],rate=cfg[4],lt=cfg[5],inc=cfg[6],gas=cfg[7];
    double act=cfg[8],target=cfg[9],budget=cfg[10],slice=cfg[11],cool=cfg[12],bottom=cfg[15];
    bool enabled=cfg[13],actual=cfg[14];int every=int(cfg[16]);
    double penalty=0,bad=0,gaspaid=0,slip=0,cash=0,borrowclamp=0,withdrawshort=0,sold=0,maxltv=0;
    int nliq=0,nsales=0,reverts=0,blocked=0;double firstliq=0,pendingq=0,lastdec=-1e18,sessstart=0;bool session=false;
    std::deque<Order> pending;std::vector<Order> sellhist;
    double Cb=C,Db=D,adjB=0,shortB=0,nextrec=cfg[2];
    double fd=0,fdl=0,fb=0,fbg=0,fr=0,frg=0,frfg=0,ftiny=0,ntiny=0,nbpre=0,nrpre=0,nspre=0,fo=0,fs=0,nact=0,lastflow=0,lastflowL=0,liqkind=-1,liqL=0,maxnot=0;int currentkind=-1;
    auto oracle=[&](double t){int i=int(std::upper_bound(ot,ot+no,int64_t(t))-ot)-1;return op[std::max(i,0)];};
    auto mi=[&](double t){return std::min(std::max(int((t-mt)/60),0),nm-1);};
    auto accrue=[&](double t){if(t>tlast && D>0)D*=std::exp(rate*(t-tlast)/(365.*86400));tlast=std::max(tlast,t);};
    auto ltv=[&](double px){return C>1e-12?D/(C*px):(D<=1e-9?0.:INFINITY);};
    auto liq=[&](double t,double px){int n=0;while(D>1e-6 && C>1e-12 && ltv(px)>lt && n<20){
        if(!nliq){liqkind=currentkind;liqL=ltv(px);}
        double repaid=D,seized=repaid*(1+inc)/px;if(seized>C){seized=C;repaid=C*px/(1+inc);}
        penalty+=seized*px-repaid;C-=seized;D-=repaid;if(C<=1e-12 && D>1e-6){bad+=D;D=0;}
        if(!nliq)firstliq=t;++nliq;++n;
    }};
    auto used=[&](double t){double u=0;for(auto x:sellhist)if(x.t>t-86400)u+=x.q;return u;};
    auto land=[&](double t,Order od){
        pendingq-=od.q;if(C<=1e-12)return;double px=oracle(t),L=ltv(px);gaspaid+=gas;double q=od.q;
        if(L>act){if(!session)sessstart=t;session=true;}
        if(session && (L<=target || t-sessstart>86400))session=false;
        double floor=.30,m;bool disc;
        if(session){m=target;disc=false;}else{
            if(L<=floor)return;double left=std::max(budget-used(t),0.)*C;
            q=std::min(q,left);m=std::max(floor,target);disc=true;
        }
        int i=mi(t);double raw=(o[i]+h[i]+l[i]+cl[i])/4;
        double notional=q*raw,s=notional<1e5?.0005:std::min(.01,.0005+.001*notional/1e6),pf=raw*(1-s);
        if(pf<px*.99){if(!firstliq){++nrpre;if(!fr){fr=t;frg=100*(px/cl[std::max(mi(t)-1,0)]-1);frfg=100*(px/pf-1);}}++reverts;return;}if(L<=m)return;
        double qmax=pf>m*px?(D-m*px*C)/(pf-m*px):C;
        q=std::max(std::min({q,qmax,C,D/pf}),0.);if(q<=0)return;
        if(!firstliq){++nspre;if(!fs)fs=t;}C-=q;D-=q*pf;slip+=q*(raw-pf);++nsales;sold+=q;if(disc)sellhist.push_back({t,q/(C+q)});
    };
    auto decide=[&](double t,double px){
        if(!enabled || actual || t-lastdec<cool || C<=1e-12)return;
        double pm=cl[std::max(mi(t)-1,0)],Cp=C-pendingq,Dp=D-pendingq*pm;if(Cp<=1e-12)return;
        double L=Dp/(Cp*px);bool active=session && t-sessstart<=86400;
        if(!(L>act || (active && L>target)))return;
        if(!firstliq)++nact;
        double pe=pm*.999,denom=1-target*px/pe,R=denom>0?(Dp-target*px*Cp)/denom:Dp;
        double q=std::min(R/pe,slice*Cp),gap=px/pm-1,allowed=.009-gap;
        double nmax=allowed<.0005?0.:(allowed>=.01?INFINITY:std::max(.999e5,(allowed-.0005)/.001*1e6));
        if(nmax<=0){if(!firstliq){++nbpre;if(!fb){fb=t;fbg=100*gap;}}++blocked;lastdec=t;return;}q=std::min(q,nmax/pm);if(!firstliq)maxnot=std::max(maxnot,q*pm);if(q*pm<.01){if(!firstliq){++ntiny;if(!ftiny)ftiny=t;}return;}
        if(!firstliq && !fo)fo=t;lastdec=t;pending.push_back({t+120,q});pendingq+=q;
    };
    for(int j=0;j<ne;++j){const double* e=ev+5*j;double t=e[0];int kind=int(e[1]);if(kind==5 && !actual)continue;
        while(!pending.empty() && pending.front().t<=t){auto od=pending.front();pending.pop_front();currentkind=7;accrue(od.t);liq(od.t,oracle(od.t));land(od.t,od);liq(od.t,oracle(od.t));}
        accrue(t);double px=e[4];currentkind=kind;
        if(!fd && (kind==0 || kind==6) && C>1e-12 && D>1e-6){fd=t;fdl=ltv(px);}
        if(kind==0){if(!actual)liq(t,px);if(every==60)decide(t,px);}
        else if(kind>=1 && kind<=4){double amt=e[2];
            if(kind==1)C+=amt;
            if(kind==2){double maxw=C-(D>0?D/(lt*px):0.),w=std::min({amt,std::max(maxw,0.),C});if(w<amt-1e-12)withdrawshort+=amt-w;C-=w;}
            if(kind==3){double b=std::min(amt,std::max(lt*C*px-D,0.));if(b<amt-1e-6)borrowclamp+=amt-b;D+=b;}
            if(kind==4){double r=std::min(amt,D);if(r<amt-1e-6)cash+=amt-r;D-=r;}
            if((kind==2 || kind==3) && !firstliq){lastflow=t;lastflowL=ltv(px);}
            if(!actual)liq(t,px);
        }else if(kind==5 && actual){penalty+=e[2]*inc;if(!nliq)firstliq=t;++nliq;C=std::max(C-e[3],0.);D=std::max(D-e[2],0.);}
        else if(kind==6){if(!actual)liq(t,px);if(every==60 || int64_t(t-cfg[2])%every==0)decide(t,px);}
        if(C>1e-12)maxltv=std::max(maxltv,ltv(px));
        if(t>=nextrec){if(t<=bottom){Cb=C;Db=D;adjB=cash-borrowclamp-gaspaid;shortB=withdrawshort;}nextrec=t+cfg[17];}
    }
    accrue(end);
    double vals[]={C,D,penalty,bad,gaspaid,slip,cash,borrowclamp,withdrawshort,double(nliq),double(nsales),sold,double(reverts),double(blocked),maxltv,Cb,Db,adjB,shortB,firstliq};
    std::copy(vals,vals+20,out);
    double extra[]={fd,fdl,fb,fbg,fr,frg,frfg,ftiny,ntiny,nbpre,nrpre,nspre,fo,fs,nact,lastflow,lastflowL,liqkind,liqL,maxnot};
    std::copy(extra,extra+20,out+20);
}
