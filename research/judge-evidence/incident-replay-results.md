<!-- Published 5 Nov 2026 as part of the research subset in cmdkfi/app. The note is unchanged; paths in it are the private planning repository's (`incident_replay/`, `backtest_scripts/`, `incident_replay_raw/`), which stay internal (see ../README.md). -->

# Incident replay: LLM risk judge results (2026-10-23, thinking-on run added 2026-10-26)

We replayed the 14 windows in `incident_replay/` (8 incidents, 6 calm controls) through three judges. Each judge saw the timeline step by step with no look-ahead. The script is `backtest_scripts/incident_replay_llm.py`. Raw calls (`calls.jsonl`), `results.json` and `tables.md` are in `<tmp>/replay/`, a temporary folder. Re-running the script with `--out` there reuses the cached calls.

## Headline

| Judge | Mode | Useful exit on incidents¹ | Exit before PONR | Median exit lead² | Controls with an exit | Controls with exit or high risk |
|---|---|---|---|---|---|---|
| J1 text only | fast | **3/8** | 3/8 | −64 min | 0/6 | 1/6 |
| J1 text only | fast→deep | **3/8** | 4/8 | −29 min | 0/6 | 0/6 |
| J2 text + on-chain | fast | **6/8** | 5/8 | +68 min | 1/6 | 3/6 |
| J2 text + on-chain | fast→deep | **6/8** | 6/8 | +83 min | 1/6 | 1/6³ |
| J1 text only | thinking on at every step⁴ | **4/8** | 3/8 | −86 min | 0/6 | 0/6 |
| J2 text + on-chain | thinking on at every step⁴ | **6/8** | 6/8 | +85 min | 1/6 | 1/6 |
| J3 rules only | — | **6/8** | 6/8 | +69 min | 1/6 | 1/6 |

¹ The exit time was at or before the archetype's `latest_useful_time_utc` in the file's `ideal_action`. That time is when the exit still worked.
² Median over the incidents where the judge exited at all. Positive means before the point of no return (PONR).
³ This count is flattered by truncation. In two CTL-06 steps the deep call used up its 4,000 tokens on reasoning, and by rule an invalid answer counts as hold. Re-running those calls with 8,000 tokens gives 2/6 (see [Deep mode](#deep-mode-what-it-added-and-what-it-cost)).
⁴ The production setting decided on 25 October: thinking on with an 8,000-token budget at every step, no fast screen (`deepall` mode, run 26 October). This is the run the judges' README cites. See [Thinking on at every step](#thinking-on-at-every-step-the-production-run).

What the numbers say:

- **On-chain data is what makes the judge useful.**
  - The text-only judge (J1) exited in time only in INC-01, INC-03 and INC-08. It also cut to target days early in INC-02. That matches the dataset's own finding that text came late in 04–07.
  - With on-chain items, J2 exited in time in 6 of 8 incidents, with leads of 55 min to 9.6 h (5.7 days in INC-02).
- **The LLM is not better than three fixed rules at detection.** J3 needed no LLM and caught the same six incidents. J2 fast→deep had more lead than J3 in four of them:
  - INC-01: 136 vs 25 min.
  - INC-02: 5.7 vs 3.5 days.
  - INC-06: 92 vs 69 min.
  - INC-07: 578 vs 529 min.

  It had less in INC-03 (55 vs 69 min). INC-08 was about even (73 vs 64 min). With thinking on at every step (the production run) J2 exited earlier than J3 in five of the six: the same four plus INC-08 (78 vs 64 min), and at the same minute in INC-03 (69 vs 69).
- **Nobody beat INC-04 or INC-05.** Both oracle-misconfiguration incidents liquidated their victims in the same block as the first signal. J2 and J3 reacted within 0–2 s of the oracle change, which is too late for any keeper that is not in the block. One weak pre-signal appeared in INC-04, covered in [Per-window results](#per-window-results).
- **False alarms are the weak point of J2 fast.** It exited on CTL-06, the precautionary rsETH freeze in 2025, and flagged high on CTL-04 (a whale borrow) and CTL-05 (an unrelated exploit). Deep confirmation removed two of these. It also *escalated* CTL-05 from reduce_to_target to a full exit.

## Setup

**Data and steps.**
- The judge was stepped at every distinct item time inside each replay window. J1 steps only at text items, because its input changes only then.
- At step *t* the judge saw items with effective time ≤ *t*, at most the latest 25, as `replay_text` only.
- Never shown: `verbatim_text`, `notes`, `holder_state`, `signal_role`, `observable_metrics`, item `venue` (which carries annotations such as "unrelated"), item ids (replaced by neutral O01, O02, … per judge) and `ground_truth`.
- On-chain authors were replaced with "chain monitor", because one author string reads "…post-mortem…".

**Look-ahead fixes.** Some on-chain `replay_text`s summarise readings taken after the item's own timestamp. For example, INC01-012 at 19:21:59 reports utilisation up to 19:36. Those items were moved to the time of their last reading:
- INC01-004, -008, -012, -015
- INC02-012
- INC03-006, -013
- INC04-009, -017
- INC06-003, -012, -031
- INC07-022
- INC08-006, -009, -011, -016, -017
- CTL04-007

One sentence that gave the future was cut from CTL03-004 ("Most of it was repaid by 16 Jan…").

**Position (one archetype per window).** The position text is our own neutral wording, because some archetype descriptions in the files carry hindsight ("before the run", "added because the pool was drained").

| Window | Archetype judged | PONR used |
|---|---|---|
| INC-01 | WETH supplier, Aave Core | 19:54:47 |
| INC-02 | Relend USDC vault depositor | 3 Nov 13:58:59 (market level) |
| INC-03 | Gauntlet USDC Core depositor | 03:40:11 (market level) |
| INC-04 | cbETH collateral, USDC debt | 18:04:35 |
| INC-05 | wstETH/WETH E-Mode looper | 11:46:11 |
| INC-06 | Rhea USDC supplier | 09:54:23 (freeze) |
| INC-07 | BSC-USDT balance in intents.near | 08:33:13 (pause) |
| INC-08 | Moonwell USDC supplier | 10:33:59 |

- **Controls:** USDC suppliers in CTL-01 and CTL-04; vault depositors in CTL-02 and CTL-05 (both Steakhouse USDC); a cbETH borrower in CTL-03; a WETH supplier in CTL-06.
- **Secondary pair:** the rsETH looper in INC-01 and CTL-06.

**Prompt.** One system prompt, identical for every window, defines the source types, the five actions and the four risk levels. It also says that both staying in a real incident and exiting on noise have costs. The user message is "You protect a user's position on <venue>: <position>. Venue risk matters; you cannot move funds, only propose an action; the corridor limits enforce safety." It then gives the current time and the observations, oldest first. Only the position and the observations differ between windows.

**Model and modes.** NEAR AI Cloud, `z-ai/glm-5.3-flash`, non-streaming, `x-no-aliasing: true`, `Accept-Encoding: identity`, temperature 0, seed 7.
- **Fast:** thinking off, max_tokens 400, `response_format` json_object.
- **Deep:** thinking on, max_tokens 4000, run when fast said high/critical or exit/reduce_to_target.

**Parsing.** The final JSON object was extracted after stripping `<think>` and validated strictly: risk, action, a reasons list and a source_ids list. An invalid answer counts as hold, and invalid answers are counted.

**J3 rules,** fixed and the same for every window. Any one of them fires an exit, at the sample's own block time.
- **R1:** utilisation of the position's market, or of a market the vault lends into, at or above 95%; or the vault's withdrawable share at or below 5%.
- **R2:** protocol oracle vs market price of the position's or the vault's collateral differs by 5% or more.
- **R3:** tokens actually held divided by the internal ledger at or below 95%.

The script maps which `observable_metrics` keys belong to each position. No rule maps to the rsETH loopers, so J3 is n/a for them. The thresholds are round numbers chosen after reading the dataset README, not tuned per window, but not blind either.

## Per-window results

First exit, then lead vs PONR in minutes, then "could the holder still withdraw at that time" from the file's `holder_state`, then useful/late against the archetype's latest useful time. For controls, any exit is shown. The thinking-on-at-every-step columns are in `incident_replay_raw/tables.md` (`deep_all`); the differences from fast→deep are listed in [Thinking on at every step](#thinking-on-at-every-step-the-production-run).

| Window | PONR | J1 fast | J1 fast→deep | J2 fast | J2 fast→deep | J3 rules |
|---|---|---|---|---|---|---|
| INC-01 Aave rsETH/Kelp | 18 Apr 19:54 | 19:21 (+33; yes/useful) | 19:45:34 (+9; partial/late by 34 s) | 17:44 (+131; yes/useful) | 17:38 (+136; yes/useful) | 19:30 (+25; yes/useful) |
| INC-02 Stream/Elixir | 3 Nov 13:58 | none; reduce_to_target from 28 Oct 19:49 (+5.8 d) | 28 Oct 20:57 (+5.7 d; yes/useful) | 4 Nov 05:01 (−15 h; partial/useful⁴) | 28 Oct 20:57 (+5.7 d; yes/useful) | 31 Oct 03:00 (+3.5 d; yes/useful) |
| INC-03 Resolv USR | 22 Mar 03:40 | 02:41 (+59; yes/useful) | 03:33 (+7; yes/useful) | 02:31 (+69; yes/useful) | 02:45 (+55; yes/useful) | 02:31 (+69; yes/useful) |
| INC-04 Moonwell cbETH oracle | 15 Feb 18:04:35 | 17 Feb 01:16 (−31 h; no) | 19:51 (−107; no) | 18:04:33 (+2 s; late) | 18:04:33 (+2 s; late) | 18:04:33 (+2 s; late) |
| INC-05 Aave wstETH CAPO | 10 Mar 11:46:11 | 12:50 (−64; late) | 12:50 (−64; late) | 11:46:11 (same block; no) | 13:56 (−131; late)⁵ | none (2.84% < 5%) |
| INC-06 Rhea Lend | 16 Apr 09:54 | 14:55 (−301; no) | 14:55 (−301; no) | 08:45 (+69; yes/useful) | 08:22 (+92; yes/useful) | 08:45 (+69; yes/useful) |
| INC-07 NEAR Intents | 1 Oct 08:33 | 12:53 (−260; partial/late) | 12:53 (−260; late) | 30 Sep 23:24 (+549; yes/useful) | 22:55 (+578; yes/useful) | 23:44 (+529; yes/useful) |
| INC-08 Moonwell MAMO | 27 Aug 10:33 | 09:48 (+46; partial/useful) | 09:48 (+46; useful) | 09:25 (+68; yes/useful) | 09:21 (+73; yes/useful) | 09:30 (+64; partial/useful) |
| CTL-01 Aave routine | — | none | none | none | none | **EXIT 14 Jul 00:00** (nightly USDC at 100%) |
| CTL-02 Morpho routine | — | none | none | none | none | none |
| CTL-03 Moonwell routine | — | none | none | none | none | none |
| CTL-04 Rhea routine | — | none | none | high, reduce_to_target at 21 Oct 16:05 (1M USDC borrow) | none (deep: low, hold) | none (93.2% < 95%) |
| CTL-05 Lazy Summer noise | — | high, reduce_to_target at 6 Jul 13:42 (CoinDesk article) | none (deep: low, hold) | high, reduce_to_target at 05:17:59 (the exploit transaction) | **EXIT 05:17:59** (deep escalated) | none |
| CTL-06 rsETH freeze 2025 | — | none | none | **EXIT 30 Apr 07:51** (Kelp pause) + high at 08:55 | none³ | none |

⁴ After the market PONR, but the Relend vault still had other liquidity until about 8 Nov.
⁵ Deep at 11:46:11 was truncated, so it counts as hold, and its 11:46:23 answer was reduce_to_target. With the 8,000-token retry the exit is 11:46:11. Either way it is too late.

Per-window observations:

- **INC-01.**
  - J2 went to high/reduce_to_target at 17:38:23, on the adapter release plus a fresh address approving rsETH to Aave. It exited at 17:44, on the borrow. Deep turned the 17:38 step into an exit.
  - J1 exited at 19:21:57 on Zeller's "withdraw now", 33 min before the PONR. At 19:55–20:13 it flipped back to **low/hold** after Aave's "Aave's contracts have not been exploited". That was after the PONR, but it shows how strongly reassurance from a protocol pulls a text-only judge.
- **INC-02.** J1 never said exit in fast mode. It sat on high/reduce_to_target from 28 Oct, which for a vault with about 10% exposure is a reasonable defensive action. Deep upgraded the 28 Oct 20:57 step (Schlagonia's "scam" thread) to an exit. That source is a researcher outside the allowlist; the dataset includes it marked as such.
- **INC-04.** J2 went to **elevated/reduce_light at 17:00** because MIP-X43 would swap the cbETH oracle feed about an hour later. This is the only pre-incident reading of the governance item in the whole run. A 25% trim would not have saved a position whose collateral was repriced to $1.12. A fixed rule such as "go to reduce_to_target before an oracle-feed change on your collateral executes" might have.
- **INC-06 and INC-07.** These were caught only through the balance-vs-ledger readings. J2 acted first on the pre-drain precursors: the margin positions opened by fresh accounts at 08:22 in INC-06, and the 0.53% gap at 22:55 in INC-07. J3 needed the first gap of 5% or more.

## CTL-06 (2025 precautionary freeze) vs INC-01 (2026 exploit)

| Step | CTL-06 (WETH supplier), J2 fast | INC-01 (WETH supplier), J2 fast |
|---|---|---|
| Baseline | low / hold | low / hold |
| Anomaly on the restaking token (CTL-06: 1.7e16 mint; INC-01: 116.5k adapter release) | invalid (truncated at 400 tokens; its text was arguing to "act now") → counted as hold | low / hold |
| Next on-chain step (CTL-06: Kelp pause; INC-01: fresh address approves rsETH to Aave) | **critical / exit** | high / reduce_to_target |
| Kelp post on X (CTL-06: "bug… funds safe") / borrow-and-run (INC-01) | high / reduce_to_target | **critical / exit** (17:44) |
| Aave freeze, WETH utilisation unchanged (CTL-06 10:03) | back to low / hold, and stays there through BGD's "NOT affected" | — |
| Deep at the alarm steps (4k tokens) | both truncated → hold | exit at 17:38 |
| Deep at the alarm steps (8k retry) | 07:51 high / reduce_to_target; 08:55 low / hold | — |

- **J2 fast reacts to the pattern, not to the consequence.** It exited on CTL-06 at the Kelp pause, before any borrowing against the minted rsETH and with WETH utilisation flat. It recovered at the first reading that showed Aave frozen and WETH normal.
- **Deep with enough tokens softened this to reduce_to_target and then hold.** A reduce_to_target on CTL-06 is a false alarm by the dataset's labels, but a cheap one. With 4,000 tokens deep never got to answer.
- **J1 held through CTL-06.** It saw only Kelp's "funds safe", BGD's "NOT affected" and the Morpho curator post. In INC-01 it exited only 33 min before the PONR. So J1 avoided the false alarm, but caught INC-01 only through a forum figure's "withdraw now".
- **J3 separates the two cleanly.** It never fired on CTL-06, because WETH utilisation stayed at 86–89%, and fired at 19:30 on INC-01. Its INC-01 lead, 25 min, is much smaller than J2's 136.
- **The rsETH looper (secondary pair) is not separated at all.** J2 exited at the first anomaly in both windows: INC-01 17:35:35 (+139 min), CTL-06 06:16:59. J1 exited in both: INC-01 19:17 (+38 min); CTL-06 11:17 in fast mode and 08:55 in deep, on the Steakhouse delisting post and Kelp's bug post. J2 also rated the looper's baseline at health factor 1.03 as high/reduce_to_target before anything happened. The CTL-06 label for the looper is elevated/reduce_light, so all of these are false alarms. For a looper whose collateral token has just minted 3×10¹⁰ times its supply, exiting is hard to call wrong in real time.

## Controls: what triggered each alarm

| Control | Judge / mode | Time | Verdict | Trigger |
|---|---|---|---|---|
| CTL-01 | J3 | 14 Jul 00:00 (and nightly 23:40–00:05) | exit | R1: USDC 99.9–100%, the nightly $184M withdraw-and-resupply (the known trap) |
| CTL-04 | J2 fast | 21 Oct 16:05 | high / reduce_to_target | CTL04-007, the 1M USDC borrow (utilisation 85% → 93%); deep said low/hold |
| CTL-05 | J1 fast | 6 Jul 13:42 | high / reduce_to_target | CTL05-012, the CoinDesk Summer.fi article; deep said low/hold |
| CTL-05 | J2 fast → deep | 6 Jul 05:17:59 | high / reduce_to_target → **high / exit** | CTL05-003, the flash-loan transaction via Morpho Blue into Lazy Summer |
| CTL-06 | J2 fast | 30 Apr 07:51:59, 08:55:22 | critical / exit; high / reduce_to_target | CTL06-002/003 mint + Kelp pause; CTL06-004 Kelp post |

J1 and J2 never alarmed on CTL-01 (the nightly 100% USDC), CTL-02 or CTL-03. That includes CTL-03's Anthias "19.8K%" borrow-cap line.

## Deep mode: what it added and what it cost

- **55 deep calls in total.** Fast flagged 328 of 495 steps. Every flagged control step got a deep call (17 steps). On incidents, deep ran on flagged steps in time order only until it had confirmed both an exit and high/critical risk. After that, later deep calls cannot change any first-time metric, so they were skipped; this saved about 270 calls of about 3 min each.
- **17 of 55 deep calls (31%) hit the 4,000-token limit** with all output spent on reasoning and empty content. Per the rule they count as hold. Three more were invalid only because they left out `source_ids`. Accepting those three would change one first exit: INC-05 J2, from 13:56 to 12:50, both late.
- **Truncation, not judgement, made some deep results look better.** It removed the CTL-06 alarms and delayed J1's INC-01 confirmation from 19:21 to 19:45:34, past the latest useful time. A re-run of the 17 with max_tokens 8,000 finished 15 (2 still truncated):
  - J1 INC-01 confirms at 19:21:57.
  - J2 INC-03 confirms at 02:31.
  - CTL-06 at 07:51 becomes high/reduce_to_target.
- **Net effect of deep.** It raised J2's in-time exits from 5/8 to 6/8 (INC-02 days earlier) and cut J2's high-or-exit alarms on controls from 3/6 to 2/6 (with 8k) and J1's from 1/6 to 0/6. It also turned one control alarm into a full exit (CTL-05). Deep mostly confirms or escalates rather than vetoes:
- Of the 35 valid 4k deep answers (all on fast-flagged steps), 12 upgraded a fast reduce_to_target to exit.
- 4 cleared the step to below high and below reduce_to_target: CTL-04, CTL-05 J1, INC-01 J1 at 19:17 and INC-03 J2 at 02:21.
- 2 cut a fast exit back to reduce_to_target.
- **Latency matters.** A deep call took a median of 176 s (p90 220 s; 277 s median with 8k). All lead times above ignore model latency. Subtract about 15 s for fast and about 3–5 min for a deep confirmation. That does not change any useful/late verdict except INC-01 J1 fast→deep, which was already late.

## Thinking on at every step: the production run

On 25 October we decided that production calls the model with thinking on and an 8,000-token budget every time, with no fast screen. The replay was re-run in that mode on 26 October (`deepall`): every step of every (window, judge) chain gets one thinking-on call, in time order; incident chains stop once the judge has given both an exit and a high or critical risk, because later steps cannot change any first-time metric; control chains run every step, because the false-alarm counts need them. The 17 steps already answered in the 8,000-token re-run were reused from the cache.

- **195 steps judged, 183 new calls, 12 from the cache.** No API errors. None of the 183 new answers hit the 8,000-token limit (the two truncated 8k answers, both INC-03 J2, are the cached ones). 4 of the 183 were invalid only because `source_ids` was missing; they count as hold by the strict rule.
- **J2 (text plus on-chain) is unchanged in substance.** Same six useful exits with the same first-exit times, except INC-08 five minutes earlier (09:16 instead of 09:21). Median lead +85 min against +83. On controls it no longer exits on CTL-05 (the Lazy Summer exploit: elevated/hold instead of the fast→deep escalation to exit) and does exit on CTL-06 at 06:16 (the 2025 rsETH precautionary freeze, where fast→deep with 8k tokens stopped at reduce_to_target). Net: one control exit and one control with exit or high risk out of six, the same count as J3, which exits on CTL-01.
- **J1 (text only) got more cautious on INC-02.** Where fast→deep exited on 28 October 20:57 (5.7 days early), thinking at every step gave reduce_to_target at 19:49 the same evening and exited only on 4 November 03:10, after the point of no return but before the vault's latest useful time (a partial withdrawal still worked). So J1 counts 4/8 useful exits and 3/8 before the PONR. Its exit answer on 31 October 00:00 left out `source_ids`; accepted leniently it would restore 4/8 before the PONR. Everything else is identical to fast→deep with 8k tokens.
- **Compared with the rules (J3), J2 exits earlier in five of the six incidents both catch:** INC-01 136 vs 25 min, INC-02 5.7 vs 3.5 days, INC-06 92 vs 69 min, INC-07 578 vs 529 min, INC-08 78 vs 64 min; INC-03 is the same minute (69). INC-04 and INC-05 stay same-block for everybody.
- **Agreement with the files' labels on controls** (all steps run): J2 action agreement 1.0 on CTL-01 to CTL-05 and 0.78 on CTL-06; risk agreement 0.65–1.0. Agreement and per-step alarm counts on incidents are not comparable with the other modes, because the chain stops after the first confirmed exit.
- **Latency was lower than in the 8k re-run:** median 83 s, p90 241 s, max 409 s (the earlier 8k calls, all on steps fast had flagged, took a median of 277 s). Most steps are calm and need little reasoning: median 3,910 characters of reasoning per call. Subtract 1.5–4 min from every lead above for the model's own latency; no useful/late verdict changes.
- **Cost:** 199,284 prompt and 305,302 completion tokens, $0.17 at the list price; 77 min of wall time at 6 parallel requests. Running the judge this way on every step costs about $0.0009 per step.

## Cost, latency, attestation

| | Calls | Prompt tokens | Completion tokens | Cost | Latency median / p90 / max |
|---|---|---|---|---|---|
| Fast | 494 unique (495 steps) | 818,220 | 113,724 | $0.122 | 14.3 / 28.0 / 65.3 s |
| Deep (4k) | 55 | 55,923 | 167,060 | $0.088 | 176 / 220 / 291 s |
| Deep retry (8k) | 17 | 19,190 | 83,547 | $0.043 | 277 / 475 / 571 s |
| Thinking on at every step (8k, 26 Oct) | 183 new (195 steps) | 199,284 | 305,302 | $0.169 | 83 / 241 / 409 s |
| **Total** | 749 | 1,092,617 | 669,633 | **≈ $0.42** | |

- **Errors:** none from the API. Two 8k calls timed out at our 300 s client read limit. They were re-run with a 900 s limit, and the timed-out attempts may have been billed, at most a few cents.
- **Fast reliability:** 7 of 494 fast answers were invalid: 6 cut off at 400 tokens and 1 missing `source_ids`.
- **Wall time:** about 35 min for the fast pass, 44 min for deep and about 50 min for the retry, at 4 parallel requests; 77 min for the thinking-on-at-every-step run at 6.
- **TEE signatures:** we checked a hash-sampled 11.5% of calls, 65 in all. **65/65 passed**: `signature_kind == provider_tee`, and the signed text equals `model:sha256(request bytes):sha256(response bytes)` exactly.
- **Agreement with the files' `expected_judge_labels`**, mean per window over the 14 primary windows:
  - J1 fast: action 0.65, risk 0.56.
  - J2 fast: action 0.79, risk 0.73.
  - Deep changed these by ±0.02.

## Caveats

- **The sample is tiny.** There are 8 incidents and 6 controls, with one archetype each and one secondary pair. One window more or less moves every rate by 12–17 points. None of the differences between J2 and J3 is statistically meaningful.
- **The dataset was built with hindsight about which items matter.** Its builders picked the on-chain readings: balance-vs-ledger, fresh-address borrows, oracle-vs-market gaps. They chose exactly those because they were the tell. A live monitor would produce far more readings, most of them noise, and J2's false-alarm rate would almost certainly be higher than 1–3 out of 6 calm windows. The controls are 2–3 days each. They cannot estimate a per-month false-alarm rate.
- **Some text items are partly secondhand.** These include quotes via news sites, X Article previews and posts cut at 280 characters. Timestamps of news come from meta tags. Two incident signals come from accounts outside the allowlist: Schlagonia in INC-02 and @yieldsandmore in INC-03.
- **This is one model at temperature 0 with one seed.** It is one prompt and one position wording per window. Earlier measurements showed the default temperature flipping between hold and reduce on the same input. We did not test other prompts, other models or several seeds.
- **The J3 thresholds were set after reading the dataset README**, which already says which signal worked in which incident. The keys for each position were also mapped by hand. J3 is therefore an optimistic baseline, though the thresholds are the same round numbers everywhere.
- **"Could still withdraw" is approximate.** It comes from each item's `holder_state`, which is not per archetype, and from the archetype's `latest_useful_time_utc`. It ignores position size, gas and slippage. For INC-04 and INC-05, "+2 s" or "same block" means the exit was impossible in practice.
- **The deep-mode results depend on the token budget.** At 4,000 tokens, 31% of deep calls returned nothing, and results with this model would change with the budget; at 8,000 tokens 2 of 200 calls ran out. The economy of stopping deep calls early does not affect first-time metrics, but per-step deep alarm counts and label agreement for incidents are incomplete, in both the fast→deep and the thinking-on-at-every-step runs.
- **J1 and J2 get no memory between steps.** Each step is judged from scratch, which is why J1 could flip from exit to hold in INC-01. A real keeper would keep state: it would not re-enter after an exit, and it would need a rule for when reassurance can undo an alarm.
