"""Final artifact consistency and holdout audit, without rerunning simulations."""
import json
from pathlib import Path
import numpy as np
import pandas as pd
D=Path(__file__).resolve().parents[1]/'data'
def read(n):return json.loads((D/n).read_text())
evaluation=pd.read_parquet(D/'evaluation_results.parquet');continuation=pd.read_parquet(D/'continuation_results.parquet')
assert not evaluation.duplicated(['case_id','policy']).any()
assert not continuation.duplicated(['case_id','policy']).any()
assert set(evaluation.policy)=={'DEFAULT','SELECTED'}
assert (evaluation.groupby('case_id').size()==2).all()
assert (continuation.groupby('case_id').size()==3).all()
assert set(continuation.case_id)==set(evaluation.case_id)
for field in ['C_end','D_end','penalty','gas','slip_cost','equity_end','equity_bottom','equity_today_managed']:
    assert np.isfinite(evaluation[field]).all(),field
for field in ['C_end','D_end','penalty','gas','slip_cost','n_liqs','n_sales']:assert (evaluation[field]>=-1e-8).all(),field
assert np.isfinite(continuation.equity_today_managed).all()
assert ((evaluation.saved_with_model_support)&~evaluation.saved_from_observed_liquidation).sum()==0
frozen=read('selected_policy.json');holdout=read('step2_replay_holdout_status.json');selected=read('step2_replay_select_status.json')
assert frozen['selected_at']<=selected['finished_at']<=holdout['started_at']
assert frozen['holdout_used_for_selection'] is False
closure=read('step1_closure.json')
assert pd.Timestamp(closure['closed_at']).timestamp()<=read('step2_market_history_status.json')['started_at']
headline=pd.read_csv(D/'headline.csv');perasset=pd.read_csv(D/'per_asset.csv')
for h in headline.itertuples():
    rows=evaluation[(evaluation.split==h.split)&(evaluation.policy==h.policy)]
    assert len(rows)==h.cases and int((rows.n_liqs>0).sum())==h.policy_liquidated
    assert abs(rows.equity_vs_actual_end.sum()-h.equity_vs_actual_end)<.01
    assets=perasset[(perasset.split==h.split)&(perasset.policy==h.policy)]
    assert assets.cases.sum()==h.cases and assets.policy_liquidated.sum()==h.policy_liquidated
assert len(pd.read_parquet(D/'aave_target_subset_side_table.parquet'))==911
assert not evaluation.case_id.str.contains('Aave',case=False).any()
checks=dict(eligible_episodes=evaluation.case_id.nunique(),case_policy_rows=len(evaluation),continuation_rows=len(continuation),
            headline_rows=len(headline),per_asset_rows=len(perasset),no_duplicate_case_policy_rows=True,finite_outputs=True,
            step1_closed_before_step2=True,selection_frozen_before_holdout=True,aave_excluded=True)
(D/'final_validation.json').write_text(json.dumps(checks,indent=2)+'\n');print(json.dumps(checks,indent=2))
