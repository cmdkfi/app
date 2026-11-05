import sys, os, numpy as np, pandas as pd; sys.path.insert(0, os.path.dirname(__file__))
from prep import *; from sim import *
for c in morpho_cases():
    mkt, orc = inputs_for(c)
    st, se = run(c, Policy("actual", enabled=False), mkt, orc, record_every=60, actual=True)
    st0, se0 = run(c, Policy("noaction", enabled=False), mkt, orc, record_every=60)
    h = c.hourly; errs = []
    for _, r in h[(h.ts > c.t0) & (h.ts <= c.t_end)].iterrows():
        Cs, Ds, _, _ = state_at(se, r.ts)
        errs.append((abs(Cs - r.collateral) / max(r.collateral, 1e-9) if r.collateral > 1e-8 else abs(Cs), abs(Ds - r.debt) / max(r.debt, 1)))
    e = np.array(errs)
    fl = [l["t"] for l in st0["liqs"]]
    print(f"{c.case_id:24s} rate={c.rate:.3%} C0={c.C0:.4f} D0={c.D0:,.0f} | replay vs API hourly: max coll err={e[:,0].max():.2%} max debt err={e[:,1].max():.2%} | real first liq {pd.Timestamp(c.meta['first_liq_ts'],unit='s')} n={c.meta['n_liqs']} pen=${st['penalty']:,.0f} | modelled no-action first liq {pd.Timestamp(fl[0],unit='s') if fl else None} pen=${st0['penalty']:,.0f}")
