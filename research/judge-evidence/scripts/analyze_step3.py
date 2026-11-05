#!/usr/bin/env python3
"""Offline repeated-arm analysis and README generation. python3 -I [--partial]."""
import argparse
import hashlib
import importlib.util
import json
import statistics
import sys
from collections import Counter
from pathlib import Path
assert sys.flags.isolated
def module(name,path):
    spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
s=module('step3',Path(__file__).with_name('step3.py'));c=s.c
a=module('a2',Path(__file__).with_name('analyze_step2.py'))
METRICS=['useful_exits','control_exits','control_high','control_exit_or_high']
LABELS={'glm8k':'GLM Flash 8k baseline','glm2k':'GLM Flash 2k','glm4k':'GLM Flash 4k',
        'qwen_vl8k':'Qwen3-VL-30B Instruct 8k','qwen368k':'Qwen3.6-35B 8k','qwen388k':'Qwen3.8-27B 8k'}

def records(folder):
    path=folder/'calls.jsonl';return [json.loads(l) for l in path.read_text().splitlines()] if path.exists() else []

def stats(rs,rates):
    lat=[r['latency_s'] for r in rs];pin=sum(r['usage']['prompt_tokens'] for r in rs);pout=sum(r['usage']['completion_tokens'] for r in rs)
    return dict(calls=len(rs),prompt_tokens=pin,completion_tokens=pout,
        token_cost_usd=(pin*rates['input_per_million']+pout*rates['output_per_million'])/1e6,
        latency_median_s=statistics.median(lat) if lat else None,latency_p90_s=a.a.p90(lat),latency_max_s=max(lat) if lat else None,
        schema_rejections=sum(not r['valid'] for r in rs),output_limit=sum(r.get('finish_reason')=='length' for r in rs),
        schema_categories=dict(Counter(e for r in rs for e in r.get('schema_errors',[]))),
        signature_checks=sum('sig' in r for r in rs),signatures_passed=sum(r.get('sig',{}).get('passed',False) for r in rs),
        eip191_verified=sum(r.get('sig',{}).get('eip191_verified',False) for r in rs),
        reasoning_chars=sum(r.get('reasoning_chars',0) for r in rs),
        reasoning_usage_exceeds_completion=sum((r['usage'].get('reasoning_tokens') or 0)>r['usage']['completion_tokens'] for r in rs))

def envelope(values):
    present=[v for v in values if v is not None]
    return dict(min=min(present) if present else None,max=max(present) if present else None,values=values)

def qualifies(candidate,baseline):
    reasons=[]
    for which in ['raw','after_agreement']:
        for metric in METRICS:
            bound=baseline['spread'][which][metric]
            for repeat,value in enumerate(candidate['spread'][which][metric]['values'],1):
                if value<bound['min'] or value>bound['max']:
                    reasons.append(dict(scoring=which,metric=metric,repeat=repeat,value=value,
                        allowed_min=bound['min'],allowed_max=bound['max']))
    if candidate['stats']['signatures_passed']!=candidate['stats']['calls']:
        reasons.append(dict(metric='provider_tee_signature',passed=candidate['stats']['signatures_passed'],required=candidate['stats']['calls']))
    return dict(qualifies=not reasons,reasons=reasons)

def analyze(partial=False):
    s.verify();arms=s.active_arms();policy=c.read(c.ART/'policy.json')
    pinned=dict(name='step2_clean',arm='glm8k',model=c.h.MODEL,max_tokens=8000,repeat=1,rates=next(x['rates'] for x in arms if x['arm']=='glm8k'),pinned=True)
    out=dict(complete=True,runs={},pairs={},policy=policy);raw_records={}
    for arm in [pinned]+arms:
        folder=c.ROOT/'runs'/arm['name'];tasks=c.read(folder/'tasks.json');rs=records(folder)
        assert len(rs)==len({r['req_sha256'] for r in rs})
        exp=a.experiment(arm['name'],tasks,{r['req_sha256']:r for r in rs},folder/'data')
        complete=len(rs)==len(tasks);out['complete'] &= complete
        exp.update(arm=arm,stats=stats(rs,arm['rates']),complete=complete,planned_calls=len(tasks))
        out['runs'][arm['name']]=exp;raw_records[arm['name']]=rs
    budget=c.read(c.ROOT/'runs/step3_budget.json');entries=budget['entries']
    out['budget']=dict(cap_usd=c.CAP,
        returned_token_cost_usd=sum(e['charged_or_reserved_usd'] for e in entries.values() if e['status']=='completed'),
        charged_plus_reserved_usd=sum(e['charged_or_reserved_usd'] for e in entries.values()),
        unknown_cost_upper_bound_usd=sum(e['charged_or_reserved_usd'] for e in entries.values() if e['status']!='completed'),
        unknown_attempts=sum(e['status']!='completed' for e in entries.values()),
        probe_cost_usd=sum(e['charged_or_reserved_usd'] for k,e in entries.items() if k.startswith('discovery/') and e['status']=='completed'))
    assert out['budget']['charged_plus_reserved_usd']<=c.CAP
    out['new_calls']=sum(len(raw_records[arm['name']]) for arm in arms)
    out['planned_new_calls']=sum(out['runs'][arm['name']]['planned_calls'] for arm in arms)
    if not out['complete']:
        assert partial,'Missing responses: use --partial; no final recommendation.'
        c.write(c.ROOT/'runs/step3_partial.json',out)
        print(json.dumps(dict(complete=False,calls=out['new_calls'],budget=out['budget'])));return out
    if not partial:assert (c.ART/'extra_selection.json').exists(),'Finish the frozen cost-only third-model admission decision first.'
    for armid in LABELS:
        runs=sorted([e for e in out['runs'].values() if e['arm']['arm']==armid],key=lambda e:e['arm']['repeat'])
        if not runs:continue
        assert len(runs)==2
        names=[r['arm']['name'] for r in runs];rs=[rec for n in names for rec in raw_records[n]]
        pair=dict(names=names,model=runs[0]['arm']['model'],max_tokens=runs[0]['arm']['max_tokens'],
            rates=runs[0]['arm']['rates'],stats=stats(rs,runs[0]['arm']['rates']),spread={},pooled={})
        for which in ['raw','after_agreement']:
            pair['spread'][which]={m:envelope([r['aggregate'][which][m] for r in runs]) for m in METRICS+['lead_median_min','incident_exits']}
            leads=[w[which]['lead_min'] for r in runs for w in r['windows'] if w['run'].startswith('INC') and w[which]['lead_min'] is not None]
            pair['pooled'][which]=dict(lead_median_min=statistics.median(leads) if leads else None,lead_observations=len(leads),
                useful_exits=sum(r['aggregate'][which]['useful_exits'] for r in runs),
                control_exits=sum(r['aggregate'][which]['control_exits'] for r in runs),
                control_high=sum(r['aggregate'][which]['control_high'] for r in runs))
        pair['mean_replay_cost_usd']=pair['stats']['token_cost_usd']/2
        pair['repeat_changes']={}
        for which in ['raw','after_agreement']:
            changes=[]
            for l,r in zip(runs[0]['windows'],runs[1]['windows']):
                for x,y in zip(l['timeline'],r['timeline']):
                    assert x['t']==y['t'] and x['measured'] and y['measured']
                    if (x[which]['risk'],x[which]['action'])!=(y[which]['risk'],y[which]['action']):
                        changes.append(dict(run=l['run'],t=x['t'],first=[x[which]['risk'],x[which]['action']],second=[y[which]['risk'],y[which]['action']]))
            pair['repeat_changes'][which]=dict(count=len(changes),denominator=runs[0]['planned_calls'],changes=changes)
        out['pairs'][armid]=pair
    baseline=out['pairs']['glm8k']
    for armid,pair in out['pairs'].items():
        if armid=='glm8k':continue
        pair['qualification']=qualifies(pair,baseline)
    eligible=[(p['mean_replay_cost_usd'],p['stats']['latency_median_s'],armid) for armid,p in out['pairs'].items() if armid!='glm8k' and p['qualification']['qualifies']]
    # The baseline itself remains available if every qualifying alternative costs more.
    if eligible and min(eligible)[0]<baseline['mean_replay_cost_usd']:
        chosen=min(eligible)[2];reason='Cheapest qualifying alternative; both repeats inside the observed baseline count ranges.'
    elif eligible:
        chosen='glm8k';reason='Alternatives match the baseline count spread, but none is cheaper than baseline GLM.'
    else:
        chosen='glm8k';reason='No alternative qualifies on both repeats; retain 8000-token GLM.'
    out['recommendation']=dict(arm=chosen,label=LABELS[chosen],reason=reason,qualifying_alternatives=[x[2] for x in sorted(eligible)],
        mean_replay_cost_usd=out['pairs'][chosen]['mean_replay_cost_usd'],
        latency_median_s=out['pairs'][chosen]['stats']['latency_median_s'],latency_p90_s=out['pairs'][chosen]['stats']['latency_p90_s'])
    out['execution']=c.read(c.ROOT/'runs/step3_execution.json')
    out['extra_selection']=c.read(c.ART/'extra_selection.json') if (c.ART/'extra_selection.json').exists() else None
    out['elapsed_s']=sum(x.get('elapsed_s',0) for x in out['execution']['sessions'])
    out['discovery']=c.read(c.ART/'discovery.json')
    out['errors']=[c.read(p) for arm in arms for p in (c.ROOT/'runs'/arm['name']/'errors').glob('*.json')]
    out['new_schema_rejections']=sum(out['runs'][a0['name']]['stats']['schema_rejections'] for a0 in arms)
    out['new_output_limit']=sum(out['runs'][a0['name']]['stats']['output_limit'] for a0 in arms)
    out['new_signatures_passed']=sum(out['runs'][a0['name']]['stats']['signatures_passed'] for a0 in arms)
    out['probe_usage_anomalies']=sum((r.get('usage',{}).get('reasoning_tokens') or 0)>r.get('usage',{}).get('completion_tokens',0) for r in out['discovery']['models'])
    measured=sum(out['runs'][a0['name']]['stats']['token_cost_usd'] for a0 in arms)+out['budget']['probe_cost_usd']
    assert abs(measured-out['budget']['returned_token_cost_usd'])<1e-8
    if not partial:
        for arm in arms:
            exp=out['runs'][arm['name']];folder=c.ROOT/'runs'/arm['name'];c.write(folder/'results.json',exp)
            c.write(c.ROOT/'runs'/(arm['name']+'_agreement')/'results.json',dict(inference_calls=0,policy=policy,
                windows=[dict(run=w['run'],summary=w['after_agreement'],timeline=w['timeline']) for w in exp['windows']]))
        c.write(c.ROOT/'runs/step3_summary.json',out);report(out)
    print(json.dumps(dict(complete=True,new_calls=out['new_calls'],recommendation=out['recommendation'],budget=out['budget'])))
    return out

def fmt(v,d=2):return '—' if v is None else f'{v:.{d}f}'
def span(values,denom=None):return ' / '.join(str(v)+(f'/{denom}' if denom else '') for v in values)

def report(o):
    b=o['budget'];p=o['policy'];rec=o['recommendation'];base=o['pairs']['glm8k']
    models=o['discovery']['models'];eligible=[m for m in models if m['eligible']]
    no_att=[m for m in models if m['suitable_text_replay'] and not m['eligible']]
    unsuitable=[m for m in models if not m['suitable_text_replay']]
    sentence=(f"Across two runs per setting on 8 incidents and 6 control windows, {LABELS[rec['arm']]} "
        f"is the selected setting under the observed baseline-spread rule, with median/p90 judgment latency "
        f"{rec['latency_median_s']:.1f}/{rec['latency_p90_s']:.1f} seconds; this is a small replay comparison, not a reproducibility guarantee.")
    lines=['# Judge evidence — Step 3: repeated thinking-budget and model comparison','',
        'Status: complete. Stopped after Step 3; Step 4 has not started. No commits. The original harness, dataset, cited 26 Oct cache, pinned clean baseline and agreement policy remain unchanged.','',
        '## Recommendation','',f"**{rec['label']}.** {rec['reason']}",'',sentence,'',
        'The founder’s repeated-run instruction supersedes the earlier single-run plan and aggregate spend allowance. Qualification was frozen before inference: each candidate repeat must lie inside the two baseline runs’ minimum/maximum for useful incident exits, control exits, high/critical control flags, and exit-or-high controls, under both raw and agreement-filtered scoring. No pooled average can hide a failing repeat. Lead and schema/latency diagnostics are reported; they are not extra acceptance thresholds. Every new candidate response must have a verified `provider_tee` signature for a recommendation.',
        '',
        '[ASSUMPTION] “Within the baseline’s own spread” means the observed two-sided count ranges, not a confidence interval or a statistical equivalence test. Selection chooses the lowest mean token cost per full replay among qualifying alternatives, retaining baseline GLM if none qualifies or none is cheaper. Tiny differences and favorable outcomes outside the strict range are not silently treated as equivalence.',
        '', '## Model discovery and budget selection','',
        f"The live catalog contained {len(models)} models. {len(eligible)} returned a usable NEAR model report and a verified `provider_tee` probe signature. "
        f"{len(no_att)} suitable text models did not provide a usable NEAR model report; {len(unsuitable)} entries were not suitable text-chat models with the requested output capacity. "
        'All catalog rows and exclusions are listed in [the discovery table](runs/step3_discovery/models.md); raw catalog, nonce requests, reports, exact probe bytes and signatures are retained. A gateway-only report does not satisfy the model-evidence requirement.',
        '', '| Model with both capabilities | Input / output USD per million | Replay selection |', '|---|---:|---|']
    tested={pair['model'] for pair in o['pairs'].values()}
    for m in eligible:
        reason='baseline; new 8k repeat plus paired 2k/4k arms' if m['model']==c.h.MODEL else 'two 8k repeats' if m['model'] in tested else 'eligible, excluded from replay by cost forecast'
        lines.append(f"| `{m['model']}` | {m['rates']['input_per_million']:.2f} / {m['rates']['output_per_million']:.2f} | {reason} |")
    lines += ['',f"The pinned baseline’s token counts forecast ${p['core_forecast_usd']:.6f} for the mandatory GLM runs, "
        f"${p['selected_forecast_usd']:.6f} including the two cheapest alternative pairs, and ${p['three_alternatives_forecast_usd']:.6f} "
        f"with all three alternative pairs, above the ${b['cap_usd']:.2f} cap. Selection used price/token estimates, never outcome quality. "
        'The initial selection did not label the deferred model as failed or untrusted. Probes are charged inside the cap.',
        '',
        (f"After first-phase completion, committed spend was ${o['extra_selection']['committed_usd']:.6f}. "
         f"The frozen third-model pair forecast was ${o['extra_selection']['forecast_pair_usd']:.6f}, "
         f"with an additional ${o['extra_selection']['retry_margin_usd']:.2f} retry margin. "
         f"The cost-only admission rule therefore {'admitted' if o['extra_selection']['admitted'] else 'excluded'} the third eligible model. "
         'This decision used no replay quality metrics; its supplemental manifest preserves the original first-phase inputs.'
         if o['extra_selection'] else 'No supplemental model admission was made.'),
        '',
        'Sources for endpoint semantics: [model catalog](https://docs.near.ai/cloud/models), [NEAR model reports](https://docs.near.ai/cloud/verification/cloud-api/model-attestations), [response signatures](https://docs.near.ai/cloud/verification/cloud-api/response-signatures), and [reasoning configuration](https://docs.near.ai/cloud/reasoning-models). Prices used in every charge are the saved live `/v1/models` values, not a static webpage table.',
        '', '## Frozen method','',
        f"The pinned `runs/step2_clean/` is baseline repeat 1; `runs/step3_glm8k_r2/` supplies repeat 2. "
        f"Each replay has {o['runs']['step2_clean']['planned_calls']} J2 judgments across the same 8 incident and 6 control windows, "
        f"including steps after the first exit. Step 3 made {o['new_calls']} replay calls in {len(o['runs'])-1} new run folders; "
        f"the {len(eligible)} tiny eligibility probes are separate. Identical requests in different repeats never share a response cache. "
        'Arms/repeats are interleaved by window and timeline rank. Only model ID and `max_tokens` differ from the pinned request body; prompt, inputs, temperature, seed and non-streaming format remain fixed. No retrieval is supplied.',
        '',
        'GLM thinking remains on by default at the requested output budgets. Qwen3.6 also reasons by default; Qwen3-VL Instruct is a non-reasoning alternative. The token limit covers reasoning plus final output, not an independently controlled thinking-only allowance. Reported usage includes reasoning; it is not charged a second time.',
        '',
        'Agreement is unchanged: two supporting visible cited items with different `source` fields, or a fired on-chain screen no older than 30 minutes at the judgment, inclusive. A same-source retraction cancels the prior text claim only, never a fresh screen. The containing observation must also be available by that time. The gate changes discretionary exits/reductions, not risk labels. Source normalization and support annotations remain the frozen offline Step 1 approximations.',
        '', '## Per-run results (repeat 1 / repeat 2)','',
        '| Setting | Useful exits | Control exits | High/critical controls | Median lead, min | Schema rejects | Output-limit completions |',
        '|---|---|---|---|---|---|---|']
    for armid,pair in o['pairs'].items():
        spread=pair['spread']['raw'];runs=[o['runs'][n] for n in pair['names']]
        lines.append(f"| {LABELS[armid]} | {span(spread['useful_exits']['values'],8)} | {span(spread['control_exits']['values'],6)} | {span(spread['control_high']['values'],6)} | {' / '.join(fmt(v) for v in spread['lead_median_min']['values'])} | {span([r['stats']['schema_rejections'] for r in runs])} | {span([r['stats']['output_limit'] for r in runs])} |")
    lines += ['', 'A schema-rejected answer is scored as hold with unknown risk. Reported medians use observed first exits; missing exits are preserved in JSON and the per-window table below. Control counts are per window, not per answer. High includes critical.',
        '', '| Setting, after agreement | Useful exits | Control exits | High/critical controls | Median lead, min |', '|---|---|---|---|---|']
    for armid,pair in o['pairs'].items():
        x=pair['spread']['after_agreement']
        lines.append(f"| {LABELS[armid]} | {span(x['useful_exits']['values'],8)} | {span(x['control_exits']['values'],6)} | {span(x['control_high']['values'],6)} | {' / '.join(fmt(v) for v in x['lead_median_min']['values'])} |")
    lines += ['', '## Both runs combined: latency and cost','',
        '| Setting | Pooled median / p90 latency, s | Per-run median / p90, s | Token USD R1 / R2 | Pair token USD | Mean replay USD | Pooled raw / gated median lead, min |',
        '|---|---|---|---|---:|---:|---|']
    for armid,pair in o['pairs'].items():
        z=pair['stats'];runs=[o['runs'][n] for n in pair['names']]
        lats='; '.join(f"{r['stats']['latency_median_s']:.2f}/{r['stats']['latency_p90_s']:.2f}" for r in runs)
        costs=' / '.join(f"{r['stats']['token_cost_usd']:.6f}" for r in runs)
        lines.append(f"| {LABELS[armid]} | {z['latency_median_s']:.2f} / {z['latency_p90_s']:.2f} | {lats} | {costs} | {z['token_cost_usd']:.6f} | {pair['mean_replay_cost_usd']:.6f} | {fmt(pair['pooled']['raw']['lead_median_min'])} / {fmt(pair['pooled']['after_agreement']['lead_median_min'])} |")
    lines += ['', 'Latency measures the completion request outside an enclave; it excludes queueing, failed attempts and the separate signature fetch/verification. P90 is the inherited sorted lower-index quantile. Pooled latency weights every returned call equally. A pooled lead median is over first exits in both runs, not the median of the two run medians. Baseline repeat 1 is historical Step 2 cost and is excluded from the Step 3 cap.',
        '', '## Baseline spread and qualification','',
        '| Metric | Baseline raw range | Baseline agreement range |', '|---|---|---|']
    for metric in METRICS+['lead_median_min']:
        r,g=base['spread']['raw'][metric],base['spread']['after_agreement'][metric]
        lines.append(f"| {metric} | {fmt(r['min'])}–{fmt(r['max'])} | {fmt(g['min'])}–{fmt(g['max'])} |")
    lines += ['', '| Candidate | Both runs qualify? | Failures against baseline range |', '|---|---|---|']
    for armid,pair in o['pairs'].items():
        if armid=='glm8k':continue
        qual=pair['qualification'];reasons=[]
        for failure in qual['reasons']:
            if failure['metric']=='provider_tee_signature':reasons.append(f"signatures {failure['passed']}/{failure['required']}")
            else:reasons.append(f"{failure['scoring']} R{failure['repeat']} {failure['metric']}={failure['value']} outside {failure['allowed_min']}–{failure['allowed_max']}")
        lines.append(f"| {LABELS[armid]} | {'yes' if qual['qualifies'] else 'no'} | {'; '.join(reasons) or 'none'} |")
    lines += ['', '| Setting | Raw risk/action changed between repeats | Gated risk/action changed |', '|---|---:|---:|']
    for armid,pair in o['pairs'].items():
        x,y=pair['repeat_changes']['raw'],pair['repeat_changes']['after_agreement']
        lines.append(f"| {LABELS[armid]} | {x['count']}/{x['denominator']} | {y['count']}/{y['denominator']} |")
    lines += ['', '## Per-incident lead ranges','',
        '| Incident | Setting | Raw leads R1 / R2, min | Agreement leads R1 / R2, min |', '|---|---|---|---|']
    for run in c.s.PRIMARY:
        if not run['run'].startswith('INC'):continue
        for armid,pair in o['pairs'].items():
            ws=[next(w for w in o['runs'][n]['windows'] if w['run']==run['run']) for n in pair['names']]
            lines.append(f"| {run['run']} | {LABELS[armid]} | {' / '.join(fmt(w['raw']['lead_min']) for w in ws)} | {' / '.join(fmt(w['after_agreement']['lead_min']) for w in ws)} |")
    lines += ['', '## Whole-step spend, evidence and failures','',
        f"Returned-token inference cost, including probes: **${b['returned_token_cost_usd']:.6f}**. "
        f"Unknown-charge upper bound: **${b['unknown_cost_upper_bound_usd']:.6f}** over {b['unknown_attempts']} attempts. "
        f"Charged plus reserved: **${b['charged_plus_reserved_usd']:.6f} ≤ ${b['cap_usd']:.2f}**. "
        f"Probe cost: ${b['probe_cost_usd']:.6f}. Each attempt reserves an input-byte allowance plus its full output limit at the model’s saved rate; "
        'actual prompt/completion counts replace the reservation only after a response. No failed-response charge is assumed to be zero. Every cost is `(prompt_tokens × input_rate + completion_tokens × output_rate) / 1,000,000`.',
        '', '| New run | Prompt tokens | Completion tokens | Returned-token USD | Unknown reservation USD |', '|---|---:|---:|---:|---:|']
    entries=c.read(c.ROOT/'runs/step3_budget.json')['entries']
    for name,e in o['runs'].items():
        if name=='step2_clean':continue
        z=e['stats'];unknown=sum(x['charged_or_reserved_usd'] for k,x in entries.items() if k.startswith(name+'/') and x['status']!='completed')
        lines.append(f"| `{name}` | {z['prompt_tokens']:,} | {z['completion_tokens']:,} | {z['token_cost_usd']:.6f} | {unknown:.6f} |")
    lines += ['',f"Replay wall time: {o['elapsed_s']/60:.2f} minutes. Recorded transport failures: {len(o['errors'])}; "
        f"categories `{json.dumps(dict(Counter(e['error_type'] for e in o['errors'])),sort_keys=True)}`. "
        'Only absent transport responses receive bounded retries; returned invalid or truncated answers are never retried. The original two-failed-approaches stop rule remains; this step used the already working HTTP/2 approach.',
        '',f"New-run schema rejections: {o['new_schema_rejections']}; output-limit completions: {o['new_output_limit']}. "
        f"Provider-TEE signatures verified on {o['new_signatures_passed']}/{o['new_calls']} new replay responses, "
        f"plus {len(eligible)} eligibility probes. Checks cover exact request/response hash text, `provider_tee` kind, a unique matching preflight signer, and EIP-191 signature verification. "
        'Model-report checks cover nonempty Intel quote evidence, client nonce/address binding and the debug bit. This step does not perform Intel DCAP chain verification, NVIDIA NRAS verification, measured-code provenance review, or prove which physical replica served a response. The pinned baseline retains its original sampled metadata checks; no stronger past verification is claimed.',
        '',f"Provider reasoning-token counts exceeded total completion tokens in {sum(e['stats']['reasoning_usage_exceeds_completion'] for n,e in o['runs'].items() if n!='step2_clean')} new replay responses and {o['probe_usage_anomalies']} probes. "
        'Billing uses the provider’s top-level prompt/completion counts; reasoning counts are diagnostic and are never added again. Discovery’s initial local verifier check looked for the wrong success wording; it was corrected against `cast` output and re-scored from saved bytes with no repeated inference. Tampered messages/signatures are rejected by the offline check.',
        '', '## Limits and stop point','',
        '- Two runs are an observed spread, not an estimate of a stable error distribution. Shared cases are dependent; the older pinned baseline and the new baseline can differ through service drift as well as sampling. Changed settings may also interact with schema truncation. The repeat variation table is part of the result.',
        '- This remains the baseline-compatible research prompt/schema, not the newer production prompt. There is no retrieval context. The positions mostly represent supplier/vault venue stress; same-block borrower oracle failures and the inherited CTL06-007 timing inconsistency remain limits. No contracts, signatures authorizing transactions or funds were acted on.',
        '- Model admission follows the cost-only budget rule described above. An eligible model excluded for cost is not shown to perform worse. Gateway/third-party-attested catalog labels alone do not meet the required NEAR model-report plus provider_tee evidence path.',
        f"- Stop after Step 3. Recommended setting: {rec['label']}; the production setting has not been edited in product code or deployment. "
        'Step 4 is optional and was not started; it would need a separate scope, inference allowance and the OpenRouter key specified by the brief.',
        '', '## README-ready sentence','',sentence,'',
        '## Exact commands','',
        'From the repository root; never rerun discovery/preparation into frozen folders. The key is read inside scripts from `~/.config/prime/nearai_key` and is never printed, saved or passed in process arguments.',
        '', '```sh',
        "python3 -I 'research_notes/Judge evidence/scripts/step3_common.py'",
        "python3 -I 'research_notes/Judge evidence/scripts/discover_step3.py'",
        "python3 -I 'research_notes/Judge evidence/scripts/discover_step3.py' --recheck",
        "python3 -I 'research_notes/Judge evidence/scripts/step3.py' prepare",
        "python3 -I 'research_notes/Judge evidence/scripts/check_step3.py'",
        "python3 -I 'research_notes/Judge evidence/scripts/run_step3.py' --parallel 48",
        "python3 -I 'research_notes/Judge evidence/scripts/step3.py' extra",
        "# If admitted, run only its new tasks; completed caches are preserved:",
        "python3 -I 'research_notes/Judge evidence/scripts/run_step3.py' --parallel 48",
        "python3 -I 'research_notes/Judge evidence/scripts/analyze_step3.py'",
        "python3 -I 'research_notes/Judge evidence/scripts/check_step3.py'",'```','',
        'Only the final analysis/check commands are needed to reproduce completed results without inference. Each new `runs/step3_<arm>_r<n>/` stores its own data, tasks, requests, responses, signatures, call ledger and results; `_agreement/` folders are offline views. Aggregate results and all qualification failures are in `runs/step3_summary.json`; the cap ledger is `runs/step3_budget.json`.','',
        '---','','## Historical Steps 1–2 report','',
        'The following preserves the prior reports and their stop-point status. Step 3 above supersedes their pending Step 3 decisions; historical cost and policy labels remain explicit.','']
    history=(c.ART/'step2_report.md').read_text()
    history=history.replace('# Judge evidence — Step 2: retrieval comparison','### Step 2: retrieval comparison (historical)')
    history=history.replace('Status: complete. Step 3 has not started.','Status at the Step 2 stop: complete; Step 3 had not started.')
    history=history.replace('\n## ','\n#### ')
    (c.ROOT/'README.md').write_text('\n'.join(lines)+'\n'+history)
    md=['# Step 3 model discovery','',f"Catalog rows: {len(models)}. Both capabilities present: {len(eligible)}.",'',
        '| Model | Catalog owner | Model-report HTTP | Eligible | Reason |','|---|---|---:|---|---|']
    for row in models:md.append(f"| `{row['model']}` | {row['owned_by']} | {row.get('attestation_http','not probed')} | {'yes' if row['eligible'] else 'no'} | {row['reason']} |")
    (c.ROOT/'runs/step3_discovery/models.md').write_text('\n'.join(md)+'\n')

if __name__=='__main__':
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--partial',action='store_true')
    analyze(ap.parse_args().partial)
