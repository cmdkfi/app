import sys, os, numpy as np, pandas as pd; sys.path.insert(0, os.path.dirname(__file__))
from prep import *; from sim import *
pd.set_option("display.width", 250)
cs = pd.read_csv(RAW + "/aave_cases.csv"); print(cs[["borrower","selection","collateral","debt","liq_repaid_usd","liq_seized","n_liqs","lt","bonus","borrow_apr","last_own_action_ts"]].to_string())
for c in aave_cases():
    mkt, orc = inputs_for(c)
    st, se = run(c, Policy("actual", enabled=False), mkt, orc, record_every=60, actual=True)
    st0, se0 = run(c, Policy("noaction", enabled=False), mkt, orc, record_every=60)
    h = c.hourly; errs = []
    for _, r in h[(h.ts > c.t0) & (h.ts <= c.t_end)].iterrows():
        Cs, Ds, _, _ = state_at(se, r.ts)
        errs.append((abs(Cs - r.coll_amt) / max(r.coll_amt, 1e-9) if r.coll_amt > 1e-6 else abs(Cs), abs(Ds + c.meta['other_collateral_lt_credit'] - r.debt_amt) / max(r.debt_amt, 1)))
    e = np.array(errs); h0 = h[h.ts == c.t0].iloc[0]
    fl = [l["t"] for l in st0["liqs"]]
    print(f"{c.case_id} {c.meta['asset']} C0={c.C0:.2f} D0={c.D0:,.0f} HF0={h0.hf:.3f} LTV0={c.D0/(c.C0*orc.at(c.t0)[0]):.3f} coll_share={c.meta['coll_share_T0']:.2f} debt_share={c.meta['debt_share_T0']:.2f} flows={len(c.flows)} | replay err coll={e[:,0].max():.2%} debt={e[:,1].max():.2%} | real 1st liq {pd.Timestamp(c.meta['first_liq_ts'],unit='s')} n={c.meta['n_liqs']} pen=${st['penalty']:,.0f} | model 1st {pd.Timestamp(fl[0],unit='s') if fl else None} n={len(fl)} pen=${st0['penalty']:,.0f}")
