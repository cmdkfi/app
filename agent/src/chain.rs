//! Base Sepolia: reads at one pinned block from two RPC providers, Morpho's share math, the EIP-1559 bytes the
//! vault builds, and broadcasting. Exact integer math (U256) for everything a proposal depends on; f64 only for
//! the ratios that feed screens and the judge's inputs.
use anyhow::{anyhow, bail, Result};
use primitive_types::U256;
use serde::Serialize;
use serde_json::{json, Value};
use sha3::{Digest, Keccak256};
use std::collections::HashMap;
use std::time::Duration;

pub const CHAIN_ID: u64 = 84532;
pub const MORPHO: &str = "0xbbbbbbbbbb9cc5e90e3b3af64bdaf62c37eeffcb";
pub const ETH_USD: &str = "0x4adc67696ba383f43dd60a9e78f2c97fbbfc7cb1";
pub const USDC_USD: &str = "0xd30e2101a97dcbaebcbc04f14c3f624e67a35165";
pub const LIVE_POOL: &str = "0x57183717a087d2fe3ad890873877244c3b96156c";
/// The vault's bounds (prototype/vault/src/evm.rs): a proposal outside them is refused there.
pub const GAS_LIMIT_MAX: u64 = 1_000_000;
pub const MAX_FEE_MAX: u64 = 2_000_000_000;
pub const DEADLINE_SECS: u64 = 600;
pub const WAD: u128 = 1_000_000_000_000_000_000;

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
fn word(b: &[u8], i: usize) -> U256 {
    if b.len() < 32 * (i + 1) {
        return U256::zero();
    }
    U256::from_big_endian(&b[32 * i..32 * (i + 1)])
}
fn addr_of(b: &[u8], i: usize) -> String {
    if b.len() < 32 * (i + 1) {
        return String::new();
    }
    format!("0x{}", hex::encode(&b[32 * i + 12..32 * (i + 1)]))
}
fn u128_of(x: U256) -> u128 {
    x.low_u128()
}
pub fn hex_u(v: &Value) -> u128 {
    u128::from_str_radix(v.as_str().unwrap_or("0x0").trim_start_matches("0x"), 16).unwrap_or(0)
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

// ------------------------------------------------------------------ snapshot

#[derive(Serialize, Clone, Default)]
pub struct Corridor {
    pub target: u128,
    pub act: u128,
    pub floor: u128,
    pub sell_budget: u128,
    pub migrate_allowed: bool,
    pub exit_allowed: bool,
    pub set: bool,
}

#[derive(Serialize, Clone, Default)]
pub struct ExecState {
    pub corridor: Corridor,
    pub sold_in_window: u128,
    pub moves_in_window: u128,
    pub last_agent_call: u64,
    pub session_start: u64,
    pub closed_for_agent: bool,
    pub cooldown: u64,
}

#[derive(Serialize, Clone, Default)]
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
    pub ltv_wad: u128,
    pub utilization: f64,
    pub borrow_apr: f64,
    pub oracle_usd: f64,
    pub pool: Option<String>,
    pub pool_usd: Option<f64>,
    pub pool_gap: Option<f64>,
    pub feeds: Vec<(String, u64, u64)>, // (feed, updatedAt, max age)
    pub feed_ages: Vec<u64>,
    pub exec: Option<ExecState>,
    pub liquidity: u128,
}

#[derive(Serialize, Clone, Default)]
pub struct Snapshot {
    pub block: u64,
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
    /// calls that failed or returned nothing, and impossible values (oracle 0, collateral value 0 with debt):
    /// a non-empty list makes the tick a failed tick (run 2, 27 Oct: a rate-limited call decoded as 0 and fired a false screen)
    pub read_errors: Vec<String>,
}

/// LTV is reported up to 10x (10 WAD); beyond that the read is treated as impossible, never serialised as u128::MAX
pub const LTV_CAP: u128 = 10 * WAD;

/// Keys whose call failed or returned no data.
fn failed_keys(r: &HashMap<String, Result<Vec<u8>, String>>, calls: &[(String, String, String)]) -> Vec<String> {
    calls
        .iter()
        .filter_map(|(k, _, _)| match r.get(k) {
            Some(Ok(b)) if !b.is_empty() => None,
            Some(Ok(_)) => Some(format!("{k}: empty result")),
            Some(Err(e)) => Some(format!("{k}: {}", e.chars().take(120).collect::<String>())),
            None => Some(format!("{k}: missing")),
        })
        .collect()
}

pub struct Base {
    http: reqwest::Client,
    pub urls: Vec<String>,
    token0: std::sync::Mutex<HashMap<String, String>>,
    /// last batch sent per provider: sepolia.base.org allows 25 calls per second and counts each call in a batch
    last_batch: tokio::sync::Mutex<HashMap<String, std::time::Instant>>,
}

/// at most this many calls per batch, and at least BATCH_SPACING between two batches to one provider
const BATCH_MAX: usize = 20;
const BATCH_SPACING: Duration = Duration::from_millis(1050);

impl Base {
    pub fn new(urls: Vec<String>) -> Self {
        Base {
            http: reqwest::Client::builder().timeout(Duration::from_secs(20)).build().unwrap(),
            urls,
            token0: Default::default(),
            last_batch: Default::default(),
        }
    }

    pub async fn rpc(&self, url: &str, method: &str, params: Value) -> Result<Value> {
        let r: Value = self
            .http
            .post(url)
            .header("User-Agent", "prime-p3-agent")
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

    /// Batched eth_calls at `block`, in chunks of BATCH_MAX spaced BATCH_SPACING apart per provider; results by key
    /// (Err = the call's error text).
    async fn batch(&self, url: &str, block: u64, calls: &[(String, String, String)]) -> Result<HashMap<String, Result<Vec<u8>, String>>> {
        let mut out = HashMap::new();
        for chunk in calls.chunks(BATCH_MAX) {
            let wait = self.last_batch.lock().await.get(url).map(|t| BATCH_SPACING.saturating_sub(t.elapsed())).unwrap_or_default();
            tokio::time::sleep(wait).await;
            self.last_batch.lock().await.insert(url.to_string(), std::time::Instant::now());
            out.extend(self.batch_once(url, block, chunk).await?);
        }
        Ok(out)
    }

    async fn batch_once(&self, url: &str, block: u64, calls: &[(String, String, String)]) -> Result<HashMap<String, Result<Vec<u8>, String>>> {
        let body: Vec<Value> = calls
            .iter()
            .enumerate()
            .map(|(i, (_, to, data))| {
                json!({"jsonrpc": "2.0", "id": i, "method": "eth_call", "params": [{"to": to, "data": format!("0x{data}")}, format!("0x{block:x}")]})
            })
            .collect();
        let r: Value = self.http.post(url).header("User-Agent", "prime-p3-agent").json(&body).send().await?.json().await?;
        let arr = r.as_array().ok_or_else(|| anyhow!("batch reply is not an array: {}", r.to_string().chars().take(300).collect::<String>()))?;
        let mut out = HashMap::new();
        for item in arr {
            let i = item["id"].as_u64().unwrap_or(u64::MAX) as usize;
            let Some((k, _, _)) = calls.get(i) else { continue };
            let v = match item.get("error") {
                Some(e) => Err(e.to_string()),
                None => hex::decode(item["result"].as_str().unwrap_or("0x").trim_start_matches("0x")).map_err(|e| e.to_string()),
            };
            out.insert(k.clone(), v);
        }
        if out.len() != calls.len() {
            bail!("batch returned {} of {} results", out.len(), calls.len());
        }
        Ok(out)
    }

    pub async fn snapshot(&self, user: &str, executor: &str, market_ids: &[String]) -> Result<Snapshot> {
        let (a, b) = (&self.urls[0], self.urls.get(1).unwrap_or(&self.urls[0]));
        let blk = self.rpc(a, "eth_getBlockByNumber", json!(["latest", false])).await?;
        let block = hex_u(&blk["number"]) as u64;
        let timestamp = hex_u(&blk["timestamp"]) as u64;
        let code = self.rpc(a, "eth_getCode", json!([executor, format!("0x{block:x}")])).await?;
        let executor_deployed = code.as_str().map(|s| s.len() > 2).unwrap_or(false);
        let u = w_hex(user);

        // phase 1: everything that does not depend on another result
        let mut calls: Vec<(String, String, String)> = vec![];
        for id in market_ids {
            let idw = w_hex(id);
            calls.push((format!("{id}/params"), MORPHO.into(), format!("{}{idw}", sel("idToMarketParams(bytes32)"))));
            calls.push((format!("{id}/market"), MORPHO.into(), format!("{}{idw}", sel("market(bytes32)"))));
            calls.push((format!("{id}/position"), MORPHO.into(), format!("{}{idw}{u}", sel("position(bytes32,address)"))));
            if executor_deployed {
                calls.push((format!("{id}/corridor"), executor.into(), format!("{}{u}{idw}", sel("corridors(address,bytes32)"))));
                calls.push((format!("{id}/config"), executor.into(), format!("{}{idw}", sel("config(bytes32)"))));
                calls.push((format!("{id}/session"), executor.into(), format!("{}{u}{idw}", sel("sessionStart(address,bytes32)"))));
                calls.push((format!("{id}/closed"), executor.into(), format!("{}{u}{idw}", sel("closedForAgent(address,bytes32)"))));
            }
        }
        if executor_deployed {
            calls.push(("x/sold".into(), executor.into(), format!("{}{u}", sel("soldInWindow(address)"))));
            calls.push(("x/moves".into(), executor.into(), format!("{}{u}", sel("movesInWindow(address)"))));
            calls.push(("x/last".into(), executor.into(), format!("{}{u}", sel("lastAgentCall(address)"))));
            calls.push(("x/cooldown".into(), executor.into(), sel("COOLDOWN()")));
        }
        let (ra, rb) = tokio::join!(self.batch(a, block, &calls), self.batch(b, block, &calls));
        let ra = ra?;
        let mut read_errors = failed_keys(&ra, &calls);
        let get = |k: &str| -> Vec<u8> { ra.get(k).and_then(|r| r.as_ref().ok()).cloned().unwrap_or_default() };

        // phase 2: oracle, IRM, feeds and pool need the market params / executor config
        let mut snaps = vec![];
        let mut calls2: Vec<(String, String, String)> = vec![];
        for id in market_ids {
            let name = market_name(id);
            let p = get(&format!("{id}/params"));
            let m = get(&format!("{id}/market"));
            let pos = get(&format!("{id}/position"));
            let mut s = MarketSnap { name: name.into(), id: id.clone(), ..Default::default() };
            s.loan_token = addr_of(&p, 0);
            s.collateral_token = addr_of(&p, 1);
            s.oracle = addr_of(&p, 2);
            s.irm = addr_of(&p, 3);
            s.lltv = u128_of(word(&p, 4));
            for i in 0..6 {
                s.market[i] = u128_of(word(&m, i));
            }
            s.exists = s.market[4] != 0;
            s.borrow_shares = u128_of(word(&pos, 1));
            s.collateral = u128_of(word(&pos, 2));
            let (mut pool, mut feeds) = fallback_pool_and_feeds(name);
            if executor_deployed {
                let c = get(&format!("{id}/corridor"));
                let cfg = get(&format!("{id}/config"));
                // config = (params(5 words), pool, feeds[2], maxAges[2])
                pool = Some(addr_of(&cfg, 5)).filter(|x| x != "0x0000000000000000000000000000000000000000" && !x.is_empty());
                feeds = (0..2)
                    .map(|i| (addr_of(&cfg, 6 + i), word(&cfg, 8 + i).low_u64()))
                    .filter(|(f, _)| f != "0x0000000000000000000000000000000000000000" && !f.is_empty())
                    .collect();
                s.exec = Some(ExecState {
                    corridor: Corridor {
                        target: u128_of(word(&c, 0)),
                        act: u128_of(word(&c, 1)),
                        floor: u128_of(word(&c, 2)),
                        sell_budget: u128_of(word(&c, 3)),
                        migrate_allowed: !word(&c, 4).is_zero(),
                        exit_allowed: !word(&c, 5).is_zero(),
                        set: !word(&c, 6).is_zero(),
                    },
                    sold_in_window: u128_of(word(&get("x/sold"), 0)),
                    moves_in_window: u128_of(word(&get("x/moves"), 0)),
                    last_agent_call: word(&get("x/last"), 0).low_u64(),
                    session_start: word(&get(&format!("{id}/session")), 0).low_u64(),
                    closed_for_agent: !word(&get(&format!("{id}/closed")), 0).is_zero(),
                    cooldown: word(&get("x/cooldown"), 0).low_u64(),
                });
            }
            s.pool = pool.clone();
            if s.exists {
                calls2.push((format!("{id}/price"), s.oracle.clone(), sel("price()")));
                let mp = format!("{}{}{}{}{}", w_hex(&s.loan_token), w_hex(&s.collateral_token), w_hex(&s.oracle), w_hex(&s.irm), w_u(U256::from(s.lltv)));
                let mk: String = s.market.iter().map(|x| w_u(U256::from(*x))).collect();
                calls2.push((
                    format!("{id}/rate"),
                    s.irm.clone(),
                    format!("{}{mp}{mk}", sel("borrowRateView((address,address,address,address,uint256),(uint128,uint128,uint128,uint128,uint128,uint128))")),
                ));
                for (f, _) in &feeds {
                    calls2.push((format!("{id}/feed/{f}"), f.clone(), sel("latestRoundData()")));
                }
                if let Some(pl) = &pool {
                    calls2.push((format!("{id}/slot0"), pl.clone(), sel("slot0()")));
                    if !self.token0.lock().unwrap().contains_key(pl) {
                        calls2.push((format!("{id}/token0"), pl.clone(), sel("token0()")));
                    }
                }
            }
            s.feeds = feeds.iter().map(|(f, a)| (f.clone(), 0, *a)).collect();
            snaps.push(s);
        }
        let (r2, r2b) = if calls2.is_empty() {
            (HashMap::new(), Ok(HashMap::new()))
        } else {
            let (x, y) = tokio::join!(self.batch(a, block, &calls2), self.batch(b, block, &calls2));
            (x?, y)
        };
        read_errors.extend(failed_keys(&r2, &calls2));
        let get2 = |k: &str| -> Vec<u8> { r2.get(k).and_then(|r| r.as_ref().ok()).cloned().unwrap_or_default() };

        for s in snaps.iter_mut().filter(|s| s.exists) {
            let id = s.id.clone();
            let price = word(&get2(&format!("{id}/price")), 0);
            if price.is_zero() {
                read_errors.push(format!("{id}/price: oracle price 0"));
            }
            s.oracle_price = price.to_string();
            s.oracle_usd = price.to_string().parse::<f64>().unwrap_or(0.0) / 1e24;
            let rate = word(&get2(&format!("{id}/rate")), 0);
            s.borrow_rate_per_sec = u128_of(rate);
            s.borrow_apr = rate.low_u128() as f64 * 31_536_000.0 / 1e18;
            // accrue interest to this block, as Morpho's _accrueInterest would (fee 0 on these markets)
            let elapsed = U256::from(timestamp.saturating_sub(s.market[4] as u64));
            let tb = U256::from(s.market[2]);
            let interest = mul_div_down(tb, w_taylor_compounded(rate, elapsed), U256::from(WAD));
            s.total_borrow_accrued = u128_of(tb + interest);
            s.total_supply_accrued = s.market[0] + u128_of(interest);
            s.liquidity = s.total_supply_accrued.saturating_sub(s.total_borrow_accrued);
            s.debt = u128_of(to_assets_up(U256::from(s.borrow_shares), U256::from(s.total_borrow_accrued), U256::from(s.market[3])));
            s.collateral_value = u128_of(mul_div_down(U256::from(s.collateral), price, U256::exp10(36)));
            s.ltv_wad = if s.collateral_value == 0 {
                if s.debt > 0 {
                    read_errors.push(format!("{id}: collateral value 0 with debt {}", s.debt));
                    LTV_CAP
                } else {
                    0
                }
            } else {
                u128_of(mul_div_up(U256::from(s.debt), U256::from(WAD), U256::from(s.collateral_value))).min(LTV_CAP)
            };
            s.utilization = if s.total_supply_accrued == 0 { 0.0 } else { s.total_borrow_accrued as f64 / s.total_supply_accrued as f64 };
            for f in s.feeds.iter_mut() {
                let rd = get2(&format!("{id}/feed/{}", f.0));
                f.1 = word(&rd, 3).low_u64(); // updatedAt
            }
            s.feed_ages = s.feeds.iter().map(|f| timestamp.saturating_sub(f.1)).collect();
            if let Some(pl) = s.pool.clone() {
                let t0 = get2(&format!("{id}/token0"));
                if !t0.is_empty() {
                    self.token0.lock().unwrap().insert(pl.clone(), addr_of(&t0, 0));
                }
                let token0 = self.token0.lock().unwrap().get(&pl).cloned().unwrap_or_default();
                let sq = word(&get2(&format!("{id}/slot0")), 0).to_string().parse::<f64>().unwrap_or(0.0) / 2f64.powi(96);
                let p = sq * sq; // token1 raw per token0 raw
                if p > 0.0 {
                    // loan token 6 decimals, collateral 18 (checked against the market params by the caller)
                    let usd = if token0 == s.collateral_token { p * 1e12 } else { 1e12 / p };
                    s.pool_usd = Some(usd);
                    s.pool_gap = Some(usd / s.oracle_usd - 1.0);
                }
            }
        }

        // second provider: every raw result of both phases must be identical at this block
        let mut disagree = vec![];
        let mut second_error = None;
        for (pa, pb, cs) in [(&ra, &rb, &calls), (&r2, &r2b, &calls2)] {
            match pb {
                Ok(pb) => {
                    for (k, _, _) in cs.iter() {
                        if pa.get(k).map(|r| r.as_ref().ok()) != pb.get(k).map(|r| r.as_ref().ok()) {
                            disagree.push(k.clone());
                        }
                    }
                }
                Err(e) => second_error = Some(e.to_string().chars().take(300).collect()),
            }
        }
        Ok(Snapshot {
            block,
            timestamp,
            base_fee: hex_u(&blk["baseFeePerGas"]),
            user: user.into(),
            executor: executor.into(),
            executor_deployed,
            markets: snaps,
            rpc_primary: a.clone(),
            rpc_second: b.clone(),
            rpc_disagree: disagree,
            rpc_second_error: second_error,
            read_errors,
        })
    }

    // ------------------------------------------------------------------ transaction parameters and broadcast

    /// One TxParams set as relayer.py `params` computes it: pending nonce, estimate x 1.3, 2 x base fee + priority
    /// capped at the vault's bound. Returns (params, estimate or the estimate's error).
    pub async fn tx_params(&self, from: &str, to: &str, data: &str) -> Result<(Value, Result<u64, String>)> {
        let a = &self.urls[0];
        let nonce = hex_u(&self.rpc(a, "eth_getTransactionCount", json!([from, "pending"])).await?) as u64;
        let est = self
            .rpc(a, "eth_estimateGas", json!([{"from": from, "to": to, "data": format!("0x{data}")}]))
            .await
            .map(|v| hex_u(&v) as u64)
            .map_err(|e| e.to_string());
        let blk = self.rpc(a, "eth_getBlockByNumber", json!(["latest", false])).await?;
        let base = hex_u(&blk["baseFeePerGas"]) as u64;
        let prio = hex_u(&self.rpc(a, "eth_maxPriorityFeePerGas", json!([])).await?) as u64;
        let gas = est.as_ref().map(|e| (*e as f64 * 1.3) as u64).unwrap_or(GAS_LIMIT_MAX);
        let max_fee = (2 * base + prio).min(MAX_FEE_MAX);
        let p = json!({"nonce": nonce, "gas_limit": gas, "max_fee_per_gas": max_fee, "max_priority_fee_per_gas": prio.min(max_fee)});
        Ok((p, est))
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
