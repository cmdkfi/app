"""Offline data integrity and label checks; no Step 2 calculations."""
import bisect
import collections
import json
from pathlib import Path
import pandas as pd
import pyarrow.parquet as pq

ROOT=Path(__file__).resolve().parents[1];DATA=ROOT/'data'
END=int(pd.Timestamp('2026-10-26',tz='UTC').timestamp())
checks={};failures=[]
liqs=pd.read_parquet(DATA/'liquidations.parquet')
assert not liqs.duplicated(['chain','tx','log']).any()
assert liqs.ts.notna().all()
assert (liqs.ts<END).all()
assert (liqs.repaid_raw.map(int)>=0).all()
assert (liqs.seized_raw.map(int)>=0).all()
checks['unique_liquidation_events']=len(liqs)
times=collections.defaultdict(list)
for r in liqs.itertuples():times[(r.chain,r.market,r.borrower)].append(int(r.ts))
for key in times:times[key].sort()
borrowers=pd.read_parquet(DATA/'borrowers.parquet')
assert not borrowers.duplicated(['chain','market','borrower']).any()
seen_group=None;seen_hours=set();n=0;positives6=0;positives24=0
counts=collections.Counter()
for batch in pq.ParquetFile(DATA/'positions_hourly.parquet').iter_batches(batch_size=50000):
    for r in batch.to_pylist():
        key=(r['chain'],r['market'],r['borrower']);t=r['ts']
        if key!=seen_group:seen_group=key;seen_hours=set()
        assert t not in seen_hours,('duplicate hour',key,t)
        seen_hours.add(t)
        assert t%3600==0
        assert t<END
        if r['missing_position_hour']:
            assert r['collateral'] is None and r['debt'] is None
        else:assert r['collateral']>=0 and r['debt']>=0
        if r['ltv'] is not None and r['oracle_px'] is not None:
            expected=r['debt']/(r['collateral']*r['oracle_px'])
            assert abs(r['ltv']-expected)<1e-9*max(1,expected)
        events=times.get(key,[]);i=bisect.bisect_right(events,t)
        for horizon,col in [(6,'liquidated_within_6h'),(24,'liquidated_within_24h')]:
            positive=i<len(events) and events[i]<=t+horizon*3600
            observed=t+horizon*3600<END
            expected=positive if positive or observed else None
            if r[f'label_window_missing_{horizon}h']:expected=None
            assert r[col]==expected,(key,t,col,r[col],expected)
        positives6+=bool(r['liquidated_within_6h']);positives24+=bool(r['liquidated_within_24h'])
        counts[key]+=1;n+=1
assert sum(borrowers.hourly_rows)==n
for row in borrowers.itertuples():assert counts[(row.chain,row.market,row.borrower)]==row.hourly_rows
checks.update(hourly_rows=n,borrowers=len(borrowers),positive_6h=positives6,positive_24h=positives24)
prices=pd.read_parquet(DATA/'binance_1h_ext.parquet')
assert not prices.duplicated(['symbol','open_time']).any()
for symbol,group in prices.groupby('symbol'):
    stamps=group.open_time.sort_values()
    assert (stamps.diff().dropna()==3600000).all()
    assert (group.low<=group.open).all() and (group.open<=group.high).all()
    assert (group.low<=group.close).all() and (group.close<=group.high).all()
    assert stamps.iloc[-1]+3600000==END*1000
checks['binance_rows']=len(prices)
oracle=pd.read_csv(DATA/'oracle_rounds_ext.csv')
assert not oracle.duplicated(['chain','aggregator','round_id']).any()
assert (oracle.answer>0).all() and (oracle.updated_at>0).all()
checks['oracle_rows']=len(oracle)
for key,g in oracle.groupby(['chain','aggregator']):
    if not (g.sort_values('round_id').round_id.diff().dropna()==1).all():failures.append({'gap_in_cached_round_sequence':list(key)})
checks['coverage_warnings']=failures
round_bounds=[]
for path in (DATA/'raw/chainlink').rglob('bounds.json'):
    bound=json.loads(path.read_text())
    group=oracle[oracle.aggregator.str.lower()==bound['aggregator'].lower()]
    actual=set(group.round_id.astype(int));expected=set(range(bound['first'],bound['last']+1))
    assert expected<=actual,('missing requested rounds',str(path),len(expected-actual))
    round_bounds.append({'aggregator':bound['aggregator'],'requested':len(expected)})
checks['verified_round_intervals']=len(round_bounds)
survivor_total=0
for path in (DATA/'survivor_selection').glob('*.json'):
    selection=json.loads(path.read_text());draw=json.loads((DATA/'survivor_draws'/path.name).read_text())
    key=selection['market'];chain_s,market=key.split('-',1);chain=int(chain_s)
    if market not in set(borrowers.market) and selection['selected']==0:continue
    selected=[]
    for user in draw['ordered_borrowers'][:selection['screened']]:
        row=json.loads((DATA/'raw/morpho_survivors'/key/f'{user}.json').read_text())
        if not row['qualified']:continue
        t=row['anchor_ts'];events=times.get((chain,market,user),[])
        assert t+30*86400<END
        assert not any(t<event<=t+30*86400 for event in events)
        found=False
        for page in row['history']:
            coll={x['x']:x['y'] for x in page['collateralValue'] or []}
            debt={x['x']:x['y'] for x in page['borrowAssets'] or []}
            if coll.get(t) is not None and int(coll[t])>0 and debt.get(t) is not None:
                assert int(debt[t])/int(coll[t])>.7;found=True
        assert found,('qualifying hour missing',key,user)
        selected.append(user)
    assert selected==selection['selected_borrowers']
    assert len(selected)==selection['selected']<=2000
    survivor_total+=len(selected)
checks['verified_survivor_samples']=survivor_total
(DATA/'validation.json').write_text(json.dumps(checks,indent=2)+'\n')
readme=ROOT/'README.md'
marker='\n## Offline validation\n'
report=(f'\nChecks in `scripts/validate.py` passed for {n:,} hourly rows, '
        f'{len(borrowers):,} sampled borrower positions, {len(liqs):,} unique liquidation events, '
        f'{survivor_total:,} selected survival episodes, {len(prices):,} Binance candles, '
        f'and {len(oracle):,} oracle rounds. Results: `data/validation.json`. '
        'Checks cover unique keys, LTV arithmetic, liquidation horizons and censoring, '
        'seeded survivor selection and follow-up, continuous hourly candles, '
        'and complete requested oracle round ranges. These checks establish internal '
        'consistency of the saved data; they do not resolve the documented source-coverage gaps.\n')
readme.write_text(readme.read_text().split(marker)[0]+marker+report)
print(json.dumps(checks,indent=2))
