//! The agent's decisions as pure functions over a snapshot (docs/01-design-and-feasibility.md §4.9 steps 3, 6, 7),
//! so the tick loop only does I/O. Everything here mirrors a check of the executor or the vault, or applies the
//! agreement rule; the model never computes an amount.
use crate::chain::{Feed, Ltv, MarketSnap, Snapshot, ETH_USD, USDC_USD, WAD};
use primitive_types::U256;
use serde::Serialize;
use serde_json::{json, Value};
use std::collections::{BTreeMap, BTreeSet};

pub const UTILIZATION_SCREEN: f64 = 0.95;
/// Execution check before any sale (31 Oct, §4.9 step 3): the executor fills a sale no worse than oracle − 1%
/// (CorridorMath.SLIPPAGE_BPS), so with the pool already more than 1% below the oracle the sale reverts `Slippage`.
/// The pool is ours on testnet, so its gap is never a screen and never confirms anything.
pub const SALE_POOL_GAP_MAX: f64 = 0.01;
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
        for f in &m.feeds {
            if !f.fresh() {
                let why = if f.answer_positive { format!("age {} s > {} s", f.age_s, f.max_age_s) } else { "answer not positive".into() };
                fire(&mut out, m, "feed_stale", format!("feed {} {why}", feed_name(&f.feed)));
            }
        }
    }
    out
}

/// Fork rehearsals (build item 43): an anvil fork freezes the Chainlink feeds while block time advances, so
/// `feed_stale` would fire on every fork tick. On a fork it is taken out of the fired screens and returned
/// separately, for the transcript's "frozen on fork" label; on the real network nothing changes.
pub fn split_frozen_on_fork(fired: Vec<Fired>, fork: bool) -> (Vec<Fired>, Vec<Fired>) {
    if !fork {
        return (fired, vec![]);
    }
    fired.into_iter().partition(|f| f.screen != "feed_stale")
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
    /// Some when this discretionary action rests on the agreement rule's text path (no on-chain screen on this market)
    pub text: Option<TextCorroboration>,
}

impl Decision {
    pub fn json(&self) -> Value {
        json!({"action": self.action, "protective": self.protective, "market": self.market.name, "market_id": self.market.id,
            "to": self.to.as_ref().map(|m| &m.name), "amount": self.amount.to_string(), "fraction": self.fraction.to_string(),
            "basis": self.basis, "text_path": self.text.is_some(), "text_corroboration": self.text, "detail": self.detail})
    }
    /// The proposal's reasons (transcript): the basis, whether the text path carried it, and the items it counted.
    pub fn reasons(&self) -> Value {
        json!({"basis": self.basis, "text_path": self.text.is_some(),
            "cited_items": self.text.as_ref().map(|t| t.counted.clone()).unwrap_or_default()})
    }
}

/// A feed item the judge cited in `source_ids`, resolved against the feeds it was given (never against text the
/// model wrote): what the transcript and the proposal's reasons record of it.
#[derive(Serialize, Clone, Debug, PartialEq, Eq)]
pub struct CitedItem {
    pub source_id: String,
    pub feed_id: String,
    pub item_id: String,
    pub title: String,
    pub body_sha256: String,
    pub published: String,
    /// None when `published` could not be read; such an item never counts for the text path
    pub published_unix: Option<u64>,
    pub url: String,
}

/// The cited source ids that name feed items, resolved against `feed` (the poll the judge was given; feeds that
/// failed carry no items). Chain and screen ids, and ids no feed carries, are not feed items and are skipped.
pub fn cited_items(source_ids: &[String], feed: &Value) -> Vec<CitedItem> {
    let items: Vec<&Value> = feed["feeds"].as_array().into_iter().flatten().filter(|f| f["ok"] == true)
        .flat_map(|f| f["items"].as_array().into_iter().flatten()).collect();
    let mut out: Vec<CitedItem> = vec![];
    for id in source_ids {
        if out.iter().any(|c| &c.source_id == id) {
            continue;
        }
        if let Some(i) = items.iter().find(|i| i["id"].as_str() == Some(id.as_str())) {
            let st = |k: &str| i[k].as_str().unwrap_or("").to_string();
            out.push(CitedItem { source_id: id.clone(), feed_id: st("feed_id"), item_id: st("item_id"), title: st("title"),
                body_sha256: st("body_sha256"), published: st("published"), published_unix: i["published_unix"].as_u64(), url: st("link") });
        }
    }
    out
}

/// A judge answer the loop may apply: accepted (schema valid, `provider_tee` signature verified), and still relevant.
#[derive(Serialize, Clone, Debug)]
pub struct Judgment {
    pub action: String,
    pub risk: String,
    /// the market the answer names (`judge::answer_market`); None ("none") only with hold. The judgment applies to
    /// this market only (3 Nov); every other market decides on the rules alone.
    pub market: Option<String>,
    /// the fired screens of the snapshot the judge was given
    pub trigger: BTreeSet<String>,
    pub block: u64,
    /// the feed items among its `source_ids` (`cited_items`)
    pub cited: Vec<CitedItem>,
}

// ------------------------------------------------------------------ the agreement rule's text path (§4.9 step 6)

/// The judge's highest risk level (`judge::RISKS`): text alone acts only at this level.
pub const TEXT_RISK: &str = "critical";
/// Independent sources the text path needs.
pub const TEXT_MIN_SOURCES: usize = 2;
/// A cited item counts only if it was published at most this long before the snapshot's block time.
pub const TEXT_WINDOW_SECS: u64 = 86_400;
/// ... and not more than this after it (clock skew); a scheduled-event item dated in the future does not count.
pub const TEXT_FUTURE_SKEW_SECS: u64 = 300;
/// The judge actions text alone can carry: `leave_market` (to `migrate`, else `exit`) and `reduce_light`.
pub const TEXT_PATH_ACTIONS: [&str; 2] = ["leave_market", "reduce_light"];

/// Whether the judge's citations corroborate a discretionary action with no on-chain screen on its market.
///
/// **Independent** (the definition the text path uses): two cited items are independent sources when they come
/// from different feeds, i.e. their `feed_id` differs (`feed.id` of a demo-feed file, `base-status` for
/// status.base.org). Each allowlisted feed is one source: any number of items from one feed count once, so a feed
/// cannot corroborate itself. A cited item counts only when it is recent: `published` readable and within
/// [block time − TEXT_WINDOW_SECS, block time + TEXT_FUTURE_SKEW_SECS]. The rule holds when `risk` is
/// TEXT_RISK and at least TEXT_MIN_SOURCES feeds have a counted item.
#[derive(Serialize, Clone, Debug, PartialEq, Eq)]
pub struct TextCorroboration {
    pub ok: bool,
    pub why: String,
    pub risk: String,
    /// the feeds with at least one counted item (each counts once)
    pub independent_feeds: Vec<String>,
    pub counted: Vec<CitedItem>,
    /// (source id, why it did not count)
    pub not_counted: Vec<(String, String)>,
}

pub fn text_corroboration(j: &Judgment, now: u64) -> TextCorroboration {
    let mut feeds: BTreeSet<String> = BTreeSet::new();
    let (mut counted, mut not_counted) = (vec![], vec![]);
    for c in &j.cited {
        let why = match c.published_unix {
            None => Some(format!("published time {:?} not readable", c.published)),
            Some(p) if p > now.saturating_add(TEXT_FUTURE_SKEW_SECS) => Some(format!("published {} s after the block time", p - now)),
            Some(p) if now.saturating_sub(p) > TEXT_WINDOW_SECS => Some(format!("published {} s before the block time, outside the {TEXT_WINDOW_SECS} s window", now - p)),
            Some(_) => None,
        };
        match why {
            Some(w) => not_counted.push((c.source_id.clone(), w)),
            None => {
                feeds.insert(c.feed_id.clone());
                counted.push(c.clone());
            }
        }
    }
    let risk_ok = j.risk == TEXT_RISK;
    let ok = risk_ok && feeds.len() >= TEXT_MIN_SOURCES;
    let why = if !risk_ok {
        format!("risk {} is not {TEXT_RISK}", j.risk)
    } else if feeds.len() < TEXT_MIN_SOURCES {
        format!("{} independent feed(s) with a recent cited item, {TEXT_MIN_SOURCES} needed (items from one feed count once)", feeds.len())
    } else {
        format!("risk {TEXT_RISK} and {} independent feeds cited: {:?}", feeds.len(), feeds)
    };
    TextCorroboration { ok, why, risk: j.risk.clone(), independent_feeds: feeds.into_iter().collect(), counted, not_counted }
}

// ------------------------------------------------------------------ feed-item trigger: items seen (§4.9 step 3)

/// Feed item ids this agent has seen, with the unix time it first saw each. Kept in memory and in the evidence
/// volume (`feeds/seen.json`), so a restart does not treat old items as new.
pub type Seen = BTreeMap<String, u64>;
/// An entry no longer in any feed's newest items is dropped this long after it was first seen.
pub const SEEN_KEEP_SECS: u64 = 30 * 86_400;

/// One poll's bookkeeping: returns the ids of items not seen before (a judge trigger, like a new screen), records
/// them as seen at `now`, writes each item's `first_seen` into `feed` (the judge sees it), and prunes old entries.
/// Feeds that failed carry no items and change nothing.
pub fn note_poll(seen: &mut Seen, feed: &mut Value, now: u64) -> Vec<String> {
    let mut new = vec![];
    let mut present = BTreeSet::new();
    for f in feed["feeds"].as_array_mut().into_iter().flatten().filter(|f| f["ok"] == true) {
        for i in f["items"].as_array_mut().into_iter().flatten() {
            let Some(id) = i["id"].as_str().map(|x| x.to_string()) else { continue };
            let first = *seen.entry(id.clone()).or_insert_with(|| {
                new.push(id.clone());
                now
            });
            i["first_seen"] = json!(first);
            present.insert(id);
        }
    }
    seen.retain(|id, t| present.contains(id) || now.saturating_sub(*t) <= SEEN_KEEP_SECS);
    new
}

/// Whether a judge call is wanted after this tick: `Some(independent)` where `independent` means the call does not
/// depend on a screen staying on (a digest or a new feed item); a call wanted only for new screens is dropped when
/// they go. The deterministic twin never wants one.
pub fn want_judge(prev: Option<bool>, new_screens: bool, digest: bool, new_feed_items: bool, twin: bool) -> Option<bool> {
    if twin {
        return None;
    }
    if new_screens || digest || new_feed_items {
        Some(prev.unwrap_or(false) || digest || new_feed_items)
    } else {
        prev
    }
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
        text: None,
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
    let confirm: Vec<&Fired> = fired.iter().filter(|f| f.market == m.name).collect();
    let is_protective = protective(s, m);
    // the judgment applies only to the market it names (3 Nov): here it is set aside, and the rules apply alone
    let elsewhere = judged.filter(|j| j.market.as_deref() != Some(m.name.as_str()));
    let judged = judged.filter(|j| j.market.as_deref() == Some(m.name.as_str()));
    let not_here = elsewhere.map(|j| match &j.market {
        Some(o) => format!("the judgment ({}) names market {o}, not this one: rules only", j.action),
        None => format!("the judgment ({}) names no market: rules only", j.action),
    });
    let action = judged.map(|j| j.action.as_str());
    let detail = json!({"ltv": m.ltv, "target": wad(c.target), "act": wad(c.act), "floor": wad(c.floor), "lltv": wad(m.lltv),
        "debt": m.debt.to_string(), "collateral_value": m.collateral_value.to_string(),
        "amount_to_target": to_target.to_string(), "amount_to_floor": floor_room.to_string(),
        "sell_budget_remaining_collateral": budget_coll.to_string(), "sell_budget_remaining_value": budget_value.to_string(),
        "slice_max": slice.to_string(), "session_start": x.session_start, "judge_action": action,
        "judge_market": judged.or(elsewhere).map(|j| j.market.as_deref().unwrap_or(crate::judge::NO_MARKET)),
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
            text: None,
        }
    };
    if is_protective {
        // rule, independent of the judge: a protective session sells to target (§3); the executor clamps again
        return reduce(to_target, "rule: LTV above the act line (or a protective session still above target), protective reduce to target", true);
    }
    // the agreement rule: the judge plus a fired on-chain screen on this market (unchanged), or, with no screen
    // here, the judge plus two independent text sources (the text path, `text_corroboration`)
    let mut text: Option<TextCorroboration> = None;
    if confirm.is_empty() {
        let Some(j) = judged.filter(|j| TEXT_PATH_ACTIONS.contains(&j.action.as_str())) else {
            let basis = match &not_here {
                Some(n) => format!("no fired on-chain screen on this market: no discretionary action; {n}"),
                None => "no fired on-chain screen on this market: no discretionary action".into(),
            };
            return none(&basis, detail);
        };
        let tc = text_corroboration(j, s.timestamp);
        if !tc.ok {
            let mut d = detail;
            d["text_corroboration"] = json!(tc);
            return none(&format!("no fired on-chain screen on this market and the text path does not hold: {}", tc.why), d);
        }
        text = Some(tc);
    }
    let path = if text.is_some() { "two independent text sources (text path)" } else { "on-chain screen" };
    let mut d = match action {
        None => none(not_here.as_deref().unwrap_or("no judgment to apply on this tick: rules only"), detail),
        Some("reduce_to_target") => reduce(to_target.min(budget_value).min(floor_room), "judge reduce_to_target + on-chain screen; clamped to budget and floor", false),
        Some("reduce_light") => reduce((budget_value / REDUCE_LIGHT_SHARE).min(floor_room), &format!("judge reduce_light + {path}: 1/4 of the remaining budget, clamped to floor"), false),
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
                    basis: format!("judge leave_market + {path}: migrate to the approved market that can take it"),
                    detail,
                    text: None,
                },
                _ if c.exit_allowed => Decision {
                    action: Act::Exit,
                    protective: false,
                    market: m.clone(),
                    to: None,
                    amount: 0,
                    fraction: 0,
                    basis: format!("judge leave_market + {path}, no approved market can take the position: exit"),
                    detail,
                    text: None,
                },
                _ => none("leave_market, but no destination qualifies and exit is off", detail),
            }
        }
        Some(_) => none("judge hold: nothing to do", detail),
    };
    if d.action != Act::None {
        d.text = text;
    }
    d
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

/// The pool-versus-oracle execution check before a sale (`reduce`, `exit`; 31 Oct): Some(reason) when the pool's
/// price is more than SALE_POOL_GAP_MAX below the oracle, so the executor would revert the sale with `Slippage`. The
/// proposal is then skipped and the decision re-runs on the cooldown boundary. A pool above the oracle fills better
/// than the bound and is not a reason to skip; an unknown pool price is recorded and not a reason either (the
/// executor still checks the fill). `migrate` sells nothing and is never skipped here.
pub fn sale_blocked_by_pool(d: &Decision) -> Option<String> {
    if !matches!(d.action, Act::Reduce | Act::Exit) {
        return None;
    }
    let g = d.market.pool_gap?;
    (g < -SALE_POOL_GAP_MAX).then(|| {
        format!("pool {:.2} is {:.2}% below the oracle {:.2} USD, beyond the executor's 1% fill bound: the sale would revert Slippage; skipped, re-checked on the cooldown boundary",
            d.market.pool_usd.unwrap_or(0.0), -g * 100.0, d.market.oracle_usd)
    })
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

// ------------------------------------------------------------------ registration: funding (item 32), re-registration

/// Gas prepaid on `register_agent`: the vault's receipt is 113.7 Tgas and the transaction 131 Tgas with its refund
/// receipts (P3), so 130 Tgas, not 300.
pub const REGISTER_GAS: u64 = 130_000_000_000_000;
/// yoctoNEAR per gas that NEAR charges against the signer's balance for prepaid gas when it accepts a transaction
/// (the pessimistic gas price). Run 2, 27 Oct: `NotEnoughBalance { cost: 0.30118166 NEAR }` for 300 Tgas, i.e.
/// 1.00394e9 per gas; rounded up.
pub const PESSIMISTIC_YOCTO_PER_GAS: u128 = 1_004_000_000;
/// The implicit account's own storage (one full-access key, about 182 bytes at 1e19 yoctoNEAR per byte), rounded up.
pub const ACCOUNT_STORAGE_YOCTO: u128 = 2_000_000_000_000_000_000_000; // 0.002 NEAR
/// One registration burns 0.0116 NEAR (P3, 131 Tgas); the margin is 1.5 of those, so what is left after the first
/// registration still passes the submission check of the next re-registration.
pub const REGISTRATION_MARGIN_YOCTO: u128 = 17_400_000_000_000_000_000_000; // 0.0174 NEAR

#[derive(Serialize, Clone, Copy, Debug, PartialEq, Eq)]
pub struct Funding {
    pub prepaid_gas: u64,
    /// prepaid gas x the pessimistic gas price
    pub gas_yocto: u128,
    pub storage_yocto: u128,
    pub margin_yocto: u128,
    /// what NEAR checks at submission: gas + storage
    pub at_submission_yocto: u128,
    /// what the agent waits for before its first registration: gas + storage + margin
    pub total_yocto: u128,
}

pub fn registration_funding(prepaid_gas: u64) -> Funding {
    let gas_yocto = prepaid_gas as u128 * PESSIMISTIC_YOCTO_PER_GAS;
    let at_submission_yocto = gas_yocto + ACCOUNT_STORAGE_YOCTO;
    Funding { prepaid_gas, gas_yocto, storage_yocto: ACCOUNT_STORAGE_YOCTO, margin_yocto: REGISTRATION_MARGIN_YOCTO,
        at_submission_yocto, total_yocto: at_submission_yocto + REGISTRATION_MARGIN_YOCTO }
}

/// yoctoNEAR as "0.14992" NEAR (five decimals, rounded up so a printed amount is never short).
pub fn near_str(yocto: u128) -> String {
    let unit = 10u128.pow(19); // 1e-5 NEAR
    let n = yocto.div_ceil(unit);
    format!("{}.{:05}", n / 100_000, n % 100_000)
}

/// Re-attest when under this much of the 7-day registration remains (31 Oct).
pub const REREGISTER_BEFORE_SECS: u64 = 86_400;

/// Whether this agent should call `register_agent` again now: the vault lists our key and its registration has
/// under 24 h left (or has lapsed). Never when the vault lists another key or none: re-registering then would
/// displace another agent or undo the owner's revoke.
pub fn reregistration_due(vault: &Value, our_pk: &str, now: u64) -> bool {
    let a = &vault["agent"];
    if a["public_key"].as_str() != Some(our_pk) {
        return false;
    }
    match a["expires_at"].as_u64() {
        Some(exp) => exp.saturating_sub(now) < REREGISTER_BEFORE_SECS,
        None => false,
    }
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
        let mut o = json!({"source_id": id, "market": m.name, "market_id": m.id, "utilization": r4(m.utilization),
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
            json!({"source_id": id, "detail": f.detail})
        })
        .collect();
    // every feed that was read, with its kind and its publisher's notice; a feed that failed is left out
    let text_feeds: Vec<Value> = feed["feeds"]
        .as_array()
        .cloned()
        .unwrap_or_default()
        .into_iter()
        .filter(|f| f["ok"] == true)
        .map(|f| {
            let items: Vec<Value> = f["items"]
                .as_array()
                .cloned()
                .unwrap_or_default()
                .into_iter()
                .map(|i| {
                    ids.push(i["id"].as_str().unwrap_or("").to_string());
                    json!({"source_id": i["id"], "item_id": i["item_id"], "published": i["published"],
                        "first_seen_by_agent_utc": i["first_seen"].as_u64().map(crate::iso), "text": i["text"]})
                })
                .collect();
            json!({"source": f["url"], "feed_id": f["feed_id"], "kind": f["kind"], "publisher": f["publisher"], "notice": f["notice"],
                "untrusted": true, "items": items})
        })
        .collect();
    let user = json!({"now_utc": crate::iso(s.timestamp), "base_block": s.block, "markets": markets, "screens_fired": sc,
        "screen_thresholds": {"utilization_above": UTILIZATION_SCREEN, "feed_stale_when_age_s_above": "max_age_s of each feed"},
        "execution_checks": {"sale_skipped_when_pool_below_oracle_pct": SALE_POOL_GAP_MAX * 100.0,
            "pool_note": "the pool price is not a screen: on testnet the team sets the pool"},
        "agent_limits": {"executor_cooldown_s": exec_cooldown(s), "vault_cooldown_s": vault["cooldown_secs"],
            "agent_may_act_in_s": free_at.saturating_sub(now)},
        "agreement_rule": {"discretionary_action_needs": "a fired screen on that market, or (text path) the conditions below",
            "text_path_actions": TEXT_PATH_ACTIONS, "text_path_risk": TEXT_RISK, "text_path_independent_feeds_min": TEXT_MIN_SOURCES,
            "independent_means": "different feed_id; items from one feed count once",
            "item_published_within_s_before_now": TEXT_WINDOW_SECS},
        "text_feeds": text_feeds});
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

    fn judgment(action: &str, market: &str, trigger: &[&str]) -> Judgment {
        Judgment { action: action.into(), risk: "high".into(), market: Some(market.into()), trigger: trigger.iter().map(|s| s.to_string()).collect(), block: 100, cited: vec![] }
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
        let j = judgment("reduce_to_target", "a", &["a:utilization_above_95pct"]);
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

    // ---- 3 Nov: a judgment applies only to the market it names

    #[test]
    fn on_the_screen_path_the_judgment_acts_only_on_the_market_it_names() {
        let mut a = market("a", 1100, 1000, 2000, 0.50, 0.70, 0.30);
        a.utilization = 0.97;
        let mut b = market("b", 1100, 1000, 2000, 0.50, 0.70, 0.30);
        b.utilization = 0.97; // a screen on both markets
        let s = snap(vec![a, b]);
        let v = vault(&s);
        let fired = screens(&s);
        let j = judgment("reduce_to_target", "a", &["a:utilization_above_95pct", "b:utilization_above_95pct"]);
        let ds = decide_all(&s, &fired, Some(&j), &v);
        let (da, db) = (ds.iter().find(|d| d.market.name == "a").unwrap(), ds.iter().find(|d| d.market.name == "b").unwrap());
        assert_eq!(da.action, Act::Reduce);
        assert_eq!(da.detail["judge_market"], "a");
        assert_eq!(db.action, Act::None, "b's own screen does not carry a judgment about a");
        assert_eq!(db.basis, "the judgment (reduce_to_target) names market a, not this one: rules only");
        assert_eq!(db.detail["judge_action"], Value::Null);
        assert_eq!(db.detail["judge_market"], "a");
        // screen on a only, the judge names b (no screen there, no text): nothing on either market
        let mut s1 = s.clone();
        s1.markets[1].utilization = 0.5;
        let fired = screens(&s1);
        let j = judgment("leave_market", "b", &["a:utilization_above_95pct"]);
        let ds = decide_all(&s1, &fired, Some(&j), &v);
        assert!(ds.iter().all(|d| d.action == Act::None), "{:?}", ds.iter().map(|d| d.json()).collect::<Vec<_>>());
        assert!(ds[0].basis.contains("names market b, not this one"), "{}", ds[0].basis);
        // the protective rule on a market is unchanged by a judgment about another
        let mut s2 = s.clone();
        s2.markets[1] = market("b", 1500, 1000, 2000, 0.50, 0.70, 0.30); // LTV 0.75 above act
        let ds = decide_all(&s2, &screens(&s2), Some(&judgment("hold", "a", &[])), &v);
        let db = ds.iter().find(|d| d.market.name == "b").unwrap();
        assert_eq!((db.action, db.protective), (Act::Reduce, true));
        // a hold that names no market applies nowhere
        let none = Judgment { market: None, ..judgment("hold", "a", &[]) };
        let ds = decide_all(&s, &screens(&s), Some(&none), &v);
        assert!(ds.iter().all(|d| d.action == Act::None && d.basis.contains("names no market")));
    }

    // ---- 31 Oct: the pool gap is an execution check, not a screen

    #[test]
    fn pool_gap_is_no_screen_and_confirms_nothing() {
        let mut a = market("a", 1100, 1000, 2000, 0.50, 0.70, 0.30);
        a.pool_gap = Some(-0.05);
        a.pool_usd = Some(1900.0);
        let s = snap(vec![a]);
        let fired = screens(&s);
        assert!(fired.is_empty(), "{fired:?}");
        assert!(!standing(&s, &fired), "a gap alone keeps nothing standing, so it never triggers the judge");
        let d = decide_market(&s, &s.markets[0], &fired, Some(&judgment("reduce_light", "a", &[])), &vault(&s));
        assert_eq!(d.action, Act::None);
    }

    #[test]
    fn a_sale_into_a_pool_more_than_1pct_below_the_oracle_is_skipped() {
        let mut a = market("a", 1500, 1000, 2000, 0.50, 0.70, 0.30); // LTV 0.75: protective reduce
        let check = |a: &MarketSnap| {
            let s = snap(vec![a.clone()]);
            let d = decide_market(&s, &s.markets[0], &screens(&s), None, &vault(&s));
            assert_eq!(d.action, Act::Reduce);
            sale_blocked_by_pool(&d)
        };
        a.pool_gap = Some(-0.0101);
        a.pool_usd = Some(1979.8);
        let why = check(&a).expect("skipped");
        assert!(why.contains("Slippage") && why.contains("1.01% below"), "{why}");
        a.pool_gap = Some(-0.0099);
        assert_eq!(check(&a), None, "within the 1% fill bound");
        a.pool_gap = Some(0.05);
        assert_eq!(check(&a), None, "a pool above the oracle fills better than the bound");
        a.pool_gap = None;
        assert_eq!(check(&a), None, "no pool price: the executor still checks the fill");
        // exit is a sale; migrate is not
        a.pool_gap = Some(-0.03);
        let s = snap(vec![a.clone()]);
        let mut d = decide_market(&s, &s.markets[0], &screens(&s), None, &vault(&s));
        d.action = Act::Exit;
        assert!(sale_blocked_by_pool(&d).is_some());
        d.action = Act::Migrate;
        assert_eq!(sale_blocked_by_pool(&d), None);
    }

    // ---- 43: fork rehearsals

    #[test]
    fn feed_stale_is_labelled_frozen_on_a_fork_only() {
        let mut a = market("a", 1500, 1000, 2000, 0.50, 0.70, 0.30);
        a.feeds[0].age_s = 5000; // over the 1,200 s heartbeat
        let s = snap(vec![a]);
        let all = screens(&s);
        assert!(all.iter().any(|f| f.screen == "feed_stale"));
        let (fired, frozen) = split_frozen_on_fork(all.clone(), false);
        assert_eq!(fired, all, "the real network: identical");
        assert!(frozen.is_empty());
        let (fired, frozen) = split_frozen_on_fork(all, true);
        assert!(fired.iter().all(|f| f.screen != "feed_stale"));
        assert!(fired.iter().any(|f| f.screen == "ltv_above_act"), "other screens still fire on a fork");
        assert_eq!(frozen.len(), 1);
        assert_eq!(frozen[0].screen, "feed_stale");
    }

    // ---- 10: the deterministic twin decides with no judgment, so it never proposes a discretionary action

    #[test]
    fn without_a_judgment_only_the_protective_rule_acts() {
        let mut a = market("a", 1100, 1000, 2000, 0.50, 0.70, 0.30); // LTV 0.55: not protective
        a.utilization = 0.99; // a confirming screen that would let a judgment act
        let b = market("b", 1500, 1000, 2000, 0.50, 0.70, 0.30); // LTV 0.75: protective
        let s = snap(vec![a, b]);
        let fired = screens(&s);
        let ds = decide_all(&s, &fired, None, &vault(&s));
        for d in &ds {
            assert!(d.action == Act::None || d.protective, "{:?}", d.json());
        }
        assert_eq!(ds.iter().find(|d| d.market.name == "b").unwrap().action, Act::Reduce);
    }

    // ---- 32: funding; 31 Oct: re-registration

    #[test]
    fn registration_funding_is_computed_from_the_gas_attached() {
        let f = registration_funding(REGISTER_GAS);
        assert_eq!(f.gas_yocto, 130_520_000_000_000_000_000_000); // 130 Tgas x 1.004e9 = 0.13052 NEAR
        assert_eq!(near_str(f.total_yocto), "0.14992");
        assert_eq!(near_str(f.at_submission_yocto), "0.13252");
        assert!(f.total_yocto <= 150_000_000_000_000_000_000_000, "within the relayer's 0.15 NEAR top-up");
        // what failed in run 2: 0.1 NEAR against 300 Tgas prepaid
        assert!(registration_funding(300_000_000_000_000).at_submission_yocto > 301_181_659_988_054_500_000_000);
        // after one registration (0.0116 NEAR burnt) the re-registration still passes the submission check
        assert!(f.total_yocto - 11_600_000_000_000_000_000_000 >= f.at_submission_yocto);
        assert_eq!(near_str(1), "0.00001", "rounded up");
    }

    #[test]
    fn reregistration_is_due_under_24_hours_and_only_for_our_key() {
        let v = |pk: &str, exp: u64| json!({"agent": {"public_key": pk, "expires_at": exp}});
        assert!(!reregistration_due(&v(PK, T + 7 * 86_400), PK, T), "fresh registration");
        assert!(!reregistration_due(&v(PK, T + 86_400), PK, T), "exactly 24 h left: not yet");
        assert!(reregistration_due(&v(PK, T + 86_399), PK, T));
        assert!(reregistration_due(&v(PK, T - 10), PK, T), "lapsed: still ours, re-register");
        assert!(!reregistration_due(&v("ed25519:other", T + 10), PK, T), "never displace another agent");
        assert!(!reregistration_due(&json!({"agent": null}), PK, T), "never undo a revoke");
        assert!(!reregistration_due(&json!({"agent": {"public_key": PK}}), PK, T));
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
        let j = judgment("leave_market", "a", &["a:utilization_above_95pct"]);
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
        let d = decide_market(&s, &s.markets[0], &fired, Some(&judgment("reduce_to_target", "a", &[])), &vault(&s));
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
        assert_eq!((m["market"].as_str(), m["market_id"].as_str()), (Some("a"), Some(s.markets[0].id.as_str())), "label and id, so the judge can name it");
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
        assert_eq!(j["execution_checks"]["sale_skipped_when_pool_below_oracle_pct"], 1.0);
        assert!(j["screen_thresholds"].get("pool_vs_oracle_abs_pct_above").is_none());
        assert!(ids.contains(&"screen:a:ltv_above_act".to_string()));
        assert!(ids.contains(&"chain:a:position".to_string()));
    }

    #[test]
    fn judge_input_carries_each_feed_with_its_notice_and_kind() {
        let s = snap(vec![market("a", 1100, 1000, 2000, 0.50, 0.70, 0.30)]);
        let body = std::fs::read("testdata/feeds/governance.json").unwrap();
        let demo = crate::judge::parse_demo_feed(crate::judge::DEMO_FEEDS[1], &body).unwrap();
        let feeds = json!({"feeds": [demo, {"url": crate::judge::DEMO_FEEDS[0], "ok": false, "error": "HTTP 404 Not Found", "items": []}]});
        let (user, ids) = judge_input(&s, &[], &feeds, &vault(&s), T);
        let j: Value = serde_json::from_str(&user).unwrap();
        let tf = j["text_feeds"].as_array().unwrap();
        assert_eq!(tf.len(), 1, "the missing feed is left out");
        assert_eq!(tf[0]["kind"], "protocol announcements (demo)");
        assert!(tf[0]["notice"].as_str().unwrap().starts_with("Published by the cmdk team for the testnet demo"));
        assert_eq!(tf[0]["untrusted"], true);
        assert!(ids.contains(&"feed:cmdk-testnet-governance:governance-2026-11-03-001".to_string()));
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

    // ---- step 3: the agreement rule's text path and the feed-item trigger

    /// 2026-11-06T12:00:00Z, the `published` of the scene-five items in testdata/scene5
    const SCENE_PUB: u64 = 1_793_966_400;

    fn cited(feed: &str, item: &str, published_unix: u64) -> CitedItem {
        CitedItem { source_id: format!("feed:{feed}:{item}"), feed_id: feed.into(), item_id: item.into(), title: "t".into(),
            body_sha256: "00".repeat(32), published: crate::iso(published_unix), published_unix: Some(published_unix), url: String::new() }
    }

    /// A text-path judgment about the live market (the scene's position).
    fn text_judgment(action: &str, risk: &str, cited: Vec<CitedItem>) -> Judgment {
        Judgment { action: action.into(), risk: risk.into(), market: Some("live".into()), trigger: BTreeSet::new(), block: 100, cited }
    }

    /// The scene's markets: a calm position on live (LTV 0.55 under act 0.70, utilization 0.5, fresh feeds) and an
    /// empty backup market with USDC to lend; block time 10 minutes after the items were published.
    fn calm_live_and_backup() -> Snapshot {
        let live = market("live", 1100, 1000, 2000, 0.50, 0.70, 0.30);
        let backup = market("backup", 0, 0, 2000, 0.50, 0.70, 0.30);
        let mut s = snap(vec![live, backup]);
        s.timestamp = SCENE_PUB + 600;
        s
    }

    #[test]
    fn the_text_path_needs_two_independent_recent_feeds_and_critical_risk() {
        let s = calm_live_and_backup();
        let v = vault(&s);
        assert!(screens(&s).is_empty(), "calm: no screen anywhere");
        let act = |j: &Judgment| decide_market(&s, &s.markets[0], &[], Some(j), &v);
        let gov = cited("cmdk-testnet-governance", "g1", SCENE_PUB);
        let gov2 = cited("cmdk-testnet-governance", "g2", SCENE_PUB);
        let sec = cited("cmdk-testnet-venue-status", "s1", SCENE_PUB);

        // two items of one feed count once: no action
        let d = act(&text_judgment("leave_market", "critical", vec![gov.clone(), gov2.clone()]));
        assert_eq!(d.action, Act::None);
        assert!(d.basis.contains("1 independent feed(s)"), "{}", d.basis);
        assert!(d.text.is_none());
        assert_eq!(d.detail["text_corroboration"]["independent_feeds"], json!(["cmdk-testnet-governance"]));

        // two feeds: migrate, recorded as the text path with the counted items
        let d = act(&text_judgment("leave_market", "critical", vec![gov.clone(), sec.clone()]));
        assert_eq!(d.action, Act::Migrate);
        assert_eq!(d.to.as_ref().unwrap().name, "backup");
        assert_eq!(d.fraction, WAD);
        let t = d.text.as_ref().expect("text path");
        assert!(t.ok);
        assert_eq!(t.independent_feeds, vec!["cmdk-testnet-governance", "cmdk-testnet-venue-status"]);
        assert!(d.basis.contains("two independent text sources (text path)"), "{}", d.basis);
        assert_eq!(d.json()["text_path"], true);

        // risk below the highest level: no action
        for r in ["low", "elevated", "high"] {
            assert_eq!(act(&text_judgment("leave_market", r, vec![gov.clone(), sec.clone()])).action, Act::None, "{r}");
        }

        // stale: one item older than 24 h before the block time leaves one feed; both stale, none
        let old = cited("cmdk-testnet-venue-status", "s0", s.timestamp - TEXT_WINDOW_SECS - 1);
        let d = act(&text_judgment("leave_market", "critical", vec![gov.clone(), old.clone()]));
        assert_eq!(d.action, Act::None);
        assert!(d.detail["text_corroboration"]["not_counted"][0][1].as_str().unwrap().contains("outside the 86400 s window"));
        let edge = cited("cmdk-testnet-venue-status", "s0", s.timestamp - TEXT_WINDOW_SECS);
        assert_eq!(act(&text_judgment("leave_market", "critical", vec![gov.clone(), edge])).action, Act::Migrate, "exactly 24 h counts");
        // dated in the future (a scheduled event) beyond the skew: does not count
        let future = cited("base-status", "f1", s.timestamp + TEXT_FUTURE_SKEW_SECS + 1);
        assert_eq!(act(&text_judgment("leave_market", "critical", vec![gov.clone(), future])).action, Act::None);
        // unreadable time: does not count
        let mut unknown = sec.clone();
        unknown.published_unix = None;
        assert_eq!(act(&text_judgment("leave_market", "critical", vec![gov.clone(), unknown])).action, Act::None);

        // reduce_light can rest on text; reduce_to_target and hold cannot
        let d = act(&text_judgment("reduce_light", "critical", vec![gov.clone(), sec.clone()]));
        assert_eq!(d.action, Act::Reduce);
        assert!(!d.protective && d.text.is_some());
        assert_eq!(d.amount, (budget(&s, &s.markets[0]).1 / REDUCE_LIGHT_SHARE).min(amount_to(&s.markets[0], s.markets[0].exec.as_ref().unwrap().corridor.floor)));
        for a in ["reduce_to_target", "hold"] {
            assert_eq!(act(&text_judgment(a, "critical", vec![gov.clone(), sec.clone()])).action, Act::None, "{a}");
        }

        // migrate impossible (backup has no USDC to lend) and exit allowed: exit; exit off too: nothing
        let mut s2 = s.clone();
        s2.markets[1].liquidity = 0;
        let j = text_judgment("leave_market", "critical", vec![gov.clone(), sec.clone()]);
        let d = decide_market(&s2, &s2.markets[0], &[], Some(&j), &v);
        assert_eq!(d.action, Act::Exit);
        assert!(d.text.is_some());
        s2.markets[0].exec.as_mut().unwrap().corridor.exit_allowed = false;
        assert_eq!(decide_market(&s2, &s2.markets[0], &[], Some(&j), &v).action, Act::None);
    }

    #[test]
    fn on_the_text_path_the_judgment_acts_only_on_the_named_market_with_debt() {
        // two calm positions (live and demo, no screen anywhere) and an empty backup market
        let mut s = calm_live_and_backup();
        let mut demo = market("demo", 1000, 1000, 2000, 0.50, 0.70, 0.30);
        demo.loan_token = "0xother".into(); // not a destination for live
        s.markets.push(demo);
        let v = vault(&s);
        assert!(screens(&s).is_empty());
        let items = vec![cited("cmdk-testnet-governance", "g1", SCENE_PUB), cited("cmdk-testnet-venue-status", "s1", SCENE_PUB)];
        let on = |m: Option<&str>| Judgment { market: m.map(|x| x.to_string()), ..text_judgment("leave_market", "critical", items.clone()) };
        // names live: live migrates, demo decides on the rules alone
        let ds = decide_all(&s, &[], Some(&on(Some("live"))), &v);
        assert_eq!(ds.len(), 2, "the markets with debt");
        let live = ds.iter().find(|d| d.market.name == "live").unwrap();
        assert_eq!((live.action, live.to.as_ref().map(|t| t.name.as_str())), (Act::Migrate, Some("backup")));
        let d = ds.iter().find(|d| d.market.name == "demo").unwrap();
        assert_eq!(d.action, Act::None);
        assert!(d.basis.contains("names market live, not this one"), "{}", d.basis);
        // names demo: only demo is judged (exit: no destination shares its loan token), live does nothing
        let ds = decide_all(&s, &[], Some(&on(Some("demo"))), &v);
        assert_eq!(ds.iter().find(|d| d.market.name == "demo").unwrap().action, Act::Exit);
        assert_eq!(ds.iter().find(|d| d.market.name == "live").unwrap().action, Act::None);
        // names backup, which has no debt: nothing anywhere
        let ds = decide_all(&s, &[], Some(&on(Some("backup"))), &v);
        assert!(ds.iter().all(|d| d.action == Act::None));
        assert_eq!(pick(&ds), None);
    }

    #[test]
    fn with_an_onchain_screen_the_onchain_path_applies_as_before() {
        let mut s = calm_live_and_backup();
        s.markets[0].utilization = 0.99;
        let v = vault(&s);
        let fired = screens(&s);
        assert!(fired.iter().any(|f| f.key() == "live:utilization_above_95pct"));
        // the judge cites no text at all and risk is only high: the on-chain screen carries it, as in step 2
        let j = judgment("leave_market", "live", &["live:utilization_above_95pct"]);
        let d = decide_market(&s, &s.markets[0], &fired, Some(&j), &v);
        assert_eq!(d.action, Act::Migrate);
        assert!(d.text.is_none());
        assert_eq!(d.basis, "judge leave_market + on-chain screen: migrate to the approved market that can take it");
        let d = decide_market(&s, &s.markets[0], &fired, Some(&judgment("reduce_to_target", "live", &[])), &v);
        assert_eq!(d.basis, "judge reduce_to_target + on-chain screen; clamped to budget and floor");
        assert_eq!(decide_market(&s, &s.markets[0], &fired, Some(&judgment("reduce_light", "live", &[])), &v).basis,
            "judge reduce_light + on-chain screen: 1/4 of the remaining budget, clamped to floor");
    }

    fn scene_feeds(venue_file: &str) -> Value {
        let f = |name: &str, url: &str| crate::judge::parse_demo_feed(url, &std::fs::read(format!("testdata/scene5/{name}.json")).unwrap()).unwrap();
        json!({"fetched_at": SCENE_PUB + 300, "feeds": [
            {"url": crate::judge::FEED_URL, "ok": false, "error": "HTTP 503", "items": []},
            f(venue_file, crate::judge::DEMO_FEEDS[0]), f("governance", crate::judge::DEMO_FEEDS[1])]})
    }

    /// Fifth scene, "Warning before the numbers move" (§6, 31 Oct), replayed offline from fixtures.
    #[test]
    fn scene_five_warning_before_the_numbers_move() {
        let s = calm_live_and_backup();
        let v = vault(&s);
        let fired = screens(&s);
        assert!(fired.is_empty() && !standing(&s, &fired), "every on-chain number calm");

        // the poll: the agent has seen the two 3 Nov items; the two scene items are new and want a judge call
        let mut seen: Seen = [("feed:cmdk-testnet-governance:governance-2026-11-03-001".to_string(), SCENE_PUB - 86_400),
            ("feed:cmdk-testnet-venue-status:venue-status-2026-11-03-001".to_string(), SCENE_PUB - 86_400)].into();
        let mut feed = scene_feeds("venue-status");
        let new = note_poll(&mut seen, &mut feed, SCENE_PUB + 300);
        let gov = "feed:cmdk-testnet-governance:governance-2026-11-06-001";
        let sec = "feed:cmdk-testnet-venue-status:venue-status-2026-11-06-001";
        assert_eq!(new, vec![sec.to_string(), gov.to_string()]);
        assert_eq!(want_judge(None, false, false, !new.is_empty(), false), Some(true), "a new item wants the judge");

        // the judge input carries both items with feed id, kind, notice and item id
        let (user, ids) = judge_input(&s, &fired, &feed, &v, s.timestamp);
        let u: Value = serde_json::from_str(&user).unwrap();
        let tf = u["text_feeds"].as_array().unwrap();
        assert_eq!(tf.len(), 2, "the failed status feed is left out");
        let g = tf.iter().find(|f| f["feed_id"] == "cmdk-testnet-governance").unwrap();
        assert_eq!(g["kind"], "protocol announcements (demo)");
        assert!(g["notice"].as_str().unwrap().starts_with("Published by the cmdk team"));
        assert_eq!(g["items"][1]["item_id"], "governance-2026-11-06-001");
        assert!(g["items"][1]["first_seen_by_agent_utc"].is_string());
        assert_eq!(u["agreement_rule"]["text_path_risk"], "critical");

        // the judge's answer, through the strict schema
        let answer = json!({"risk": "critical", "action": "leave_market", "market": "live",
            "reasons": ["governance deprecates the live market's oracle", "a security alert from a second feed reports the same"],
            "source_ids": [gov, sec, "chain:live:market"]});
        assert_eq!(crate::judge::validate(&answer, &ids), Ok(()));
        let sids: Vec<String> = answer["source_ids"].as_array().unwrap().iter().map(|x| x.as_str().unwrap().to_string()).collect();
        let cited = cited_items(&sids, &feed);
        assert_eq!(cited.len(), 2, "the chain id is not a feed item");
        assert_eq!(cited[0].title, "Live market oracle deprecated");
        assert_eq!(cited[0].body_sha256.len(), 64);
        assert_eq!(cited[0].published_unix, Some(SCENE_PUB));
        let market = crate::judge::answer_market(&answer);
        assert_eq!(market.as_deref(), Some("live"));
        let j = Judgment { action: "leave_market".into(), risk: "critical".into(), market, trigger: fired_set(&fired), block: s.block, cited };
        assert!(relevant(&j.trigger, &fired_set(&fired)));

        // the agent: migrate(live -> backup, 1e18), on the text path, with the cited items in its reasons
        let ds = decide_all(&s, &fired, Some(&j), &v);
        let i = pick(&ds).expect("one proposal");
        let d = &ds[i];
        assert_eq!((d.action, d.market.name.as_str(), d.to.as_ref().map(|t| t.name.as_str()), d.fraction), (Act::Migrate, "live", Some("backup"), WAD));
        assert_eq!(d.fraction, 1_000_000_000_000_000_000);
        let r = d.reasons();
        assert_eq!(r["text_path"], true);
        let items = r["cited_items"].as_array().unwrap();
        assert_eq!(items.len(), 2);
        for k in ["feed_id", "item_id", "title", "body_sha256"] {
            assert!(items.iter().all(|x| x[k].as_str().map(|v| !v.is_empty()).unwrap_or(false)), "{k}");
        }
        assert!(refusals(&prechecks(d, &s, &v, PK, s.timestamp)).is_empty(), "{:?}", refusals(&prechecks(d, &s, &v, PK, s.timestamp)));
        assert_eq!(sale_blocked_by_pool(d), None, "migrate sells nothing");

        // the deterministic twin on the same fixtures: no judge wanted, no judgment, nothing
        assert_eq!(want_judge(None, false, false, true, true), None);
        let twin = decide_all(&s, &fired, None, &v);
        assert!(twin.iter().all(|d| d.action == Act::None), "{:?}", twin.iter().map(|d| d.json()).collect::<Vec<_>>());
        assert_eq!(pick(&twin), None);
    }

    #[test]
    fn an_injected_item_in_one_feed_moves_nothing() {
        let s = calm_live_and_backup();
        let v = vault(&s);
        let feed = scene_feeds("venue-status-injected");
        let (_, ids) = judge_input(&s, &[], &feed, &v, s.timestamp);
        let inj = "feed:cmdk-testnet-venue-status:venue-status-2026-11-06-002";
        assert!(ids.contains(&inj.to_string()));
        // whatever the model was talked into, citing only the injected item (one source) moves nothing
        for action in crate::judge::ACTIONS {
            for risk in crate::judge::RISKS {
                let a = json!({"risk": risk, "action": action, "market": "live", "reasons": ["the item says so"], "source_ids": [inj]});
                assert_eq!(crate::judge::validate(&a, &ids), Ok(()));
                let j = Judgment { action: action.into(), risk: risk.into(), market: Some("live".into()), trigger: BTreeSet::new(), block: s.block, cited: cited_items(&[inj.to_string()], &feed) };
                for d in decide_all(&s, &[], Some(&j), &v) {
                    assert_eq!(d.action, Act::None, "{action}/{risk}: {}", d.basis);
                }
            }
        }
        // the item's own instruction is no option: the schema has no borrow or withdraw, and no free numbers
        for bad in [json!({"risk": "critical", "action": "borrow_max", "reasons": [], "source_ids": [inj]}),
                    json!({"risk": "critical", "action": "leave_market", "reasons": [], "source_ids": [inj], "withdraw_to": "0xdead"})] {
            assert!(crate::judge::validate(&bad, &ids).is_err());
        }
        // and no decision is ever anything but none, reduce, migrate or exit
        let gov = "feed:cmdk-testnet-governance:governance-2026-11-03-001";
        let j = Judgment { action: "leave_market".into(), risk: "critical".into(), market: Some("live".into()), trigger: BTreeSet::new(), block: s.block,
            cited: cited_items(&[inj.to_string(), gov.to_string()], &feed) };
        for d in decide_all(&s, &[], Some(&j), &v) {
            let a = d.json()["action"].as_str().unwrap().to_string();
            assert!(["none", "reduce", "migrate", "exit"].contains(&a.as_str()), "{a}");
        }
    }

    #[test]
    fn seen_items_trigger_once_and_survive_a_restart() {
        let feed0 = scene_feeds("venue-status");
        let mut seen = Seen::new();
        let mut f = feed0.clone();
        let new = note_poll(&mut seen, &mut f, 1000);
        assert_eq!(new.len(), 4, "a first boot sees every item as new");
        assert_eq!(f["feeds"][1]["items"][0]["first_seen"], 1000);
        let mut f = feed0.clone();
        assert!(note_poll(&mut seen, &mut f, 1060).is_empty(), "the next poll: nothing new");
        assert_eq!(f["feeds"][1]["items"][0]["first_seen"], 1000, "first seen is kept");
        // the seen file round-trips: a restart does not re-trigger
        let file = json!({"version": 1, "seen": seen}).to_string();
        let mut back: Seen = serde_json::from_value(serde_json::from_str::<Value>(&file).unwrap()["seen"].clone()).unwrap();
        let mut f = feed0.clone();
        assert!(note_poll(&mut back, &mut f, 2000).is_empty());
        // a failed poll (404) changes nothing and triggers nothing
        let mut failed = json!({"feeds": [{"url": "u", "ok": false, "error": "HTTP 404 Not Found", "items": []}]});
        assert!(note_poll(&mut back, &mut failed, 3000).is_empty());
        assert_eq!(back.len(), 4);
        // entries gone from the feeds are dropped after SEEN_KEEP_SECS
        note_poll(&mut back, &mut failed, 1000 + SEEN_KEEP_SECS + 1);
        assert!(back.is_empty());
    }

    #[test]
    fn judge_wanted_for_screens_digests_and_feed_items() {
        assert_eq!(want_judge(None, false, false, false, false), None);
        assert_eq!(want_judge(None, true, false, false, false), Some(false), "screens only: dropped when they go");
        assert_eq!(want_judge(None, false, true, false, false), Some(true));
        assert_eq!(want_judge(None, false, false, true, false), Some(true), "a new feed item does not depend on a screen");
        assert_eq!(want_judge(Some(false), false, false, true, false), Some(true));
        assert_eq!(want_judge(Some(true), true, false, false, false), Some(true));
        assert_eq!(want_judge(Some(false), false, false, false, false), Some(false), "kept until a slot is free");
        for (a, b, c) in [(true, false, false), (false, true, false), (false, false, true), (true, true, true)] {
            assert_eq!(want_judge(None, a, b, c, true), None, "the twin never");
        }
    }
}
