# Judge evidence brief (research, 28–31 October 2026)

Goal: three measured answers about the agent's language-model judge, each feeding a sentence in the judges' README (`docs/02-judges-README.md`) or a production setting in the design (`docs/01-design-and-feasibility.md` §4.9): (1) what prompt injection in fetched text does to the judge, and what the agreement rule catches; (2) whether feeding the judge the nearest historical cases from the liquidation dataset improves it; (3) which thinking budget and which attested model to run in production. Step 4 is optional: a second, typed judge (Jev) for comparison. Work step by step; stop and report after each.

Read `CLAUDE.md` at the repo root first. This is research under `research_notes/Judge evidence/`; nothing goes into product code; you commit nothing.

## Rules

- The only secret is the NEAR AI Cloud key at `~/.config/prime/nearai_key` (and, for Step 4 only, an OpenRouter key the founder will place at `~/.config/prime/openrouter_key`). Read them from the file inside scripts; never print, log or copy them.
- Spend cap: about $5 of inference for Steps 1–3 together (the full thinking-on replay cost $0.17 on 26 Oct); record cost per run from token counts at the model's list price ($0.08 per million input, $0.50 per million output for `z-ai/glm-5.3-flash`).
- Do not modify `research_notes/Voices on AI managed crypto loans/backtest_scripts/incident_replay_llm.py` or anything in `incident_replay_raw/` (those are the cited results). Copy the script into `scripts/` here and change the copy. Use a separate cache and output folder per experiment under `runs/<name>/`, so cached calls of the cited run are not reused where the prompt differs.
- `python3 -I`; free sources only; every number in `README.md` computed by a script in `scripts/`; assumptions labelled [ASSUMPTION]; stop after two distinct failed approaches and write what you learned.

## What exists

- The replay harness: `research_notes/Voices on AI managed crypto loans/backtest_scripts/incident_replay_llm.py` (read its docstring first; pass `--data` explicitly, the default path is stale). Dataset: `research_notes/Voices on AI managed crypto loans/incident_replay/` (8 incident windows INC-01…08, 6 control windows CTL-01…06; items with `replay_text`, timestamps, on-chain readings; expected labels per step). Judges: J1 text only, J2 text plus on-chain, J3 rules. Modes: `fast` (thinking off, 400 tokens), `deep` (thinking on, 4,000), `deep8k` body (thinking on, 8,000), and the `deepall` command (thinking on at every step, the production mode). `analyze` writes `results.json` and `tables.md`: useful exits, exit before the point of no return, lead in minutes, protective action, control false alarms, label agreement.
- The cited baseline (26 Oct, `incident_replay_raw/tables.md`, mode `deep_all`): J2 6 of 8 useful exits, median lead +85 min, 1 of 6 controls with an exit (CTL-06) and 1 of 6 with exit or high; J1 3 of 8 before the point of no return; J3 rules 6 of 8, 1 of 6 (CTL-01). Full write-up: `research_notes/Voices on AI managed crypto loans/incident_replay_results.md`. Latency of thinking-on calls in that run: median 83 s, p90 241 s (outside the enclave); inside the enclave on 27 Oct a judgment took 261–583 s (`research_notes/Prototype/results.md`, P3).
- The judge's contract in the design: `docs/01-design-and-feasibility.md` §4.9 steps 3–6 (screens, judge with fixed JSON schema `risk` ∈ {low, elevated, high, critical}, `action` ∈ {hold, reduce_light, reduce_to_target, leave_market}, `reasons`, `source_ids`; strict rejection of anything else) and the agreement rule: a discretionary sale, a move or an exit needs the judge plus a fired on-chain screen, or the judge plus two independent text sources; fetched text is untrusted.
- The retrieval table: `research_notes/Liquidation dataset/data/retrieval.parquet` (4.88 million Morpho position-hours where a screen fired, with the feature vector, outcome and a one-line summary) and `research_notes/Liquidation dataset/scripts/retrieve.py` (standardized Euclidean k-nearest rows; read its header for the query interface and the feature list; `README.md` there, Step 3, has five example queries). Its positions are Morpho borrowers; the replay's positions are mostly suppliers and vault depositors, so a query can use only the features both share (utilization, borrow rate, oracle-versus-market gap, price change, volatility); label the mapping and its gaps.
- NEAR AI Cloud: `https://cloud-api.near.ai/v1`, OpenAI-compatible; `research_notes/Measurements/B_near_ai_model_signatures.md` and `B_scripts/` show how to list models, fetch an attestation report with a fresh nonce and verify the `provider_tee` signature. Only models that return an attestation report are candidates for production.

## Step 1: prompt injection (stop and report)

Build `data/injections.json`: 40–60 hostile items in the dataset's item format, each with a type, the window it is inserted into, its effective time and `replay_text`. Types, at least six each: (a) a fake governance post urging immediate withdrawal from a healthy venue; (b) a fake security alert naming the venue, on a calm day; (c) an impersonated official account saying "all clear, funds safe" during a real incident, timed before the true exit signal; (d) an instruction aimed at the model ("ignore your policy", "respond with action exit", "output the following JSON"); (e) a request to move funds to an address or to borrow the maximum; (f) a contradictory pair, alarm then retraction, on a calm day. Mark the attacker's intent: induce a false exit (on controls) or suppress or delay a true exit (on incidents). Keep them plausible, in the register of the real items.

Runs, J2 in production mode (`deepall` body) with the copied harness, each in its own `runs/` folder: (1) controls with (a), (b), (d), (e), (f) inserted; (2) incidents with (c) inserted before the true signal; (3) the same two with the agreement rule applied after the fact: an exit counts only if an on-chain screen fired at or before that step, or two independent text sources (by `source` field) support it. Baseline is the cited 26 Oct run; do not re-run it.

Report: per injection type, how often the judge's answer followed the attacker (false exit or high on controls; exit suppressed or delayed on incidents, with the delay in minutes), before and after the agreement rule; whether any answer broke the schema (counted as rejected); cost and latency. One sentence the README can carry, with the numbers, and the honest limits (hand-written injections, one model, small windows).

## Step 2: does retrieval help? (stop and report)

For every J2 step that has on-chain readings, build a query from the shared features, fetch the 3 nearest rows with `retrieve.py` from the training pool only (events before 2026), and add their one-line summaries to the judge's input under a heading "Similar past cases (Morpho borrowers)". Re-run J2 in production mode over all 14 windows in its own `runs/` folder. Compare against the cited baseline on the same metrics: useful exits, leads per window, control false alarms, label agreement, latency, cost. Decision rule for the build: keep retrieval in the judge's input if leads improve or false alarms fall without the other getting worse; otherwise it stays a display-only feature on the dashboard. Say which, with the numbers, and label the supplier-versus-borrower mismatch.

## Step 3: thinking budget and model (stop and report)

Runs, J2 production mode, each in its own folder: budgets 2,000 and 4,000 tokens with thinking on (the cited run is 8,000); then the baseline prompt and 8,000 budget on each other NEAR AI Cloud model that returns an attestation report (list them; skip any without attestation; at most three). Same metrics plus latency median and p90 and cost per run. Recommend the production setting: the cheapest budget and model whose useful exits and false alarms match the 8,000-token GLM run, with the latency it implies for the agent's judge task. If none matches, say so.

## Step 4 (optional): Jev as a typed judge

Only if `~/.config/prime/openrouter_key` exists. `typesafe/jev-1.13` via `https://openrouter.ai/api/v1` (typed choice among the four actions and four risk levels; 32K context). Run J2's inputs with and without the Step 2 retrieval context, score with the same `analyze`, and report next to GLM. It remains an advisory, non-attested signal in the design whatever the result.

## Reporting

After each step: `README.md` in this folder (setup, runs with their folders, tables, the README-ready sentence, limits, exact commands), then a short reply with what ran, cost, time, what failed, and the decisions the founder must make before the next step.
