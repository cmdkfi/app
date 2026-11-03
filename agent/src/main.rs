//! PRIME agent (hackathon build, from the 27–31 Oct prototype). Spec: docs/01-design-and-feasibility.md §4.4 (key,
//! quote, registration) and §4.9 (collect, compute, screen, judge, decide, propose).
//! Deviations (brief): one vault, the positions of its Base address; on-chain reads plus one allowlisted text feed;
//! no scores and no similar-case retrieval.
//!
//! Behaviour is fixed in the binary; the compose file (measured) passes only `--mode`. `--mode dry` builds every
//! proposal exactly as the vault would and runs the vault's `check_transaction` view on it, but sends nothing to
//! the vault's agent methods and nothing to Base. `--mode live` calls the vault through the agent key and
//! broadcasts the transaction the vault signs. `--no-tee` (local runs only) skips the quote and registration.
//!
//! Tick loop (build items 44–54): each tick runs in its own task, so a panic is logged and the next tick runs. The
//! judge runs in a separate task, at most one call in flight; its result is taken on the first tick after it
//! arrives, applied only while the screens it was asked about are still on, and never delays the protective rule.
//! Decisions are pure functions in `logic.rs`.
mod chain;
mod judge;
mod logic;
mod near;
mod tee;

use anyhow::{anyhow, Result};
use chain::{Base, Snapshot, DEADLINE_SECS};
use ed25519_dalek::SigningKey;
use logic::{Act, AgentStatus, Decision, Due, Fired, Judgment};
use serde_json::{json, Value};
use std::collections::BTreeSet;
use std::path::PathBuf;
use std::sync::Arc;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};
use tokio::sync::Mutex;
use tokio::task::JoinHandle;

const VAULT: &str = "p2-vault.prime-measure-1792736012.testnet";
const NEAR_RPC: &str = "https://rpc.testnet.near.org";
const BASE_RPCS: [&str; 2] = ["https://base-sepolia-rpc.publicnode.com", "https://sepolia.base.org"];
const DIGEST_SECS: u64 = 6 * 3600;
const FEED_SECS: u64 = 600;
// NEAR checks prepaid gas x the pessimistic gas price against the balance at submission: 300 Tgas needed 0.3012 NEAR
// (run 2, 27 Oct: NotEnoughBalance with 0.1). 130 Tgas covers the measured registration (vault receipt 113.7 Tgas).
const MIN_REGISTRATION_BALANCE: u128 = 150_000_000_000_000_000_000_000; // 0.15 NEAR: about 0.1305 for 130 Tgas + margin
const REGISTER_GAS: u64 = 130_000_000_000_000;
const REGISTER_ATTEMPTS: u32 = 4; // then exit non-zero: never tick unregistered
const AGENT_CALL_GAS: u64 = 150_000_000_000_000;
/// one status line every this many ticks while nothing is on
const STATUS_EVERY: u64 = 30;

struct Args {
    mode: String,
    vault: String,
    tee: bool,
    base_rpcs: Vec<String>,
    near_rpc: String,
    tick_secs: u64,
    max_ticks: u64,
    digest_at_start: bool,
    evidence: PathBuf,
    nearai_key_file: Option<String>,
}

fn args() -> Result<Args> {
    let mut a = Args {
        mode: "dry".into(),
        vault: VAULT.into(),
        tee: true,
        base_rpcs: vec![],
        near_rpc: NEAR_RPC.into(),
        tick_secs: 2,
        max_ticks: u64::MAX,
        digest_at_start: true,
        evidence: PathBuf::from("/evidence"),
        nearai_key_file: None,
    };
    let v: Vec<String> = std::env::args().skip(1).collect();
    let mut i = 0;
    while i < v.len() {
        let val = || v.get(i + 1).cloned().ok_or_else(|| anyhow!("{} needs a value", v[i]));
        match v[i].as_str() {
            "--mode" => a.mode = val()?,
            "--vault" => a.vault = val()?,
            "--no-tee" => {
                a.tee = false;
                i -= 1
            }
            "--no-digest" => {
                a.digest_at_start = false;
                i -= 1
            }
            "--base-rpc" => a.base_rpcs.push(val()?),
            "--near-rpc" => a.near_rpc = val()?,
            "--tick-secs" => a.tick_secs = val()?.parse()?,
            "--max-ticks" => a.max_ticks = val()?.parse()?,
            "--evidence" => a.evidence = PathBuf::from(val()?),
            "--nearai-key-file" => a.nearai_key_file = Some(val()?),
            x => return Err(anyhow!("unknown argument {x}")),
        }
        i += 2;
    }
    if a.mode != "dry" && a.mode != "live" {
        return Err(anyhow!("--mode is dry or live"));
    }
    if a.base_rpcs.is_empty() {
        a.base_rpcs = BASE_RPCS.iter().map(|s| s.to_string()).collect();
    }
    Ok(a)
}

fn now() -> u64 {
    SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_secs()).unwrap_or(0)
}

/// UTC "YYYY-MM-DDTHH:MM:SSZ" from unix seconds (civil-from-days).
fn iso(t: u64) -> String {
    let (d, s) = ((t / 86400) as i64, t % 86400);
    let z = d + 719468;
    let era = z.div_euclid(146097);
    let doe = z - era * 146097;
    let yoe = (doe - doe / 1460 + doe / 36524 - doe / 146096) / 365;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let day = doy - (153 * mp + 2) / 5 + 1;
    let month = if mp < 10 { mp + 3 } else { mp - 9 };
    let year = yoe + era * 400 + if month <= 2 { 1 } else { 0 };
    format!("{year:04}-{month:02}-{day:02}T{:02}:{:02}:{:02}Z", s / 3600, s % 3600 / 60, s % 60)
}

// ------------------------------------------------------------------ context and loop state

struct Ctx {
    a: Args,
    base: Base,
    nearc: near::Near,
    sk: SigningKey,
    our_pk: String,
    judge: Arc<judge::Judge>,
    agent_record: Value,
    boot_id: String,
    base_address: String,
    executor: String,
    market_ids: Vec<String>,
}

/// What the judge task returns: the full record for the transcript and, if accepted, the answer.
struct JudgeOutcome {
    record: Value,
    feed: (Value, u64),
    accepted: Option<(String, String)>, // (action, risk)
}

struct InFlight {
    handle: JoinHandle<JudgeOutcome>,
    trigger: BTreeSet<String>,
    block: u64,
    started: Instant,
}

struct Ready {
    outcome: JudgeOutcome,
    trigger: BTreeSet<String>,
    block: u64,
    arrived_tick: u64,
}

struct State {
    vault: Value,
    fired_prev: BTreeSet<String>,
    next_digest: u64,
    feed: (Value, u64),
    loops: u64,
    next_recheck: u64,
    inflight: Option<InFlight>,
    ready: Option<Ready>,
    /// a judge call is wanted (Some(digest)) but one is in flight; started on the first tick the slot is free
    judge_wanted: Option<bool>,
    status: AgentStatus,
}

// ------------------------------------------------------------------ judge task (build items 44, 45)

async fn judge_job(judge: Arc<judge::Judge>, mut feed: (Value, u64), s: Snapshot, fired: Vec<Fired>, vault: Value) -> JudgeOutcome {
    let t = Instant::now();
    if now() >= feed.1 + FEED_SECS {
        feed = (judge::fetch_feed(&reqwest::Client::new()).await.unwrap_or_else(|e| json!({"error": e.to_string(), "items": []})), now());
    }
    let (user, ids) = logic::judge_input(&s, &fired, &feed.0, &vault, now());
    let att = judge.attest().await.unwrap_or_else(|e| json!({"ok": false, "error": e.to_string()}));
    let call = if att["ok"] == true {
        judge.ask(&user, &ids, att["signing_address"].as_str().unwrap_or("")).await.unwrap_or_else(|e| json!({"error": e.to_string()}))
    } else {
        json!({"skipped": "model attestation did not verify"})
    };
    let outcome = judge::classify(&call);
    let accepted = if outcome == "answer" {
        Some((call["parsed"]["action"].as_str().unwrap_or("").to_string(), call["parsed"]["risk"].as_str().unwrap_or("").to_string()))
    } else {
        None
    };
    let record = json!({"attestation": att, "call": call, "outcome": outcome, "accepted": accepted.is_some(),
        "applies": if accepted.is_some() { "the judgment, through the agreement rule" } else { "rules only" },
        "input_block": s.block, "feed": feed.0, "seconds": t.elapsed().as_secs_f64()});
    JudgeOutcome { record, feed, accepted }
}

fn spawn_judge(ctx: &Ctx, st: &mut State, s: &Snapshot, fired: &[Fired], tick: u64) {
    let trigger = logic::fired_set(fired);
    println!("RESULT judge started tick={tick} block={} trigger={trigger:?}", s.block);
    let handle = tokio::spawn(judge_job(ctx.judge.clone(), st.feed.clone(), s.clone(), fired.to_vec(), st.vault.clone()));
    st.inflight = Some(InFlight { handle, trigger, block: s.block, started: Instant::now() });
}

// ------------------------------------------------------------------ propose (build items 10, 11, 12, 50, 54)

async fn propose(ctx: &Ctx, s: &Snapshot, d: &Decision, vault: &mut Value, status: &mut AgentStatus) -> Value {
    let a = &ctx.a;
    // fresh vault state right before the pre-flight: a revoke is seen here, not as a "no nonce" error later
    match ctx.nearc.view(&a.vault, "get_state", &json!({})).await {
        Ok(v) => *vault = v,
        Err(e) => return json!({"proposed": false, "why_not_proposed": format!("vault state not readable for the pre-flight: {e}")}),
    }
    *status = logic::agent_status(vault, &ctx.our_pk, now());
    let checks = logic::prechecks(d, s, vault, &ctx.our_pk, now());
    let refused = logic::refusals(&checks);
    if !logic::may_propose(*status, a.tee) {
        println!("RESULT revoked status={} market={}: not proposing; ticking read-only", status.label(), d.market.name);
        return json!({"proposed": false, "why_not_proposed": status.label(), "prechecks": checks});
    }
    let m = &d.market;
    let deadline = now() + DEADLINE_SECS - 10;
    let (method, args, data) = match d.action {
        Act::Reduce => ("reduce", json!({"market": m.id, "amount": d.amount.to_string()}), chain::calldata_reduce(&m.id, d.amount, deadline)),
        Act::Migrate => {
            let Some(t) = d.to.as_ref() else { return json!({"proposed": false, "why_not_proposed": "migrate without a destination"}) };
            ("migrate", json!({"from": m.id, "to": t.id, "fraction": d.fraction.to_string()}), chain::calldata_migrate(&m.id, &t.id, d.fraction, deadline))
        }
        Act::Exit => ("exit", json!({"market": m.id}), chain::calldata_exit(&m.id, deadline)),
        Act::None => return json!({"proposed": false, "why_not_proposed": "no action"}),
    };
    let swap = matches!(d.action, Act::Reduce | Act::Exit);
    // estimate at the latest block right before the vault call; the margin depends on the call kind
    let (params, estimate) = match ctx.base.tx_params(&s.user, &s.executor, &data, swap).await {
        Ok(x) => x,
        Err(e) => return json!({"proposed": false, "method": method, "args": args, "error": format!("tx params: {e}"), "prechecks": checks}),
    };
    println!("RESULT gas method={method} estimate={:?} gas_limit={} margin_pct={} cap={}", estimate, params["gas_limit"], params["margin_pct"], chain::GAS_LIMIT_MAX);
    let unsigned = match chain::unsigned_1559(&params, &s.executor, &data) {
        Ok(u) => u,
        Err(e) => return json!({"proposed": false, "method": method, "args": args, "error": format!("unsigned tx: {e}"), "prechecks": checks}),
    };
    let l1_fee = ctx.base.l1_fee(&unsigned).await.map(|x| x.to_string()).map_err(|e| e.to_string());
    let check = ctx
        .nearc
        .view(&a.vault, "check_transaction", &json!({"tx_hex": hex::encode(&unsigned), "caller": "agent"}))
        .await
        .map(|v| v.to_string())
        .unwrap_or_else(|e| e.to_string());
    let check_ok = check == "\"ok\"";
    let mut out = json!({"proposed": true, "method": method, "args": args, "tx": params, "estimate": estimate.as_ref().map_err(|e| e.clone()),
        "l1_fee_wei": l1_fee, "calldata": format!("0x{data}"), "deadline_in_check": deadline, "unsigned_1559": format!("0x{}", hex::encode(&unsigned)),
        "vault_check_transaction": check, "prechecks": checks, "refusals": refused, "agent_status": status.label(), "mode": a.mode});
    if a.mode != "live" {
        out["sent"] = json!(false);
        return out;
    }
    let mut why = refused.clone();
    if let Err(e) = &estimate {
        why.push(format!("gas: {e}"));
    }
    if !check_ok {
        why.push(format!("vault check_transaction: {check}"));
    }
    if !why.is_empty() {
        out["sent"] = json!(false);
        out["why_not_sent"] = json!(why);
        return out;
    }
    let mut call_args = args.clone();
    call_args["tx"] = json!({"nonce": params["nonce"], "gas_limit": params["gas_limit"], "max_fee_per_gas": params["max_fee_per_gas"],
        "max_priority_fee_per_gas": params["max_priority_fee_per_gas"]});
    let t = Instant::now();
    match ctx.nearc.function_call(&ctx.sk, &a.vault, &a.vault, method, call_args.to_string().as_bytes(), AGENT_CALL_GAS, 0).await {
        Ok((h, r)) => {
            out["near_tx"] = json!(h);
            out["near_gas"] = near::gas_summary(&r);
            out["near_seconds"] = json!(t.elapsed().as_secs_f64());
            let signed = near::success_value(&r);
            out["signed"] = signed.clone().unwrap_or(Value::Null);
            if let Some(tx) = signed.as_ref().and_then(|v| v.as_array()).and_then(|v| v.first()) {
                let t2 = Instant::now();
                out["base"] = match ctx.base.broadcast(tx["raw"].as_str().unwrap_or(""), tx["hash"].as_str().unwrap_or("")).await {
                    Ok(rc) => rc,
                    Err(e) => json!({"error": e.to_string()}),
                };
                out["base_seconds"] = json!(t2.elapsed().as_secs_f64());
            }
            out["sent"] = json!(true);
        }
        Err(e) => out["near_error"] = json!(e.to_string()),
    }
    out
}

// ------------------------------------------------------------------ one tick (§4.9)

fn status_line(s: &Snapshot) -> String {
    s.markets
        .iter()
        .filter(|m| m.exists)
        .map(|m| {
            let l = m.ltv.as_f64().map(|v| format!("{v:.4}")).unwrap_or("no_collateral_value".into());
            format!("{}: ltv={l} debt={} util={:.3} oracle={:.2} gap={:?}", m.name, m.debt, m.utilization, m.oracle_usd, m.pool_gap.map(|g| (g * 1e4).round() / 1e2))
        })
        .collect::<Vec<_>>()
        .join(" | ")
}

async fn tick(ctx: Arc<Ctx>, st: Arc<Mutex<State>>, n: u64) {
    let mut guard = st.lock().await;
    let st = &mut *guard;
    let t0 = Instant::now();
    if n % STATUS_EVERY == 0 {
        if let Ok(v) = ctx.nearc.view(&ctx.a.vault, "get_state", &json!({})).await {
            st.vault = v;
        }
    }
    let status = logic::agent_status(&st.vault, &ctx.our_pk, now());
    if status != st.status {
        println!("RESULT status agent={} (was {})", status.label(), st.status.label());
        st.status = status;
    }

    // a judge result that arrived since the last tick is taken now (build item 44)
    if st.inflight.as_ref().map(|f| f.handle.is_finished()).unwrap_or(false) {
        if let Some(f) = st.inflight.take() {
            let outcome = match f.handle.await {
                Ok(o) => o,
                Err(e) => JudgeOutcome { record: json!({"error": format!("judge task failed: {e}"), "outcome": "error", "applies": "rules only"}), feed: st.feed.clone(), accepted: None },
            };
            st.feed = outcome.feed.clone();
            println!("RESULT judge arrived tick={n} block={} outcome={} accepted={} seconds={:.1}", f.block, outcome.record["outcome"], outcome.accepted.is_some(), f.started.elapsed().as_secs_f64());
            st.ready = Some(Ready { outcome, trigger: f.trigger, block: f.block, arrived_tick: n });
        }
    }

    let s = match ctx.base.snapshot(&ctx.base_address, &ctx.executor, &ctx.market_ids).await {
        Ok(s) => s,
        Err(e) => {
            println!("TICK {n} failed: read error: {e}");
            return;
        }
    };
    let read_ms = t0.elapsed().as_millis();
    if let Err(why) = logic::gate(&s) {
        // a failed tick: no screens, no judge, no decision, and the screen state is left as it was
        println!("TICK {n} block={} failed: {why}", s.block);
        return;
    }
    let fired = logic::screens(&s);
    let fired_set = logic::fired_set(&fired);
    let new: Vec<String> = fired_set.difference(&st.fired_prev).cloned().collect();
    let t_now = now();

    // judge: on a new screen or a digest, at most one call in flight
    let digest = t_now >= st.next_digest;
    if digest {
        st.next_digest = t_now + DIGEST_SECS;
    }
    if !new.is_empty() || digest {
        st.judge_wanted = Some(st.judge_wanted.unwrap_or(false) || digest);
    }
    if let Some(want_digest) = st.judge_wanted {
        if !want_digest && fired.is_empty() {
            st.judge_wanted = None; // the screens that wanted a judgment are gone
        } else if st.inflight.is_none() && st.ready.is_none() {
            st.judge_wanted = None;
            spawn_judge(&ctx, st, &s, &fired, n);
        }
    }

    // a ready judgment: ignored if its screens are gone, else applied once both cooldowns let a proposal in
    let free_at = logic::cooldown_free_at(&st.vault, &s);
    let ready_relevant = st.ready.as_ref().map(|r| logic::relevant(&r.trigger, &fired_set));
    let judge_ready = match ready_relevant {
        Some(false) => true,
        Some(true) => t_now >= free_at,
        None => false,
    };
    let standing = logic::standing(&s, &fired);
    let due = logic::due(t_now, !new.is_empty(), judge_ready, standing, st.next_recheck);
    let judge_note = st.inflight.as_ref().map(|f| format!(" judge=in_flight({:.0}s)", f.started.elapsed().as_secs_f64())).unwrap_or_default();
    if let Due::No { standing } = due {
        if standing {
            // one line per tick while a condition stays on; decide re-runs on the cooldown boundary (build item 53)
            println!("TICK {n} block={} read_ms={read_ms} status={} screens={fired_set:?} waiting: next decision in {} s{judge_note}{}",
                s.block, st.status.label(), st.next_recheck.saturating_sub(t_now),
                if ready_relevant == Some(true) { format!(" judgment ready, applied in {} s", free_at.saturating_sub(t_now)) } else { String::new() });
        } else if n % STATUS_EVERY == 1 || fired_set != st.fired_prev {
            println!("TICK {n} block={} read_ms={read_ms} status={} l1_base_fee={:?} screens={fired_set:?}{judge_note} | {}", s.block, st.status.label(), s.l1_base_fee, status_line(&s));
        }
        st.fired_prev = fired_set;
        return;
    }

    // ---- a decision loop: decide every market, propose at most one, write one transcript
    st.loops += 1;
    let loops = st.loops;
    let tl = Instant::now();
    let mut judge_record = Value::Null;
    let mut judgment: Option<Judgment> = None;
    if judge_ready {
        if let Some(r) = st.ready.take() {
            let mut rec = r.outcome.record;
            rec["trigger"] = json!(r.trigger);
            rec["arrived_tick"] = json!(r.arrived_tick);
            if ready_relevant == Some(false) {
                let gone: Vec<&String> = r.trigger.difference(&fired_set).collect();
                rec["ignored"] = json!(format!("the screens it was asked about are gone: {gone:?}; rules only"));
            } else if let Some((action, risk)) = r.outcome.accepted {
                judgment = Some(Judgment { action, risk, trigger: r.trigger, block: r.block });
            }
            judge_record = rec;
        }
    }
    let decisions = logic::decide_all(&s, &fired, judgment.as_ref(), &st.vault);
    let chosen = logic::pick(&decisions);
    let trigger = match due {
        Due::NewScreens => "new_screens",
        Due::JudgeResult => "judge_result",
        _ => "recheck_on_cooldown",
    };
    let mut tr = json!({"agent": ctx.agent_record, "loop": loops, "tick": n, "time_utc": iso(t_now), "agent_status": st.status.label(),
        "trigger": {"kind": trigger, "new_screens": new, "judge_in_flight": st.inflight.is_some()},
        "inputs": {"snapshot": serde_json::to_value(&s).unwrap_or_else(|e| json!({"serialize_error": e.to_string()}))},
        "screens": fired, "judge": judge_record, "judgment_applied": judgment,
        "decisions": decisions.iter().map(|d| d.json()).collect::<Vec<_>>(), "chosen": chosen.map(|i| decisions[i].market.name.clone())});
    for d in &decisions {
        println!("RESULT decision loop={loops} market={} action={:?} protective={} amount={} fraction={} basis={}", d.market.name, d.action, d.protective, d.amount, d.fraction, d.basis);
    }
    if let Some(i) = chosen {
        let d = &decisions[i];
        let mut status = st.status;
        let p = propose(&ctx, &s, d, &mut st.vault, &mut status).await;
        st.status = status;
        println!("RESULT proposal loop={loops} {}", json!({"market": d.market.name, "method": p["method"], "args": p["args"], "tx": p["tx"],
            "estimate": p["estimate"], "l1_fee_wei": p["l1_fee_wei"], "vault_check_transaction": p["vault_check_transaction"], "refusals": p["refusals"],
            "sent": p["sent"], "why_not_sent": p["why_not_sent"], "why_not_proposed": p["why_not_proposed"], "near_tx": p["near_tx"],
            "base": p["base"], "error": p["error"], "near_error": p["near_error"]}));
        tr["proposal"] = p;
    }
    st.next_recheck = logic::next_recheck(now(), logic::exec_cooldown(&s), logic::cooldown_free_at(&st.vault, &s));
    tr["next_recheck_utc"] = json!(iso(st.next_recheck));
    tr["seconds"] = json!(tl.elapsed().as_secs_f64());
    let name = format!("transcripts/{}-{loops:04}.json", ctx.boot_id);
    // a transcript that cannot be written is reported, never fatal (build item 49)
    match serde_json::to_string_pretty(&tr).map_err(anyhow::Error::from).and_then(|t| tee::save_evidence(&ctx.a.evidence, &name, t.as_bytes())) {
        Ok(h) => println!("RESULT transcript loop={loops} {name} sha256={h} seconds={:.1}", tl.elapsed().as_secs_f64()),
        Err(e) => println!("RESULT transcript loop={loops} {name} write failed: {e}"),
    }
    st.fired_prev = fired_set;
}

// ------------------------------------------------------------------ main

#[tokio::main]
async fn main() -> Result<()> {
    let a = args()?;
    let sk = SigningKey::generate(&mut rand::rngs::OsRng);
    let implicit = near::implicit_account(&sk);
    let boot_id = format!("boot-{}-{}", now(), &implicit[..8]);
    println!("RESULT start mode={} vault={} tee={} boot={boot_id}", a.mode, a.vault, a.tee);
    println!("RESULT agent_public_key={} implicit_account={implicit}", near::pk_string(&sk));
    std::fs::create_dir_all(&a.evidence)?;
    let nearc = near::Near::new(&a.near_rpc);
    let base = Base::new(a.base_rpcs.clone());
    let key = match &a.nearai_key_file {
        Some(f) => std::fs::read_to_string(f)?.trim().to_string(),
        None => std::env::var("NEARAI_API_KEY").map_err(|_| anyhow!("NEARAI_API_KEY not set"))?,
    };
    let judge = Arc::new(judge::Judge::new(key));
    let mut agent_record = json!({"public_key": near::pk_string(&sk), "implicit_account": implicit, "boot": boot_id, "mode": a.mode});

    // ---- 1. quote, evidence, registration (§4.4)
    if a.tee {
        let b = tee::boot(&sk, &a.evidence, &boot_id).await?;
        println!("RESULT measurement={} rtmr3_replay_matches_quote={} debug={}", b.measurement, b.rtmr3_replayed_matches, b.debug);
        agent_record["measurement"] = b.measurement.clone();
        let imr3: Vec<Value> = b.event_log.iter().filter(|e| e["imr"] == 3).cloned().collect();
        let mut last_wait = Instant::now() - Duration::from_secs(120);
        loop {
            let bal = nearc.balance(&implicit).await.unwrap_or(None);
            let st = nearc.view(&a.vault, "get_state", &json!({})).await.unwrap_or_default();
            let approved = st["approved"] == b.measurement;
            if bal.unwrap_or(0) >= MIN_REGISTRATION_BALANCE && approved {
                break;
            }
            if last_wait.elapsed() > Duration::from_secs(60) {
                println!("WAITING implicit_account={implicit} balance_yocto={:?} vault_approves_this_measurement={approved}", bal);
                last_wait = Instant::now();
            }
            tokio::time::sleep(Duration::from_secs(15)).await;
        }
        let mut attempt = 0;
        let (rec, registered, st) = loop {
            attempt += 1;
            let (coll, cinfo) = tee::collateral(&b.quote).await?;
            println!("RESULT collateral={cinfo}");
            let reg_args = json!({"quote_hex": hex::encode(&b.quote), "collateral": coll, "event_log": imr3});
            let bytes = reg_args.to_string().into_bytes();
            let bal_before = nearc.balance(&implicit).await?.unwrap_or(0);
            let t = Instant::now();
            let res = nearc.function_call(&sk, &implicit, &a.vault, "register_agent", &bytes, REGISTER_GAS, 0).await;
            let secs = t.elapsed().as_secs_f64();
            let bal_after = nearc.balance(&implicit).await?.unwrap_or(0);
            let rec = match res {
                Ok((h, r)) => json!({"near_tx": h, "args_bytes": bytes.len(), "seconds": secs, "gas": near::gas_summary(&r), "attempt": attempt,
                    "balance_before_yocto": bal_before.to_string(), "balance_after_yocto": bal_after.to_string(), "collateral": cinfo}),
                Err(e) => json!({"error": e.to_string(), "args_bytes": bytes.len(), "seconds": secs, "attempt": attempt, "collateral": cinfo}),
            };
            let st = nearc.view(&a.vault, "get_state", &json!({})).await.unwrap_or_default();
            let registered = st["agent"]["public_key"].as_str() == Some(&near::pk_string(&sk));
            if registered || attempt >= REGISTER_ATTEMPTS {
                break (rec, registered, st);
            }
            let wait = 15u64 << (attempt - 1); // 15, 30, 60 s
            println!("RESULT register_agent attempt={attempt} failed, retry in {wait} s: {}", rec["error"]);
            tokio::time::sleep(Duration::from_secs(wait)).await;
        };
        println!("RESULT register_agent registered={registered} vault_agent={} record={}", st["agent"], json!({"near_tx": rec["near_tx"], "tgas": rec["gas"]["tgas_total"], "near_burnt": rec["gas"]["near_burnt"], "seconds": rec["seconds"], "attempt": attempt, "error": rec["error"]}));
        let mut full = rec.clone();
        full["registered"] = json!(registered);
        full["vault_agent"] = st["agent"].clone();
        tee::save_evidence(&a.evidence, &format!("{boot_id}/registration.json"), serde_json::to_string_pretty(&full)?.as_bytes())?;
        agent_record["registration"] = json!({"near_tx": rec["near_tx"], "registered": registered});
        if !registered {
            return Err(anyhow!("register_agent failed after {attempt} attempts: not ticking unregistered"));
        }
    }

    // ---- 2. the tick loop (§4.9)
    let vault = nearc.view(&a.vault, "get_state", &json!({})).await?;
    let base_address = vault["base_address"].as_str().unwrap_or("").to_string();
    let markets = vault["markets"].as_array().ok_or_else(|| anyhow!("vault state has no markets: {vault}"))?;
    let market_ids: Vec<String> = markets.iter().filter_map(|m| m["id"].as_str().map(|s| s.to_string())).collect();
    let executor = markets.first().and_then(|m| m["executor"].as_str()).unwrap_or("").to_string();
    if market_ids.is_empty() || base_address.is_empty() || executor.is_empty() {
        return Err(anyhow!("vault state lacks markets, base address or executor"));
    }
    println!("RESULT watching base_address={base_address} executor={executor} markets={market_ids:?} rpcs={:?}", a.base_rpcs);
    let our_pk = near::pk_string(&sk);
    let status = logic::agent_status(&vault, &our_pk, now());
    println!("RESULT status agent={}", status.label());
    let next_digest = if a.digest_at_start { now() } else { now() + DIGEST_SECS };
    let (tick_secs, max_ticks) = (a.tick_secs, a.max_ticks);
    let ctx = Arc::new(Ctx { a, base, nearc, sk, our_pk, judge, agent_record, boot_id, base_address, executor, market_ids });
    let st = Arc::new(Mutex::new(State {
        vault,
        fired_prev: BTreeSet::new(),
        next_digest,
        feed: (Value::Null, 0),
        loops: 0,
        next_recheck: 0,
        inflight: None,
        ready: None,
        judge_wanted: None,
        status,
    }));
    let mut n = 0u64;
    while n < max_ticks {
        n += 1;
        let t0 = Instant::now();
        // each tick in its own task: a panic anywhere in it is logged and the loop goes on (build item 49)
        if let Err(e) = tokio::spawn(tick(ctx.clone(), st.clone(), n)).await {
            println!("TICK {n} aborted: {e}");
        }
        let el = t0.elapsed();
        if el < Duration::from_secs(tick_secs) {
            tokio::time::sleep(Duration::from_secs(tick_secs) - el).await;
        }
    }
    let st = st.lock().await;
    if let Some(f) = &st.inflight {
        f.handle.abort();
    }
    println!("RESULT done ticks={n} loops={}", st.loops);
    Ok(())
}
