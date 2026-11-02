//! PRIME prototype P3 (throwaway, 27–31 Oct 2026): the agent. Spec: docs/01-design-and-feasibility.md §4.4 (key,
//! quote, registration) and §4.9 (collect, compute, screen, judge, decide, propose); brief: prototype/BRIEF.md P3.
//! Deviations (brief): one vault, the positions of its Base address; on-chain reads plus one allowlisted text feed;
//! no scores and no similar-case retrieval.
//!
//! Behaviour is fixed in the binary; the compose file (measured) passes only `--mode`. `--mode dry` builds every
//! proposal exactly as the vault would and runs the vault's `check_transaction` view on it, but sends nothing to
//! the vault's agent methods and nothing to Base. `--mode live` calls the vault through the agent key and
//! broadcasts the transaction the vault signs. `--no-tee` (local runs only) skips the quote and registration.
mod chain;
mod judge;
mod near;
mod tee;

use anyhow::{anyhow, Result};
use chain::{Base, MarketSnap, Snapshot, DEADLINE_SECS, WAD};
use ed25519_dalek::SigningKey;
use primitive_types::U256;
use serde_json::{json, Value};
use std::collections::BTreeSet;
use std::path::PathBuf;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

const VAULT: &str = "p2-vault.prime-measure-1792736012.testnet";
const NEAR_RPC: &str = "https://rpc.testnet.near.org";
const BASE_RPCS: [&str; 2] = ["https://base-sepolia-rpc.publicnode.com", "https://sepolia.base.org"];
const UTILIZATION_SCREEN: f64 = 0.95;
const POOL_GAP_SCREEN: f64 = 0.02;
const REDUCE_LIGHT_SHARE: u128 = 4; // reduce_light = 1/4 of the remaining sell budget (§4.9 step 6)
const DIGEST_SECS: u64 = 6 * 3600;
const FEED_SECS: u64 = 600;
// NEAR checks prepaid gas x the pessimistic gas price against the balance at submission: 300 Tgas needed 0.3012 NEAR
// (run 2, 27 Oct: NotEnoughBalance with 0.1). 130 Tgas covers the measured registration (vault receipt 113.7 Tgas).
const MIN_REGISTRATION_BALANCE: u128 = 150_000_000_000_000_000_000_000; // 0.15 NEAR: about 0.1305 for 130 Tgas + margin
const REGISTER_GAS: u64 = 130_000_000_000_000;
const REGISTER_ATTEMPTS: u32 = 4; // then exit non-zero: never tick unregistered
const AGENT_CALL_GAS: u64 = 150_000_000_000_000;

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
    SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_secs()
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

fn wad(x: u128) -> f64 {
    x as f64 / 1e18
}

// ------------------------------------------------------------------ screens

fn screens(s: &Snapshot) -> Vec<(String, String, String)> {
    // (market name, screen, detail)
    let mut out = vec![];
    for m in s.markets.iter().filter(|m| m.exists) {
        if let Some(x) = &m.exec {
            if x.corridor.set && m.debt > 0 && m.ltv_wad > x.corridor.act as u128 {
                out.push((m.name.clone(), "ltv_above_act".into(), format!("LTV {:.4} > act {:.4}", wad(m.ltv_wad), wad(x.corridor.act))));
            }
        }
        if m.utilization > UTILIZATION_SCREEN {
            out.push((m.name.clone(), "utilization_above_95pct".into(), format!("utilization {:.4}", m.utilization)));
        }
        if let Some(g) = m.pool_gap {
            if g.abs() > POOL_GAP_SCREEN {
                out.push((m.name.clone(), "oracle_pool_gap_above_2pct".into(), format!("pool {:.2} vs oracle {:.2} USD ({:+.2}%)", m.pool_usd.unwrap_or(0.0), m.oracle_usd, g * 100.0)));
            }
        }
        for (f, age) in m.feeds.iter().zip(&m.feed_ages) {
            if *age > f.2 {
                out.push((m.name.clone(), "feed_stale".into(), format!("feed {} age {} s > {} s", f.0, age, f.2)));
            }
        }
    }
    out
}

// ------------------------------------------------------------------ decide (code; the model never computes an amount)

/// Repay amount (loan units) that brings LTV to `line` when collateral worth the same amount is sold at the oracle.
fn amount_to(m: &MarketSnap, line: u128) -> u128 {
    let (d, v, w) = (U256::from(m.debt), U256::from(m.collateral_value), U256::from(WAD));
    let lv = U256::from(line) * v;
    if d * w <= lv {
        return 0;
    }
    ((d * w - lv) / (w - U256::from(line))).low_u128()
}

struct Decision {
    action: String, // none | reduce | migrate | exit
    market: Option<MarketSnap>,
    to: Option<MarketSnap>,
    amount: u128,
    fraction: u128,
    basis: String,
    detail: Value,
}

fn decide(s: &Snapshot, fired: &[(String, String, String)], judged: Option<&Value>, vault: &Value) -> Decision {
    let none = |why: &str, detail: Value| Decision { action: "none".into(), market: None, to: None, amount: 0, fraction: 0, basis: why.into(), detail };
    let Some(pos) = s.markets.iter().filter(|m| m.exists && m.debt > 0).max_by_key(|m| m.ltv_wad).cloned() else {
        return none("no position with debt at the vault's Base address", json!({}));
    };
    let Some(x) = pos.exec.clone() else { return none("executor not deployed: no corridor in force", json!({"market": pos.name})) };
    let c = &x.corridor;
    if !c.set || x.closed_for_agent {
        return none("no corridor in force for this market (unset, or closed by exit)", json!({"market": pos.name}));
    }
    let slice_max: u128 = vault["markets"]
        .as_array()
        .and_then(|a| a.iter().find(|m| m["id"].as_str() == Some(&pos.id)))
        .and_then(|m| m["slice_max"].as_str())
        .and_then(|v| v.parse().ok())
        .unwrap_or(0);
    let to_target = amount_to(&pos, c.target as u128);
    let floor_room = amount_to(&pos, c.floor as u128);
    // remaining discretionary budget: sell_budget x (collateral of this token across markets + sold) - sold, valued at the oracle
    let coll_total: u128 = s.markets.iter().filter(|m| m.collateral_token == pos.collateral_token).map(|m| m.collateral).sum();
    let budget_coll = (U256::from(c.sell_budget) * U256::from(coll_total + x.sold_in_window) / U256::from(WAD)).low_u128().saturating_sub(x.sold_in_window);
    let price = U256::from_dec_str(&pos.oracle_price).unwrap_or_default();
    let budget_value = (U256::from(budget_coll) * price / U256::exp10(36)).low_u128();
    let confirm: Vec<_> = fired.iter().filter(|f| f.0 == pos.name).collect();
    let protective = pos.ltv_wad > c.act as u128;
    let action = judged.and_then(|j| j["action"].as_str()).unwrap_or("none");
    let detail = json!({"market": pos.name, "ltv": wad(pos.ltv_wad), "target": wad(c.target as u128), "act": wad(c.act as u128),
        "floor": wad(c.floor as u128), "debt": pos.debt.to_string(), "collateral_value": pos.collateral_value.to_string(),
        "amount_to_target": to_target.to_string(), "amount_to_floor": floor_room.to_string(),
        "sell_budget_remaining_collateral": budget_coll.to_string(), "sell_budget_remaining_value": budget_value.to_string(),
        "slice_max": slice_max.to_string(), "judge_action": action, "onchain_confirmation": confirm.len()});
    let reduce = |amount: u128, basis: &str| Decision {
        action: if amount > 0 { "reduce".into() } else { "none".into() },
        market: Some(pos.clone()),
        to: None,
        amount: amount.min(slice_max).min(pos.debt), // bounded: never more than the slice maximum or the debt
        fraction: 0,
        basis: basis.into(),
        detail: detail.clone(),
    };
    if protective {
        // rule, independent of the judge: a protective session sells to target (§3); the executor clamps again
        return reduce(to_target, "rule: LTV above the act line, protective reduce to target (deterministic)");
    }
    if confirm.is_empty() {
        return none("no on-chain screen on the position's market: no discretionary action", detail);
    }
    match action {
        "reduce_to_target" => reduce(to_target.min(budget_value).min(floor_room), "judge reduce_to_target + on-chain screen; clamped to budget and floor"),
        "reduce_light" => reduce((budget_value / REDUCE_LIGHT_SHARE).min(floor_room), "judge reduce_light + on-chain screen: 1/4 of the remaining budget, clamped to floor"),
        "leave_market" => {
            // the code picks the destination: same tokens and executor, corridor in force, no screen, fresh feeds, USDC to lend
            let dest = s
                .markets
                .iter()
                .filter(|m| m.id != pos.id && m.exists && m.loan_token == pos.loan_token && m.collateral_token == pos.collateral_token)
                .filter(|m| m.exec.as_ref().map(|e| e.corridor.set && !e.closed_for_agent).unwrap_or(false))
                .filter(|m| !fired.iter().any(|f| f.0 == m.name))
                .filter(|m| m.feeds.iter().zip(&m.feed_ages).all(|(f, a)| *a <= f.2))
                .filter(|m| m.liquidity > 0)
                .max_by_key(|m| m.liquidity)
                .cloned();
            match dest {
                Some(d) if c.migrate_allowed && x.moves_in_window < 2 => {
                    let fraction = if d.liquidity >= pos.debt + pos.debt / 100 {
                        WAD
                    } else {
                        (U256::from(d.liquidity) * U256::from(WAD) / U256::from(pos.debt) * U256::from(99u8) / U256::from(100u8)).low_u128()
                    };
                    Decision { action: "migrate".into(), market: Some(pos.clone()), to: Some(d), amount: 0, fraction, basis: "judge leave_market + on-chain screen: migrate to the approved market that can take it".into(), detail }
                }
                _ if c.exit_allowed => Decision { action: "exit".into(), market: Some(pos.clone()), to: None, amount: 0, fraction: 0, basis: "judge leave_market + on-chain screen, no approved market can take the position: exit".into(), detail },
                _ => none("leave_market, but no destination qualifies and exit is off", detail),
            }
        }
        _ => none("judge hold (or no valid judgment): nothing to do", detail),
    }
}

// ------------------------------------------------------------------ propose

async fn propose(a: &Args, base: &Base, nearc: &near::Near, sk: &SigningKey, s: &Snapshot, d: &Decision, vault: &Value) -> Value {
    let m = d.market.as_ref().unwrap();
    let deadline = now() + DEADLINE_SECS - 10;
    let (method, args, data) = match d.action.as_str() {
        "reduce" => ("reduce", json!({"market": m.id, "amount": d.amount.to_string()}), chain::calldata_reduce(&m.id, d.amount, deadline)),
        "migrate" => {
            let t = d.to.as_ref().unwrap();
            ("migrate", json!({"from": m.id, "to": t.id, "fraction": d.fraction.to_string()}), chain::calldata_migrate(&m.id, &t.id, d.fraction, deadline))
        }
        _ => ("exit", json!({"market": m.id}), chain::calldata_exit(&m.id, deadline)),
    };
    // the vault's own pre-checks, mirrored so a refusal is explained before anything is sent
    let vm = vault["markets"].as_array().and_then(|x| x.iter().find(|v| v["id"].as_str() == Some(&m.id))).cloned().unwrap_or_default();
    let ours = near::pk_string(sk);
    let agent_ok = vault["agent"]["public_key"].as_str() == Some(&ours) && vault["agent"]["expires_at"].as_u64().unwrap_or(0) > now();
    let cooldown_ok = now() >= vault["last_agent_action"].as_u64().unwrap_or(0) + vault["cooldown_secs"].as_u64().unwrap_or(0);
    let prechecks = json!({"agent_registered_with_this_key": agent_ok, "vault_cooldown_elapsed": cooldown_ok,
        "vault_corridor_set": vm["corridor_set"], "executor": vm["executor"], "slice_max": vm["slice_max"]});
    let (params, estimate) = match base.tx_params(&s.user, &s.executor, &data).await {
        Ok(x) => x,
        Err(e) => return json!({"method": method, "args": args, "error": format!("tx params: {e}")}),
    };
    let unsigned = chain::unsigned_1559(&params, &s.executor, &data).unwrap_or_default();
    let check = nearc
        .view(&a.vault, "check_transaction", &json!({"tx_hex": hex::encode(&unsigned), "caller": "agent"}))
        .await
        .map(|v| v.to_string())
        .unwrap_or_else(|e| e.to_string());
    let mut out = json!({"method": method, "args": args, "tx": params, "estimate": estimate.as_ref().map_err(|e| e.clone()),
        "calldata": format!("0x{data}"), "deadline_in_check": deadline, "unsigned_1559": format!("0x{}", hex::encode(&unsigned)),
        "vault_check_transaction": check, "vault_prechecks": prechecks, "mode": a.mode});
    if a.mode != "live" {
        out["sent"] = json!(false);
        return out;
    }
    if estimate.is_err() || !s.executor_deployed {
        out["sent"] = json!(false);
        out["why_not_sent"] = json!("the gas estimate failed or the executor is not deployed");
        return out;
    }
    let mut call_args = args.clone();
    call_args["tx"] = params.clone();
    let t = Instant::now();
    match nearc.function_call(sk, &a.vault, &a.vault, method, call_args.to_string().as_bytes(), AGENT_CALL_GAS, 0).await {
        Ok((h, r)) => {
            out["near_tx"] = json!(h);
            out["near_gas"] = near::gas_summary(&r);
            out["near_seconds"] = json!(t.elapsed().as_secs_f64());
            let signed = near::success_value(&r);
            out["signed"] = signed.clone().unwrap_or(Value::Null);
            if let Some(tx) = signed.as_ref().and_then(|v| v.as_array()).and_then(|v| v.first()) {
                let t2 = Instant::now();
                out["base"] = match base.broadcast(tx["raw"].as_str().unwrap_or(""), tx["hash"].as_str().unwrap_or("")).await {
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

fn decision_json(d: &Decision) -> Value {
    json!({"action": d.action, "market": d.market.as_ref().map(|m| &m.name), "to": d.to.as_ref().map(|m| &m.name),
        "amount": d.amount.to_string(), "fraction": d.fraction.to_string(), "basis": d.basis, "detail": d.detail})
}

async fn run_proposal(a: &Args, base: &Base, nearc: &near::Near, sk: &SigningKey, s: &Snapshot, d: &Decision, vault: &mut Value, loops: u64) -> Value {
    let p = propose(a, base, nearc, sk, s, d, vault).await;
    println!("RESULT proposal loop={loops} {}", json!({"method": p["method"], "args": p["args"], "tx": p["tx"], "estimate": p["estimate"],
        "vault_check_transaction": p["vault_check_transaction"], "prechecks": p["vault_prechecks"], "sent": p["sent"], "near_tx": p["near_tx"],
        "base": p["base"], "error": p["error"], "near_error": p["near_error"]}));
    if let Ok(v) = nearc.view(&a.vault, "get_state", &json!({})).await {
        *vault = v;
    }
    p
}

// ------------------------------------------------------------------ judge input

fn judge_input(s: &Snapshot, fired: &[(String, String, String)], feed: &Value) -> (String, Vec<String>) {
    let mut ids = vec![];
    let mut markets = vec![];
    for m in s.markets.iter().filter(|m| m.exists) {
        let id = format!("chain:{}:market", m.name);
        ids.push(id.clone());
        let mut o = json!({"source_id": id, "market": m.name, "utilization": (m.utilization * 1e4).round() / 1e4,
            "borrow_apr": (m.borrow_apr * 1e4).round() / 1e4, "oracle_usd_per_weth": (m.oracle_usd * 100.0).round() / 100.0,
            "pool_usd_per_weth": m.pool_usd.map(|p| (p * 100.0).round() / 100.0),
            "pool_vs_oracle_pct": m.pool_gap.map(|g| (g * 1e4).round() / 100.0),
            "feeds": m.feeds.iter().zip(&m.feed_ages).map(|(f, age)| json!({"feed": if f.0 == chain::ETH_USD { "ETH/USD" } else if f.0 == chain::USDC_USD { "USDC/USD" } else { f.0.as_str() }, "age_s": age, "max_age_s": f.2})).collect::<Vec<_>>(),
            "loan_token_available_to_borrow": m.liquidity as f64 / 1e6});
        if m.debt > 0 || m.collateral > 0 {
            let pid = format!("chain:{}:position", m.name);
            ids.push(pid.clone());
            let c = m.exec.as_ref().map(|x| json!({"target": wad(x.corridor.target as u128), "act": wad(x.corridor.act as u128), "floor": wad(x.corridor.floor as u128)}));
            o["position"] = json!({"source_id": pid, "collateral_weth": m.collateral as f64 / 1e18, "debt": m.debt as f64 / 1e6,
                "ltv": (wad(m.ltv_wad) * 1e4).round() / 1e4, "liquidation_ltv": 0.86,
                "price_drop_to_liquidation_pct": ((1.0 - wad(m.ltv_wad) / 0.86) * 1e4).round() / 100.0, "corridor": c});
        }
        markets.push(o);
    }
    let sc: Vec<Value> = fired
        .iter()
        .map(|f| {
            let id = format!("screen:{}:{}", f.0, f.1);
            ids.push(id.clone());
            json!({"source_id": id, "detail": f.2})
        })
        .collect();
    let items: Vec<Value> = feed["items"]
        .as_array()
        .cloned()
        .unwrap_or_default()
        .into_iter()
        .map(|i| {
            ids.push(i["id"].as_str().unwrap_or("").to_string());
            json!({"source_id": i["id"], "published": i["published"], "text": i["text"]})
        })
        .collect();
    let user = json!({"now_utc": iso(s.timestamp), "base_block": s.block, "markets": markets, "screens_fired": sc,
        "text_feed": {"source": judge::FEED_URL, "untrusted": true, "items": items}});
    (serde_json::to_string_pretty(&user).unwrap(), ids)
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
    let judge = judge::Judge::new(key);
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
    let mut vault = nearc.view(&a.vault, "get_state", &json!({})).await?;
    let base_address = vault["base_address"].as_str().unwrap_or("").to_string();
    let market_ids: Vec<String> = vault["markets"].as_array().unwrap().iter().map(|m| m["id"].as_str().unwrap().to_string()).collect();
    let executor = vault["markets"][0]["executor"].as_str().unwrap_or("").to_string();
    println!("RESULT watching base_address={base_address} executor={executor} markets={market_ids:?} rpcs={:?}", a.base_rpcs);
    let mut fired_prev: BTreeSet<String> = BTreeSet::new();
    let mut next_digest = if a.digest_at_start { now() } else { now() + DIGEST_SECS };
    let mut feed: (Value, u64) = (Value::Null, 0);
    let mut last_proposal = 0u64;
    let mut loops = 0u64;
    let mut tick = 0u64;
    while tick < a.max_ticks {
        tick += 1;
        let t0 = Instant::now();
        if tick % 30 == 0 {
            if let Ok(v) = nearc.view(&a.vault, "get_state", &json!({})).await {
                vault = v;
            }
        }
        let s = match base.snapshot(&base_address, &executor, &market_ids).await {
            Ok(s) => s,
            Err(e) => {
                println!("TICK {tick} read error: {e}");
                tokio::time::sleep(Duration::from_secs(a.tick_secs)).await;
                continue;
            }
        };
        let read_ms = t0.elapsed().as_millis();
        if !s.read_errors.is_empty() || !s.rpc_disagree.is_empty() || s.rpc_second_error.is_some() {
            // a failed tick: no screens, no judge, no decision, and the screen state is left as it was
            println!("TICK {tick} block={} failed: read_errors={:?} rpc_disagree={:?} second_err={:?}", s.block, s.read_errors, s.rpc_disagree, s.rpc_second_error);
            tokio::time::sleep(Duration::from_secs(a.tick_secs)).await;
            continue;
        }
        let fired = screens(&s);
        let fired_set: BTreeSet<String> = fired.iter().map(|f| format!("{}:{}", f.0, f.1)).collect();
        let new: Vec<String> = fired_set.difference(&fired_prev).cloned().collect();
        let protective_again = fired.iter().any(|f| f.1 == "ltv_above_act") && now() >= last_proposal + vault["cooldown_secs"].as_u64().unwrap_or(120) + 5;
        let digest = now() >= next_digest;
        if tick % 30 == 1 || fired_set != fired_prev {
            let pos: Vec<String> = s.markets.iter().filter(|m| m.exists).map(|m| format!("{}: ltv={:.4} debt={} util={:.3} oracle={:.2} gap={:?}", m.name, wad(m.ltv_wad), m.debt, m.utilization, m.oracle_usd, m.pool_gap.map(|g| (g * 1e4).round() / 1e2))).collect();
            println!("TICK {tick} block={} read_ms={read_ms} rpc_disagree={:?} second_err={:?} executor_deployed={} screens={:?} | {}", s.block, s.rpc_disagree, s.rpc_second_error, s.executor_deployed, fired_set, pos.join(" | "));
        }
        let agree = s.rpc_disagree.is_empty() && s.rpc_second_error.is_none();
        if (!new.is_empty() || digest || protective_again) && agree {
            loops += 1;
            let tl = Instant::now();
            let mut tr = json!({"agent": agent_record, "loop": loops, "tick": tick, "time_utc": iso(now()),
                "trigger": {"new_screens": new, "digest": digest, "protective_repeat": protective_again && new.is_empty() && !digest},
                "inputs": {"snapshot": serde_json::to_value(&s).unwrap_or_else(|e| json!({"serialize_error": e.to_string()}))}, "screens": fired.iter().map(|f| json!({"market": f.0, "screen": f.1, "detail": f.2})).collect::<Vec<_>>()});
            // rule first: a protective slice (LTV above act) is decided and proposed before, and independent of,
            // the judge, whose call takes minutes with thinking on; the judge still runs in the same loop
            let rule = decide(&s, &fired, None, &vault);
            let rule_acts = rule.action == "reduce" && rule.basis.starts_with("rule:");
            if rule_acts {
                tr["decision"] = decision_json(&rule);
                println!("RESULT decision loop={loops} action={} amount={} basis={}", rule.action, rule.amount, rule.basis);
                tr["proposal"] = run_proposal(&a, &base, &nearc, &sk, &s, &rule, &mut vault, loops).await;
                last_proposal = now();
            }
            // judge: on a new screen or a digest; a repeated protective slice is deterministic and skips it
            let mut judged: Option<Value> = None;
            if !new.is_empty() || digest {
                if now() >= feed.1 + FEED_SECS {
                    feed = (judge::fetch_feed(&reqwest::Client::new()).await.unwrap_or_else(|e| json!({"error": e.to_string(), "items": []})), now());
                }
                tr["inputs"]["feed"] = feed.0.clone();
                let (user, ids) = judge_input(&s, &fired, &feed.0);
                let tj = Instant::now();
                let att = judge.attest().await.unwrap_or_else(|e| json!({"ok": false, "error": e.to_string()}));
                let call = if att["ok"] == true {
                    judge.ask(&user, &ids, att["signing_address"].as_str().unwrap_or("")).await.unwrap_or_else(|e| json!({"error": e.to_string()}))
                } else {
                    json!({"skipped": "model attestation did not verify"})
                };
                if call["valid"] == true && call["signature"]["ok"] == true {
                    judged = call["parsed"].as_object().map(|o| Value::Object(o.clone()));
                }
                println!("RESULT judge loop={loops} attestation_ok={} dcap={} sig_ok={} kind={} valid={} parsed={} latency_s={} usage={} total_s={:.1}",
                    att["ok"], att["dcap"], call["signature"]["ok"], call["signature"]["signature_kind"], call["valid"], call["parsed"], call["latency_s"], call["usage"], tj.elapsed().as_secs_f64());
                tr["judge"] = json!({"attestation": att, "call": call, "seconds": tj.elapsed().as_secs_f64(), "accepted": judged.is_some()});
                if digest {
                    next_digest = now() + DIGEST_SECS;
                }
            } else {
                tr["judge"] = json!({"skipped": "repeated protective slice: deterministic rule, no judge call"});
            }
            let d = decide(&s, &fired, judged.as_ref(), &vault);
            if rule_acts {
                tr["decision_after_judge"] = json!({"not_acted_on_this_loop": true, "decision": decision_json(&d)});
            } else {
                tr["decision"] = decision_json(&d);
                println!("RESULT decision loop={loops} action={} amount={} fraction={} basis={}", d.action, d.amount, d.fraction, d.basis);
                if d.action != "none" {
                    tr["proposal"] = run_proposal(&a, &base, &nearc, &sk, &s, &d, &mut vault, loops).await;
                    last_proposal = now();
                }
            }
            tr["seconds"] = json!(tl.elapsed().as_secs_f64());
            let name = format!("transcripts/{boot_id}-{loops:04}.json");
            // a transcript that cannot be written is reported, never fatal
            match serde_json::to_string_pretty(&tr).map_err(anyhow::Error::from).and_then(|t| tee::save_evidence(&a.evidence, &name, t.as_bytes())) {
                Ok(h) => println!("RESULT transcript loop={loops} {name} sha256={h} seconds={:.1}", tl.elapsed().as_secs_f64()),
                Err(e) => println!("RESULT transcript loop={loops} {name} write failed: {e}"),
            }
        }
        fired_prev = fired_set;
        let el = t0.elapsed();
        if el < Duration::from_secs(a.tick_secs) {
            tokio::time::sleep(Duration::from_secs(a.tick_secs) - el).await;
        }
    }
    println!("RESULT done ticks={tick} loops={loops}");
    Ok(())
}
