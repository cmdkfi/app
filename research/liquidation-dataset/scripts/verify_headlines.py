"""Recompute the README's headline counts from the published files (no release assets needed), in about a minute.

Run from this folder with a Python that has pandas and pyarrow (`requirements.txt`): `python3 -I scripts/verify_headlines.py`.
Prints each figure as computed next to the README's value and exits 1 on any difference.

Inputs, all in `data/`: `liquidations.parquet` (the liquidation records; `included_steps_2_4` marks the Morpho rows the
replay uses), `selected_and_default_results.parquet` (the corridor replay per episode; `n_liqs` is the number of
liquidations under the policy), `step4_episode_cohorts.parquet` (collateral value at the episode start in USD,
and the Binance-fallback flag), `replay_baselines.parquet` (the ACTUAL baseline: the liquidations the borrower really
suffered inside the replay window, `n_liqs`).

Definitions (README, "Default headline, venue-feed episodes only"): episodes of the DEFAULT policy whose start value is
at or above the cut and whose prices come from the venue feed (no Binance fallback); "observed liquidated" = the
ACTUAL baseline shows at least one liquidation in the window; "saved" = observed liquidated and zero liquidations under
the default corridor.
"""
import sys
from pathlib import Path
import pandas as pd

D = Path(__file__).resolve().parents[1] / 'data'
README = {  # (cut_usd, split): (episodes, observed liquidated, saved)
    (1000, 'train'): (2468, 1895, 1890), (1000, 'holdout'): (5897, 5011, 5009),
    (100, 'train'): (3174, 2475, 2469), (100, 'holdout'): (8174, 7038, 7034),
}
README_MORPHO = 20479

ok = True
def show(label, got, want):
    global ok
    flag = 'ok' if got == want else 'DIFFERS'
    ok &= got == want
    print(f'{label:<52} computed {got:>9,}   README {want:>9,}   {flag}')

liq = pd.read_parquet(D / 'liquidations.parquet')
show('Morpho Blue liquidations in the replay (2024 - Oct 2026)', int(liq[liq.venue == 'Morpho Blue'].included_steps_2_4.sum()), README_MORPHO)

res = pd.read_parquet(D / 'selected_and_default_results.parquet')
coh = pd.read_parquet(D / 'step4_episode_cohorts.parquet')[['case_id', 'start_size_usd']]
act = pd.read_parquet(D / 'replay_baselines.parquet')
act = act[act.policy == 'ACTUAL'][['case_id', 'n_liqs']].rename(columns={'n_liqs': 'observed_liqs'})
d = res[res.policy == 'DEFAULT'].merge(coh, on='case_id').merge(act, on='case_id', how='left')
assert d.observed_liqs.notna().all(), 'every DEFAULT episode has an ACTUAL baseline'
for (cut, split), want in README.items():
    x = d[(d.start_size_usd >= cut) & (~d.binance_fallback) & (d.split == split)]
    observed = x.observed_liqs > 0
    got = (len(x), int(observed.sum()), int((observed & (x.n_liqs == 0)).sum()))
    for name, g, w in zip(('episodes', 'observed liquidated', 'saved vs observed'), got, want):
        show(f'>= ${cut:,} {split}: {name}', g, w)

print('all match' if ok else 'DIFFERENCES above')
sys.exit(0 if ok else 1)
