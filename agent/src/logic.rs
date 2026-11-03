//! The agent's decisions as pure functions over a snapshot (docs/01-design-and-feasibility.md §4.9 steps 3, 6, 7),
//! so the tick loop only does I/O. Everything here mirrors a check of the executor or the vault, or applies the
//! agreement rule; the model never computes an amount.
use crate::chain::{Feed, Ltv, MarketSnap, Snapshot, ETH_USD, USDC_USD, WAD};
use primitive_types::U256;
use serde::Serialize;
use serde_json::{json, Value};
use std::collections::BTreeSet;

pub const UTILIZATION_SCREEN: f64 = 0.95;
pub const POOL_GAP_SCREEN: f64 = 0.02;
/// reduce_light = 1/4 of the remaining sell budget (§4.9 step 6)
pub const REDUCE_LIGHT_SHARE: u128 = 4;
/// CorridorMath.TARGET_TOLERANCE: a protective session ends within 0.1 pp of target
pub const TARGET_TOLERANCE: u128 = WAD / 1000;
/// Executor SESSION: a protective session lapses 24 h after it started
pub const SESSION_SECS: u64 = 86_400;
/// Executor MAX_MOVES per rolling 24 h
pub const MAX_MOVES: u128 = 2;
/// CorridorMath.SLIPPAGE_BPS: fills no worse than 1% below the oracle
const SLIPPAGE_BPS: u128 = 100;
const BPS: u128 = 10_000;
/// The executor's COOLDOWN when it cannot be read (it is 60 s on the deployed executor)
pub const DEFAULT_EXEC_COOLDOWN: u64 = 60;

fn wad(x: u128) -> f64 {
    x as f64 / 1e18
}
fn r4(x: f64) -> f64 {
    (x * 1e4).round() / 1e4
}

// ------------------------------------------------------------------ tick gate (build item 48)

/// A tick decides only on a complete read on which both providers agree at the pinned block.
pub fn gate(s: &Snapshot) -> Result<(), String> {
    if !s.read_errors.is_empty() {
        return Err(format!("read errors: {:?}", s.read_errors));
    }
    if let Some(e) = &s.rpc_second_error {
        return Err(format!("second provider failed: {e}"));
    }
    if !s.rpc_disagree.is_empty() {
        return Err(format!("providers disagree on: {:?}", s.rpc_disagree));
    }
    Ok(())
}

// ------------------------------------------------------------------ screens (§4.9 step 3)

#[derive(Serialize, Clone, Debug, PartialEq, Eq)]
pub struct Fired {
    pub market: String,
    pub screen: String,
    pub detail: String,
}

impl Fired {
    pub fn key(&self) -> String {
        format!("{}:{}", self.market, self.screen)
    }
}

/// Screens that count as the fired on-chain screen of the agreement rule (§4.9 steps 3 and 6, 31 Oct): the
/// pool-gap screen does not, because on testnet the pool is ours.
pub fn confirms(screen: &str) -> bool {
    screen != "oracle_pool_gap_above_2pct"
}

fn feed_name(f: &str) -> &str {
    if f == ETH_USD {
        "ETH/USD"
    } else if f == USDC_USD {
        "USDC/USD"
    } else {
        f
    }
}

pub fn screens(s: &Snapshot) -> Vec<Fired> {
    let mut out = vec![];
    let fire = |out: &mut Vec<Fired>, m: &MarketSnap, screen: &str, detail: String| {
        out.push(Fired { market: m.name.clone(), screen: screen.into(), detail })
    };
    for m in s.markets.iter().filter(|m| m.exists) {
        if let Some(x) = &m.exec {
            if x.in_force() && m.debt > 0 && m.ltv.above(x.corridor.act) {
                let l = m.ltv.as_f64().map(|v| format!("{v:.4}")).unwrap_or("unbounded".into());
                fire(&mut out, m, "ltv_above_act", format!("LTV {l} > act {:.4}", wad(x.corridor.act)));
            }
        }
        if m.ltv == Ltv::NoCollateralValue {
            fire(&mut out, m, "collateral_value_zero", format!("debt {} with collateral worth 0 at the oracle", m.debt));
        }
        if m.utilization > UTILIZATION_SCREEN {
            fire(&mut out, m, "utilization_above_95pct", format!("utilization {:.4}", m.utilization));
        }
        if let Some(g) = m.pool_gap {
            if g.abs() > POOL_GAP_SCREEN {
                fire(&mut out, m, "oracle_pool_gap_above_2pct", format!("pool {:.2} vs oracle {:.2} USD ({:+.2}%)", m.pool_usd.unwrap_or(0.0), m.oracle_usd, g * 100.0));
            }
        }
        for f in &m.feeds {
            if !f.fresh() {
                let why = if f.answer_positive { format!("age {} s > {} s", f.age_s, f.max_age_s) } else { "answer not positive".into() };
                fire(&mut out, m, "feed_stale", format!("feed {} {why}", feed_name(&f.feed)));
            }
        }
    }
    out
}

pub fn fired_set(f: &[Fired]) -> BTreeSet<String> {
    f.iter().map(|x| x.key()).collect()
}

// ------------------------------------------------------------------ amounts (mirroring CorridorMath)

/// CorridorMath.repayToLtv: the repay amount that brings LTV to `line` when collateral worth the same amount is sold
/// at the oracle; 0 if LTV is already at or below it; capped at the debt.
pub fn amount_to(m: &MarketSnap, line: u128) -> u128 {
    if line >= WAD {
        return 0;
    }
    let (d, v, w) = (U256::from(m.debt), U256::from(m.collateral_value), U256::from(WAD));
    let lv = U256::from(line) * v;
    if d * w <= lv {
        return 0;
    }
    let x = (d * w - lv) / (w - U256::from(line));
    if x > d {
        m.debt
    } else {
        x.low_u128()
    }
}

/// CorridorMath.worstCaseOut: loan units `collateral` is guaranteed to fetch at the oracle minus 1%.
pub fn worst_case_out(collateral: u128, price: U256) -> u128 {
    let x = U256::from(collateral) * price * U256::from(BPS - SLIPPAGE_BPS) / (U256::exp10(36) * U256::from(BPS));
    if x > U256::from(u128::MAX) {
        u128::MAX
    } else {
        x.low_u128()
    }
}

/// The executor's remaining discretionary budget for `m`'s collateral token (2 Nov rule, `_activeCollateral` and
/// `remainingBudget`): base = collateral on the agent-active markets in that token (corridor in force, collateral
/// held) plus what was sold in the window; fraction = the smallest sell budget among those corridors.
/// Returns (collateral units, loan units at the worst allowed fill).
pub fn budget(s: &Snapshot, m: &MarketSnap) -> (u128, u128) {
    let Some(x) = &m.exec else { return (0, 0) };
    let mut sum = U256::zero();
    let mut min_budget: Option<u128> = None;
    for o in s.markets.iter().filter(|o| o.collateral_token == m.collateral_token && o.collateral > 0) {
        let Some(ox) = &o.exec else { continue };
        if !ox.in_force() {
            continue;
        }
        sum += U256::from(o.collateral);
        min_budget = Some(min_budget.map_or(ox.corridor.sell_budget, |b| b.min(ox.corridor.sell_budget)));
    }
    let b = min_budget.unwrap_or(0);
    let sold = U256::from(x.sold_in_window);
    let limit = (sum + sold) * U256::from(b) / U256::from(WAD);
    let rem = if limit > sold { (limit - sold).low_u128() } else { 0 };
    (rem, worst_case_out(rem, m.price()))
}

/// The executor's protective test: LTV above act, or a live session (started, not lapsed) with LTV still more than
/// TARGET_TOLERANCE above target. Protective selling is not budget-limited and stops at target.
pub fn protective(s: &Snapshot, m: &MarketSnap) -> bool {
    let Some(x) = &m.exec else { return false };
    if !x.in_force() || m.debt == 0 || m.ltv == Ltv::NoCollateralValue {
        return false;
    }
    let c = &x.corridor;
    let session_live = x.session_start != 0 && s.timestamp < x.session_start.saturating_add(SESSION_SECS);
    m.ltv.above(c.act) || (session_live && m.ltv.above(c.target.saturating_add(TARGET_TOLERANCE)))
}

fn vault_market<'a>(vault: &'a Value, id: &str) -> Option<&'a Value> {
    vault["markets"].as_array().and_then(|a| a.iter().find(|m| m["id"].as_str().map(|x| x.eq_ignore_ascii_case(id)).unwrap_or(false)))
}

fn slice_max(vault: &Value, id: &str) -> u128 {
    vault_market(vault, id).and_then(|m| m["slice_max"].as_str()).and_then(|v| v.parse().ok()).unwrap_or(0)
}

/// Fraction (WAD) of the position a destination can take: all of it when its free liquidity covers the debt plus
/// 1%, otherwise 99% of what its liquidity covers.
pub fn migrate_fraction(dest_liquidity: u128, debt: u128) -> u128 {
    if debt == 0 {
        return 0;
    }
    if dest_liquidity >= debt.saturating_add(debt / 100) {
        WAD
    } else {
        (U256::from(dest_liquidity) * U256::from(WAD) / U256::from(debt) * U256::from(99u8) / U256::from(100u8)).low_u128()
    }
}

/// LTV on the destination after moving `fraction` of the source position (debt and collateral), at the
/// destination's oracle. The executor refuses a move that lands above the destination's act line (2 Nov).
pub fn ltv_after_move(src: &MarketSnap, dest: &MarketSnap, fraction: u128) -> Ltv {
    let w = U256::from(WAD);
    let f = U256::from(fraction);
    let debt = U256::from(dest.debt) + (U256::from(src.debt) * f + w - 1) / w;
    let coll = U256::from(dest.collateral) + U256::from(src.collateral) * f / w;
    let value = coll * dest.price() / U256::exp10(36);
    let cap = U256::from(u128::MAX);
    Ltv::of(debt.min(cap).low_u128(), value.min(cap).low_u128())
}

// ------------------------------------------------------------------ decide (§4.9 step 6), per market (build item 52)

#[derive(Serialize, Clone, Copy, Debug, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum Act {
    None,
    Reduce,
    Migrate,
    Exit,
}

#[derive(Clone, Debug)]
pub struct Decision {
    pub action: Act,
    pub protective: bool,
    pub market: MarketSnap,
    pub to: Option<MarketSnap>,
    pub amount: u128,
    pub fraction: u128,
    pub basis: String,
    pub detail: Value,
}

impl Decision {
    pub fn json(&self) -> Value {
        json!({"action": self.action, "protective": self.protective, "market": self.market.name, "market_id": self.market.id,
            "to": self.to.as_ref().map(|m| &m.name), "amount": self.amount.to_string(), "fraction": self.fraction.to_string(),
            "basis": self.basis, "detail": self.detail})
    }
}

/// A judge answer the loop may apply: accepted (schema valid, `provider_tee` signature verified), and still relevant.
#[derive(Serialize, Clone, Debug)]
pub struct Judgment {
    pub action: String,
    pub risk: String,
    /// the fired screens of the snapshot the judge was given
    pub trigger: BTreeSet<String>,
    pub block: u64,
}

/// A judgment is relevant while every screen that was on when it was asked is still on (build item 44). Otherwise
/// the situation it judged has changed: it is recorded and ignored, and only the rules apply.
pub fn relevant(trigger: &BTreeSet<String>, fired_now: &BTreeSet<String>) -> bool {
    trigger.is_subset(fired_now)
}

pub fn decide_market(s: &Snapshot, m: &MarketSnap, fired: &[Fired], judged: Option<&Judgment>, vault: &Value) -> Decision {
    let none = |basis: &str, detail: Value| Decision {
        action: Act::None,
        protective: false,
        market: m.clone(),
        to: None,
        amount: 0,
        fraction: 0,
        basis: basis.into(),
        detail,
    };
    if m.ltv == Ltv::NoCollateralValue {
        return none("collateral worth 0 at the oracle: nothing to sell, no action (screen collateral_value_zero)", json!({"debt": m.debt.to_string()}));
    }
    let Some(x) = m.exec.clone() else { return none("executor not deployed: no corridor in force", json!({})) };
    if !x.in_force() {
        return none("no corridor in force on the executor for this market (corridors() unset, or closed by exit)", json!({"executor_corridor_set": x.corridor.set, "closed_for_agent": x.closed_for_agent}));
    }
    let c = &x.corridor;
    let slice = slice_max(vault, &m.id);
    let to_target = amount_to(m, c.target);
    let floor_room = amount_to(m, c.floor);
    let (budget_coll, budget_value) = budget(s, m);
    let confirm: Vec<&Fired> = fired.iter().filter(|f| f.market == m.name && confirms(&f.screen)).collect();
    let is_protective = protective(s, m);
    let action = judged.map(|j| j.action.as_str());
    let detail = json!({"ltv": m.ltv, "target": wad(c.target), "act": wad(c.act), "floor": wad(c.floor), "lltv": wad(m.lltv),
        "debt": m.debt.to_string(), "collateral_value": m.collateral_value.to_string(),
        "amount_to_target": to_target.to_string(), "amount_to_floor": floor_room.to_string(),
        "sell_budget_remaining_collateral": budget_coll.to_string(), "sell_budget_remaining_value": budget_value.to_string(),
        "slice_max": slice.to_string(), "session_start": x.session_start, "judge_action": action,
        "onchain_confirmation": confirm.iter().map(|f| f.screen.clone()).collect::<Vec<_>>()});
    let reduce = |amount: u128, basis: &str, prot: bool| {
        let a = amount.min(slice).min(m.debt); // bounded: never more than the slice maximum or the debt
        Decision {
            action: if a > 0 { Act::Reduce } else { Act::None },
            protective: prot,
            market: m.clone(),
            to: None,
            amount: a,
            fraction: 0,
            basis: if a > 0 { basis.into() } else { format!("{basis}; amount 0 after the clamps (slice maximum {slice})") },
            detail: detail.clone(),
        }
    };
    if is_protective {
        // rule, independent of the judge: a protective session sells to target (§3); the executor clamps again
        return reduce(to_target, "rule: LTV above the act line (or a protective session still above target), protective reduce to target", true);
    }
    if confirm.is_empty() {
        return none("no fired on-chain screen on this market (the pool-gap screen does not count): no discretionary action", detail);
    }
    match action {
        None => none("no judgment to apply on this tick: rules only", detail),
        Some("reduce_to_target") => reduce(to_target.min(budget_value).min(floor_room), "judge reduce_to_target + on-chain screen; clamped to budget and floor", false),
        Some("reduce_light") => reduce((budget_value / REDUCE_LIGHT_SHARE).min(floor_room), "judge reduce_light + on-chain screen: 1/4 of the remaining budget, clamped to floor", false),
        Some("leave_market") => {
            // the code picks the destination: same tokens and executor, corridor in force with migrate allowed, no
            // screen, fresh feeds, USDC to lend, and LTV after the move at or below its act line
            let src_exec = vault_market(vault, &m.id).and_then(|v| v["executor"].as_str()).map(|e| e.to_lowercase());
            let dest = s
                .markets
                .iter()
                .filter(|d| d.id != m.id && d.exists && d.loan_token == m.loan_token && d.collateral_token == m.collateral_token)
                .filter(|d| vault_market(vault, &d.id).and_then(|v| v["executor"].as_str()).map(|e| e.to_lowercase()) == src_exec)
                .filter(|d| d.exec.as_ref().map(|e| e.in_force() && e.corridor.migrate_allowed).unwrap_or(false))
                .filter(|d| !fired.iter().any(|f| f.market == d.name))
                .filter(|d| d.feeds.iter().all(Feed::fresh))
                .filter(|d| d.liquidity > 0)
                .filter(|d| {
                    let act = d.exec.as_ref().map(|e| e.corridor.act).unwrap_or(0);
                    !ltv_after_move(m, d, migrate_fraction(d.liquidity, m.debt)).above(act)
                })
                .max_by_key(|d| d.liquidity)
                .cloned();
            match dest {
                Some(d) if c.migrate_allowed && x.moves_in_window < MAX_MOVES => Decision {
                    action: Act::Migrate,
                    protective: false,
                    market: m.clone(),
                    fraction: migrate_fraction(d.liquidity, m.debt),
                    to: Some(d),
                    amount: 0,
                    basis: "judge leave_market + on-chain screen: migrate to the approved market that can take it".into(),
                    detail,
                },
                _ if c.exit_allowed => Decision {
                    action: Act::Exit,
                    protective: false,
                    market: m.clone(),
                    to: None,
                    amount: 0,
                    fraction: 0,
                    basis: "judge leave_market + on-chain screen, no approved market can take the position: exit".into(),
                    detail,
                },
                _ => none("leave_market, but no destination qualifies and exit is off", detail),
            }
        }
        Some(_) => none("judge hold: nothing to do", detail),
    }
}

/// Every market of the vault where the position has debt, each with its own decision (build item 52).
pub fn decide_all(s: &Snapshot, fired: &[Fired], judged: Option<&Judgment>, vault: &Value) -> Vec<Decision> {
    s.markets.iter().filter(|m| m.exists && m.debt > 0).map(|m| decide_market(s, m, fired, judged, vault)).collect()
}

/// The one decision to propose now (both cooldowns are per caller across markets, so at most one per cooldown):
/// protective first, then the market closest to its liquidation LTV.
pub fn pick(ds: &[Decision]) -> Option<usize> {
    let closeness = |d: &Decision| {
        let lltv = d.market.lltv.max(1);
        U256::from(d.market.ltv.order_key()) * U256::from(WAD) / U256::from(lltv)
    };
    ds.iter().enumerate().filter(|(_, d)| d.action != Act::None).max_by(|(_, a), (_, b)| (a.protective, closeness(a)).cmp(&(b.protective, closeness(b)))).map(|(i, _)| i)
}

/// Markets where a condition stays on: a fired screen or a protective state. While any is on, `decide` re-runs on
/// the cooldown boundary (build item 53).
pub fn standing(s: &Snapshot, fired: &[Fired]) -> bool {
    !fired.is_empty() || s.markets.iter().any(|m| m.exists && protective(s, m))
}

// ------------------------------------------------------------------ decision rate (build item 53)

#[derive(Debug, PartialEq, Eq)]
pub enum Due {
    /// a screen that was off on the last good tick is on: decide in this tick (a protective slice is proposed now)
    NewScreens,
    /// a judge result is ready to apply, or to be recorded as ignored
    JudgeResult,
    /// a condition stayed on and the cooldown boundary is reached
    Recheck,
    /// nothing to do on this tick; `standing` says whether a condition is on (one log line)
    No { standing: bool },
}

pub fn due(now: u64, new_screens: bool, judge_ready: bool, standing: bool, next_recheck: u64) -> Due {
    if new_screens {
        Due::NewScreens
    } else if judge_ready {
        Due::JudgeResult
    } else if standing && now >= next_recheck {
        Due::Recheck
    } else {
        Due::No { standing }
    }
}

/// The executor's COOLDOWN from the snapshot (60 s), or the default when it was not read.
pub fn exec_cooldown(s: &Snapshot) -> u64 {
    s.markets.iter().find_map(|m| m.exec.as_ref().map(|x| x.cooldown)).filter(|c| *c > 0).unwrap_or(DEFAULT_EXEC_COOLDOWN)
}

/// The earliest time both the vault and the executor accept an agent call: the larger cooldown applies (§4.1).
pub fn cooldown_free_at(vault: &Value, s: &Snapshot) -> u64 {
    let v = vault["last_agent_action"].as_u64().unwrap_or(0).saturating_add(vault["cooldown_secs"].as_u64().unwrap_or(0));
    let e = s
        .markets
        .iter()
        .find_map(|m| m.exec.as_ref())
        .map(|x| if x.last_agent_call == 0 { 0 } else { x.last_agent_call.saturating_add(x.cooldown) })
        .unwrap_or(0);
    v.max(e)
}

/// The next re-check after a decision: the executor's cooldown from now, and not before both cooldowns let a call in.
pub fn next_recheck(now: u64, exec_cooldown: u64, free_at: u64) -> u64 {
    now.saturating_add(exec_cooldown).max(free_at)
}

// ------------------------------------------------------------------ agent status and pre-flight (items 50, 54)

#[derive(Serialize, Clone, Copy, Debug, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum AgentStatus {
    Active,
    /// the vault lists no agent key (revoke_agent)
    Revoked,
    /// the vault lists another key (a newer registration replaced this agent)
    Replaced,
    /// the vault lists this key, but its registration expired
    Expired,
}

impl AgentStatus {
    pub fn label(&self) -> &'static str {
        match self {
            AgentStatus::Active => "active",
            AgentStatus::Revoked => "revoked",
            AgentStatus::Replaced => "revoked (another key registered)",
            AgentStatus::Expired => "revoked (registration expired)",
        }
    }
}

pub fn agent_status(vault: &Value, our_pk: &str, now: u64) -> AgentStatus {
    match vault["agent"]["public_key"].as_str() {
        None => AgentStatus::Revoked,
        Some(k) if k != our_pk => AgentStatus::Replaced,
        Some(_) if vault["agent"]["expires_at"].as_u64().unwrap_or(0) <= now => AgentStatus::Expired,
        Some(_) => AgentStatus::Active,
    }
}

/// Whether the agent proposes at all: only with an active registration. A local `--no-tee` run never registers, so
/// it still builds dry proposals (nothing is sent in dry mode).
pub fn may_propose(status: AgentStatus, tee: bool) -> bool {
    status == AgentStatus::Active || !tee
}

#[derive(Serialize, Clone, Debug)]
pub struct Check {
    pub name: &'static str,
    pub ok: bool,
    pub detail: String,
}

/// The vault's and the executor's checks on this proposal, mirrored so a refusal is explained before anything is
/// sent (build item 50). `now` is the wall clock (the vault uses NEAR block time); the executor's checks use the
/// snapshot's block time.
pub fn prechecks(d: &Decision, s: &Snapshot, vault: &Value, our_pk: &str, now: u64) -> Vec<Check> {
    let mut out = vec![];
    let mut add = |name: &'static str, ok: bool, detail: String| out.push(Check { name, ok, detail });
    let st = agent_status(vault, our_pk, now);
    add("vault_agent_key_registered_and_unexpired", st == AgentStatus::Active, st.label().into());
    let free = vault["last_agent_action"].as_u64().unwrap_or(0).saturating_add(vault["cooldown_secs"].as_u64().unwrap_or(0));
    add("vault_cooldown_elapsed", now >= free, format!("free at {free}, now {now}"));
    let mut markets = vec![&d.market];
    if let Some(t) = &d.to {
        markets.push(t);
    }
    for m in &markets {
        let vm = vault_market(vault, &m.id);
        add("vault_market_allowlisted", vm.is_some(), m.name.clone());
        // the vault's flag means "signed", not "in force" (build item 10); it still refuses without it
        add("vault_corridor_flag_set", vm.map(|v| v["corridor_set"] == true).unwrap_or(false), m.name.clone());
        let x = m.exec.as_ref();
        add("executor_corridor_in_force", x.map(|x| x.in_force()).unwrap_or(false), format!("{} (executor corridors() is the source of truth)", m.name));
    }
    add("executor_deployed", s.executor_deployed, s.executor.clone());
    if let Some(x) = d.market.exec.as_ref() {
        let free = if x.last_agent_call == 0 { 0 } else { x.last_agent_call.saturating_add(x.cooldown) };
        add("executor_cooldown_elapsed", s.timestamp >= free, format!("free at {free}, block time {}", s.timestamp));
    }
    match d.action {
        Act::Reduce => {
            let slice = slice_max(vault, &d.market.id);
            add("vault_amount_within_slice_max", d.amount > 0 && d.amount <= slice, format!("{} of {slice}", d.amount));
        }
        Act::Migrate => {
            let ex = |m: &MarketSnap| vault_market(vault, &m.id).and_then(|v| v["executor"].as_str()).map(|e| e.to_lowercase());
            let same = d.to.as_ref().map(|t| ex(t) == ex(&d.market) && ex(t).is_some()).unwrap_or(false);
            add("vault_same_executor", same, String::new());
            add("vault_fraction_in_range", d.fraction > 0 && d.fraction <= WAD, d.fraction.to_string());
            let allowed = markets.iter().all(|m| m.exec.as_ref().map(|x| x.corridor.migrate_allowed).unwrap_or(false));
            add("executor_migrate_allowed_both", allowed, String::new());
            let moves = d.market.exec.as_ref().map(|x| x.moves_in_window).unwrap_or(u128::MAX);
            add("executor_moves_below_limit", moves < MAX_MOVES, format!("{moves} of {MAX_MOVES}"));
            let fresh = d.to.as_ref().map(|t| t.feeds.iter().all(Feed::fresh)).unwrap_or(false);
            add("executor_destination_feeds_fresh", fresh, String::new());
        }
        Act::Exit => {
            let allowed = d.market.exec.as_ref().map(|x| x.corridor.exit_allowed).unwrap_or(false);
            add("executor_exit_allowed", allowed, String::new());
        }
        Act::None => add("an_action", false, "no action".into()),
    }
    out
}

pub fn refusals(checks: &[Check]) -> Vec<String> {
    checks.iter().filter(|c| !c.ok).map(|c| if c.detail.is_empty() { c.name.to_string() } else { format!("{}: {}", c.name, c.detail) }).collect()
}

// ------------------------------------------------------------------ judge input (build item 46)

/// The judge's user message and the source ids it may cite. Every threshold the model is asked to judge against is
/// in the input: screen thresholds, feed heartbeats, the corridor (act, target, floor), the liquidation LTV of the
/// market, the remaining sell budget, the move limit and the cooldowns.
pub fn judge_input(s: &Snapshot, fired: &[Fired], feed: &Value, vault: &Value, now: u64) -> (String, Vec<String>) {
    let mut ids = vec![];
    let mut markets = vec![];
    let free_at = cooldown_free_at(vault, s);
    for m in s.markets.iter().filter(|m| m.exists) {
        let id = format!("chain:{}:market", m.name);
        ids.push(id.clone());
        let mut o = json!({"source_id": id, "market": m.name, "utilization": r4(m.utilization),
            "borrow_apr": r4(m.borrow_apr), "oracle_usd_per_weth": (m.oracle_usd * 100.0).round() / 100.0,
            "pool_usd_per_weth": m.pool_usd.map(|p| (p * 100.0).round() / 100.0),
            "pool_vs_oracle_pct": m.pool_gap.map(|g| (g * 1e4).round() / 100.0),
            "feeds": m.feeds.iter().map(|f| json!({"feed": feed_name(&f.feed), "age_s": f.age_s, "max_age_s": f.max_age_s, "answer_positive": f.answer_positive})).collect::<Vec<_>>(),
            "liquidation_ltv": r4(wad(m.lltv)),
            "loan_token_available_to_borrow": m.liquidity as f64 / 1e6});
        if m.debt > 0 || m.collateral > 0 {
            let pid = format!("chain:{}:position", m.name);
            ids.push(pid.clone());
            let ltv = m.ltv.as_f64();
            let lltv = wad(m.lltv);
            let c = m.exec.as_ref().map(|x| {
                let (bc, bv) = budget(s, m);
                json!({"in_force": x.in_force(), "target": r4(wad(x.corridor.target)), "act": r4(wad(x.corridor.act)), "floor": r4(wad(x.corridor.floor)),
                    "sell_budget_fraction_per_24h": r4(wad(x.corridor.sell_budget)),
                    "sell_budget_remaining_weth": bc as f64 / 1e18, "sell_budget_remaining_usd_worst_fill": bv as f64 / 1e6,
                    "migrate_allowed": x.corridor.migrate_allowed, "exit_allowed": x.corridor.exit_allowed,
                    "moves_in_24h": x.moves_in_window, "max_moves_per_24h": MAX_MOVES,
                    "protective_session_open": x.session_start != 0 && s.timestamp < x.session_start.saturating_add(SESSION_SECS)})
            });
            o["position"] = json!({"source_id": pid, "collateral_weth": m.collateral as f64 / 1e18, "debt": m.debt as f64 / 1e6,
                "ltv": ltv.map(r4), "ltv_state": m.ltv,
                "liquidation_ltv": r4(lltv),
                "price_drop_to_liquidation_pct": ltv.filter(|_| lltv > 0.0).map(|l| ((1.0 - l / lltv) * 1e4).round() / 100.0), "corridor": c});
        }
        markets.push(o);
    }
    let sc: Vec<Value> = fired
        .iter()
        .map(|f| {
            let id = format!("screen:{}:{}", f.market, f.screen);
            ids.push(id.clone());
            json!({"source_id": id, "detail": f.detail, "counts_as_onchain_confirmation": confirms(&f.screen)})
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
    let user = json!({"now_utc": crate::iso(s.timestamp), "base_block": s.block, "markets": markets, "screens_fired": sc,
        "screen_thresholds": {"utilization_above": UTILIZATION_SCREEN, "pool_vs_oracle_abs_pct_above": POOL_GAP_SCREEN * 100.0,
            "feed_stale_when_age_s_above": "max_age_s of each feed"},
        "agent_limits": {"executor_cooldown_s": exec_cooldown(s), "vault_cooldown_s": vault["cooldown_secs"],
            "agent_may_act_in_s": free_at.saturating_sub(now)},
        "text_feed": {"source": crate::judge::FEED_URL, "untrusted": true, "items": items}});
    (serde_json::to_string_pretty(&user).unwrap_or_default(), ids)
}

// ------------------------------------------------------------------ tests (no network)

#[cfg(test)]
pub mod fixtures {
    use super::*;
    use crate::chain::{Corridor, ExecState};

    pub const PK: &str = "ed25519:testkey";
    pub const EXEC: &str = "0x00000000000000000000000000000000000000e1";
    pub const T: u64 = 1_800_000_000;

    /// A market with debt and collateral in human units (USDC 6 decimals, WETH 18), oracle at `usd` per WETH, and a
    /// corridor in force (target, act, floor, 20% budget, migrate and exit allowed).
    pub fn market(name: &str, debt_usdc: u128, coll_weth_milli: u128, usd: u128, target: f64, act: f64, floor: f64) -> MarketSnap {
        let collateral = coll_weth_milli * 1_000_000_000_000_000; // milli-WETH to wei
        let price = U256::from(usd) * U256::exp10(24);
        let debt = debt_usdc * 1_000_000;
        let value = (U256::from(collateral) * price / U256::exp10(36)).low_u128();
        let w = |x: f64| (x * 1e6) as u128 * 1_000_000_000_000;
        MarketSnap {
            name: name.into(),
            id: format!("0x{:0>64}", hex::encode(name)),
            exists: true,
            loan_token: "0xusdc".into(),
            collateral_token: "0xweth".into(),
            lltv: w(0.86),
            market: [10_000_000_000_000, 0, 1_000_000_000_000, 0, T as u128, 0],
            collateral,
            oracle_price: price.to_string(),
            debt,
            collateral_value: value,
            ltv: Ltv::of(debt, value),
            utilization: 0.5,
            oracle_usd: usd as f64,
            feeds: vec![Feed { feed: ETH_USD.into(), updated_at: T - 60, max_age_s: 1200, age_s: 60, answer_positive: true }],
            exec: Some(ExecState {
                corridor: Corridor { target: w(target), act: w(act), floor: w(floor), sell_budget: w(0.2), migrate_allowed: true, exit_allowed: true, set: true },
                sold_in_window: 0,
                moves_in_window: 0,
                last_agent_call: 0,
                session_start: 0,
                closed_for_agent: false,
                cooldown: 60,
            }),
            liquidity: 9_000_000_000_000,
            ..Default::default()
        }
    }

    pub fn snap(markets: Vec<MarketSnap>) -> Snapshot {
        Snapshot { block: 100, timestamp: T, executor: EXEC.into(), executor_deployed: true, markets, ..Default::default() }
    }

    pub fn vault(s: &Snapshot) -> Value {
        json!({"cooldown_secs": 120, "last_agent_action": 0, "agent": {"public_key": PK, "expires_at": T + 7 * 86400},
            "markets": s.markets.iter().map(|m| json!({"id": m.id, "slice_max": "1000000000000", "corridor_set": true, "executor": EXEC})).collect::<Vec<_>>()})
    }
}

#[cfg(test)]
mod tests {
    use super::fixtures::*;
    use super::*;

    fn judgment(action: &str, trigger: &[&str]) -> Judgment {
        Judgment { action: action.into(), risk: "high".into(), trigger: trigger.iter().map(|s| s.to_string()).collect(), block: 100 }
    }

    // ---- 52: every position

    #[test]
    fn every_market_with_debt_is_evaluated_and_the_lower_ltv_breach_is_caught() {
        // a: LTV 0.65 under act 0.70 (no breach); b: LTV 0.60 above its act 0.55 (breach), lower LTV than a
        let a = market("a", 1300, 1000, 2000, 0.50, 0.70, 0.30);
        let b = market("b", 1200, 1000, 2000, 0.40, 0.55, 0.30);
        let idle = market("idle", 0, 1000, 2000, 0.50, 0.70, 0.30);
        let s = snap(vec![a, b, idle]);
        let v = vault(&s);
        let fired = screens(&s);
        assert!(fired.iter().any(|f| f.market == "b" && f.screen == "ltv_above_act"));
        assert!(!fired.iter().any(|f| f.market == "a" && f.screen == "ltv_above_act"));
        let ds = decide_all(&s, &fired, None, &v);
        assert_eq!(ds.len(), 2, "both markets with debt are evaluated, the one without debt is not");
        let db = ds.iter().find(|d| d.market.name == "b").unwrap();
        assert_eq!(db.action, Act::Reduce);
        assert!(db.protective);
        assert_eq!(db.amount, amount_to(&s.markets[1], s.markets[1].exec.as_ref().unwrap().corridor.target));
        assert_eq!(ds.iter().find(|d| d.market.name == "a").unwrap().action, Act::None);
        assert_eq!(ds[pick(&ds).unwrap()].market.name, "b");
    }

    #[test]
    fn judge_action_applies_per_market_with_its_own_confirmation() {
        let mut a = market("a", 1100, 1000, 2000, 0.50, 0.70, 0.30);
        a.utilization = 0.97; // a confirming on-chain screen on a only
        let b = market("b", 1100, 1000, 2000, 0.50, 0.70, 0.30);
        let s = snap(vec![a, b]);
        let v = vault(&s);
        let fired = screens(&s);
        let j = judgment("reduce_to_target", &["a:utilization_above_95pct"]);
        let ds = decide_all(&s, &fired, Some(&j), &v);
        let da = ds.iter().find(|d| d.market.name == "a").unwrap();
        let db = ds.iter().find(|d| d.market.name == "b").unwrap();
        assert_eq!(da.action, Act::Reduce);
        assert!(!da.protective);
        assert_eq!(db.action, Act::None, "no screen on b: the judgment does not act there");
        // discretionary reduce_to_target is clamped by the remaining budget (20% of 2 WETH at 2000 less 1%)
        let (_, bv) = budget(&s, &s.markets[0]);
        assert_eq!(bv, 792_000_000);
        assert_eq!(da.amount, amount_to(&s.markets[0], s.markets[0].exec.as_ref().unwrap().corridor.target).min(bv));
    }

    #[test]
    fn pool_gap_alone_is_no_onchain_confirmation() {
        let mut a = market("a", 1100, 1000, 2000, 0.50, 0.70, 0.30);
        a.pool_gap = Some(-0.05);
        a.pool_usd = Some(1900.0);
        let s = snap(vec![a]);
        let fired = screens(&s);
        assert_eq!(fired.len(), 1);
        let d = decide_market(&s, &s.markets[0], &fired, Some(&judgment("reduce_light", &["a:oracle_pool_gap_above_2pct"])), &vault(&s));
        assert_eq!(d.action, Act::None);
    }

    #[test]
    fn protective_session_continues_below_act_until_target() {
        let mut a = market("a", 1200, 2000, 1000, 0.50, 0.70, 0.30); // LTV 0.60: below act, above target
        a.exec.as_mut().unwrap().session_start = T - 100;
        let s = snap(vec![a.clone()]);
        let d = decide_market(&s, &s.markets[0], &screens(&s), None, &vault(&s));
        assert_eq!(d.action, Act::Reduce);
        assert!(d.protective);
        assert!(standing(&s, &screens(&s)));
        // a lapsed session (older than 24 h) is not protective
        a.exec.as_mut().unwrap().session_start = T - SESSION_SECS - 1;
        let s = snap(vec![a]);
        assert_eq!(decide_market(&s, &s.markets[0], &screens(&s), None, &vault(&s)).action, Act::None);
    }

    #[test]
    fn executor_corridor_is_the_truth_not_the_vault_flag() {
        let mut a = market("a", 1500, 1000, 2000, 0.50, 0.70, 0.30); // LTV 0.75 above act
        a.exec.as_mut().unwrap().corridor.set = false; // vault says corridor_set, executor has none
        let s = snap(vec![a]);
        assert!(screens(&s).is_empty());
        let d = decide_market(&s, &s.markets[0], &[], None, &vault(&s));
        assert_eq!(d.action, Act::None);
        assert!(d.basis.contains("no corridor in force"));
    }

    #[test]
    fn leave_market_migrates_to_a_destination_under_its_act_line_else_exits() {
        let mut a = market("a", 1100, 1000, 2000, 0.50, 0.70, 0.30);
        a.utilization = 0.99;
        let b = market("b", 0, 0, 2000, 0.50, 0.70, 0.30);
        let s = snap(vec![a.clone(), b]);
        let fired = screens(&s);
        let j = judgment("leave_market", &["a:utilization_above_95pct"]);
        let d = decide_market(&s, &s.markets[0], &fired, Some(&j), &vault(&s));
        assert_eq!(d.action, Act::Migrate);
        assert_eq!(d.fraction, WAD);
        // destination whose oracle is low enough that the moved position lands above its act line: exit instead
        let mut low = market("b", 0, 0, 1500, 0.50, 0.70, 0.30);
        low.oracle_price = (U256::from(1500u32) * U256::exp10(24)).to_string();
        let s = snap(vec![a, low]);
        let d = decide_market(&s, &s.markets[0], &screens(&s), Some(&j), &vault(&s));
        assert_eq!(d.action, Act::Exit);
    }

    // ---- 49: zero collateral value

    #[test]
    fn zero_collateral_value_is_an_explicit_state() {
        assert_eq!(Ltv::of(100, 0), Ltv::NoCollateralValue);
        assert_eq!(Ltv::of(0, 0), Ltv::NoDebt);
        assert_eq!(Ltv::of(1, 2), Ltv::Ratio(WAD / 2));
        let mut a = market("a", 1000, 0, 2000, 0.50, 0.70, 0.30);
        a.collateral_value = 0;
        a.ltv = Ltv::of(a.debt, 0);
        let s = snap(vec![a]);
        let fired = screens(&s);
        assert!(fired.iter().any(|f| f.screen == "collateral_value_zero"));
        let d = decide_market(&s, &s.markets[0], &fired, Some(&judgment("reduce_to_target", &[])), &vault(&s));
        assert_eq!(d.action, Act::None);
        let j = serde_json::to_value(&s.markets[0]).unwrap();
        assert_eq!(j["ltv"], json!({"state": "no_collateral_value"}));
        assert!(!serde_json::to_string(&s).unwrap().contains(&u128::MAX.to_string()));
    }

    // ---- 48: failed or disagreeing reads gate the tick

    #[test]
    fn a_tick_with_read_errors_or_disagreement_decides_nothing() {
        let a = market("a", 1500, 1000, 2000, 0.50, 0.70, 0.30);
        let ok = snap(vec![a]);
        assert!(gate(&ok).is_ok());
        let mut s = ok.clone();
        s.rpc_disagree = vec!["a/position".into()];
        assert!(gate(&s).unwrap_err().contains("disagree"));
        let mut s = ok.clone();
        s.read_errors = vec!["a/position: empty result".into()];
        assert!(gate(&s).is_err());
        let mut s = ok;
        s.rpc_second_error = Some("timeout".into());
        assert!(gate(&s).is_err());
    }

    // ---- 53: decision rate

    #[test]
    fn a_standing_screen_redecides_only_on_the_cooldown_boundary() {
        let next = next_recheck(1000, 60, 0);
        assert_eq!(next, 1060);
        assert_eq!(due(1001, false, false, true, next), Due::No { standing: true });
        assert_eq!(due(1059, false, false, true, next), Due::No { standing: true });
        assert_eq!(due(1060, false, false, true, next), Due::Recheck);
        assert_eq!(due(1001, true, false, true, next), Due::NewScreens, "a new screen decides at once");
        assert_eq!(due(1001, false, true, true, next), Due::JudgeResult);
        assert_eq!(due(5000, false, false, false, next), Due::No { standing: false });
        // after a proposal the vault's 120 s cooldown is the later one
        let s = snap(vec![market("a", 1500, 1000, 2000, 0.50, 0.70, 0.30)]);
        let mut v = vault(&s);
        v["last_agent_action"] = json!(1000);
        assert_eq!(cooldown_free_at(&v, &s), 1120);
        assert_eq!(next_recheck(1000, exec_cooldown(&s), cooldown_free_at(&v, &s)), 1120);
    }

    // ---- 54 and 50: revoked key, pre-checks

    #[test]
    fn an_unregistered_key_stops_proposals() {
        let s = snap(vec![market("a", 1500, 1000, 2000, 0.50, 0.70, 0.30)]);
        let mut v = vault(&s);
        assert_eq!(agent_status(&v, PK, T), AgentStatus::Active);
        let d = decide_market(&s, &s.markets[0], &screens(&s), None, &v);
        assert_eq!(d.action, Act::Reduce);
        assert!(refusals(&prechecks(&d, &s, &v, PK, T)).is_empty(), "{:?}", refusals(&prechecks(&d, &s, &v, PK, T)));
        v["agent"] = Value::Null;
        assert_eq!(agent_status(&v, PK, T), AgentStatus::Revoked);
        assert!(!may_propose(agent_status(&v, PK, T), true));
        let r = refusals(&prechecks(&d, &s, &v, PK, T));
        assert!(r.iter().any(|x| x.contains("vault_agent_key_registered_and_unexpired")), "{r:?}");
        v["agent"] = json!({"public_key": "ed25519:other", "expires_at": T + 10});
        assert_eq!(agent_status(&v, PK, T), AgentStatus::Replaced);
        v["agent"] = json!({"public_key": PK, "expires_at": T});
        assert_eq!(agent_status(&v, PK, T), AgentStatus::Expired);
        assert!(!may_propose(AgentStatus::Expired, true));
        assert!(may_propose(AgentStatus::Revoked, false), "a --no-tee run still builds dry proposals");
    }

    #[test]
    fn prechecks_explain_cooldown_slice_and_corridor_refusals() {
        let mut a = market("a", 1500, 1000, 2000, 0.50, 0.70, 0.30);
        a.exec.as_mut().unwrap().last_agent_call = T - 10;
        let s = snap(vec![a]);
        let mut v = vault(&s);
        v["last_agent_action"] = json!(T - 30);
        v["markets"][0]["corridor_set"] = json!(false);
        v["markets"][0]["slice_max"] = json!("1");
        let d = decide_market(&s, &s.markets[0], &screens(&s), None, &v);
        assert_eq!(d.amount, 1, "clamped to the slice maximum");
        let names: Vec<&str> = prechecks(&d, &s, &v, PK, T).into_iter().filter(|c| !c.ok).map(|c| c.name).collect();
        assert_eq!(names, vec!["vault_cooldown_elapsed", "vault_corridor_flag_set", "executor_cooldown_elapsed"]);
    }

    // ---- 44: judgment relevance

    #[test]
    fn a_judgment_whose_screens_are_gone_is_not_relevant() {
        let t: BTreeSet<String> = ["a:feed_stale".to_string()].into();
        assert!(relevant(&t, &["a:feed_stale".to_string(), "b:x".to_string()].into()));
        assert!(!relevant(&t, &BTreeSet::new()));
        assert!(relevant(&BTreeSet::new(), &BTreeSet::new()), "a digest judgment with no screens stays relevant");
    }

    // ---- 46: thresholds in the judge input

    #[test]
    fn judge_input_carries_every_threshold() {
        let mut a = market("a", 1500, 1000, 2000, 0.50, 0.70, 0.30);
        a.feeds.push(Feed { feed: USDC_USD.into(), updated_at: T - 44_517, max_age_s: 86_400, age_s: 44_517, answer_positive: true });
        let s = snap(vec![a]);
        let v = vault(&s);
        let (user, ids) = judge_input(&s, &screens(&s), &json!({"items": []}), &v, T);
        let j: Value = serde_json::from_str(&user).unwrap();
        let m = &j["markets"][0];
        assert_eq!(m["feeds"][1]["max_age_s"], 86_400);
        assert_eq!(m["feeds"][1]["feed"], "USDC/USD");
        assert_eq!(m["liquidation_ltv"], 0.86);
        let p = &m["position"];
        assert_eq!(p["liquidation_ltv"], 0.86);
        assert_eq!(p["ltv"], 0.75);
        let c = &p["corridor"];
        assert_eq!(c["act"], 0.7);
        assert_eq!(c["target"], 0.5);
        assert_eq!(c["floor"], 0.3);
        assert!(c["sell_budget_remaining_weth"].as_f64().unwrap() > 0.0);
        assert!(c["sell_budget_remaining_usd_worst_fill"].as_f64().unwrap() > 0.0);
        assert_eq!(c["max_moves_per_24h"], 2);
        assert_eq!(j["agent_limits"]["executor_cooldown_s"], 60);
        assert_eq!(j["agent_limits"]["vault_cooldown_s"], 120);
        assert_eq!(j["screen_thresholds"]["utilization_above"], 0.95);
        assert_eq!(j["screen_thresholds"]["pool_vs_oracle_abs_pct_above"], 2.0);
        assert!(ids.contains(&"screen:a:ltv_above_act".to_string()));
        assert!(ids.contains(&"chain:a:position".to_string()));
    }

    #[test]
    fn budget_mirrors_the_executor() {
        let mut a = market("a", 1000, 1000, 2000, 0.50, 0.70, 0.30);
        let mut b = market("b", 1000, 3000, 2000, 0.50, 0.70, 0.30);
        b.exec.as_mut().unwrap().corridor.sell_budget = WAD / 10; // the smallest budget applies
        a.exec.as_mut().unwrap().sold_in_window = 100_000_000_000_000_000; // 0.1 WETH sold
        let mut closed = market("c", 1000, 5000, 2000, 0.50, 0.70, 0.30);
        closed.exec.as_mut().unwrap().closed_for_agent = true; // not agent-active: does not count
        let s = snap(vec![a, b, closed]);
        let (bc, _) = budget(&s, &s.markets[0]);
        // (1 + 3 + 0.1) * 10% - 0.1 = 0.31 WETH
        assert_eq!(bc, 310_000_000_000_000_000);
    }
}
