//! Base Sepolia: reads at one pinned block from two RPC providers, Morpho's share math, the EIP-1559 bytes the
//! vault builds, and broadcasting. Exact integer math (U256) for everything a proposal depends on; f64 only for
//! the ratios that feed screens and the judge's inputs.
//!
//! Reads are strict (build items 48, 49): a call that fails, returns too few words or a value outside its type is a
//! read error, never a zero, and any read error makes the tick a failed tick. Every call of both phases is sent to
//! both providers at the same pinned block (and the block's hash is compared); any difference is a failed tick.
//! The planning, decoding and comparison steps are pure functions over the raw results, so they are unit-tested.
use anyhow::{anyhow, bail, Result};
use primitive_types::U256;
use serde::Serialize;
use serde_json::{json, Value};
use sha3::{Digest, Keccak256};
use std::collections::{HashMap, VecDeque};
use std::time::{Duration, Instant};

pub const CHAIN_ID: u64 = 84532;
pub const MORPHO: &str = "0xbbbbbbbbbb9cc5e90e3b3af64bdaf62c37eeffcb";
pub const ETH_USD: &str = "0x4adc67696ba383f43dd60a9e78f2c97fbbfc7cb1";
pub const USDC_USD: &str = "0xd30e2101a97dcbaebcbc04f14c3f624e67a35165";
pub const LIVE_POOL: &str = "0x57183717a087d2fe3ad890873877244c3b96156c";
/// Base's L1 fee oracle (OP Stack predeploy). `getL1Fee(bytes)` takes the unsigned RLP transaction.
pub const GAS_PRICE_ORACLE: &str = "0x420000000000000000000000000000000000000f";
/// The vault's bounds (`vault/src/evm.rs`: GAS_LIMIT_MAX, MAX_FEE_PER_GAS_MAX): a proposal outside them is refused there.
pub const GAS_LIMIT_MAX: u64 = 1_000_000;
pub const MAX_FEE_MAX: u64 = 2_000_000_000;
pub const DEADLINE_SECS: u64 = 600;
pub const WAD: u128 = 1_000_000_000_000_000_000;
/// Gas margins over `eth_estimateGas` at the latest block (build item 11): calls that swap against a pool that can
/// move between estimate and landing (`reduce`, `exit`) get 1.5x; `migrate` (flash loan, no swap) 1.3x. In percent.
pub const GAS_MARGIN_SWAP_PCT: u64 = 150;
pub const GAS_MARGIN_PCT: u64 = 130;

/// Markets the prototype knows by id; pool and feeds are used only while the executor is not deployed
/// (otherwise they are read from `Executor.config`).
pub fn market_name(id: &str) -> &'static str {
    match id {
        "0xa25ac5171b7e500b82653d3750b91bb44e2409c32ca3c9e80b0db077e8a4535c" => "live",
        "0x99d43c0f2a0eb6172774598a4e34e7eee09caa502692c2b2df0f40e1e3c5ea75" => "backup",
        "0x55872b53dba6147b6badf9d4277bc166dfb7d5f8ac8d491da093960dbef6d585" => "demo",
        _ => "unknown",
    }
}
fn fallback_pool_and_feeds(name: &str) -> (Option<String>, Vec<(String, u64)>) {
    match name {
        "live" | "backup" => (Some(LIVE_POOL.into()), vec![(ETH_USD.into(), 1200), (USDC_USD.into(), 86400)]),
        _ => (None, vec![]),
    }
}

pub fn keccak(b: &[u8]) -> [u8; 32] {
    Keccak256::digest(b).into()
}
pub fn sel(sig: &str) -> String {
    hex::encode(&keccak(sig.as_bytes())[..4])
}
fn w_hex(h: &str) -> String {
    format!("{:0>64}", h.trim_start_matches("0x").to_lowercase())
}
fn w_u(x: U256) -> String {
    format!("{:064x}", x)
}
/// ABI encoding of a single dynamic `bytes` argument (offset, length, data padded to 32 bytes).
fn w_bytes(b: &[u8]) -> String {
    let mut out = format!("{}{}", w_u(U256::from(32u8)), w_u(U256::from(b.len())));
    out.push_str(&hex::encode(b));
    let pad = (32 - b.len() % 32) % 32;
    out.push_str(&"00".repeat(pad));
    out
}
/// Lenient hex quantity (receipt fields that are only recorded).
pub fn hex_u(v: &Value) -> u128 {
    u128::from_str_radix(v.as_str().unwrap_or("0x0").trim_start_matches("0x"), 16).unwrap_or(0)
}
/// Strict hex quantity: anything a decision or a transaction depends on.
pub fn hex_strict(v: &Value, what: &str) -> Result<u128> {
    let s = v.as_str().ok_or_else(|| anyhow!("{what}: not a hex string: {v}"))?;
    u128::from_str_radix(s.trim_start_matches("0x"), 16).map_err(|e| anyhow!("{what}: {s}: {e}"))
}

// ------------------------------------------------------------------ Morpho math (SharesMathLib, MathLib)

pub fn mul_div_down(a: U256, b: U256, d: U256) -> U256 {
    a * b / d
}
pub fn mul_div_up(a: U256, b: U256, d: U256) -> U256 {
    (a * b + d - 1) / d
}
pub fn to_assets_up(shares: U256, total_assets: U256, total_shares: U256) -> U256 {
    mul_div_up(shares, total_assets + 1, total_shares + 1_000_000)
}
fn w_taylor_compounded(x: U256, n: U256) -> U256 {
    let wad = U256::from(WAD);
    let first = x * n;
    let second = mul_div_down(first, first, wad * 2);
    let third = mul_div_down(second, first, wad * 3);
    first + second + third
}
fn sat_u128(x: U256) -> u128 {
    if x > U256::from(u128::MAX) {
        u128::MAX
    } else {
        x.low_u128()
    }
}

// ------------------------------------------------------------------ snapshot

#[derive(Serialize, Clone, Default, Debug)]
pub struct Corridor {
    pub target: u128,
    pub act: u128,
    pub floor: u128,
    pub sell_budget: u128,
    pub migrate_allowed: bool,
    pub exit_allowed: bool,
    pub set: bool,
}

#[derive(Serialize, Clone, Default, Debug)]
pub struct ExecState {
    /// `Executor.corridors(vault, id)`: the source of truth for whether the agent may act (build item 10)
    pub corridor: Corridor,
    /// `soldInWindow(vault, collateral token)`: discretionary sales in the rolling window, per collateral token
    pub sold_in_window: u128,
    pub moves_in_window: u128,
    pub last_agent_call: u64,
    pub session_start: u64,
    pub closed_for_agent: bool,
    pub cooldown: u64,
}

impl ExecState {
    /// A corridor in force: set on the executor and not closed by an exit (the executor's `_agentCorridor`).
    pub fn in_force(&self) -> bool {
        self.corridor.set && !self.closed_for_agent
    }
}

/// LTV of a position (build item 49): an explicit state, never a sentinel number.
#[derive(Serialize, Clone, Copy, Debug, PartialEq, Eq, Default)]
#[serde(tag = "state", content = "wad", rename_all = "snake_case")]
pub enum Ltv {
    /// no debt (LTV 0)
    #[default]
    NoDebt,
    /// debt over collateral value at the oracle, WAD, rounded up; capped at LTV_CAP
    Ratio(u128),
    /// debt with a collateral value of 0 at the oracle price: nothing can be sold to repay it
    NoCollateralValue,
}

/// LTV is reported up to 10x (10 WAD); a position that far underwater is shown as 10x.
pub const LTV_CAP: u128 = 10 * WAD;

impl Ltv {
    pub fn of(debt: u128, collateral_value: u128) -> Ltv {
        if debt == 0 {
            Ltv::NoDebt
        } else if collateral_value == 0 {
            Ltv::NoCollateralValue
        } else {
            Ltv::Ratio(sat_u128(mul_div_up(U256::from(debt), U256::from(WAD), U256::from(collateral_value))).min(LTV_CAP))
        }
    }
    /// The ratio for ordering and display (NoDebt 0; NoCollateralValue above any ratio).
    pub fn order_key(&self) -> u128 {
        match self {
            Ltv::NoDebt => 0,
            Ltv::Ratio(x) => *x,
            Ltv::NoCollateralValue => u128::MAX,
        }
    }
    pub fn above(&self, line: u128) -> bool {
        match self {
            Ltv::NoDebt => false,
            Ltv::Ratio(x) => *x > line,
            Ltv::NoCollateralValue => true,
        }
    }
    pub fn as_f64(&self) -> Option<f64> {
        match self {
            Ltv::NoDebt => Some(0.0),
            Ltv::Ratio(x) => Some(*x as f64 / 1e18),
            Ltv::NoCollateralValue => None,
        }
    }
}

#[derive(Serialize, Clone, Default, Debug)]
pub struct Feed {
    pub feed: String,
    pub updated_at: u64,
    pub max_age_s: u64,
    pub age_s: u64,
    /// latestRoundData answer > 0 (the executor's `_requireFresh` treats anything else as stale)
    pub answer_positive: bool,
}

impl Feed {
    pub fn fresh(&self) -> bool {
        self.answer_positive && self.age_s <= self.max_age_s
    }
}

#[derive(Serialize, Clone, Default, Debug)]
pub struct MarketSnap {
    pub name: String,
    pub id: String,
    pub exists: bool,
    pub loan_token: String,
    pub collateral_token: String,
    pub oracle: String,
    pub irm: String,
    pub lltv: u128,
    /// totalSupplyAssets, totalSupplyShares, totalBorrowAssets, totalBorrowShares, lastUpdate, fee (stored, before accrual)
    pub market: [u128; 6],
    pub borrow_shares: u128,
    pub collateral: u128,
    pub oracle_price: String,
    pub borrow_rate_per_sec: u128,
    /// Computed (exact): debt after accrual to the block's timestamp, rounded up as Morpho does.
    pub total_borrow_accrued: u128,
    pub total_supply_accrued: u128,
    pub debt: u128,
    /// collateral value in loan-token units at the oracle price
    pub collateral_value: u128,
    pub ltv: Ltv,
    pub utilization: f64,
    pub borrow_apr: f64,
    pub oracle_usd: f64,
    pub pool: Option<String>,
    pub pool_usd: Option<f64>,
    pub pool_gap: Option<f64>,
    pub feeds: Vec<Feed>,
    pub exec: Option<ExecState>,
    pub liquidity: u128,
}

impl MarketSnap {
    pub fn price(&self) -> U256 {
        U256::from_dec_str(&self.oracle_price).unwrap_or_default()
    }
}

#[derive(Serialize, Clone, Default, Debug)]
pub struct Snapshot {
    pub block: u64,
    pub block_hash: String,
    pub timestamp: u64,
    pub base_fee: u128,
    pub user: String,
    pub executor: String,
    pub executor_deployed: bool,
    pub markets: Vec<MarketSnap>,
    pub rpc_primary: String,
    pub rpc_second: String,
    /// keys whose raw results differ between the two providers at this block (empty = they agree)
    pub rpc_disagree: Vec<String>,
    pub rpc_second_error: Option<String>,
    /// calls that failed, returned too little data or an impossible value (oracle 0): a non-empty list makes the
    /// tick a failed tick (run 2, 27 Oct: a rate-limited call decoded as 0 and fired a false screen)
    pub read_errors: Vec<String>,
    /// Base's L1 data fee (build item 12): `GasPriceOracle.l1BaseFee()` and `getL1Fee` of a template `reduce`
    /// transaction (gas limit and fee caps at the vault's bounds), in wei. Informational: a failure here is
    /// recorded in `info_errors` and does not fail the tick.
    pub l1_base_fee: Option<u128>,
    pub l1_fee_reduce_template: Option<u128>,
    pub info_errors: Vec<String>,
}

// ------------------------------------------------------------------ calls, results, decoding (pure)

#[derive(Clone, Debug)]
pub enum Req {
    Call { to: String, data: String },
    Code { addr: String },
}

#[derive(Clone, Debug)]
pub struct Call {
    pub key: String,
    pub req: Req,
}

fn call(key: String, to: &str, data: String) -> Call {
    Call { key, req: Req::Call { to: to.to_string(), data } }
}

pub type Results = HashMap<String, Result<Vec<u8>, String>>;

/// Keys whose reads are informational: a failure is recorded, the tick goes on, and they are not compared.
/// (`cmp/` keys are read only to compare the providers; decode never reads them.)
fn is_info(k: &str) -> bool {
    k.starts_with("info/")
}

/// The raw bytes of `k`, or the read error.
fn raw<'a>(r: &'a Results, k: &str) -> Result<&'a [u8], String> {
    match r.get(k) {
        Some(Ok(b)) if !b.is_empty() => Ok(b),
        Some(Ok(_)) => Err(format!("{k}: empty result")),
        Some(Err(e)) => Err(format!("{k}: {}", e.chars().take(160).collect::<String>())),
        None => Err(format!("{k}: missing")),
    }
}
/// At least `n` 32-byte words, or a read error (a short answer is never padded with zeros).
fn words(r: &Results, k: &str, n: usize) -> Result<Vec<U256>, String> {
    let b = raw(r, k)?;
    if b.len() < 32 * n {
        return Err(format!("{k}: {} bytes, expected at least {}", b.len(), 32 * n));
    }
    Ok((0..n).map(|i| U256::from_big_endian(&b[32 * i..32 * (i + 1)])).collect())
}
fn as_u128(x: U256, k: &str) -> Result<u128, String> {
    if x > U256::from(u128::MAX) {
        return Err(format!("{k}: value {x} above uint128"));
    }
    Ok(x.low_u128())
}
fn as_u64(x: U256, k: &str) -> Result<u64, String> {
    if x > U256::from(u64::MAX) {
        return Err(format!("{k}: value {x} above uint64"));
    }
    Ok(x.low_u64())
}
fn as_bool(x: U256, k: &str) -> Result<bool, String> {
    match x.low_u64() {
        0 if x.is_zero() => Ok(false),
        1 if x == U256::one() => Ok(true),
        _ => Err(format!("{k}: {x} is not a bool")),
    }
}
fn as_addr(x: U256, k: &str) -> Result<String, String> {
    let mut b = [0u8; 32];
    x.to_big_endian(&mut b);
    if b[..12].iter().any(|v| *v != 0) {
        return Err(format!("{k}: {x:x} is not an address"));
    }
    Ok(format!("0x{}", hex::encode(&b[12..])))
}
const ZERO_ADDR: &str = "0x0000000000000000000000000000000000000000";

/// Phase 1: everything that does not depend on another result.
pub fn plan1(user: &str, executor: &str, executor_deployed: bool, market_ids: &[String]) -> Vec<Call> {
    let u = w_hex(user);
    let mut calls = vec![];
    for id in market_ids {
        let idw = w_hex(id);
        calls.push(call(format!("{id}/params"), MORPHO, format!("{}{idw}", sel("idToMarketParams(bytes32)"))));
        calls.push(call(format!("{id}/market"), MORPHO, format!("{}{idw}", sel("market(bytes32)"))));
        calls.push(call(format!("{id}/position"), MORPHO, format!("{}{idw}{u}", sel("position(bytes32,address)"))));
        if executor_deployed {
            calls.push(call(format!("{id}/corridor"), executor, format!("{}{u}{idw}", sel("corridors(address,bytes32)"))));
            calls.push(call(format!("{id}/config"), executor, format!("{}{idw}", sel("config(bytes32)"))));
            calls.push(call(format!("{id}/session"), executor, format!("{}{u}{idw}", sel("sessionStart(address,bytes32)"))));
            calls.push(call(format!("{id}/closed"), executor, format!("{}{u}{idw}", sel("closedForAgent(address,bytes32)"))));
        }
    }
    if executor_deployed {
        calls.push(call("x/moves".into(), executor, format!("{}{u}", sel("movesInWindow(address)"))));
        calls.push(call("x/last".into(), executor, format!("{}{u}", sel("lastAgentCall(address)"))));
        calls.push(call("x/cooldown".into(), executor, sel("COOLDOWN()")));
    }
    // the primary's view of the executor's code decided the plan; the second provider must see the same code
    calls.push(Call { key: "cmp/executor_code".into(), req: Req::Code { addr: executor.to_string() } });
    calls.push(call("info/l1_base_fee".into(), GAS_PRICE_ORACLE, sel("l1BaseFee()")));
    if let Some(id) = market_ids.first() {
        if let Ok(tx) = template_reduce_tx(id, executor) {
            calls.push(call("info/l1_fee_template".into(), GAS_PRICE_ORACLE, format!("{}{}", sel("getL1Fee(bytes)"), w_bytes(&tx))));
        }
    }
    calls
}

/// An unsigned `reduce` at the vault's gas and fee bounds, for the per-tick L1 fee figure.
fn template_reduce_tx(id: &str, executor: &str) -> Result<Vec<u8>> {
    let p = json!({"nonce": 1000, "gas_limit": GAS_LIMIT_MAX, "max_fee_per_gas": MAX_FEE_MAX, "max_priority_fee_per_gas": 1_000_000});
    unsigned_1559(&p, executor, &calldata_reduce(id, 1_000_000_000, 2_000_000_000))
}

/// Phase 1 decoded: one partial MarketSnap per market id (Morpho params, totals, position, executor state except
/// the per-token sale window), plus the read errors.
pub fn decode1(market_ids: &[String], executor_deployed: bool, r: &Results) -> (Vec<MarketSnap>, Vec<String>) {
    let mut errs = vec![];
    let mut out = vec![];
    let mut global = None;
    if executor_deployed {
        let g = (|| -> Result<(u128, u64, u64), String> {
            let moves = as_u128(words(r, "x/moves", 1)?[0], "x/moves")?;
            let last = as_u64(words(r, "x/last", 1)?[0], "x/last")?;
            let cooldown = as_u64(words(r, "x/cooldown", 1)?[0], "x/cooldown")?;
            Ok((moves, last, cooldown))
        })();
        match g {
            Ok(g) => global = Some(g),
            Err(e) => errs.push(e),
        }
    }
    for id in market_ids {
        let mut s = MarketSnap { name: market_name(id).into(), id: id.clone(), ..Default::default() };
        let res = (|| -> Result<(), String> {
            let k = format!("{id}/params");
            let p = words(r, &k, 5)?;
            s.loan_token = as_addr(p[0], &k)?;
            s.collateral_token = as_addr(p[1], &k)?;
            s.oracle = as_addr(p[2], &k)?;
            s.irm = as_addr(p[3], &k)?;
            s.lltv = as_u128(p[4], &k)?;
            let k = format!("{id}/market");
            let m = words(r, &k, 6)?;
            for i in 0..6 {
                s.market[i] = as_u128(m[i], &k)?;
            }
            s.exists = s.market[4] != 0;
            let k = format!("{id}/position");
            let pos = words(r, &k, 3)?;
            s.borrow_shares = as_u128(pos[1], &k)?;
            s.collateral = as_u128(pos[2], &k)?;
            let (mut pool, mut feeds) = fallback_pool_and_feeds(&s.name);
            if executor_deployed {
                let k = format!("{id}/corridor");
                let c = words(r, &k, 7)?;
                let k2 = format!("{id}/config");
                // config = (params(5 words), pool, feeds[2], maxAges[2]); all static, so inline
                let cfg = words(r, &k2, 10)?;
                let pl = as_addr(cfg[5], &k2)?;
                pool = Some(pl).filter(|x| x != ZERO_ADDR);
                feeds = vec![];
                for i in 0..2 {
                    let f = as_addr(cfg[6 + i], &k2)?;
                    if f != ZERO_ADDR {
                        feeds.push((f, as_u64(cfg[8 + i], &k2)?));
                    }
                }
                let ks = format!("{id}/session");
                let kc = format!("{id}/closed");
                let (moves, last, cooldown) = global.unwrap_or_default();
                s.exec = Some(ExecState {
                    corridor: Corridor {
                        target: as_u128(c[0], &k)?,
                        act: as_u128(c[1], &k)?,
                        floor: as_u128(c[2], &k)?,
                        sell_budget: as_u128(c[3], &k)?,
                        migrate_allowed: as_bool(c[4], &k)?,
                        exit_allowed: as_bool(c[5], &k)?,
                        set: as_bool(c[6], &k)?,
                    },
                    sold_in_window: 0, // phase 2: needs the collateral token
                    moves_in_window: moves,
                    last_agent_call: last,
                    session_start: as_u64(words(r, &ks, 1)?[0], &ks)?,
                    closed_for_agent: as_bool(words(r, &kc, 1)?[0], &kc)?,
                    cooldown,
                });
            }
            s.pool = pool;
            s.feeds = feeds.into_iter().map(|(f, a)| Feed { feed: f, max_age_s: a, ..Default::default() }).collect();
            Ok(())
        })();
        if let Err(e) = res {
            errs.push(e);
        }
        out.push(s);
    }
    (out, errs)
}

/// Phase 2: oracle, IRM, feeds, pool and the per-token sale window need phase 1.
pub fn plan2(snaps: &[MarketSnap], user: &str, executor: &str, token0_known: &HashMap<String, String>) -> Vec<Call> {
    let u = w_hex(user);
    let mut calls = vec![];
    for s in snaps.iter().filter(|s| s.exists) {
        let id = &s.id;
        calls.push(call(format!("{id}/price"), &s.oracle, sel("price()")));
        let mp = format!("{}{}{}{}{}", w_hex(&s.loan_token), w_hex(&s.collateral_token), w_hex(&s.oracle), w_hex(&s.irm), w_u(U256::from(s.lltv)));
        let mk: String = s.market.iter().map(|x| w_u(U256::from(*x))).collect();
        calls.push(call(
            format!("{id}/rate"),
            &s.irm,
            format!("{}{mp}{mk}", sel("borrowRateView((address,address,address,address,uint256),(uint128,uint128,uint128,uint128,uint128,uint128))")),
        ));
        for f in &s.feeds {
            calls.push(call(format!("{id}/feed/{}", f.feed), &f.feed, sel("latestRoundData()")));
        }
        if let Some(pl) = &s.pool {
            calls.push(call(format!("{id}/slot0"), pl, sel("slot0()")));
            if !token0_known.contains_key(pl) {
                calls.push(call(format!("{id}/token0"), pl, sel("token0()")));
            }
        }
        if s.exec.is_some() {
            // since 2 Nov the window is per caller and collateral token
            calls.push(call(format!("{id}/sold"), executor, format!("{}{u}{}", sel("soldInWindow(address,address)"), w_hex(&s.collateral_token))));
        }
    }
    calls
}

/// Phase 2 decoded into the snapshots, with every derived value (accrual, debt, value, LTV, pool gap, feed ages).
pub fn decode2(snaps: &mut [MarketSnap], timestamp: u64, r: &Results, token0_known: &mut HashMap<String, String>) -> Vec<String> {
    let mut errs = vec![];
    for s in snaps.iter_mut().filter(|s| s.exists) {
        let id = s.id.clone();
        let res = (|| -> Result<(), String> {
            let k = format!("{id}/price");
            let price = words(r, &k, 1)?[0];
            if price.is_zero() {
                return Err(format!("{k}: oracle price 0"));
            }
            s.oracle_price = price.to_string();
            s.oracle_usd = price.to_string().parse::<f64>().unwrap_or(0.0) / 1e24;
            let k = format!("{id}/rate");
            let rate = words(r, &k, 1)?[0];
            s.borrow_rate_per_sec = as_u128(rate, &k)?;
            s.borrow_apr = s.borrow_rate_per_sec as f64 * 31_536_000.0 / 1e18;
            if let Some(x) = s.exec.as_mut() {
                let k = format!("{id}/sold");
                x.sold_in_window = as_u128(words(r, &k, 1)?[0], &k)?;
            }
            // accrue interest to this block, as Morpho's _accrueInterest would (fee 0 on these markets)
            let elapsed = U256::from(timestamp.saturating_sub(s.market[4] as u64));
            let tb = U256::from(s.market[2]);
            let interest = mul_div_down(tb, w_taylor_compounded(rate, elapsed), U256::from(WAD));
            s.total_borrow_accrued = as_u128(tb + interest, "accrued borrow")?;
            s.total_supply_accrued = as_u128(U256::from(s.market[0]) + interest, "accrued supply")?;
            s.liquidity = s.total_supply_accrued.saturating_sub(s.total_borrow_accrued);
            s.debt = as_u128(to_assets_up(U256::from(s.borrow_shares), U256::from(s.total_borrow_accrued), U256::from(s.market[3])), "debt")?;
            s.collateral_value = sat_u128(mul_div_down(U256::from(s.collateral), price, U256::exp10(36)));
            s.ltv = Ltv::of(s.debt, s.collateral_value);
            s.utilization = if s.total_supply_accrued == 0 { 0.0 } else { s.total_borrow_accrued as f64 / s.total_supply_accrued as f64 };
            for f in s.feeds.iter_mut() {
                let k = format!("{id}/feed/{}", f.feed);
                let rd = words(r, &k, 5)?; // roundId, answer, startedAt, updatedAt, answeredInRound
                let answer_negative = rd[1].bit(255);
                f.answer_positive = !answer_negative && !rd[1].is_zero();
                f.updated_at = as_u64(rd[3], &k)?;
                f.age_s = timestamp.saturating_sub(f.updated_at);
            }
            if let Some(pl) = s.pool.clone() {
                let k0 = format!("{id}/token0");
                if r.contains_key(&k0) {
                    let t0 = as_addr(words(r, &k0, 1)?[0], &k0)?;
                    token0_known.insert(pl.clone(), t0);
                }
                let token0 = token0_known.get(&pl).cloned().ok_or_else(|| format!("{k0}: unknown"))?;
                let k = format!("{id}/slot0");
                let sq = words(r, &k, 7)?[0].to_string().parse::<f64>().unwrap_or(0.0) / 2f64.powi(96);
                let p = sq * sq; // token1 raw per token0 raw
                if p > 0.0 {
                    // loan token 6 decimals, collateral 18
                    let usd = if token0 == s.collateral_token { p * 1e12 } else { 1e12 / p };
                    s.pool_usd = Some(usd);
                    s.pool_gap = Some(usd / s.oracle_usd - 1.0);
                }
            }
            Ok(())
        })();
        if let Err(e) = res {
            errs.push(e);
        }
    }
    errs
}

/// Keys whose raw results differ between the providers (an error on either side counts as a difference unless
/// both failed, which is a read error already). Informational keys are excluded.
pub fn compare(calls: &[Call], ra: &Results, rb: &Results) -> Vec<String> {
    calls
        .iter()
        .filter(|c| !is_info(&c.key))
        .filter(|c| ra.get(&c.key).map(|x| x.as_ref().ok()) != rb.get(&c.key).map(|x| x.as_ref().ok()))
        .map(|c| c.key.clone())
        .collect()
}

/// An informational value (L1 fee); its failure goes to `info_errors` and does not fail the tick.
fn info_value(r: &Results, k: &str, errs: &mut Vec<String>) -> Option<u128> {
    match words(r, k, 1).and_then(|w| as_u128(w[0], k)) {
        Ok(v) => Some(v),
        Err(e) => {
            if r.contains_key(k) {
                errs.push(e);
            }
            None
        }
    }
}

// ------------------------------------------------------------------ per-provider pacing

/// sepolia.base.org allows 25 calls per second and counts each call of a batch (build item 48). The same limit
/// is applied to every provider, over a sliding window slightly longer than a second.
pub const RATE_PER_WINDOW: usize = 25;
pub const RATE_WINDOW: Duration = Duration::from_millis(1050);
/// at most this many calls in one batch request
pub const BATCH_MAX: usize = 20;

/// How long to wait before `n` more calls fit the window, given what was sent (time, calls); None = now.
pub fn pace_wait(sent: &VecDeque<(Instant, usize)>, now: Instant, n: usize) -> Option<Duration> {
    let live: Vec<&(Instant, usize)> = sent.iter().filter(|(t, _)| now.duration_since(*t) < RATE_WINDOW).collect();
    let used: usize = live.iter().map(|x| x.1).sum();
    if used + n.min(RATE_PER_WINDOW) <= RATE_PER_WINDOW {
        return None;
    }
    // wait until enough of the oldest entries leave the window
    let mut freed = 0;
    for (t, k) in live {
        freed += k;
        if used - freed + n.min(RATE_PER_WINDOW) <= RATE_PER_WINDOW {
            return Some(RATE_WINDOW.saturating_sub(now.duration_since(*t)));
        }
    }
    Some(RATE_WINDOW)
}

pub struct Base {
    http: reqwest::Client,
    pub urls: Vec<String>,
    token0: std::sync::Mutex<HashMap<String, String>>,
    sent: tokio::sync::Mutex<HashMap<String, VecDeque<(Instant, usize)>>>,
}

impl Base {
    pub fn new(urls: Vec<String>) -> Self {
        Base {
            http: reqwest::Client::builder().timeout(Duration::from_secs(20)).build().expect("http client"),
            urls,
            token0: Default::default(),
            sent: Default::default(),
        }
    }

    /// Waits until `n` calls fit the provider's rate window, then records them as sent.
    async fn pace(&self, url: &str, n: usize) {
        loop {
            let wait = {
                let mut g = self.sent.lock().await;
                let q = g.entry(url.to_string()).or_default();
                let now = Instant::now();
                while q.front().map(|(t, _)| now.duration_since(*t) >= RATE_WINDOW).unwrap_or(false) {
                    q.pop_front();
                }
                match pace_wait(q, now, n) {
                    None => {
                        q.push_back((now, n));
                        return;
                    }
                    Some(w) => w,
                }
            };
            tokio::time::sleep(wait.max(Duration::from_millis(5))).await;
        }
    }

    pub async fn rpc(&self, url: &str, method: &str, params: Value) -> Result<Value> {
        self.pace(url, 1).await;
        let r: Value = self
            .http
            .post(url)
            .header("User-Agent", "cmdk-agent")
            .json(&json!({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}))
            .send()
            .await?
            .json()
            .await?;
        if let Some(e) = r.get("error") {
            bail!("{method}: {e}");
        }
        Ok(r["result"].clone())
    }

    /// Fork detection (build item 43): true only when every provider answers `anvil_nodeInfo`, which an anvil fork
    /// does and a real Base Sepolia endpoint refuses (method not found). Read-only.
    pub async fn all_providers_are_anvil(&self) -> bool {
        for u in &self.urls {
            match self.rpc(u, "anvil_nodeInfo", json!([])).await {
                Ok(v) if v.is_object() => {}
                _ => return false,
            }
        }
        !self.urls.is_empty()
    }

    /// Batched reads at `block`, in chunks of BATCH_MAX paced per provider; results by key (Err = the call's error).
    async fn batch(&self, url: &str, block: u64, calls: &[Call]) -> Result<Results> {
        let mut out = HashMap::new();
        for chunk in calls.chunks(BATCH_MAX) {
            self.pace(url, chunk.len()).await;
            out.extend(self.batch_once(url, block, chunk).await?);
        }
        Ok(out)
    }

    async fn batch_once(&self, url: &str, block: u64, calls: &[Call]) -> Result<Results> {
        let blk = format!("0x{block:x}");
        let body: Vec<Value> = calls
            .iter()
            .enumerate()
            .map(|(i, c)| match &c.req {
                Req::Call { to, data } => {
                    json!({"jsonrpc": "2.0", "id": i, "method": "eth_call", "params": [{"to": to, "data": format!("0x{data}")}, blk]})
                }
                Req::Code { addr } => json!({"jsonrpc": "2.0", "id": i, "method": "eth_getCode", "params": [addr, blk]}),
            })
            .collect();
        let r: Value = self.http.post(url).header("User-Agent", "cmdk-agent").json(&body).send().await?.json().await?;
        let arr = r.as_array().ok_or_else(|| anyhow!("batch reply is not an array: {}", r.to_string().chars().take(300).collect::<String>()))?;
        let mut out = HashMap::new();
        for item in arr {
            let Some(i) = item["id"].as_u64() else { continue };
            let Some(c) = calls.get(i as usize) else { continue };
            let v = match item.get("error") {
                Some(e) => Err(e.to_string()),
                None => match item["result"].as_str() {
                    Some(h) => hex::decode(h.trim_start_matches("0x")).map_err(|e| e.to_string()),
                    None => Err(format!("result is not a hex string: {}", item["result"])),
                },
            };
            out.insert(c.key.clone(), v);
        }
        if out.len() != calls.len() {
            bail!("batch returned {} of {} results", out.len(), calls.len());
        }
        Ok(out)
    }

    pub async fn snapshot(&self, user: &str, executor: &str, market_ids: &[String]) -> Result<Snapshot> {
        let (a, b) = (&self.urls[0], self.urls.get(1).unwrap_or(&self.urls[0]));
        let blk = self.rpc(a, "eth_getBlockByNumber", json!(["latest", false])).await?;
        let block = hex_strict(&blk["number"], "block number")? as u64;
        let timestamp = hex_strict(&blk["timestamp"], "block timestamp")? as u64;
        let block_hash = blk["hash"].as_str().unwrap_or("").to_lowercase();
        let code = self.rpc(a, "eth_getCode", json!([executor, format!("0x{block:x}")])).await?;
        let executor_deployed = code.as_str().map(|s| s.len() > 2).ok_or_else(|| anyhow!("eth_getCode: not a string"))?;
        let mut token0 = self.token0.lock().unwrap_or_else(|e| e.into_inner()).clone();

        let mut disagree = vec![];
        let mut second_error: Option<String> = None;
        let mut info_errors = vec![];
        let calls1 = plan1(user, executor, executor_deployed, market_ids);
        let (ra, rb, bh) = tokio::join!(
            self.batch(a, block, &calls1),
            self.batch(b, block, &calls1),
            self.rpc(b, "eth_getBlockByNumber", json!([format!("0x{block:x}"), false]))
        );
        let ra = ra?;
        match bh {
            Ok(h) if h.is_null() => second_error = Some(format!("second provider has no block {block}")),
            Ok(h) if h["hash"].as_str().map(|x| x.to_lowercase()) != Some(block_hash.clone()) => disagree.push("block hash".into()),
            Ok(_) => {}
            Err(e) => second_error = Some(e.to_string().chars().take(300).collect()),
        }
        let (mut snaps, mut read_errors) = decode1(market_ids, executor_deployed, &ra);
        match &rb {
            Ok(rb) => disagree.extend(compare(&calls1, &ra, rb)),
            Err(e) => second_error = second_error.or(Some(e.to_string().chars().take(300).collect())),
        }
        let l1_base_fee = info_value(&ra, "info/l1_base_fee", &mut info_errors);
        let l1_fee_reduce_template = info_value(&ra, "info/l1_fee_template", &mut info_errors);

        if read_errors.is_empty() {
            let calls2 = plan2(&snaps, user, executor, &token0);
            if !calls2.is_empty() {
                let (r2, r2b) = tokio::join!(self.batch(a, block, &calls2), self.batch(b, block, &calls2));
                let r2 = r2?;
                read_errors.extend(decode2(&mut snaps, timestamp, &r2, &mut token0));
                match &r2b {
                    Ok(r2b) => disagree.extend(compare(&calls2, &r2, r2b)),
                    Err(e) => second_error = second_error.or(Some(e.to_string().chars().take(300).collect())),
                }
            }
        }
        *self.token0.lock().unwrap_or_else(|e| e.into_inner()) = token0;
        Ok(Snapshot {
            block,
            block_hash,
            timestamp,
            base_fee: hex_strict(&blk["baseFeePerGas"], "baseFeePerGas")?,
            user: user.into(),
            executor: executor.into(),
            executor_deployed,
            markets: snaps,
            rpc_primary: a.clone(),
            rpc_second: b.clone(),
            rpc_disagree: disagree,
            rpc_second_error: second_error,
            read_errors,
            l1_base_fee,
            l1_fee_reduce_template,
            info_errors,
        })
    }

    // ------------------------------------------------------------------ transaction parameters and broadcast

    /// One TxParams set: pending nonce, `eth_estimateGas` at the latest block with the margin for this call kind,
    /// capped at the vault's bound (build item 11), 2 x base fee + priority capped at the vault's bound.
    /// Returns (params, estimate or the estimate's error). Called right before the proposal is sent.
    pub async fn tx_params(&self, from: &str, to: &str, data: &str, swap: bool) -> Result<(Value, Result<u64, String>)> {
        let a = &self.urls[0];
        let nonce = hex_strict(&self.rpc(a, "eth_getTransactionCount", json!([from, "pending"])).await?, "nonce")? as u64;
        let blk = self.rpc(a, "eth_getBlockByNumber", json!(["latest", false])).await?;
        let est = self
            .rpc(a, "eth_estimateGas", json!([{"from": from, "to": to, "data": format!("0x{data}")}, "latest"]))
            .await
            .map_err(|e| e.to_string())
            .and_then(|v| hex_strict(&v, "estimate").map(|x| x as u64).map_err(|e| e.to_string()));
        let base = hex_strict(&blk["baseFeePerGas"], "baseFeePerGas")? as u64;
        let prio = hex_strict(&self.rpc(a, "eth_maxPriorityFeePerGas", json!([])).await?, "priority fee")? as u64;
        let (gas, est) = match est {
            Ok(e) => match gas_limit(e, swap) {
                Ok(g) => (g, Ok(e)),
                Err(why) => (GAS_LIMIT_MAX, Err(why)),
            },
            Err(e) => (GAS_LIMIT_MAX, Err(e)),
        };
        let max_fee = (2 * base + prio).min(MAX_FEE_MAX);
        let p = json!({"nonce": nonce, "gas_limit": gas, "max_fee_per_gas": max_fee, "max_priority_fee_per_gas": prio.min(max_fee),
            "estimate_block": hex_u(&blk["number"]) as u64, "margin_pct": if swap { GAS_MARGIN_SWAP_PCT } else { GAS_MARGIN_PCT }});
        Ok((p, est))
    }

    /// `GasPriceOracle.getL1Fee(unsigned tx)` at the latest block, in wei.
    pub async fn l1_fee(&self, unsigned: &[u8]) -> Result<u128> {
        let a = &self.urls[0];
        let data = format!("0x{}{}", sel("getL1Fee(bytes)"), w_bytes(unsigned));
        let v = self.rpc(a, "eth_call", json!([{"to": GAS_PRICE_ORACLE, "data": data}, "latest"])).await?;
        let b = hex::decode(v.as_str().unwrap_or("").trim_start_matches("0x"))?;
        if b.len() < 32 {
            bail!("getL1Fee: {} bytes", b.len());
        }
        let x = U256::from_big_endian(&b[..32]);
        as_u128(x, "getL1Fee").map_err(|e| anyhow!(e))
    }

    /// eth_sendRawTransaction, then the receipt (polled up to 120 s).
    pub async fn broadcast(&self, raw: &str, expect_hash: &str) -> Result<Value> {
        let a = &self.urls[0];
        let h = self.rpc(a, "eth_sendRawTransaction", json!([raw])).await?;
        let h = h.as_str().unwrap_or("").to_lowercase();
        if h != expect_hash.to_lowercase() {
            bail!("RPC hash {h} differs from the vault's hash-to-be {expect_hash}");
        }
        for _ in 0..120 {
            let rc = self.rpc(a, "eth_getTransactionReceipt", json!([h])).await?;
            if !rc.is_null() {
                return Ok(json!({"hash": h, "status": hex_u(&rc["status"]), "block": hex_u(&rc["blockNumber"]),
                    "gas_used": hex_u(&rc["gasUsed"]), "effective_gas_price": hex_u(&rc["effectiveGasPrice"]),
                    "l1_fee": hex_u(&rc["l1Fee"]), "from": rc["from"], "to": rc["to"]}));
            }
            tokio::time::sleep(Duration::from_secs(1)).await;
        }
        bail!("no receipt for {h} after 120 s")
    }
}

/// Gas limit from an estimate (build item 11): 1.5x for calls that swap, 1.3x otherwise, rounded up, capped at the
/// vault's GAS_LIMIT_MAX. An estimate already above the bound cannot be sent.
pub fn gas_limit(estimate: u64, swap: bool) -> Result<u64, String> {
    if estimate == 0 {
        return Err("estimate 0".into());
    }
    if estimate > GAS_LIMIT_MAX {
        return Err(format!("estimate {estimate} above the vault's gas bound {GAS_LIMIT_MAX}"));
    }
    let pct = if swap { GAS_MARGIN_SWAP_PCT } else { GAS_MARGIN_PCT };
    Ok(((estimate as u128 * pct as u128 + 99) / 100).min(GAS_LIMIT_MAX as u128) as u64)
}

// ------------------------------------------------------------------ the vault's transaction, rebuilt

pub fn calldata_reduce(id: &str, amount: u128, deadline: u64) -> String {
    format!("{}{}{}{}", sel("reduce(bytes32,uint256,uint256)"), w_hex(id), w_u(U256::from(amount)), w_u(U256::from(deadline)))
}
pub fn calldata_migrate(from: &str, to: &str, fraction: u128, deadline: u64) -> String {
    format!(
        "{}{}{}{}{}",
        sel("migrate(bytes32,bytes32,uint256,uint256)"),
        w_hex(from),
        w_hex(to),
        w_u(U256::from(fraction)),
        w_u(U256::from(deadline))
    )
}
pub fn calldata_exit(id: &str, deadline: u64) -> String {
    format!("{}{}{}", sel("exit(bytes32,uint256)"), w_hex(id), w_u(U256::from(deadline)))
}

fn rlp_bytes(out: &mut Vec<u8>, b: &[u8]) {
    if b.len() == 1 && b[0] < 0x80 {
        out.push(b[0]);
    } else {
        rlp_len(out, b.len(), 0x80);
        out.extend_from_slice(b);
    }
}
fn rlp_len(out: &mut Vec<u8>, len: usize, off: u8) {
    if len < 56 {
        out.push(off + len as u8);
    } else {
        let lb: Vec<u8> = (len as u64).to_be_bytes().iter().copied().skip_while(|x| *x == 0).collect();
        out.push(off + 55 + lb.len() as u8);
        out.extend_from_slice(&lb);
    }
}
fn rlp_uint(out: &mut Vec<u8>, x: u128) {
    let b: Vec<u8> = x.to_be_bytes().iter().copied().skip_while(|v| *v == 0).collect();
    rlp_bytes(out, &b);
}

/// The EIP-1559 signing payload (0x02 || rlp([...])) the vault builds for an executor call, so the vault's own
/// `check_transaction` view can be run on it before anything is proposed.
pub fn unsigned_1559(p: &Value, to: &str, data_hex: &str) -> Result<Vec<u8>> {
    let mut body = vec![];
    rlp_uint(&mut body, CHAIN_ID as u128);
    rlp_uint(&mut body, p["nonce"].as_u64().ok_or_else(|| anyhow!("nonce"))? as u128);
    rlp_uint(&mut body, p["max_priority_fee_per_gas"].as_u64().unwrap_or(0) as u128);
    rlp_uint(&mut body, p["max_fee_per_gas"].as_u64().unwrap_or(0) as u128);
    rlp_uint(&mut body, p["gas_limit"].as_u64().unwrap_or(0) as u128);
    rlp_bytes(&mut body, &hex::decode(to.trim_start_matches("0x"))?);
    rlp_uint(&mut body, 0);
    rlp_bytes(&mut body, &hex::decode(data_hex)?);
    body.push(0xc0); // empty access list
    let mut out = vec![0x02];
    rlp_len(&mut out, body.len(), 0xc0);
    out.extend_from_slice(&body);
    Ok(out)
}

#[cfg(test)]
mod tests {
    use super::*;

    const ID: &str = "0xa25ac5171b7e500b82653d3750b91bb44e2409c32ca3c9e80b0db077e8a4535c";
    const USER: &str = "0x00000000000000000000000000000000000000a1";
    const EXEC: &str = "0x00000000000000000000000000000000000000e1";
    const WETH: &str = "0x00000000000000000000000000000000000000c1";
    const USDC: &str = "0x00000000000000000000000000000000000000d1";
    const ORACLE: &str = "0x00000000000000000000000000000000000000b1";
    const IRM: &str = "0x00000000000000000000000000000000000000b2";
    const POOL: &str = "0x00000000000000000000000000000000000000b3";
    const FEED: &str = "0x00000000000000000000000000000000000000b4";
    const TS: u64 = 1_800_000_000;

    fn a(h: &str) -> U256 {
        U256::from_str_radix(h.trim_start_matches("0x"), 16).unwrap()
    }
    fn enc(ws: &[U256]) -> Result<Vec<u8>, String> {
        let mut out = vec![];
        for w in ws {
            let mut b = [0u8; 32];
            w.to_big_endian(&mut b);
            out.extend_from_slice(&b);
        }
        Ok(out)
    }
    fn u(x: u128) -> U256 {
        U256::from(x)
    }

    /// Raw phase-1 results of one market with a position (debt about 1,000 USDC on 1 WETH) and a corridor in force.
    fn phase1() -> Results {
        let mut r = Results::new();
        let k = |s: &str| format!("{ID}/{s}");
        r.insert(k("params"), enc(&[a(USDC), a(WETH), a(ORACLE), a(IRM), u(860_000_000_000_000_000)]));
        r.insert(k("market"), enc(&[u(10_000_000_000), u(10_000_000_000_000_000), u(5_000_000_000), u(5_000_000_000_000_000), u(TS as u128), u(0)]));
        r.insert(k("position"), enc(&[u(0), u(1_000_000_000_000_000), u(1_000_000_000_000_000_000)]));
        let w = |x: f64| u((x * 1e6) as u128 * 1_000_000_000_000);
        r.insert(k("corridor"), enc(&[w(0.5), w(0.7), w(0.3), w(0.2), u(1), u(1), u(1)]));
        r.insert(k("config"), enc(&[a(USDC), a(WETH), a(ORACLE), a(IRM), u(860_000_000_000_000_000), a(POOL), a(FEED), u(0), u(1200), u(0)]));
        r.insert(k("session"), enc(&[u(0)]));
        r.insert(k("closed"), enc(&[u(0)]));
        r.insert("x/moves".into(), enc(&[u(0)]));
        r.insert("x/last".into(), enc(&[u(0)]));
        r.insert("x/cooldown".into(), enc(&[u(60)]));
        r
    }

    fn phase2() -> Results {
        let mut r = Results::new();
        let k = |s: &str| format!("{ID}/{s}");
        r.insert(k("price"), enc(&[u(2000) * U256::exp10(24)]));
        r.insert(k("rate"), enc(&[u(0)]));
        r.insert(format!("{ID}/feed/{FEED}"), enc(&[u(1), u(2000_00000000), u(0), u((TS - 60) as u128), u(1)]));
        let sq = ((2000e6f64 / 1e18).sqrt() * 2f64.powi(96)) as u128; // token0 WETH, token1 USDC at 2000
        r.insert(k("slot0"), enc(&[u(sq), u(0), u(0), u(0), u(0), u(0), u(1)]));
        r.insert(k("token0"), enc(&[a(WETH)]));
        r.insert(k("sold"), enc(&[u(0)]));
        r
    }

    fn ids() -> Vec<String> {
        vec![ID.to_string()]
    }

    #[test]
    fn selectors_match_the_current_executor() {
        // methodIdentifiers of executor/out/Executor.sol/Executor.json built from executor/src/Executor.sol (2 Nov)
        for (sig, id) in [
            ("corridors(address,bytes32)", "2e18e9e7"),
            ("config(bytes32)", "cc718f76"),
            ("sessionStart(address,bytes32)", "2b9c8a03"),
            ("closedForAgent(address,bytes32)", "91dea56f"),
            ("soldInWindow(address,address)", "03e29885"),
            ("movesInWindow(address)", "c9eecddf"),
            ("lastAgentCall(address)", "a8b44768"),
            ("COOLDOWN()", "a2724a4d"),
            ("reduce(bytes32,uint256,uint256)", "643d3b9e"),
            ("migrate(bytes32,bytes32,uint256,uint256)", "32bdcf59"),
            ("exit(bytes32,uint256)", "5c7da70e"),
            ("getL1Fee(bytes)", "49948e0e"),
            ("l1BaseFee()", "519b4bd3"),
        ] {
            assert_eq!(sel(sig), id, "{sig}");
        }
        // the plan sends the two-argument window read with the market's collateral token
        let (snaps, e) = decode1(&ids(), true, &phase1());
        assert!(e.is_empty(), "{e:?}");
        let c2 = plan2(&snaps, USER, EXEC, &HashMap::new());
        let sold = c2.iter().find(|c| c.key.ends_with("/sold")).unwrap();
        let Req::Call { to, data } = &sold.req else { panic!() };
        assert_eq!(to, EXEC);
        assert_eq!(data, &format!("03e29885{}{}", w_hex(USER), w_hex(WETH)));
    }

    #[test]
    fn a_complete_read_decodes_every_decision_input() {
        let (mut snaps, e) = decode1(&ids(), true, &phase1());
        assert!(e.is_empty(), "{e:?}");
        let mut t0 = HashMap::new();
        let e = decode2(&mut snaps, TS, &phase2(), &mut t0);
        assert!(e.is_empty(), "{e:?}");
        let s = &snaps[0];
        assert_eq!(s.name, "live");
        assert_eq!(s.collateral_value, 2_000_000_000);
        assert_eq!(s.debt, 1_000_000_000, "1e15 of 5e15 shares on 5,000 USDC, rounded up");
        assert_eq!(s.ltv, Ltv::Ratio(WAD / 2));
        let x = s.exec.as_ref().unwrap();
        assert!(x.in_force());
        assert_eq!(x.cooldown, 60);
        assert_eq!(s.feeds[0].age_s, 60);
        assert!(s.feeds[0].fresh());
        assert!((s.pool_usd.unwrap() - 2000.0).abs() < 1e-3, "{:?}", s.pool_usd);
        assert_eq!(t0.get(POOL).map(|s| s.as_str()), Some(WETH));
    }

    #[test]
    fn a_failed_or_short_read_is_a_read_error_never_a_zero() {
        // a short position (one word: the call answered with less than the ABI) must not decode as collateral 0
        let mut r = phase1();
        r.insert(format!("{ID}/position"), enc(&[u(0)]));
        let (_, e) = decode1(&ids(), true, &r);
        assert!(e.iter().any(|x| x.contains("/position") && x.contains("expected at least 96")), "{e:?}");
        // an RPC error (rate limit) on the oracle price
        let (mut snaps, _) = decode1(&ids(), true, &phase1());
        let mut r2 = phase2();
        r2.insert(format!("{ID}/price"), Err("{\"code\":-32016,\"message\":\"over rate limit\"}".into()));
        let e = decode2(&mut snaps, TS, &r2, &mut HashMap::new());
        assert!(e.iter().any(|x| x.contains("/price") && x.contains("rate limit")), "{e:?}");
        // an empty result and a missing key
        let mut r = phase1();
        r.insert("x/cooldown".into(), Ok(vec![]));
        r.remove(&format!("{ID}/closed"));
        let (_, e) = decode1(&ids(), true, &r);
        assert!(e.iter().any(|x| x.contains("x/cooldown: empty result")), "{e:?}");
        assert!(e.iter().any(|x| x.contains("/closed: missing")), "{e:?}");
        // a value outside its type: a bool that is 2, an address with high bytes
        let mut r = phase1();
        r.insert(format!("{ID}/closed"), enc(&[u(2)]));
        let (_, e) = decode1(&ids(), true, &r);
        assert!(e.iter().any(|x| x.contains("not a bool")), "{e:?}");
        // oracle price 0 is an impossible value
        let (mut snaps, _) = decode1(&ids(), true, &phase1());
        let mut r2 = phase2();
        r2.insert(format!("{ID}/price"), enc(&[u(0)]));
        assert!(decode2(&mut snaps, TS, &r2, &mut HashMap::new()).iter().any(|x| x.contains("oracle price 0")));
    }

    #[test]
    fn providers_must_agree_on_every_call() {
        let calls = plan1(USER, EXEC, true, &ids());
        let ra = phase1();
        let mut rb = phase1();
        assert!(compare(&calls, &ra, &rb).is_empty());
        let mut ra = ra;
        ra.insert("cmp/executor_code".into(), Ok(vec![0x60]));
        assert_eq!(compare(&calls, &ra, &rb), vec!["cmp/executor_code".to_string()], "the second provider sees no executor code");
        ra.insert("cmp/executor_code".into(), Ok(vec![0x60]));
        rb.insert("cmp/executor_code".into(), Ok(vec![0x60]));
        assert!(compare(&calls, &ra, &rb).is_empty());
        rb.insert(format!("{ID}/position"), enc(&[u(0), u(1), u(1_000_000_000_000_000_000)]));
        rb.insert("x/last".into(), Err("header not found".into()));
        rb.insert("info/l1_base_fee".into(), enc(&[u(7)])); // informational: not compared
        let mut d = compare(&calls, &ra, &rb);
        d.sort();
        assert_eq!(d, vec![format!("{ID}/position"), "x/last".to_string()]);
    }

    #[test]
    fn a_negative_feed_answer_is_not_fresh() {
        let (mut snaps, _) = decode1(&ids(), true, &phase1());
        let mut r2 = phase2();
        r2.insert(format!("{ID}/feed/{FEED}"), enc(&[u(1), U256::MAX, u(0), u((TS - 60) as u128), u(1)]));
        assert!(decode2(&mut snaps, TS, &r2, &mut HashMap::new()).is_empty());
        assert!(!snaps[0].feeds[0].fresh());
    }

    #[test]
    fn gas_margins_and_cap() {
        assert_eq!(gas_limit(400_000, true), Ok(600_000));
        assert_eq!(gas_limit(400_000, false), Ok(520_000));
        assert_eq!(gas_limit(415_522, true), Ok(623_283), "the 27 Oct reduce that ran out of gas at x1.3 (402,973 used of 415,522 limit)");
        assert_eq!(gas_limit(3, true), Ok(5), "rounded up");
        assert_eq!(gas_limit(700_000, true), Ok(GAS_LIMIT_MAX), "capped at the vault's bound");
        assert_eq!(gas_limit(GAS_LIMIT_MAX, false), Ok(GAS_LIMIT_MAX));
        assert!(gas_limit(GAS_LIMIT_MAX + 1, false).is_err());
        assert!(gas_limit(0, false).is_err());
    }

    #[test]
    fn pacing_keeps_each_provider_under_25_calls_per_second() {
        let t = Instant::now();
        let mut q = VecDeque::new();
        assert_eq!(pace_wait(&q, t, 20), None);
        q.push_back((t, 20));
        assert_eq!(pace_wait(&q, t, 5), None);
        let w = pace_wait(&q, t + Duration::from_millis(100), 6).unwrap();
        assert_eq!(w, RATE_WINDOW - Duration::from_millis(100));
        assert_eq!(pace_wait(&q, t + RATE_WINDOW, 20), None, "the batch left the window");
    }

    #[test]
    fn l1_fee_call_encodes_the_unsigned_tx_as_bytes() {
        assert_eq!(w_bytes(&[0xab]), format!("{}{}ab{}", w_u(U256::from(32u8)), w_u(U256::one()), "00".repeat(31)));
        assert_eq!(w_bytes(&[0u8; 32]).len(), 64 * 3);
        let calls = plan1(USER, EXEC, true, &ids());
        let c = calls.iter().find(|c| c.key == "info/l1_fee_template").unwrap();
        let Req::Call { to, data } = &c.req else { panic!() };
        assert_eq!(to, GAS_PRICE_ORACLE);
        assert!(data.starts_with("49948e0e"));
    }
}
