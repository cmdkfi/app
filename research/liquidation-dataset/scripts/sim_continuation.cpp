// Same continuation construction as the original continue_today.py:
// repeated completed hourly closes, minute ticks, no later owner flows.
#include "sim_grid.cpp"
extern "C" void continue_replay(const int64_t* ot,const double* op,int no,
    const double* prices,int nm,int64_t mt,const double* cfg,double* out) {
    std::vector<double> events;
    events.reserve((size_t)((cfg[3]-cfg[2])/60+100)*5);
    int oi=int(std::upper_bound(ot,ot+no,int64_t(cfg[2]))-ot)-1;
    for(int64_t t=int64_t(cfg[2]);t<=int64_t(cfg[3]);t+=60){
        while(oi+1<no && ot[oi+1]<=t)++oi;
        double px=op[std::max(oi,0)];
        if(t%3600==0)events.insert(events.end(),{double(t),0.,0.,0.,px});
        if(t>cfg[2])events.insert(events.end(),{double(t),6.,0.,0.,px});
    }
    replay(events.data(),int(events.size()/5),ot,op,no,prices,prices,prices,prices,nm,mt,cfg,out);
}
