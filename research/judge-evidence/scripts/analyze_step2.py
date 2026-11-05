#!/usr/bin/env python3
"""Step 2 offline analysis; no inference. All README measurements computed here.

python3 -I scripts/analyze_step2.py [--partial]
Full output requires every planned response; partial output never decides retrieval.
"""
import argparse
import hashlib
import json
import statistics
import sys
from collections import Counter
from pathlib import Path
assert sys.flags.isolated
import importlib.util
def module(name,path):
    spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
s=module('step2',Path(__file__).with_name('step2.py'))
a=module('analysis1',Path(__file__).with_name('analyze_step1.py'))
g=module('agreement',Path(__file__).with_name('agreement.py'))
a.gate=g.gate  # Only this evaluation; historical Step 1 artifacts remain immutable.
h=s.h

def score(rows,which,d,run):
    score=a.summarize(rows,which,d,run)
    labeled=[]
    for r in rows:
        if not r['measured']:continue
        lbl=h.label_at(d['ground_truth'],run['arch'],h.P(r['t']))
        r['label']=[lbl['risk'],lbl['action']] if lbl else None
        if lbl:labeled.append((r[which],lbl))
    score.update(label_rows=len(labeled),
        action_matches=sum(v['action']==l['action'] for v,l in labeled),
        risk_matches=sum(v['risk']==l['risk'] for v,l in labeled))
    score['action_agreement']=score['action_matches']/len(labeled) if labeled else None
    score['risk_agreement']=score['risk_matches']/len(labeled) if labeled else None
    score['first_protect']=a.first(rows,which,lambda v:v['action'] in ('exit','reduce_to_target'))
    score['first_reduce']=a.first(rows,which,lambda v:v['action'] in ('exit','reduce_to_target','reduce_light'))
    score['exit_worked']=h.holder_ok(a.prepare_items(run,s.DATA)[1],h.P(score['first_exit']),run['side']) if score['first_exit'] else None
    return score

def aggregate(windows,which):
    inc=[w[which] for w in windows if w['run'].startswith('INC')]
    ctl=[w[which] for w in windows if w['run'].startswith('CTL')]
    allw=inc+ctl
    leads=[w['lead_min'] for w in inc if w['lead_min'] is not None]
    labels=sum(w['label_rows'] for w in allw)
    return dict(incidents=len(inc),controls=len(ctl),useful_exits=sum(w['useful'] for w in inc),
        before_ponr=sum(w['before_ponr'] for w in inc),incident_exits=sum(bool(w['first_exit']) for w in inc),
        lead_median_min=statistics.median(leads) if leads else None,
        control_exits=sum(bool(w['first_exit']) for w in ctl),
        control_high=sum(bool(w['first_high']) for w in ctl),
        control_exit_or_high=sum(w['exit_or_high'] for w in ctl),
        label_rows=labels,action_matches=sum(w['action_matches'] for w in allw),risk_matches=sum(w['risk_matches'] for w in allw),
        action_agreement=sum(w['action_matches'] for w in allw)/labels if labels else None,
        risk_agreement=sum(w['risk_matches'] for w in allw)/labels if labels else None)

def experiment(name,tasks,cache,folder):
    windows=[]
    for run in s.PRIMARY:
        d,items,rows=a.timeline(run,folder,tasks,cache)
        windows.append(dict(run=run['run'],raw=score(rows,'raw',d,run),
            after_agreement=score(rows,'after_agreement',d,run),timeline=rows))
    return dict(windows=windows,aggregate={w:aggregate(windows,w) for w in ('raw','after_agreement')})

def matched_labels(left,right,which):
    matches=Counter()
    for lw,rw in zip(left['windows'],right['windows']):
        assert lw['run']==rw['run']
        rm={r['t']:r for r in rw['timeline'] if r['measured']}
        for r in lw['timeline']:
            rr=rm.get(r['t'])
            if not r['measured'] or not rr or not r.get('label'):continue
            matches['rows']+=1
            for name,row in [('left',r),('right',rr)]:
                matches[name+'_action']+=row[which]['action']==r['label'][1]
                matches[name+'_risk']+=row[which]['risk']==r['label'][0]
    return dict(matches)

def comparison(reference,trial,which):
    rows=[]
    for rw,tw in zip(reference['windows'],trial['windows']):
        assert rw['run']==tw['run']
        if not rw['run'].startswith('INC'):continue
        r,t=rw[which]['first_exit'],tw[which]['first_exit']
        if r and t:delta=(h.P(r)-h.P(t)).total_seconds()/60;status='earlier' if delta>0 else 'later' if delta<0 else 'same'
        elif r:delta=None;status='lost_exit'
        elif rw[which].get('unmeasured_after_first_exit'):delta=None;status='reference_censored'
        elif t:delta=None;status='new_exit'
        else:delta=None;status='neither_exit'
        rows.append(dict(run=rw['run'],reference_exit=r,trial_exit=t,lead_gain_min=delta,status=status))
    ra,ta=reference['aggregate'][which],trial['aggregate'][which]
    worse_lead=any(r['status'] in ('later','lost_exit') for r in rows)
    better_lead=any(r['status'] in ('earlier','new_exit') for r in rows)
    alarms_worse=any(ta[k]>ra[k] for k in ('control_exits','control_exit_or_high'))
    alarms_better=any(ta[k]<ra[k] for k in ('control_exits','control_exit_or_high'))
    useful_worse=ta['useful_exits']<ra['useful_exits']
    keep=(better_lead or alarms_better) and not (worse_lead or alarms_worse or useful_worse)
    return dict(per_incident=rows,lead_status_counts=dict(Counter(r['status'] for r in rows)),
        useful_delta=ta['useful_exits']-ra['useful_exits'],control_exit_delta=ta['control_exits']-ra['control_exits'],
        control_alarm_delta=ta['control_exit_or_high']-ra['control_exit_or_high'],
        keep_retrieval=keep,lead_worsened=worse_lead,alarms_worsened=alarms_worse,
        matched_labels=matched_labels(reference,trial,which))

def historical_official():
    out=s.read(s.BASELINE/'results.json');windows=[]
    for run in s.PRIMARY:
        w=next(w for w in out['runs'] if w['run']==run['run']);v=w['judges']['J2']['deep_all']
        d,_=h.load_window(str(s.DATA),run['file'])
        ponr=d['ground_truth']['t_point_of_no_return'].get('timestamp_utc')
        windows.append(dict(run=run['run'],raw=dict(first_exit=v['first_exit'],first_high=v['first_high'],
            lead_min=(h.P(ponr)-h.P(v['first_exit'])).total_seconds()/60 if ponr and v['first_exit'] else None,
            useful=bool(v['exit_before_latest_useful']),before_ponr=bool(ponr and v['first_exit'] and v['first_exit']<ponr),
            exit_or_high=bool(v['alarms']),label_rows=0,action_matches=0,risk_matches=0),timeline=[]))
    return dict(windows=windows,aggregate={'raw':aggregate(windows,'raw')})

def updated_step1():
    out={}
    for name in s.old.NAMES:
        folder=s.ROOT/'runs'/name;tasks=s.read(folder/'tasks.json')
        cache={r['req_sha256']:r for r in a.records(folder)};ws=[]
        for run in s.PRIMARY:
            if not (folder/'data'/run['file']).exists():continue
            d,items,rows=a.timeline(run,folder/'data',tasks,cache)
            ws.append(dict(run=run['run'],raw=a.summarize(rows,'raw',d,run),
                after_agreement=a.summarize(rows,'after_agreement',d,run),timeline=rows))
        out[name]=dict(windows=ws,exit_before=sum(bool(w['raw']['first_exit']) for w in ws),
            exit_after=sum(bool(w['after_agreement']['first_exit']) for w in ws),
            useful_after=sum(w['after_agreement']['useful'] for w in ws))
    return out

def analyze(partial=False):
    s.verify();out=dict(complete=True,experiments={})
    allrs=[]
    for name in s.NAMES:
        folder=s.ROOT/'runs'/name;tasks=s.read(folder/'tasks.json');rs=a.records(folder)
        cache={r['req_sha256']:r for r in rs};assert len(cache)==len(rs)
        complete=len(rs)==len(tasks);out['complete'] &= complete;allrs+=rs
        exp=experiment(name,tasks,cache,folder/'data')
        exp.update(stats=a.stats(rs),planned_calls=len(tasks),complete=complete)
        out['experiments'][name]=exp
    out['total']=a.stats(allrs)
    budget=s.read(s.ROOT/'runs/step2_budget.json');entries=list(budget['entries'].values())
    out['budget']=dict(cap_usd=s.CAP,returned_token_cost_usd=sum(e['charged_or_reserved_usd'] for e in entries if e['status']=='completed'),
        unknown_cost_upper_bound_usd=sum(e['charged_or_reserved_usd'] for e in entries if e['status']!='completed'),
        charged_plus_reserved_usd=sum(e['charged_or_reserved_usd'] for e in entries),
        unknown_attempts=sum(e['status']!='completed' for e in entries))
    out['budget']['by_run']={name:dict(
        returned_token_cost_usd=sum(e['charged_or_reserved_usd'] for k,e in budget['entries'].items() if k.startswith(name+'/') and e['status']=='completed'),
        unknown_cost_upper_bound_usd=sum(e['charged_or_reserved_usd'] for k,e in budget['entries'].items() if k.startswith(name+'/') and e['status']!='completed'))
        for name in s.NAMES}
    assert out['budget']['charged_plus_reserved_usd']<=s.CAP
    if not out['complete']:
        assert partial,'Missing responses; use --partial (no final decision/report).'
        s.write(s.ROOT/'runs/step2_partial.json',out)
        print(json.dumps(dict(complete=False,total=out['total'],budget=out['budget'])));return out
    tasks=s.read(s.ART/'clean_tasks.json')
    tasks=[dict(t,req_sha256=hashlib.sha256(h.request_body(t['user'],'deep8k')).hexdigest()) for t in tasks]
    baseline_cache={r['req_sha256']:r for r in a.records(s.BASELINE)}
    hist=experiment('cited_strict',tasks,baseline_cache,s.DATA)
    used=[baseline_cache[t['req_sha256']] for t in tasks if t['req_sha256'] in baseline_cache]
    # Re-score cached content with the same validator for normalized schema/cost tables.
    normalized=[]
    for t in tasks:
        if t['req_sha256'] not in baseline_cache:continue
        r=baseline_cache[t['req_sha256']];obj,errors=s.strict(r['content'],t['oid_map'])
        normalized.append(dict(r,valid=not errors,schema_errors=errors,legacy_valid=r['valid'],
            production_schema_valid=not errors and obj['action'] in ('hold','reduce_light','reduce_to_target','leave_market')))
    hist['stats']=a.stats(normalized);out['historical_strict']=hist
    out['historical_official']=historical_official()
    clean,trial=[out['experiments'][n] for n in s.NAMES]
    out['comparisons']={
        'retrieval_vs_clean':{w:comparison(clean,trial,w) for w in ('raw','after_agreement')},
        'clean_vs_cited_strict':{w:comparison(hist,clean,w) for w in ('raw','after_agreement')},
        'retrieval_vs_cited_strict':{w:comparison(hist,trial,w) for w in ('raw','after_agreement')},
        'retrieval_vs_cited_official':comparison(out['historical_official'],trial,'raw')}
    # Identical prompts in untreated steps are a direct provider-variation diagnostic.
    untouched=[];cts=s.read(s.ROOT/'runs/step2_clean/tasks.json');rts=s.read(s.ROOT/'runs/step2_retrieval/tasks.json')
    cc={r['req_sha256']:r for r in a.records(s.ROOT/'runs/step2_clean')}
    rc={r['req_sha256']:r for r in a.records(s.ROOT/'runs/step2_retrieval')}
    diagnostics=[]
    for change in out['comparisons']['retrieval_vs_clean']['raw']['per_incident']:
        if change['status']!='later':continue
        ct=next(t for t in cts if t['run']==change['run'] and t['t']==change['reference_exit'])
        rt=next(t for t in rts if t['run']==change['run'] and t['t']==change['reference_exit'])
        cr,rr=cc[ct['req_sha256']],rc[rt['req_sha256']]
        diagnostics.append(dict(run=change['run'],checkpoint=change['reference_exit'],
            identical_request=ct['req_sha256']==rt['req_sha256'],context_cases=rt['retrieval_neighbors'],
            clean_valid=cr['valid'],retrieval_valid=rr['valid'],retrieval_finish=rr['finish_reason'],
            retrieval_action=(rr['parsed'] or {}).get('action'),schema_errors=rr['schema_errors'],
            delay_min=-change['lead_gain_min']))
    out['delayed_exit_diagnostics']=diagnostics
    for c,r in zip(cts,rts):
        if c['req_sha256']!=r['req_sha256']:continue
        ca,ra=cc[c['req_sha256']],rc[r['req_sha256']]
        cv=ca['parsed'] if ca['valid'] else dict(action='hold',risk=None)
        rv=ra['parsed'] if ra['valid'] else dict(action='hold',risk=None)
        untouched.append(dict(run=c['run'],t=c['t'],req_sha256=c['req_sha256'],
            clean_action=cv['action'],retrieval_action=rv['action'],clean_risk=cv['risk'],retrieval_risk=rv['risk'],
            changed=(cv['action'],cv['risk'])!=(rv['action'],rv['risk'])))
    out['identical_prompt_comparisons']=untouched
    out['identical_prompt_changed']=sum(r['changed'] for r in untouched)
    ret=s.read(s.ART/'retrieval.json');queries=s.read(s.ART/'queries.json')
    out['retrieval']=dict(elapsed_s=ret['elapsed_s'],rows=len(ret['results']),
        with_context=sum(bool(r['neighbors']) for r in ret['results']),
        status_counts=dict(Counter(q['status'] for q in queries)),
        missing_by_feature={f:sum(f in q['missing_features'] for q in queries) for f in s.SHARED},
        used_by_feature={f:sum(f in r['used_features'] for r in ret['results']) for f in s.SHARED},
        neighbors=sum(len(r['neighbors']) for r in ret['results']),
        unique_neighbors=len({n['retrieval_id'] for r in ret['results'] for n in r['neighbors']}),
        unique_positions=len({(n['market'],n['borrower']) for r in ret['results'] for n in r['neighbors']}),
        pre2026_context=sum(bool(r['neighbors']) and r['query']['as_of']<1767225600 for r in ret['results']),
        row_details=[dict(run=q['run'],t=q['t'],onchain_item=q['onchain_item'],features=q['features'],
                         missing=q['missing_features'],neighbor_ids=[n['retrieval_id'] for n in r['neighbors']]) for q,r in zip(queries,ret['results'])])
    execution=s.read(s.ROOT/'runs/step2_execution.json');out['execution']=execution
    out['elapsed_s']=sum(x.get('elapsed_s',0) for x in execution['sessions'])
    errors=[s.read(p) for n in s.NAMES for p in (s.ROOT/'runs'/n/'errors').glob('*.json')]
    out['errors']=dict(count=len(errors),types=dict(Counter(e['error_type'] for e in errors)))
    out['updated_step1']=updated_step1()
    out['decision']='keep in judge input' if out['comparisons']['retrieval_vs_clean']['raw']['keep_retrieval'] else 'display-only'
    if not partial:
        for name,exp in out['experiments'].items():
            folder=s.ROOT/'runs'/name;s.write(folder/'results.json',exp)
            s.write(s.ROOT/'runs'/(name+'_agreement')/'results.json',dict(policy=s.read(s.ART/'policy.json'),
                inference_calls=0,windows=[dict(run=w['run'],summary=w['after_agreement'],timeline=w['timeline']) for w in exp['windows']]))
        s.write(s.ROOT/'runs/step1_policy_30m/results.json',dict(policy=s.read(s.ART/'policy.json'),inference_calls=0,experiments=out['updated_step1']))
        s.write(s.ROOT/'runs/step2_summary.json',out)
        s.write(s.ROOT/'runs/step2_clean/reuse_for_step3.json',dict(model=h.MODEL,mode='deep8k',
            prompt_sha256=hashlib.sha256(h.SYSTEM.encode()).hexdigest(),
            tasks_sha256=s.sha(s.ROOT/'runs/step2_clean/tasks.json'),calls_sha256=s.sha(s.ROOT/'runs/step2_clean/calls.jsonl'),
            policy_sha256=s.sha(s.ART/'policy.json'),steps=len(cts),complete=True,
            instruction='Reuse these measured clean J2 responses for Step 3; no baseline inference needed.'))
        report(out)
    print(json.dumps(dict(complete=True,decision=out['decision'],total=out['total'],budget=out['budget'])))
    return out

def fmt(v,d=2):return '—' if v is None else f'{v:.{d}f}'
def pct(v):return '—' if v is None else f'{100*v:.1f}%'

def report(o):
    clean,trial=[o['experiments'][n] for n in s.NAMES];hist=o['historical_strict'];official=o['historical_official']
    r=o['retrieval'];b=o['budget'];cmp=o['comparisons']['retrieval_vs_clean'];c=clean['aggregate']['raw'];t=trial['aggregate']['raw']
    sentence=(f"In a paired replay of {c['incidents']} incident and {c['controls']} control windows, clean and retrieval J2 runs each had "
        f"{c['useful_exits']}/{c['incidents']} useful exits and {c['control_exits']}/{c['controls']} control exits, "
        f"but the retrieval arm had {cmp['raw']['lead_status_counts'].get('later',0)} later first exits and high-risk control flags in "
        f"{t['control_high']}/{t['controls']} windows versus {c['control_high']}/{c['controls']}; historical cases remain {o['decision']}.")
    lines=['# Judge evidence — Step 2: retrieval comparison','',
        'Status: complete. Step 3 has not started. No commits. The original harness, incident dataset and `incident_replay_raw/` are unchanged. The clean baseline is saved for reuse in Step 3.','',
        '## Result and build decision','',sentence,'',
        f"Raw paired lead outcomes: `{json.dumps(cmp['raw']['lead_status_counts'],sort_keys=True)}`. "
        f"Agreement-filtered outcomes: `{json.dumps(cmp['after_agreement']['lead_status_counts'],sort_keys=True)}`. "
        f"The same keep/drop test on agreement-filtered outcomes says **{'keep' if cmp['after_agreement']['keep_retrieval'] else 'display-only'}**.",
        '',
        '[ASSUMPTION] The brief’s “without the other getting worse” is applied conservatively to paired per-incident first-exit leads: no later or lost exit; at least one earlier exit, or fewer control alarms with nonworse leads. Both exit and exit-or-high control counts must not worsen, and useful exits must not fall. This rule was frozen before inference. Primary comparison is raw J2 against the contemporaneous clean arm; agreement is reported separately.',
        '', '## Setup and accepted policies','',
        f"Both arms run all {clean['planned_calls']} primary J2 steps, including after the first exit, over the same frozen observations, using `{h.MODEL}`. "
        'The baseline-compatible prompt, effective-time fixes, latest-observation bound, temperature, seed and thinking-on `deep8k` body are unchanged. “Production mode” here means thinking on at every step, as in the brief; this is not an updated production-prompt/schema test. The two arms are interleaved by window and timeline rank. No prior inference cache supplies either arm.',
        '',
        'Runs: `runs/step2_clean/` and `runs/step2_retrieval/`; each contains copied input data, exact prompts, request/response bytes, usage, schema results and analysis. The corresponding `_agreement/` folders contain offline results only. `runs/step2_clean/reuse_for_step3.json` pins the completed baseline’s prompt, tasks and responses.',
        '',
        'Agreement: two supporting, visible, cited items with different `source` fields, or one fired on-chain screen whose sample age is at most 30 minutes. The item containing the reading must also be available by the judgment. A same-source retraction cancels that source’s earlier text claim, never a fresh screen. Different-source claims survive. The exact 30-minute boundary is inclusive. The rule gates exits/reductions, not risk labels. Retrieved historical examples are not current corroborating sources.',
        '',
        '[ASSUMPTION] Originals lack `source`: use the frozen Step 1 canonical publisher mapping and manually labelled supporting claims. Source independence and semantic support remain offline approximations. J3 uses the inherited venue thresholds; it is not the complete borrower production screen set. All agreement evaluations in this Step 2 report use the new policy, including re-evaluation of Step 1 caches below.',
        '', '## Retrieval mapping and temporal controls','',
        f"The adapted copy `scripts/retrieve.py` performs exact standardized Euclidean kNN with deterministic ID tie-breaking. "
        f"It returned {r['neighbors']} neighbor appearances ({r['unique_neighbors']} distinct rows, {r['unique_positions']} positions) "
        f"for {r['with_context']}/{r['rows']} steps, including {r['pre2026_context']} pre-2026 steps. "
        f"Offline batch retrieval took {r['elapsed_s']:.2f} seconds. Context appears only under the exact heading **{s.HEADING}**; each hit contributes its stored one-line summary.",
        '',
        'Candidates must be eligible members of the original training pool and have their entire outcome window mature by the replay timestamp. The original frozen scaler is used for 2026 queries; 2025 scalers are fitted only to training rows already mature at that timestamp. No holdout neighbors or future outcomes enter the query, scaler or context. The full-feature test reproduces the original retriever’s neighbor IDs and distances.',
        '',
        '[ASSUMPTION] Query the latest visible on-chain item and record its age. Use only fields present in that item; do not fill them from older observations or infer them from prose. Timestamped series contribute only samples at or before replay time. Utilization is the position’s debt/supply market; for vaults use the maximum among relevant contemporaneously listed markets. APR percent becomes an annual-rate fraction. Oracle-versus-market percentage points proxy the library’s oracle-versus-Binance gap, with the reference-market mismatch explicit. Exact-horizon collateral returns and volatility are required; short price jumps, vault share returns and TVL changes are not substituted.',
        '', '| Shared feature | Used query rows | Missing / omitted rows |', '|---|---:|---:|']
    for f in s.SHARED:lines.append(f"| `{f}` | {r['used_by_feature'][f]} | {r['missing_by_feature'][f]} |")
    lines += ['',f"Query statuses: `{json.dumps(r['status_counts'],sort_keys=True)}`. Missing features and provenance are recorded for every row in `data/step2/queries.json`; neighbors, distances, scalers and maturity timestamps are in `data/step2/retrieval.json`. Empty queries add no context and trigger no imputation. The excluded borrower-only dimensions are LTV, distance to liquidation, owner inactivity and position size.",
        '', '## Outcomes against both baselines','',
        '| Run / scoring | Useful incidents | Before PONR | Median lead, min | Control exit | Control exit or high | Action / risk label agreement | Label rows |',
        '|---|---:|---:|---:|---:|---:|---|---:|']
    for label,exp in [('Clean',clean),('Retrieval',trial),('26 Oct, strict replay validator',hist)]:
        for which,title in [('raw','raw'),('after_agreement','agreement')]:
            z=exp['aggregate'][which]
            lines.append(f"| {label}, {title} | {z['useful_exits']}/{z['incidents']} | {z['before_ponr']}/{z['incidents']} | {fmt(z['lead_median_min'])} | {z['control_exits']}/{z['controls']} | {z['control_exit_or_high']}/{z['controls']} | {pct(z['action_agreement'])} / {pct(z['risk_agreement'])} | {z['label_rows']} |")
    z=official['aggregate']['raw']
    lines += [f"| 26 Oct, original published scoring | {z['useful_exits']}/{z['incidents']} | {z['before_ponr']}/{z['incidents']} | {fmt(z['lead_median_min'])} | {z['control_exits']}/{z['controls']} | {z['control_exit_or_high']}/{z['controls']} | see matched comparison below | — |",'',
        'Median leads include observed incident exits only; the per-window table exposes missing exits. Label agreement is over measured, labelled steps, with schema failures rejected to hold/risk unknown. The cited run stopped incident chains early and used a looser parser. Its agreement results are censored where an accepted exit is unobserved; missing cached calls are never treated as holds. The historical row is descriptive, not the retrieval decision baseline.',
        '', '| Incident | 26 Oct exit | Clean exit | Retrieval exit | Lead: 26 Oct / clean / retrieval, min | Retrieval lead gain vs clean |', '|---|---|---|---|---|---:|']
    for old,cw,tw,delta in zip(official['windows'],clean['windows'],trial['windows'],cmp['raw']['per_incident']):
        if not cw['run'].startswith('INC'):break
        vals=[x['raw'] for x in (old,cw,tw)]
        lines.append(f"| {cw['run']} | {vals[0]['first_exit'] or 'none'} | {vals[1]['first_exit'] or 'none'} | {vals[2]['first_exit'] or 'none'} | {' / '.join(fmt(v['lead_min']) for v in vals)} | {fmt(delta['lead_gain_min'])} ({delta['status']}) |")
    lines += ['', '| Incident, agreement | Clean accepted exit | Retrieval accepted exit | Clean / retrieval lead, min |', '|---|---|---|---|']
    for cw,tw in zip(clean['windows'],trial['windows']):
        if not cw['run'].startswith('INC'):continue
        x,y=cw['after_agreement'],tw['after_agreement']
        lines.append(f"| {cw['run']} | {x['first_exit'] or 'none'} | {y['first_exit'] or 'none'} | {fmt(x['lead_min'])} / {fmt(y['lead_min'])} |")
    lines += ['', '| Later retrieval-arm exit | Delay, min | At the clean first-exit checkpoint | Same request bytes? |', '|---|---:|---|---|']
    for d in o['delayed_exit_diagnostics']:
        reason=('schema rejected: '+', '.join(d['schema_errors'])+'; finish='+str(d['retrieval_finish'])) if not d['retrieval_valid'] else 'valid action '+str(d['retrieval_action'])
        lines.append(f"| {d['run']} | {fmt(d['delay_min'])} | {reason} | {'yes' if d['identical_request'] else 'no'} |")
    lines += ['', 'This separates rejected output from a valid non-exit proposal. A delay at an identical prompt is service variation, not retrieval exposure; even changed-prompt differences are not causally isolated by this single run.']
    lines += ['', '| Control | Clean exit / high | Retrieval exit / high | Clean / retrieval accepted exit |', '|---|---|---|---|']
    for cw,tw in zip(clean['windows'],trial['windows']):
        if not cw['run'].startswith('CTL'):continue
        lines.append(f"| {cw['run']} | {cw['raw']['first_exit'] or 'none'} / {cw['raw']['first_high'] or 'none'} | {tw['raw']['first_exit'] or 'none'} / {tw['raw']['first_high'] or 'none'} | {cw['after_agreement']['first_exit'] or 'none'} / {tw['after_agreement']['first_exit'] or 'none'} |")
    lines += ['', '| Matched label comparison, raw | Shared labelled steps | Reference action / risk | Trial action / risk |', '|---|---:|---|---|']
    for key,label in [('retrieval_vs_clean','Clean → retrieval'),('clean_vs_cited_strict','26 Oct strict → clean'),('retrieval_vs_cited_strict','26 Oct strict → retrieval')]:
        m=o['comparisons'][key]['raw']['matched_labels'];n=m['rows']
        lines.append(f"| {label} | {n} | {pct(m['left_action']/n)} / {pct(m['left_risk']/n)} | {pct(m['right_action']/n)} / {pct(m['right_risk']/n)} |")
    lines += ['',f"Retrieval versus the original published 26 Oct exits: `{json.dumps(o['comparisons']['retrieval_vs_cited_official']['lead_status_counts'],sort_keys=True)}`. "
        f"Clean versus the strictly re-scored cached baseline: `{json.dumps(o['comparisons']['clean_vs_cited_strict']['raw']['lead_status_counts'],sort_keys=True)}`.",
        '', '## Updated agreement policy on Step 1 caches','',
        'No Step 1 inference was repeated. These results supersede its old unlimited-screen-freshness agreement counts for the accepted policy; original artifacts remain historical evidence.',
        '', '| Injection type | Control exits before → after new rule |', '|---|---:|']
    for name,z in o['updated_step1'].items():
        if name.startswith('controls'):lines.append(f"| {s.old.TYPES[name[-1]]} | {z['exit_before']}/{len(z['windows'])} → {z['exit_after']}/{len(z['windows'])} |")
    zi=o['updated_step1']['incidents_c'];lines += ['',f"All-clear incident useful exits after the new rule: {zi['useful_after']}/{len(zi['windows'])}. Full timelines and gate reasons: `runs/step1_policy_30m/results.json`.",
        '', '## Cost, latency and failures','',
        '| Run | Calls | Input / output tokens | Token USD | Median / p90 / max latency, s | Schema rejects | Output limit |',
        '|---|---:|---|---:|---|---:|---:|']
    for label,stats in [('Clean',clean['stats']),('Retrieval',trial['stats']),('Step 2 total',o['total']),('26 Oct cached primary J2 only (historical cost)',hist['stats'])]:
        lines.append(f"| {label} | {stats['calls']} | {stats['prompt_tokens']:,} / {stats['completion_tokens']:,} | {stats['cost_usd']:.6f} | {fmt(stats['latency_median_s'])} / {fmt(stats['latency_p90_s'])} / {fmt(stats['latency_max_s'])} | {stats['schema_rejected']} | {stats['finish_length']} |")
    lines += ['',f"Step 2 returned-token cost **${b['returned_token_cost_usd']:.6f}**; unknown-charge upper bound **${b['unknown_cost_upper_bound_usd']:.6f}** "
        f"across {b['unknown_attempts']} attempts; charged plus reserved **${b['charged_plus_reserved_usd']:.6f} ≤ ${b['cap_usd']:.2f}**. "
        f"Token pricing follows the brief: input $0.08/million and output $0.50/million, including reported thinking tokens. "
        f"Formula: `({o['total']['prompt_tokens']} × 0.08 + {o['total']['completion_tokens']} × 0.50) / 1,000,000`. "
        'Every attempt reserves a conservative input-byte allowance plus the full output budget before sending; success replaces it with actual usage. Unknown charges stay reserved. Historical costs are excluded from the Step 2 cap.',
        '',f"Unknown-charge reservations by run: clean ${b['by_run']['step2_clean']['unknown_cost_upper_bound_usd']:.6f}; retrieval ${b['by_run']['step2_retrieval']['unknown_cost_upper_bound_usd']:.6f}.",
        '', f"Inference wall time: {o['elapsed_s']/60:.2f} minutes. Recorded transport errors: {o['errors']['count']} "
        f"(`{json.dumps(o['errors']['types'],sort_keys=True)}`). Retries are bounded and separately reserved; schema failures and truncated model answers are scored, never retried. "
        'Latency is non-streaming completion duration outside an enclave, excluding queue time, failed attempts and signature requests. P90 uses the inherited lower-index convention. Retrieval time above is one offline batch, not online per-tick latency.',
        '',f"Schema error categories (overlap): `{json.dumps(o['total']['schema_errors'],sort_keys=True)}`. "
        f"Sampled provider-signature metadata checks: {o['total']['signatures_passed']}/{o['total']['signatures_sampled']} passed. "
        'These check `provider_tee` and request/response hash text, not independent cryptographic signature verification or refreshed model attestation.',
        '', '## Limits and decisions before Step 3','',
        f"- Identical no-context requests changed strict risk/action in {o['identical_prompt_changed']}/{len(o['identical_prompt_comparisons'])} paired answers. "
        'Fixed temperature and seed do not make this service deterministic. Differences at these untreated steps cannot be caused by retrieval. This is one paired run, not a causal or statistical guarantee.',
        '- Most replay positions are suppliers, vault depositors or an Intents balance; retrieved positions are Morpho borrowers. Sparse market-feature resemblance does not imply matching position risk. Many queries use only utilization or oracle gap; nearest rows may repeat a position/hour and are not independent case studies. No imputation was used, so coverage is deliberately limited.',
        '- The library is a retrospective sampled cohort and screen-filtered collection. Outcome maturity prevents direct future-label leakage, but does not make cohort selection prospective. Original observation timestamps and labels are inherited; the known CTL06-007 text/time inconsistency remains, and same-block borrower failures remain unpreventable in this replay.',
        '- The preserved prompt uses the baseline action vocabulary and lacks newer production hardening. No separate prompt test was requested. Schema rejection can suppress exits independently of substantive risk reasoning. This research makes no transactions or borrower-protection guarantee.',
        f"- Step 3 decision: authorize its inference allowance and execution. The reusable clean {clean['planned_calls']}-step, thinking-on baseline is already frozen; no new baseline call is needed. "
        'Keep the baseline prompt and clean inputs fixed for the thinking-budget/model comparison specified in the brief, so retrieval does not confound it.',
        '- Step 3 candidates: the brief permits up to three other NEAR AI Cloud models with attestation reports. No model discovery or Step 3 inference has started. State any model exclusions or tighter cap when authorizing; otherwise use the brief’s candidate limit and measured costs.',
        '', '## README-ready sentence','',sentence,'',
        '## Reproduce (offline analysis needs no key)','',
        'Run from the repository root. Preparation refuses to overwrite frozen inputs. The key is read inside the inference script from `~/.config/prime/nearai_key`; it is never printed, saved or placed in process arguments.',
        '', '```sh',
        "python3 -I 'research_notes/Judge evidence/scripts/step2.py' queries",
        "'research_notes/Liquidation dataset/scripts/.venv/bin/python3' -I 'research_notes/Judge evidence/scripts/retrieve.py' --queries-file 'research_notes/Judge evidence/data/step2/queries.json' --output 'research_notes/Judge evidence/data/step2/retrieval.json'",
        "python3 -I 'research_notes/Judge evidence/scripts/step2.py' freeze",
        "python3 -I 'research_notes/Judge evidence/scripts/check_step2.py'",
        "'research_notes/Liquidation dataset/scripts/.venv/bin/python3' -I 'research_notes/Judge evidence/scripts/check_retrieval_step2.py'",
        "python3 -I 'research_notes/Judge evidence/scripts/run_step2.py' --parallel 24",
        "python3 -I 'research_notes/Judge evidence/scripts/analyze_step2.py'",
        "python3 -I 'research_notes/Judge evidence/scripts/check_step2.py'",'```','',
        'After completion, run only the last analysis and check commands to reproduce the report without inference. `runs/step2_summary.json` contains all measurements; `runs/step2_budget.json` contains the durable spend ledger.','',
        '---','','## Historical Step 1 report','',
        'The following is the completed Step 1 report as it stood before the founder’s Step 2 decisions. Its unlimited-freshness agreement counts and pending decisions are historical; the current policy/results and accepted decisions appear above.','']
    history=(s.ART/'step1_report.md').read_text()
    history=history.replace('# Judge evidence — Step 1: prompt injection','### Step 1: prompt injection (historical)')
    history=history.replace('Status: complete. Step 2 has not been started.','Status at the Step 1 stop: complete; Step 2 had not started.')
    history=history.replace('\n## ','\n#### ')
    (s.ROOT/'README.md').write_text('\n'.join(lines)+'\n'+history)

if __name__=='__main__':
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--partial',action='store_true')
    analyze(ap.parse_args().partial)
