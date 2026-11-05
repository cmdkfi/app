//! cmdk agent (hackathon build, from the 27–31 Oct prototype). Spec: docs/01-design-and-feasibility.md §4.4 (key,
//! quote, registration) and §4.9 (collect, compute, screen, judge, decide, propose).
//! Deviations (brief): the positions of each watched vault's Base address; on-chain reads plus three allowlisted text
//! feeds (status.base.org and the two labelled demo feeds); no scores and no similar-case retrieval.
//!
//! Vaults (decided 3 Nov; §4.4 "one shared enclave serves every vault that approved our compose hash"): `--factory
//! <account>` watches every vault `v1..vN.<factory>` whose `approved` equals this boot's measurement; `vaults.rs` has
//! the scan's set logic. Every STATUS_EVERY ticks the refresh reads the factory's count, resolves only the indices
//! not yet known, re-reads each known vault's state (an approval joins, a revoke leaves), registers on each vault that
//! joined (one key and one implicit account for all; each vault has its own expiry and re-attestation) and starts one
//! tick loop per registered vault. Per vault: its own state, Base position, screens, judge slot, cooldowns, sessions
//! and transcripts; nothing mutable about positions is shared. `--vault <account>` (repeatable) replaces the factory
//! in dry and twin runs and tests; the live modes need `--factory`.
//!
//! Behaviour is fixed in the binary; the compose file (measured) passes only `--mode`, `--operator`, `--factory` and
//! the evidence port. Modes (one flag, four values; `MEASUREMENT.md` has the table):
//! - `dry`: builds every proposal exactly as the vault would and runs the vault's `check_transaction` view on it,
//!   but sends nothing to the vault's agent methods and nothing to Base;
//! - `live`: calls the vault through the agent key and broadcasts the transaction the vault signs;
//! - `twin`: the deterministic twin (decided 25 Oct): the same ticks, screens and protective rule, no judge call
//!   and no text feed ever, so it never proposes a discretionary action; read-only like `dry`;
//! - `twin-live`: the twin, sending like `live` (only protective slices can go out).
//!
//! `--operator <name>` names who runs this agent; it sits in the measured compose's command line, so the owner's
//! `approve_measurement` binds "this code, run by this operator" (31 Oct). `--no-tee` (local runs only) skips the
//! quote and registration. `--fork` (or every provider answering `anvil_nodeInfo`) marks an anvil-fork rehearsal.
//!
//! Tick loop (build items 44–54), one per watched vault: each tick runs in its own task, so a panic is logged and the
//! next tick runs. The judge runs in a separate task, at most one call in flight per vault; its result is taken on the first tick after it
//! arrives, applied only while the screens it was asked about are still on, and never delays the protective rule.
//! Feed-item trigger (§4.9 step 3): outside the twin modes a feed task polls the allowlisted feeds every `--feed-secs`
//! (60 s); an item id not seen before (memory plus `feeds/seen.json` in the evidence volume) wants a judge call, like
//! a new screen, on every watched vault (each vault keeps its own cursor into the list of new items). The tick never
//! waits for the poll: it takes what the last poll left. Decisions are pure functions
//! in `logic.rs`; the agreement rule's text path is `logic::text_corroboration`.
mod chain;
mod judge;
mod logic;
mod near;
mod serve;
mod tee;
mod vaults;

use anyhow::{anyhow, Result};
use chain::{Base, Snapshot, DEADLINE_SECS};
use ed25519_dalek::SigningKey;
use logic::{Act, AgentStatus, CitedItem, Decision, Due, Fired, Judgment};
use serde_json::{json, Value};
use std::collections::{BTreeMap, BTreeSet};
use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};
use tokio::sync::Mutex;
use tokio::task::JoinHandle;

const NEAR_RPC: &str = "https://rpc.testnet.near.org";
const BASE_RPCS: [&str; 2] = ["https://base-sepolia-rpc.publicnode.com", "https://sepolia.base.org"];
const DIGEST_SECS: u64 = 6 * 3600;
/// The feed poll's default interval (`--feed-secs`).
const FEED_POLL_SECS: u64 = 60;
/// The seen-items file in the evidence volume (private: not served on 8080).
const SEEN_FILE: &str = "feeds/seen.json";
/// `register_agent` calls per vault per boot; then that vault is given up for this boot. When every watched vault is
/// given up and none is registered, the agent exits non-zero (the restart policy gives a fresh key): never tick
/// unregistered.
const REGISTER_ATTEMPTS: u32 = 4;
/// How often the refresh checks a registered vault's expiry; a failed re-registration is retried on the next check.
const REREGISTER_POLL_SECS: u64 = 600;
const AGENT_CALL_GAS: u64 = 150_000_000_000_000;
const EVIDENCE_HTTP: &str = "0.0.0.0:8080";
const FUNDER: &str = "funded by the relayer's fund-agent endpoint (POST /fund-agent {\"implicit_account\": \"<this account>\"}, scripts/RELAYER.md)";
/// one status line every this many ticks while nothing is on; also the cadence of each vault's state read and of the
/// factory refresh (scan, membership, registrations)
const STATUS_EVERY: u64 = 30;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum Mode {
    Dry,
    Live,
    Twin,
    TwinLive,
}

impl Mode {
    fn parse(s: &str) -> Result<Mode> {
        Ok(match s {
            "dry" => Mode::Dry,
            "live" => Mode::Live,
            "twin" => Mode::Twin,
            "twin-live" => Mode::TwinLive,
            _ => return Err(anyhow!("--mode is dry, live, twin or twin-live")),
        })
    }
    fn label(&self) -> &'static str {
        match self {
            Mode::Dry => "dry",
            Mode::Live => "live",
            Mode::Twin => "twin",
            Mode::TwinLive => "twin-live",
        }
    }
    /// Sends to the vault's agent methods and broadcasts to Base.
    fn sends(&self) -> bool {
        matches!(self, Mode::Live | Mode::TwinLive)
    }
    /// The deterministic twin: no judge, no text feeds.
    fn twin(&self) -> bool {
        matches!(self, Mode::Twin | Mode::TwinLive)
    }
}

/// `--mode live` in the TEE refuses to start without the NEAR AI key (decided 3 Nov); `dry`, the twins and a local
/// `--no-tee` run may still run without it (every judgment skipped and logged, rules only). Names the source, never the value.
fn require_judge_key(mode: Mode, tee: bool, key_file: Option<&str>, has_key: bool) -> Result<()> {
    if mode != Mode::Live || !tee || has_key {
        return Ok(());
    }
    let source = match key_file {
        Some(f) => format!("the file {f} given by --nearai-key-file is empty"),
        None => "NEARAI_API_KEY is not set or empty".into(),
    };
    Err(anyhow!("--mode live needs the NEAR AI API key for the judge: {source}; refusing to start"))
}

/// The boot line of the NEAR AI key check (`judge::key_check_record`): HTTP status, validity, key id and, on failure,
/// the provider's error type and message. The record never holds the key.
fn key_check_line(r: &Value) -> String {
    let http = r["http"].as_u64().map_or("none".to_string(), |c| c.to_string());
    let valid = r["valid"].as_bool().map_or("unknown", |v| if v { "true" } else { "false" });
    let id = r["api_key_id"].as_str().unwrap_or("none");
    let mut line = format!("RESULT nearai_key http={http} valid={valid} api_key_id={id} seconds={:.1}", r["seconds"].as_f64().unwrap_or(0.0));
    if r["ok"] != true {
        line += &format!(" error_type={} error={:?}", r["error"]["type"].as_str().unwrap_or("none"), r["error"]["message"].as_str().unwrap_or(""));
    }
    line
}

/// An operator name: 1–32 of a-z, 0-9 and '-', starting with a letter or digit.
fn valid_operator(s: &str) -> bool {
    !s.is_empty() && s.len() <= 32 && s.bytes().all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'-') && !s.starts_with('-')
}

struct Args {
    mode: Mode,
    operator: String,
    fork: bool,
    evidence_http: Option<String>,
    source: vaults::Source,
    tee: bool,
    base_rpcs: Vec<String>,
    near_rpc: String,
    tick_secs: u64,
    max_ticks: u64,
    digest_at_start: bool,
    evidence: PathBuf,
    nearai_key_file: Option<String>,
    feed_secs: u64,
}

fn args() -> Result<Args> {
    let mut a = Args {
        mode: Mode::Dry,
        operator: String::new(),
        fork: false,
        evidence_http: Some(EVIDENCE_HTTP.into()),
        source: vaults::Source::List(vec![]),
        tee: true,
        base_rpcs: vec![],
        near_rpc: NEAR_RPC.into(),
        tick_secs: 2,
        max_ticks: u64::MAX,
        digest_at_start: true,
        evidence: PathBuf::from("/evidence"),
        nearai_key_file: None,
        feed_secs: FEED_POLL_SECS,
    };
    let v: Vec<String> = std::env::args().skip(1).collect();
    let (mut factory, mut list) = (None, vec![]);
    let mut i = 0;
    while i < v.len() {
        // flags without a value
        match v[i].as_str() {
            "--no-tee" => a.tee = false,
            "--no-digest" => a.digest_at_start = false,
            "--fork" => a.fork = true,
            flag => {
                let val = v.get(i + 1).cloned().ok_or_else(|| anyhow!("{flag} needs a value"))?;
                match flag {
                    "--mode" => a.mode = Mode::parse(&val)?,
                    "--operator" => a.operator = val,
                    "--evidence-http" => a.evidence_http = Some(val).filter(|v| v != "off"),
                    "--factory" => factory = Some(val),
                    "--vault" => list.push(val),
                    "--base-rpc" => a.base_rpcs.push(val),
                    "--near-rpc" => a.near_rpc = val,
                    "--tick-secs" => a.tick_secs = val.parse()?,
                    "--max-ticks" => a.max_ticks = val.parse()?,
                    "--evidence" => a.evidence = PathBuf::from(val),
                    "--nearai-key-file" => a.nearai_key_file = Some(val),
                    "--feed-secs" => a.feed_secs = val.parse::<u64>()?.max(1),
                    x => return Err(anyhow!("unknown argument {x}")),
                }
                i += 1;
            }
        }
        i += 1;
    }
    if a.operator.is_empty() && !a.tee {
        a.operator = "local".into(); // a --no-tee run never registers, so no approval names it
    }
    if !valid_operator(&a.operator) {
        return Err(anyhow!("--operator <name> is required (1-32 of a-z, 0-9, '-'); it is part of the measured compose"));
    }
    if a.base_rpcs.is_empty() {
        a.base_rpcs = BASE_RPCS.iter().map(|s| s.to_string()).collect();
    }
    a.source = vault_source(a.mode, factory, list)?;
    Ok(a)
}

/// A NEAR account id as the agent accepts it on the command line: 2-64 of a-z, 0-9, '.', '-', '_'.
fn valid_account(s: &str) -> bool {
    (2..=64).contains(&s.len()) && s.bytes().all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b"._-".contains(&b))
}

/// `--factory` (every vault of the factory that approves our measurement; decided 3 Nov) or `--vault`, repeatable
/// (dry and twin runs and tests). The live modes need the factory; the two never mix.
fn vault_source(mode: Mode, factory: Option<String>, list: Vec<String>) -> Result<vaults::Source> {
    if let Some(bad) = factory.iter().chain(list.iter()).find(|x| !valid_account(x)) {
        return Err(anyhow!("{bad:?} is not a NEAR account id"));
    }
    if list.iter().collect::<BTreeSet<_>>().len() != list.len() {
        return Err(anyhow!("--vault given twice for the same account"));
    }
    match (factory, list.is_empty()) {
        (Some(_), false) => Err(anyhow!("--factory and --vault are mutually exclusive")),
        (Some(f), true) => Ok(vaults::Source::Factory(f)),
        (None, false) if mode.sends() => Err(anyhow!("--mode {} needs --factory <account> (--vault is for dry and twin runs)", mode.label())),
        (None, false) => Ok(vaults::Source::List(list)),
        (None, true) => Err(anyhow!("--factory <account> is required (or, in dry and twin runs, --vault <account>, repeatable)")),
    }
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
    /// None in the twin modes (never called) and when no NEAR AI key is set (skipped, logged)
    judge: Option<Arc<judge::Judge>>,
    agent_record: Value,
    /// an anvil-fork rehearsal: `feed_stale` is labelled "frozen on fork" instead of firing (build item 43)
    fork: bool,
    boot_id: String,
    /// what the feed task left: the latest poll and every new item id of this boot, in arrival order
    feeds: Arc<std::sync::Mutex<FeedShared>>,
}

#[derive(Default)]
struct FeedShared {
    latest: Option<Value>,
    /// append-only; each vault's tick takes the items past its own cursor (`vaults::take_new_items`)
    items: Vec<String>,
}

/// One watched vault, fixed when its tick loop starts. Its mutable state is its own `State`.
struct VaultCtx {
    slot: u64,
    /// `v<n>` (factory vault n) or `vault<n>` (the n-th --vault): in log lines, transcript and evidence names
    tag: String,
    id: String,
    /// this boot's registration on this vault, as its transcripts record it
    registration: Value,
    progress: Arc<vaults::Progress>,
    /// set when the vault leaves the watched set: its loop ends before its next tick
    stop: Arc<AtomicBool>,
}

fn lock_feeds(f: &std::sync::Mutex<FeedShared>) -> std::sync::MutexGuard<'_, FeedShared> {
    f.lock().unwrap_or_else(|e| e.into_inner())
}

/// What the judge task returns: the full record for the transcript and, if accepted, the answer.
struct JudgeOutcome {
    record: Value,
    accepted: Option<(String, String, Option<String>, Vec<CitedItem>)>, // (action, risk, the market it names, the feed items it cited)
}

struct InFlight {
    handle: JoinHandle<JudgeOutcome>,
    trigger: BTreeSet<String>,
    /// the new feed items that wanted this call (empty for a screen or digest trigger)
    feed_items: Vec<String>,
    block: u64,
    started: Instant,
}

struct Ready {
    outcome: JudgeOutcome,
    trigger: BTreeSet<String>,
    feed_items: Vec<String>,
    block: u64,
    arrived_tick: u64,
}

struct State {
    vault: Value,
    fired_prev: BTreeSet<String>,
    next_digest: u64,
    loops: u64,
    next_recheck: u64,
    inflight: Option<InFlight>,
    ready: Option<Ready>,
    /// a judge call is wanted (Some(independent of screens: digest or new feed item)) but one is in flight;
    /// started on the first tick the slot is free (`logic::want_judge`)
    judge_wanted: Option<bool>,
    /// new feed items taken from the feed task, handed to the next judge call started
    feed_trigger: Vec<String>,
    /// a judgment that was wanted but skipped (no key), recorded in the next transcript
    judge_skipped: Option<Value>,
    status: AgentStatus,
    /// how many of `FeedShared::items` this vault has taken
    feed_cursor: usize,
}

// ------------------------------------------------------------------ judge task (build items 44, 45)

async fn judge_job(judge: Arc<judge::Judge>, feed: Option<Value>, s: Snapshot, fired: Vec<Fired>, vault: Value) -> JudgeOutcome {
    let t = Instant::now();
    // the feed task's latest poll; only before its first poll does the judge fetch the feeds itself
    let feed = match feed {
        Some(f) => f,
        None => {
            let http = reqwest::Client::builder().timeout(Duration::from_secs(20)).build().unwrap_or_default();
            let f = judge::fetch_feeds(&http).await;
            judge::log_feeds(&f);
            f
        }
    };
    let (user, ids) = logic::judge_input(&s, &fired, &feed, &vault, now());
    let att = judge.attest().await.unwrap_or_else(|e| json!({"ok": false, "error": e.to_string()}));
    let call = if att["ok"] == true {
        judge.ask(&user, &ids, att["signing_address"].as_str().unwrap_or("")).await.unwrap_or_else(|e| json!({"error": e.to_string()}))
    } else {
        // TCB status not UpToDate, nonce not bound, debug, or the quote did not verify: the judge is not called
        json!({"skipped": format!("model attestation did not verify: {}", att["failed_because"].as_str().or(att["error"].as_str()).unwrap_or("unknown"))})
    };
    let outcome = judge::classify(&call);
    let accepted = if outcome == "answer" {
        let p = &call["parsed"];
        let sids: Vec<String> = p["source_ids"].as_array().into_iter().flatten().filter_map(|x| x.as_str().map(|x| x.to_string())).collect();
        Some((p["action"].as_str().unwrap_or("").to_string(), p["risk"].as_str().unwrap_or("").to_string(), judge::answer_market(p), logic::cited_items(&sids, &feed)))
    } else {
        None
    };
    let record = json!({"attestation": att, "model_tcb_status": att["tcb_status"], "call": call, "outcome": outcome, "accepted": accepted.is_some(),
        "applies": if accepted.is_some() { "the judgment, on the market it names only, through the agreement rule" } else { "rules only" },
        "judgment_market": accepted.as_ref().map(|a| a.2.clone().unwrap_or_else(|| judge::NO_MARKET.into())),
        "cited_feed_items": accepted.as_ref().map(|a| a.3.clone()),
        "input_block": s.block, "feed": feed, "seconds": t.elapsed().as_secs_f64()});
    JudgeOutcome { record, accepted }
}

fn spawn_judge(ctx: &Ctx, v: &VaultCtx, st: &mut State, s: &Snapshot, fired: &[Fired], tick: u64) {
    let trigger = logic::fired_set(fired);
    let feed_items = std::mem::take(&mut st.feed_trigger);
    let status = logic::agent_status(&st.vault, &ctx.our_pk, now());
    let tag = &v.tag;
    if !logic::may_judge(status, ctx.a.tee) {
        // revoked on this vault (revoke_agent, another key, or expired): no judge call; the rules and ticks go on
        println!("RESULT judge skipped vault={tag} tick={tick} block={} trigger={trigger:?} feed_items={feed_items:?}: agent {}, rules only", s.block, status.label());
        st.judge_skipped = Some(json!({"outcome": "skipped_revoked", "agent_status": status.label(), "applies": "rules only", "trigger": trigger, "trigger_feed_items": feed_items, "tick": tick}));
        return;
    }
    let Some(judge) = ctx.judge.clone() else {
        // twin modes never reach here; without a NEAR AI key the judgment is skipped and the rules apply alone
        println!("RESULT judge skipped vault={tag} tick={tick} block={} trigger={trigger:?} feed_items={feed_items:?}: no NEARAI_API_KEY set, rules only", s.block);
        st.judge_skipped = Some(json!({"outcome": "skipped_no_api_key", "applies": "rules only", "trigger": trigger, "trigger_feed_items": feed_items, "tick": tick}));
        return;
    };
    println!("RESULT judge started vault={tag} tick={tick} block={} trigger={trigger:?} feed_items={feed_items:?}", s.block);
    let latest = lock_feeds(&ctx.feeds).latest.clone();
    let handle = tokio::spawn(judge_job(judge, latest, s.clone(), fired.to_vec(), st.vault.clone()));
    st.inflight = Some(InFlight { handle, trigger, feed_items, block: s.block, started: Instant::now() });
}

// ------------------------------------------------------------------ feed-item trigger (§4.9 step 3)

fn load_seen(path: &std::path::Path) -> logic::Seen {
    match std::fs::read(path) {
        Ok(b) => match serde_json::from_slice::<Value>(&b).ok().and_then(|v| serde_json::from_value(v["seen"].clone()).ok()) {
            Some(m) => m,
            None => {
                println!("RESULT feed seen file {} unreadable: starting with no items seen", path.display());
                logic::Seen::new()
            }
        },
        Err(_) => logic::Seen::new(),
    }
}

/// Polls every allowlisted feed every `every`, in its own task (never in a tick). New item ids are appended to `items`
/// for each vault's next good tick and are written to the seen file at once, so a restart does not re-trigger them. A feed that
/// fails (the demo feeds return 404 while the repository is private) is logged and left out; the others are used.
async fn feed_task(shared: Arc<std::sync::Mutex<FeedShared>>, evidence: PathBuf, every: Duration) {
    let http = reqwest::Client::builder().timeout(Duration::from_secs(20)).build().unwrap_or_default();
    let mut seen = load_seen(&evidence.join(SEEN_FILE));
    println!("RESULT feed poll started every={}s feeds={:?} seen_on_disk={}", every.as_secs(),
        [judge::FEED_URL].iter().chain(judge::DEMO_FEEDS.iter()).collect::<Vec<_>>(), seen.len());
    let mut last_sig = vec![];
    let mut n = 0u64;
    loop {
        n += 1;
        let t = Instant::now();
        let mut feed = judge::fetch_feeds(&http).await;
        let new = logic::note_poll(&mut seen, &mut feed, now());
        let sig = judge::feeds_signature(&feed);
        if sig != last_sig || !new.is_empty() {
            judge::log_feeds(&feed); // per-feed lines on the first poll and whenever a feed's state changes
            last_sig = sig;
        }
        if !new.is_empty() {
            let body = json!({"version": 1, "seen": seen});
            if let Err(e) = tee::save_private(&evidence, SEEN_FILE, body.to_string().as_bytes()) {
                println!("RESULT feed seen file write failed (kept in memory): {e}");
            }
        }
        let ok = feed["feeds"].as_array().map(|a| a.iter().filter(|f| f["ok"] == true).count()).unwrap_or(0);
        println!("RESULT feed poll={n} feeds_ok={ok}/{} new_items={} {new:?} seen={} seconds={:.1}",
            feed["feeds"].as_array().map(|a| a.len()).unwrap_or(0), new.len(), seen.len(), t.elapsed().as_secs_f64());
        {
            let mut g = lock_feeds(&shared);
            g.latest = Some(feed);
            for id in new {
                if !g.items.contains(&id) {
                    g.items.push(id);
                }
            }
        }
        let el = t.elapsed();
        tokio::time::sleep(every.saturating_sub(el)).await;
    }
}

// ------------------------------------------------------------------ propose (build items 10, 11, 12, 50, 54)

async fn propose(ctx: &Ctx, v: &VaultCtx, s: &Snapshot, d: &Decision, vault: &mut Value, status: &mut AgentStatus) -> Value {
    let a = &ctx.a;
    // fresh vault state right before the pre-flight: a revoke is seen here, not as a "no nonce" error later
    match ctx.nearc.view(&v.id, "get_state", &json!({})).await {
        Ok(v) => *vault = v,
        Err(e) => return json!({"proposed": false, "why_not_proposed": format!("vault state not readable for the pre-flight: {e}")}),
    }
    *status = logic::agent_status(vault, &ctx.our_pk, now());
    let checks = logic::prechecks(d, s, vault, &ctx.our_pk, now());
    let refused = logic::refusals(&checks);
    if !logic::may_propose(*status, a.tee) {
        println!("RESULT revoked vault={} status={} market={}: not proposing; ticking read-only", v.tag, status.label(), d.market.name);
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
    println!("RESULT gas vault={} method={method} estimate={:?} gas_limit={} margin_pct={} cap={}", v.tag, estimate, params["gas_limit"], params["margin_pct"], chain::GAS_LIMIT_MAX);
    let unsigned = match chain::unsigned_1559(&params, &s.executor, &data) {
        Ok(u) => u,
        Err(e) => return json!({"proposed": false, "method": method, "args": args, "error": format!("unsigned tx: {e}"), "prechecks": checks}),
    };
    let l1_fee = ctx.base.l1_fee(&unsigned).await.map(|x| x.to_string()).map_err(|e| e.to_string());
    let check = ctx
        .nearc
        .view(&v.id, "check_transaction", &json!({"tx_hex": hex::encode(&unsigned), "caller": "agent"}))
        .await
        .map(|v| v.to_string())
        .unwrap_or_else(|e| e.to_string());
    let check_ok = check == "\"ok\"";
    let mut out = json!({"proposed": true, "method": method, "args": args, "tx": params, "estimate": estimate.as_ref().map_err(|e| e.clone()),
        "l1_fee_wei": l1_fee, "calldata": format!("0x{data}"), "deadline_in_check": deadline, "unsigned_1559": format!("0x{}", hex::encode(&unsigned)),
        "vault_check_transaction": check, "prechecks": checks, "refusals": refused, "agent_status": status.label(), "mode": a.mode.label()});
    if !a.mode.sends() {
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
    match ctx.nearc.function_call(&ctx.sk, &v.id, &v.id, method, call_args.to_string().as_bytes(), AGENT_CALL_GAS, 0).await {
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

async fn tick(ctx: Arc<Ctx>, v: Arc<VaultCtx>, st: Arc<Mutex<State>>, n: u64) {
    let mut guard = st.lock().await;
    let st = &mut *guard;
    let t0 = Instant::now();
    let tag = v.tag.as_str();
    v.progress.tick(n, now());
    if n % STATUS_EVERY == 0 {
        if let Ok(x) = ctx.nearc.view(&v.id, "get_state", &json!({})).await {
            st.vault = x;
        }
    }
    let status = logic::agent_status(&st.vault, &ctx.our_pk, now());
    if status != st.status {
        println!("RESULT status vault={tag} agent={} (was {})", status.label(), st.status.label());
        st.status = status;
    }

    // a judge result that arrived since the last tick is taken now (build item 44)
    if st.inflight.as_ref().map(|f| f.handle.is_finished()).unwrap_or(false) {
        if let Some(f) = st.inflight.take() {
            let outcome = match f.handle.await {
                Ok(o) => o,
                Err(e) => JudgeOutcome { record: json!({"error": format!("judge task failed: {e}"), "outcome": "error", "applies": "rules only"}), accepted: None },
            };
            let market = outcome.accepted.as_ref().map(|a| a.2.clone().unwrap_or_else(|| judge::NO_MARKET.into()));
            let invalid = outcome.record["call"]["invalid_reason"].as_str().map(|r| format!(" invalid_reason={r:?}")).unwrap_or_default();
            println!("RESULT judge arrived vault={tag} tick={n} block={} outcome={} accepted={} market={market:?} seconds={:.1}{invalid}", f.block, outcome.record["outcome"], outcome.accepted.is_some(), f.started.elapsed().as_secs_f64());
            st.ready = Some(Ready { outcome, trigger: f.trigger, feed_items: f.feed_items, block: f.block, arrived_tick: n });
        }
    }

    // this vault's own position: its derived Base address, its executor and markets, from its own state
    let (base_address, executor, market_ids) = match vaults::watch_target(&st.vault) {
        Ok(t) => t,
        Err(why) => {
            println!("TICK {n} vault={tag} failed: {why}");
            return;
        }
    };
    let s = match ctx.base.snapshot(&base_address, &executor, &market_ids).await {
        Ok(s) => s,
        Err(e) => {
            println!("TICK {n} vault={tag} failed: read error: {e}");
            return;
        }
    };
    let read_ms = t0.elapsed().as_millis();
    if let Err(why) = logic::gate(&s) {
        // a failed tick: no screens, no judge, no decision, and the screen state is left as it was
        println!("TICK {n} vault={tag} block={} failed: {why}", s.block);
        return;
    }
    let (fired, frozen) = logic::split_frozen_on_fork(logic::screens(&s), ctx.fork);
    let fired_set = logic::fired_set(&fired);
    let new: Vec<String> = fired_set.difference(&st.fired_prev).cloned().collect();
    let t_now = now();

    // judge: on a new screen, a new feed item or a digest, at most one call in flight
    let digest = t_now >= st.next_digest;
    if digest {
        st.next_digest = t_now + DIGEST_SECS;
    }
    let feed_new: Vec<String> = if ctx.a.mode.twin() { vec![] } else { vaults::take_new_items(&lock_feeds(&ctx.feeds).items, &mut st.feed_cursor) };
    if !feed_new.is_empty() {
        println!("TICK {n} vault={tag} block={} new feed items {feed_new:?}: judge wanted", s.block);
        st.feed_trigger.extend(feed_new.iter().cloned());
    }
    st.judge_wanted = logic::want_judge(st.judge_wanted, !new.is_empty(), digest, !feed_new.is_empty(), ctx.a.mode.twin());
    if let Some(want_digest) = st.judge_wanted {
        if !want_digest && fired.is_empty() {
            st.judge_wanted = None; // the screens that wanted a judgment are gone
        } else if st.inflight.is_none() && st.ready.is_none() {
            st.judge_wanted = None;
            // fresh vault state right before a judge call, so a revoke is seen here (no judge call while revoked)
            if let Ok(x) = ctx.nearc.view(&v.id, "get_state", &json!({})).await {
                st.vault = x;
            }
            spawn_judge(&ctx, &v, st, &s, &fired, n);
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
            println!("TICK {n} vault={tag} block={} read_ms={read_ms} status={} screens={fired_set:?} waiting: next decision in {} s{judge_note}{}",
                s.block, st.status.label(), st.next_recheck.saturating_sub(t_now),
                if ready_relevant == Some(true) { format!(" judgment ready, applied in {} s", free_at.saturating_sub(t_now)) } else { String::new() });
        } else if n % STATUS_EVERY == 1 || fired_set != st.fired_prev {
            let fz = if frozen.is_empty() { String::new() } else { format!(" frozen_on_fork={:?}", logic::fired_set(&frozen)) };
            println!("TICK {n} vault={tag} block={} read_ms={read_ms} status={} l1_base_fee={:?} screens={fired_set:?}{fz}{judge_note} | {}", s.block, st.status.label(), s.l1_base_fee, status_line(&s));
        }
        st.fired_prev = fired_set;
        return;
    }

    // ---- a decision loop: decide every market, propose at most one, write one transcript
    st.loops += 1;
    let loops = st.loops;
    let tl = Instant::now();
    let mut judge_record = if ctx.a.mode.twin() {
        json!({"outcome": "not called: deterministic twin (no model, no text feeds; rules only)"})
    } else {
        st.judge_skipped.take().unwrap_or(Value::Null)
    };
    let mut judgment: Option<Judgment> = None;
    if judge_ready {
        if let Some(r) = st.ready.take() {
            let mut rec = r.outcome.record;
            rec["trigger"] = json!(r.trigger);
            rec["trigger_feed_items"] = json!(r.feed_items);
            rec["arrived_tick"] = json!(r.arrived_tick);
            if ready_relevant == Some(false) {
                let gone: Vec<&String> = r.trigger.difference(&fired_set).collect();
                rec["ignored"] = json!(format!("the screens it was asked about are gone: {gone:?}; rules only"));
            } else if let Some((action, risk, market, cited)) = r.outcome.accepted {
                // applied to the market it names only; every other market decides on the rules alone (3 Nov)
                println!("RESULT judgment applies vault={tag} loop={loops} market={} action={action} risk={risk} (other markets: rules only)", market.as_deref().unwrap_or(judge::NO_MARKET));
                judgment = Some(Judgment { action, risk, market, trigger: r.trigger, block: r.block, cited });
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
    let mut tr = json!({"agent": ctx.agent_record, "vault": {"id": v.id, "tag": tag, "slot": v.slot, "registration": v.registration},
        "mode": ctx.a.mode.label(), "twin": ctx.a.mode.twin(),
        "label": if ctx.a.mode.twin() { "deterministic twin: same ticks, screens and protective rule; no judge, no text feeds" } else { "agent" },
        "network": if ctx.fork { "anvil fork (rehearsal)" } else { "Base Sepolia" },
        "loop": loops, "tick": n, "time_utc": iso(t_now), "agent_status": st.status.label(),
        "trigger": {"kind": trigger, "new_screens": new, "judge_in_flight": st.inflight.is_some()},
        "inputs": {"snapshot": serde_json::to_value(&s).unwrap_or_else(|e| json!({"serialize_error": e.to_string()}))},
        "screens": fired,
        "frozen_on_fork": frozen.iter().map(|f| json!({"market": f.market, "screen": f.screen, "detail": f.detail,
            "label": "frozen on fork: anvil freezes Chainlink while block time advances; not fired"})).collect::<Vec<_>>(),
        "judge": judge_record, "judgment_applied": judgment,
        "decisions": decisions.iter().map(|d| d.json()).collect::<Vec<_>>(), "chosen": chosen.map(|i| decisions[i].market.name.clone())});
    for d in &decisions {
        println!("RESULT decision vault={tag} loop={loops} market={} action={:?} protective={} text_path={} amount={} fraction={} basis={}", d.market.name, d.action, d.protective, d.text.is_some(), d.amount, d.fraction, d.basis);
    }
    if let Some(i) = chosen {
        let d = &decisions[i];
        // execution check before any sale (31 Oct): a pool more than 1% below the oracle would make it revert Slippage
        let p = if let Some(why) = logic::sale_blocked_by_pool(d) {
            println!("RESULT sale skipped vault={tag} loop={loops} market={} action={:?}: {why}", d.market.name, d.action);
            json!({"proposed": false, "why_not_proposed": why, "execution_check": "pool_vs_oracle", "pool_gap": d.market.pool_gap,
                "pool_usd": d.market.pool_usd, "oracle_usd": d.market.oracle_usd})
        } else {
            let mut status = st.status;
            let p = propose(&ctx, &v, &s, d, &mut st.vault, &mut status).await;
            st.status = status;
            p
        };
        let mut p = p;
        p["reasons"] = d.reasons();
        println!("RESULT proposal vault={tag} loop={loops} {}", json!({"market": d.market.name, "method": p["method"], "args": p["args"], "text_path": d.text.is_some(), "tx": p["tx"],
            "estimate": p["estimate"], "l1_fee_wei": p["l1_fee_wei"], "vault_check_transaction": p["vault_check_transaction"], "refusals": p["refusals"],
            "sent": p["sent"], "why_not_sent": p["why_not_sent"], "why_not_proposed": p["why_not_proposed"], "near_tx": p["near_tx"],
            "base": p["base"], "error": p["error"], "near_error": p["near_error"]}));
        tr["proposal"] = p;
    }
    st.next_recheck = logic::next_recheck(now(), logic::exec_cooldown(&s), logic::cooldown_free_at(&st.vault, &s));
    tr["next_recheck_utc"] = json!(iso(st.next_recheck));
    tr["seconds"] = json!(tl.elapsed().as_secs_f64());
    let name = format!("transcripts/{}-{tag}{}-{loops:04}.json", ctx.boot_id, if ctx.a.mode.twin() { "-twin" } else { "" });
    // private (build item 38): written to the evidence volume only; the public log gets its hash and size.
    // A transcript that cannot be written is reported, never fatal (build item 49).
    match serde_json::to_string_pretty(&tr).map_err(anyhow::Error::from).and_then(|t| tee::save_private(&ctx.a.evidence, &name, t.as_bytes())) {
        Ok((h, len)) => println!("RESULT transcript vault={tag} loop={loops} {name} sha256={h} bytes={len} seconds={:.1}", tl.elapsed().as_secs_f64()),
        Err(e) => println!("RESULT transcript vault={tag} loop={loops} {name} write failed: {e}"),
    }
    st.fired_prev = fired_set;
}

// ------------------------------------------------------------------ registration (§4.4; item 32; re-registration 31 Oct)

/// What a registration needs: the quote and its imr-3 events (kept from boot; a reboot keeps them byte-identical).
struct Attest {
    quote: Vec<u8>,
    imr3: Vec<Value>,
}

/// One `register_agent` call with fresh collateral: refused here, before any gas is spent, when the collateral does
/// not verify locally as `UpToDate` or the balance cannot pass NEAR's submission check. Returns the record.
async fn register_once(nearc: &near::Near, sk: &SigningKey, implicit: &str, vault: &str, at: &Attest, attempt: u32) -> Result<Value> {
    let (coll, cinfo) = tee::collateral(&at.quote).await.map_err(|e| anyhow!("collateral from Phala's PCCS: {e}"))?;
    println!("RESULT collateral={cinfo}");
    if !judge::tcb_up_to_date(&cinfo["local_verify"]) {
        return Err(anyhow!("collateral does not verify locally as UpToDate ({}); not sent", cinfo["local_verify"]));
    }
    let f = logic::registration_funding(logic::REGISTER_GAS);
    let bal_before = nearc.balance(implicit).await?.unwrap_or(0);
    if bal_before < f.at_submission_yocto {
        return Err(anyhow!("balance {} NEAR is below the {} NEAR NEAR checks at submission; {FUNDER}", logic::near_str(bal_before), logic::near_str(f.at_submission_yocto)));
    }
    let reg_args = json!({"quote_hex": hex::encode(&at.quote), "collateral": coll, "event_log": at.imr3});
    let bytes = reg_args.to_string().into_bytes();
    let t = Instant::now();
    let res = nearc.function_call(sk, implicit, vault, "register_agent", &bytes, logic::REGISTER_GAS, 0).await;
    let secs = t.elapsed().as_secs_f64();
    let bal_after = nearc.balance(implicit).await.ok().flatten().unwrap_or(0);
    let (h, r) = res?;
    let failed = r["status"].get("Failure").cloned();
    let rec = json!({"near_tx": h, "args_bytes": bytes.len(), "seconds": secs, "gas": near::gas_summary(&r), "attempt": attempt,
        "prepaid_tgas": logic::REGISTER_GAS / 1_000_000_000_000,
        "balance_before_yocto": bal_before.to_string(), "balance_after_yocto": bal_after.to_string(), "collateral": cinfo});
    if let Some(f) = failed {
        return Err(anyhow!("register_agent {h} failed: {f}"));
    }
    Ok(rec)
}

/// Saves a registration record of one vault as public evidence (`registration-<tag>.json`,
/// `reregistration-<tag>-<n>.json`); a failed write is logged, never fatal.
fn save_registration(ctx: &Ctx, name: &str, rec: &Value) {
    if let Err(e) = serde_json::to_string_pretty(rec).map_err(anyhow::Error::from).and_then(|t| tee::save_evidence(&ctx.a.evidence, &format!("{}/{name}", ctx.boot_id), t.as_bytes())) {
        println!("RESULT registration record {name} write failed: {e}");
    }
}

// ------------------------------------------------------------------ the watched vaults (3 Nov; vaults.rs)

/// One watched vault in the refresh's map, keyed by slot.
struct Entry {
    tag: String,
    id: String,
    /// the vault's state as the last refresh read it
    state: Value,
    phase: vaults::Phase,
    registration: Value,
    progress: Arc<vaults::Progress>,
    stop: Arc<AtomicBool>,
    handle: Option<JoinHandle<()>>,
    next_rereg_check: u64,
    rereg_n: u32,
}

struct Fleet {
    scan: vaults::Scan,
    watched: BTreeSet<u64>,
    entries: BTreeMap<u64, Entry>,
    board: vaults::Board,
    /// what the last scan line said, so an unchanged refresh prints nothing
    last_line: String,
}

/// What the refresh needs from the boot: the quote for registrations (None without a TEE) and the measurement the
/// vaults must approve (None without a TEE: every vault is watched, read-only).
struct Boot {
    attest: Option<Arc<Attest>>,
    measurement: Option<Value>,
    implicit: String,
}

/// One refresh, every STATUS_EVERY ticks: scan, membership, registrations, re-registrations, tick loops, listing.
/// Errors of one vault are logged and retried on the next refresh; only the exit rule returns an error.
async fn refresh(ctx: &Arc<Ctx>, fl: &mut Fleet, boot: &Boot) -> Result<()> {
    let n = &ctx.nearc;
    let source = &ctx.a.source;
    // ---- 1. discovery: only the indices not known yet
    let count = match source {
        vaults::Source::Factory(f) => match n.view(f, "get_config", &json!({})).await {
            Ok(c) => c["count"].as_u64(),
            Err(e) => {
                println!("RESULT scan factory={f} get_config failed, retry next refresh: {e}");
                None
            }
        },
        vaults::Source::List(l) => Some(l.len() as u64),
    };
    let mut fresh: BTreeMap<u64, Value> = BTreeMap::new();
    if let Some(count) = count {
        for (slot, id) in fl.scan.plan_ids(source, count) {
            let first = !fl.scan.missing.contains(&slot);
            match n.view(&id, "get_state", &json!({})).await {
                Ok(st) => {
                    fl.scan.resolve(slot, &id, true);
                    fresh.insert(slot, st);
                }
                Err(e) => {
                    if first {
                        println!("RESULT scan {id} not readable yet (retried every refresh): {}", e.to_string().chars().take(200).collect::<String>());
                    }
                    fl.scan.resolve(slot, &id, false);
                }
            }
        }
    }
    // ---- 2. every known vault's state, for its membership (an approval joins, a revoke leaves)
    for (slot, id) in fl.scan.known.clone() {
        if fresh.contains_key(&slot) {
            continue;
        }
        match n.view(&id, "get_state", &json!({})).await {
            Ok(st) => {
                fresh.insert(slot, st);
            }
            Err(e) => println!("RESULT vault={} state not readable, membership kept: {}", vaults::tag(source, slot), e.to_string().chars().take(200).collect::<String>()),
        }
    }
    let (watched, joined, left) = vaults::membership(&fl.watched, &fresh, boot.measurement.as_ref());
    for slot in &left {
        if let Some(e) = fl.entries.remove(slot) {
            e.stop.store(true, Ordering::SeqCst);
            println!("RESULT vault left vault={} {}: it no longer approves this measurement (revoked or changed); its tick loop stops", e.tag, e.id);
        }
        fl.board.lock().unwrap_or_else(|e| e.into_inner()).remove(slot);
    }
    for slot in &joined {
        let id = fl.scan.known.get(slot).cloned().unwrap_or_default();
        let tag = vaults::tag(source, *slot);
        let phase = if boot.attest.is_some() { vaults::Phase::Pending { attempts: 0 } } else { vaults::Phase::NoTee };
        println!("RESULT vault joined vault={tag} {id}: {}", if boot.attest.is_some() { "approves this measurement; registering" } else { "no TEE: watched read-only, never registered" });
        fl.entries.insert(*slot, Entry { tag, id, state: Value::Null, phase, registration: Value::Null, progress: Default::default(),
            stop: Default::default(), handle: None, next_rereg_check: now() + REREGISTER_POLL_SECS, rereg_n: 0 });
    }
    fl.watched = watched;
    for (slot, e) in fl.entries.iter_mut() {
        if let Some(st) = fresh.get(slot) {
            e.state = st.clone();
        }
    }
    let line = format!("count={count:?} known={} missing={:?} watched={:?}", fl.scan.known.len(), fl.scan.missing,
        fl.entries.values().map(|e| e.tag.clone()).collect::<Vec<_>>());
    if line != fl.last_line || !joined.is_empty() || !left.is_empty() {
        let src = match source {
            vaults::Source::Factory(f) => format!("factory={f}"),
            vaults::Source::List(l) => format!("vaults={l:?}"),
        };
        println!("RESULT scan {src} {line} joined={joined:?} left={left:?}");
        fl.last_line = line;
    }

    // ---- 3. registrations, one vault at a time (one implicit account pays for all)
    if let Some(at) = &boot.attest {
        let f = logic::registration_funding(logic::REGISTER_GAS);
        let pending: Vec<u64> = fl.entries.iter().filter(|(_, e)| matches!(e.phase, vaults::Phase::Pending { .. })).map(|(s, _)| *s).collect();
        let pending_tags: Vec<String> = pending.iter().filter_map(|s| fl.entries.get(s).map(|e| e.tag.clone())).collect();
        for (k, slot) in pending.iter().enumerate() {
            let Some(e) = fl.entries.get_mut(slot) else { continue };
            let vaults::Phase::Pending { attempts } = e.phase else { continue };
            match vaults::reg_action(e.phase, &e.state, &ctx.our_pk, REGISTER_ATTEMPTS) {
                vaults::RegAction::MarkRegistered => {
                    e.phase = vaults::Phase::Registered;
                    println!("RESULT register_agent vault={} registered=true (the vault already names this key)", e.tag);
                }
                vaults::RegAction::GiveUp => {
                    e.phase = vaults::Phase::GaveUp;
                    println!("RESULT register_agent vault={} gave up after {attempts} attempts: not ticking it unregistered", e.tag);
                }
                vaults::RegAction::Nothing => {}
                vaults::RegAction::Register => {
                    let bal = n.balance(&boot.implicit).await.unwrap_or(None);
                    if bal.unwrap_or(0) < f.total_yocto {
                        let rest = (pending.len() - k) as u64;
                        let tags = &pending_tags[k..];
                        println!("WAITING implicit_account={} balance={} NEAR needs={} NEAR for the next registration; pending_vaults={rest} {tags:?} need {} NEAR in all (+{} per further vault); {FUNDER}",
                            boot.implicit, bal.map(logic::near_str).unwrap_or("none (account does not exist yet)".into()), logic::near_str(f.total_yocto),
                            logic::near_str(logic::registrations_needed(&f, rest)), logic::near_str(logic::REGISTRATION_BURN_YOCTO));
                        break; // the balance only grows by a top-up; the next refresh tries again
                    }
                    let attempt = attempts + 1;
                    e.phase = vaults::Phase::Pending { attempts: attempt };
                    let rec = register_once(n, &ctx.sk, &boot.implicit, &e.id, at, attempt).await.unwrap_or_else(|x| json!({"error": x.to_string(), "attempt": attempt}));
                    let st = n.view(&e.id, "get_state", &json!({})).await.unwrap_or_default();
                    let registered = st["agent"]["public_key"].as_str() == Some(&ctx.our_pk);
                    println!("RESULT register_agent vault={} {} registered={registered} vault_agent={} record={}", e.tag, e.id, st["agent"],
                        json!({"near_tx": rec["near_tx"], "tgas": rec["gas"]["tgas_total"], "near_burnt": rec["gas"]["near_burnt"], "seconds": rec["seconds"], "attempt": attempt, "error": rec["error"]}));
                    let mut full = rec.clone();
                    full["vault"] = json!(e.id);
                    full["registered"] = json!(registered);
                    full["vault_agent"] = st["agent"].clone();
                    save_registration(ctx, &format!("registration-{}.json", e.tag), &full);
                    if !st.is_null() {
                        e.state = st;
                    }
                    if registered {
                        e.phase = vaults::Phase::Registered;
                        e.registration = json!({"near_tx": rec["near_tx"], "registered": true});
                        e.next_rereg_check = now() + REREGISTER_POLL_SECS;
                    } else {
                        println!("RESULT register_agent vault={} attempt={attempt} failed, retry next refresh: {}", e.tag, rec["error"]);
                    }
                }
            }
        }

        // ---- 4. re-attestation per vault (31 Oct): our key, under 24 h left; never over another key or a revoke
        for e in fl.entries.values_mut().filter(|e| e.phase == vaults::Phase::Registered) {
            if now() < e.next_rereg_check {
                continue;
            }
            e.next_rereg_check = now() + REREGISTER_POLL_SECS;
            if !logic::reregistration_due(&e.state, &ctx.our_pk, now()) {
                continue;
            }
            e.rereg_n += 1;
            let k = e.rereg_n;
            let old = e.state["agent"]["expires_at"].as_u64().unwrap_or(0);
            println!("RESULT reregister due vault={} attempt={k} expires_at={old} ({}) remaining_s={}", e.tag, iso(old), old.saturating_sub(now()));
            let rec = match register_once(n, &ctx.sk, &boot.implicit, &e.id, at, k).await {
                Ok(r) => r,
                Err(x) => {
                    println!("RESULT reregister vault={} attempt={k} failed, retry in {REREGISTER_POLL_SECS} s: {x}", e.tag);
                    continue;
                }
            };
            let st = n.view(&e.id, "get_state", &json!({})).await.unwrap_or_default();
            let new = st["agent"]["expires_at"].as_u64().unwrap_or(0);
            let same_key = st["agent"]["public_key"].as_str() == Some(&ctx.our_pk);
            println!("RESULT reregistered vault={} attempt={k} near_tx={} tgas={} same_key={same_key} expires_at {old} -> {new} ({})", e.tag, rec["near_tx"], rec["gas"]["tgas_total"], iso(new));
            let mut full = rec;
            full["vault"] = json!(e.id);
            full["expires_at_before"] = json!(old);
            full["expires_at_after"] = json!(new);
            save_registration(ctx, &format!("reregistration-{}-{k}.json", e.tag), &full);
            if !st.is_null() {
                e.state = st;
            }
        }
    }

    // ---- 5. one tick loop per vault that may tick and has none yet
    for (slot, e) in fl.entries.iter_mut() {
        if e.handle.is_some() || !e.phase.ticks() {
            continue;
        }
        let v = Arc::new(VaultCtx { slot: *slot, tag: e.tag.clone(), id: e.id.clone(), registration: e.registration.clone(),
            progress: e.progress.clone(), stop: e.stop.clone() });
        let status = logic::agent_status(&e.state, &ctx.our_pk, now());
        println!("RESULT watching vault={} {} agent={} target={:?}", e.tag, e.id, status.label(), vaults::watch_target(&e.state));
        let next_digest = if ctx.a.digest_at_start { now() } else { now() + DIGEST_SECS };
        let feed_cursor = lock_feeds(&ctx.feeds).items.len();
        let st = State { vault: e.state.clone(), fired_prev: BTreeSet::new(), next_digest, loops: 0, next_recheck: 0, inflight: None, ready: None,
            judge_wanted: None, feed_trigger: vec![], judge_skipped: None, status, feed_cursor };
        e.handle = Some(tokio::spawn(vault_loop(ctx.clone(), v, st)));
    }

    // ---- 6. the public listing
    {
        let mut b = fl.board.lock().unwrap_or_else(|e| e.into_inner());
        for (slot, e) in &fl.entries {
            let exp = e.state["agent"]["expires_at"].as_u64();
            let info = json!({"slot": slot, "tag": e.tag, "vault": e.id, "base_address": e.state["base_address"],
                "registration": e.phase.label(), "registration_tx": e.registration["near_tx"],
                "agent_status": logic::agent_status(&e.state, &ctx.our_pk, now()).label(),
                "expires_at": exp, "expires_utc": exp.map(iso)});
            b.insert(*slot, vaults::Row { info, progress: e.progress.clone() });
        }
    }

    // ---- 7. the exit rule
    let phases: Vec<vaults::Phase> = fl.entries.values().map(|e| e.phase).collect();
    if vaults::should_exit(&phases) {
        return Err(anyhow!("register_agent failed on every watched vault ({REGISTER_ATTEMPTS} attempts each): not ticking unregistered"));
    }
    Ok(())
}

/// One vault's tick loop: its own `State`, ended by its stop flag (the vault left) or `--max-ticks`.
async fn vault_loop(ctx: Arc<Ctx>, v: Arc<VaultCtx>, st: State) {
    let st = Arc::new(Mutex::new(st));
    let (c, vv, s2) = (ctx.clone(), v.clone(), st.clone());
    let n = run_loop(ctx.a.max_ticks, Duration::from_secs(ctx.a.tick_secs), v.stop.clone(), move |n| tick(c.clone(), vv.clone(), s2.clone(), n)).await;
    let st = st.lock().await;
    if let Some(f) = &st.inflight {
        f.handle.abort();
    }
    println!("RESULT vault loop ended vault={} ticks={n} loops={} stopped={}", v.tag, st.loops, v.stop.load(Ordering::SeqCst));
}

// ------------------------------------------------------------------ the tick loop driver (build item 49)

/// Runs `tick(n)` for n = 1..=max_ticks, one every `every`, until `stop` is set. Each tick runs in its own task, so a
/// panic or an error inside one (a provider outage, a failed read, a failed write) is logged and the next tick runs:
/// the loop itself never stops on a tick's failure.
async fn run_loop<F, Fut>(max_ticks: u64, every: Duration, stop: Arc<AtomicBool>, mut tick: F) -> u64
where
    F: FnMut(u64) -> Fut,
    Fut: std::future::Future<Output = ()> + Send + 'static,
{
    let mut n = 0u64;
    while n < max_ticks && !stop.load(Ordering::SeqCst) {
        n += 1;
        let t0 = Instant::now();
        if let Err(e) = tokio::spawn(tick(n)).await {
            println!("TICK {n} aborted: {e}");
        }
        let el = t0.elapsed();
        if el < every {
            tokio::time::sleep(every - el).await;
        }
    }
    n
}

// ------------------------------------------------------------------ main

#[tokio::main]
async fn main() -> Result<()> {
    let a = args()?;
    let sk = SigningKey::generate(&mut rand::rngs::OsRng);
    let implicit = near::implicit_account(&sk);
    let boot_id = format!("boot-{}-{}", now(), &implicit[..8]);
    let src = match &a.source {
        vaults::Source::Factory(f) => format!("factory={f}"),
        vaults::Source::List(l) => format!("vaults={l:?}"),
    };
    println!("RESULT start mode={} operator={} {src} tee={} boot={boot_id}", a.mode.label(), a.operator, a.tee);
    println!("RESULT agent_public_key={} implicit_account={implicit}", near::pk_string(&sk));
    std::fs::create_dir_all(&a.evidence)?;
    let nearc = near::Near::new(&a.near_rpc);
    let base = Base::new(a.base_rpcs.clone());
    let key = match &a.nearai_key_file {
        Some(f) => Some(std::fs::read_to_string(f)?.trim().to_string()),
        None => std::env::var("NEARAI_API_KEY").ok().map(|k| k.trim().to_string()),
    }
    .filter(|k| !k.is_empty());
    require_judge_key(a.mode, a.tee, a.nearai_key_file.as_deref(), key.is_some())?;
    let judge = if a.mode.twin() {
        println!("RESULT judge never called: mode {} is the deterministic twin (no model, no text feeds)", a.mode.label());
        None
    } else if let Some(k) = key {
        let j = judge::Judge::new(k);
        // one key check at boot; a failed check is printed and the boot continues (each judge call records its own error)
        println!("{}", key_check_line(&j.check_key().await));
        Some(Arc::new(j))
    } else {
        println!("RESULT judge disabled: NEARAI_API_KEY not set; every judgment is skipped and logged, the rules apply alone");
        None
    };
    let fork = a.fork || base.all_providers_are_anvil().await;
    println!("RESULT network base={} fork={fork}{}", if fork { "anvil fork" } else { "Base Sepolia" },
        if fork { " (feed_stale is labelled frozen on fork, not fired)" } else { "" });
    let mut agent_record = json!({"public_key": near::pk_string(&sk), "implicit_account": implicit, "boot": boot_id, "mode": a.mode.label(), "operator": a.operator});
    let board: vaults::Board = Default::default();

    // ---- 1. quote and evidence (§4.4); the registrations follow per vault in the refresh
    let mut boot = Boot { attest: None, measurement: None, implicit: implicit.clone() };
    if a.tee {
        let b = tee::boot(&sk, &a.evidence, &boot_id).await?;
        println!("RESULT measurement={} rtmr3_replay_matches_quote={} debug={}", b.measurement, b.rtmr3_replayed_matches, b.debug);
        agent_record["measurement"] = b.measurement.clone();
        boot.attest = Some(Arc::new(Attest { quote: b.quote.clone(), imr3: b.event_log.iter().filter(|e| e["imr"] == 3).cloned().collect() }));
        boot.measurement = Some(b.measurement.clone());
        if let Some(addr) = &a.evidence_http {
            match serve::spawn(addr, a.evidence.clone(), boot_id.clone(), a.operator.clone(), board.clone()) {
                Ok(l) => println!("RESULT evidence_http listening={l} (GET /, GET /vaults, GET /evidence/<file>; measurement evidence and the watched vaults only, transcripts are not served)"),
                Err(e) => println!("RESULT evidence_http not started on {addr}: {e}"),
            }
        }
        // item 32: the amount, computed from the gas the registration attaches, printed once
        let f = logic::registration_funding(logic::REGISTER_GAS);
        println!("FUNDING implicit_account={implicit} needs={} NEAR = prepaid {} Tgas x pessimistic gas price {} yoctoNEAR/gas ({}) + storage {} + margin {} (1.5 registrations' burn); +{} per further vault (one registration's burn); {FUNDER}",
            logic::near_str(f.total_yocto), f.prepaid_gas / 1_000_000_000_000, logic::PESSIMISTIC_YOCTO_PER_GAS, logic::near_str(f.gas_yocto),
            logic::near_str(f.storage_yocto), logic::near_str(f.margin_yocto), logic::near_str(logic::REGISTRATION_BURN_YOCTO));
    } else {
        println!("RESULT no TEE: no measurement, so every vault of the source is watched read-only and none is registered");
        if let Some(addr) = &a.evidence_http {
            match serve::spawn(addr, a.evidence.clone(), boot_id.clone(), a.operator.clone(), board.clone()) {
                Ok(l) => println!("RESULT evidence_http listening={l} (no TEE: no measurement files; GET /vaults lists the watched vaults; transcripts are not served)"),
                Err(e) => println!("RESULT evidence_http not started on {addr}: {e}"),
            }
        }
    }

    // ---- 2. the feed poll (shared by every vault) and the refresh, which starts one tick loop per vault (§4.9)
    let feeds = Arc::new(std::sync::Mutex::new(FeedShared::default()));
    if a.mode.twin() {
        println!("RESULT feed poll off: mode {} is the deterministic twin (no text feeds)", a.mode.label());
    } else {
        tokio::spawn(feed_task(feeds.clone(), a.evidence.clone(), Duration::from_secs(a.feed_secs)));
    }
    let (tick_secs, max_ticks) = (a.tick_secs, a.max_ticks);
    let our_pk = near::pk_string(&sk);
    let ctx = Arc::new(Ctx { a, base, nearc, sk, our_pk, judge, agent_record, fork, boot_id, feeds });
    let mut fl = Fleet { scan: Default::default(), watched: BTreeSet::new(), entries: BTreeMap::new(), board, last_line: String::new() };
    let every = Duration::from_secs(STATUS_EVERY * tick_secs.max(1));
    // a bounded run (`--max-ticks`) ends once its time is up and every vault loop has finished
    let bound = (max_ticks != u64::MAX).then(|| Duration::from_secs(max_ticks.saturating_mul(tick_secs)));
    let started = Instant::now();
    println!("RESULT refresh every {} s (scan, membership, registrations); re-attestation checked every {REREGISTER_POLL_SECS} s per vault, under {} s left",
        every.as_secs(), logic::REREGISTER_BEFORE_SECS);
    let result = 'run: loop {
        let t = Instant::now();
        if let Err(e) = refresh(&ctx, &mut fl, &boot).await {
            break 'run Err(e);
        }
        loop {
            let done = bound.map(|b| started.elapsed() >= b && fl.entries.values().all(|e| e.handle.as_ref().map(|h| h.is_finished()).unwrap_or(true))).unwrap_or(false);
            if done {
                break 'run Ok(());
            }
            if t.elapsed() >= every {
                break;
            }
            tokio::time::sleep(Duration::from_millis(500)).await;
        }
    };
    for e in fl.entries.values_mut() {
        e.stop.store(true, Ordering::SeqCst);
        if let Some(h) = e.handle.take() {
            let _ = h.await;
        }
    }
    println!("RESULT done vaults={:?} seconds={:.0}", fl.entries.values().map(|e| (e.tag.clone(), e.progress.ticks.load(Ordering::SeqCst))).collect::<Vec<_>>(), started.elapsed().as_secs_f64());
    result
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::{AtomicU64, Ordering};

    #[tokio::test]
    async fn a_failing_or_panicking_tick_does_not_stop_the_loop() {
        let ran = Arc::new(AtomicU64::new(0));
        let r = ran.clone();
        let n = run_loop(6, Duration::from_millis(0), Arc::new(AtomicBool::new(false)), move |n| {
            let r = r.clone();
            async move {
                r.fetch_add(1, Ordering::SeqCst);
                if n == 2 {
                    panic!("provider outage surfaced as a panic in tick {n}");
                }
                if n == 3 {
                    // a failed read ends the tick early, as `tick` does on a snapshot error
                    let read: Result<()> = Err(anyhow!("both providers down"));
                    assert!(read.is_err());
                }
            }
        })
        .await;
        assert_eq!(n, 6);
        assert_eq!(ran.load(Ordering::SeqCst), 6, "every tick ran after the panic and the error");
    }

    #[tokio::test]
    async fn a_vault_that_leaves_stops_its_own_loop_only() {
        // two vaults, two loops, two states: the stop flag of one ends that loop before its next tick
        let (stop1, stop2) = (Arc::new(AtomicBool::new(false)), Arc::new(AtomicBool::new(false)));
        let (ran1, ran2) = (Arc::new(AtomicU64::new(0)), Arc::new(AtomicU64::new(0)));
        let (r1, s1) = (ran1.clone(), stop1.clone());
        let l1 = tokio::spawn(run_loop(10, Duration::from_millis(1), stop1.clone(), move |n| {
            let (r, s) = (r1.clone(), s1.clone());
            async move {
                r.fetch_add(1, Ordering::SeqCst);
                if n == 3 {
                    s.store(true, Ordering::SeqCst); // the refresh saw the revoke
                }
            }
        }));
        let r2 = ran2.clone();
        let l2 = tokio::spawn(run_loop(10, Duration::from_millis(1), stop2, move |_| {
            let r = r2.clone();
            async move {
                r.fetch_add(1, Ordering::SeqCst);
            }
        }));
        assert_eq!((l1.await.unwrap(), l2.await.unwrap()), (3, 10));
        assert_eq!((ran1.load(Ordering::SeqCst), ran2.load(Ordering::SeqCst)), (3, 10));
    }

    #[test]
    fn the_live_modes_need_the_factory_and_the_sources_never_mix() {
        use vaults::Source;
        let f = || Some("cmdk-factory.testnet".to_string());
        let l = |v: &[&str]| v.iter().map(|s| s.to_string()).collect::<Vec<_>>();
        assert_eq!(vault_source(Mode::Live, f(), vec![]).unwrap(), Source::Factory("cmdk-factory.testnet".into()));
        assert_eq!(vault_source(Mode::Dry, None, l(&["a.testnet", "b.testnet"])).unwrap(), Source::List(l(&["a.testnet", "b.testnet"])));
        assert!(vault_source(Mode::Twin, None, l(&["a.testnet"])).is_ok());
        for m in [Mode::Live, Mode::TwinLive] {
            let e = vault_source(m, None, l(&["a.testnet"])).unwrap_err().to_string();
            assert!(e.contains("needs --factory"), "{e}");
        }
        assert!(vault_source(Mode::Dry, f(), l(&["a.testnet"])).unwrap_err().to_string().contains("mutually exclusive"));
        assert!(vault_source(Mode::Dry, None, vec![]).unwrap_err().to_string().contains("--factory <account> is required"));
        assert!(vault_source(Mode::Dry, None, l(&["a.testnet", "a.testnet"])).is_err(), "the same vault twice would run two loops on it");
        for bad in ["", "A.testnet", "a b", "x", "a/b"] {
            assert!(vault_source(Mode::Dry, Some(bad.into()), vec![]).is_err(), "{bad}");
        }
    }

    #[test]
    fn modes_and_operator_names() {
        assert_eq!(Mode::parse("twin").unwrap(), Mode::Twin);
        assert!(Mode::Twin.twin() && !Mode::Twin.sends());
        assert!(Mode::TwinLive.twin() && Mode::TwinLive.sends());
        assert!(!Mode::Dry.sends() && !Mode::Dry.twin());
        assert!(Mode::Live.sends() && !Mode::Live.twin());
        assert!(Mode::parse("judge").is_err());
        assert!(valid_operator("cmdk") && valid_operator("alice-2"));
        // no NEAR AI key: only `--mode live` in the TEE refuses to start, and the error names the source
        let e = require_judge_key(Mode::Live, true, None, false).unwrap_err().to_string();
        assert!(e.contains("NEARAI_API_KEY") && e.contains("refusing to start"), "{e}");
        let e = require_judge_key(Mode::Live, true, Some("/run/k"), false).unwrap_err().to_string();
        assert!(e.contains("--nearai-key-file") && e.contains("/run/k"), "{e}");
        assert!(require_judge_key(Mode::Live, true, None, true).is_ok());
        assert!(require_judge_key(Mode::Live, false, None, false).is_ok(), "a local --no-tee run may run without the key");
        for m in [Mode::Dry, Mode::Twin, Mode::TwinLive] {
            assert!(require_judge_key(m, true, None, false).is_ok(), "{m:?}");
        }
        for bad in ["", "-x", "Cmdk", "a b", "a_b", &"x".repeat(33)] {
            assert!(!valid_operator(bad), "{bad}");
        }
    }

    #[test]
    fn the_key_check_line() {
        let ok = judge::key_check_record(Some(200), r#"{"valid":true,"organization_id":"o","workspace_id":"w","api_key_id":"key-1"}"#, 0.42);
        assert_eq!(key_check_line(&ok), "RESULT nearai_key http=200 valid=true api_key_id=key-1 seconds=0.4");
        let spent = judge::key_check_record(Some(402), r#"{"error":{"message":"API key spend limit exceeded. Spent: $5.09, Limit: $5.00","type":"api_key_limit_exceeded","code":null,"param":null}}"#, 0.3);
        assert_eq!(key_check_line(&spent), r#"RESULT nearai_key http=402 valid=unknown api_key_id=none seconds=0.3 error_type=api_key_limit_exceeded error="API key spend limit exceeded. Spent: $5.09, Limit: $5.00""#);
        let down = judge::key_check_record(None, "connect timed out", 15.0);
        assert_eq!(key_check_line(&down), r#"RESULT nearai_key http=none valid=unknown api_key_id=none seconds=15.0 error_type=transport error="connect timed out""#);
    }
}
