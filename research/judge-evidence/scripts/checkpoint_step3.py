#!/usr/bin/env python3
"""Offline account-exhaustion checkpoint; no HTTP or inference. python3 -I."""
import importlib.util
import json
import sys
from collections import Counter
from pathlib import Path
assert sys.flags.isolated
spec=importlib.util.spec_from_file_location('analysis3',Path(__file__).with_name('analyze_step3.py'))
a=importlib.util.module_from_spec(spec);spec.loader.exec_module(a)
c=a.c
o=a.analyze(partial=True)
assert not o['complete']
arms=a.s.active_arms()
errors=[c.read(p) for arm in arms for p in (c.ROOT/'runs'/arm['name']/'errors').glob('*.json')]
counts=Counter(str(e.get('http_status')) for e in errors)
status=c.read(c.ROOT/'runs/step3_interruption.json')
status.update(status='incomplete_account_exhausted',process_stopped=True,
    saved_responses=o['new_calls'],missing_responses=o['planned_new_calls']-o['new_calls'],
    user_reported_account_totals=dict(spent_usd=5.07,tokens=15129148,requests=4138),
    error_http_counts=dict(counts),budget=o['budget'],recommendation=None)
c.write(c.ROOT/'runs/step3_interruption.json',status)
models=c.read(c.ART/'discovery.json')['models']
md=['# Step 3 model discovery','',
    '| Model | Catalog owner | Model-report HTTP | Eligible | Reason |',
    '|---|---|---:|---|---|']
for m in models:
    md.append(f"| `{m['model']}` | {m['owned_by']} | {m.get('attestation_http','not probed')} | {'yes' if m['eligible'] else 'no'} | {m['reason']} |")
(c.ROOT/'runs/step3_discovery/models.md').write_text('\n'.join(md)+'\n')
b=o['budget']
lines=['# Step 3 — incomplete: account credit exhausted','',
    f"The founder reported an account-wide total of $5.07, 15,129,148 tokens and 4,138 requests. The API returned HTTP 402. All local replay requests have stopped; no additional inference was sent to diagnose the balance.",'',
    f"Saved **{o['new_calls']}/{o['planned_new_calls']}** planned new replay responses; **{status['missing_responses']} remain**. The pinned clean baseline is preserved separately. No new run is complete, so the required paired comparison and production recommendation cannot be made. Keep the existing 8,000-token GLM setting pending completion; this is not a finding that alternatives failed.",'',
    f"Returned-token Step 3 cost including eligibility probes: **${b['returned_token_cost_usd']:.8f}**. Conservative charged-plus-reserved total: **${b['charged_plus_reserved_usd']:.8f}**, below the authorized ${b['cap_usd']:.2f} ceiling. Of that, ${b['unknown_cost_upper_bound_usd']:.8f} is an unresolved reservation, not measured spend. The account-wide dashboard total is not attributable solely to Step 3.",'',
    f"Recorded error HTTP statuses: `{json.dumps(dict(counts),sort_keys=True)}`. The runner incorrectly retried payment errors; those attempts remain in the audit trail. It now stops new dispatch on 401/402/403 and never retries them, and preserves future error bodies without credentials. The old payment-error bodies were not saved. Unknown charges remain reserved until billing evidence reconciles them.",'',
    '## Saved responses only','',
    'These latency/schema/cost figures cover returned responses, not complete replay outcomes. Missing timeline rows prevent useful-exit, false-alarm and lead comparisons; do not interpret unmeasured rows in the partial JSON as negative outcomes.','',
    '| Run | Saved / planned | Schema rejects | Output-limit | Median / p90 seconds | Token USD | Verified signatures |',
    '|---|---:|---:|---:|---:|---:|---:|']
for arm in arms:
    e=o['runs'][arm['name']];z=e['stats']
    lines.append(f"| `{arm['name']}` | {z['calls']} / {e['planned_calls']} | {z['schema_rejections']} | {z['output_limit']} | {z['latency_median_s']:.2f} / {z['latency_p90_s']:.2f} | {z['token_cost_usd']:.8f} | {z['signatures_passed']} / {z['calls']} |")
lines += ['', '## Discovery and continuation','',
    'Models with both a usable model report and a verified provider_tee probe signature: '+', '.join('`'+m['model']+'`' for m in models if m['eligible'])+'. Full inclusions/exclusions: [model discovery](runs/step3_discovery/models.md). Qwen3.8 was deferred by the frozen cost forecast, not rejected for missing attestation. Its conditional admission is still pending first-phase completion. Report checks do not constitute full Intel DCAP/NVIDIA chain verification.','',
    'Completion needs replenished account credit and reconciliation of failed-attempt reservations before another call can be reserved under the unchanged $6 Step 3 cap. Resume only missing responses, preserving each separate run and all frozen prompts; never restart completed calls. Do not proceed to Step 4. No commits or changes to the original harness/cited cache.','',
    'README-ready status sentence: “The repeated model comparison is incomplete because account credit ran out; the saved partial runs do not establish a replacement for 8,000-token GLM.”','',
    'Offline regeneration: `python3 -I "research_notes/Judge evidence/scripts/checkpoint_step3.py"`. Detailed partial data: `runs/step3_partial.json`; interruption and account-total provenance: `runs/step3_interruption.json`.','']
report='\n'.join(lines)
(c.ROOT/'STEP3_PARTIAL.md').write_text(report)
readme=c.ROOT/'README.md';prior=readme.read_text();marker='<!-- STEP3 ACCOUNT CHECKPOINT -->'
if marker in prior:prior=prior.split('<!-- END STEP3 ACCOUNT CHECKPOINT -->\n',1)[1]
readme.write_text(marker+'\n'+report+'\n<!-- END STEP3 ACCOUNT CHECKPOINT -->\n'+prior)
print(json.dumps(dict(saved=o['new_calls'],missing=status['missing_responses'],token_usd=b['returned_token_cost_usd'])))
