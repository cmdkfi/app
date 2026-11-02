//! PRIME prototype P2 (throwaway, 27–31 Oct 2026): the vault on NEAR. Spec: docs/01-design-and-feasibility.md
//! §4.1 (methods, state, transaction path), §4.4 (attestation), §4.5 (owner model). Deviations (prototype/BRIEF.md):
//! a plain deployment to a sub-account, one vault, one owner, executor set at `init` and by `set_executor`.
//!
//! Three kinds of caller:
//! - the owner, through `owner_call(message, signature, txs)`: an ERC-191 message signed by the owner's EVM wallet,
//!   submitted by anyone (measurement G's `ecrecover` check);
//! - the agent, through `reduce`, `migrate`, `exit`: called with the function-call key the vault added to its own
//!   account at registration, re-checked with `signer_account_pk`;
//! - the agent's implicit account, through `register_agent` (measurement F's verifier plus the §4.4 checks).
//!
//! Every Base transaction is built here with omni-transaction, decoded back and checked against the allowlist,
//! signed by `v1.signer-prod.testnet` (Chain Signatures), and assembled in `on_signed`, which returns the raw
//! signed transaction for the relayer to broadcast.
mod attest;
pub mod evm;
mod owner;

pub use attest::{EventLogEntry, Measurement};
pub use evm::{BaseTx, SignResponse, TxParams};

use dcap_qvl::QuoteCollateralV3;
use evm::*;
use near_sdk::json_types::U128;
use near_sdk::serde::de::DeserializeOwned;
use near_sdk::serde::Deserialize;
use near_sdk::serde_json::{self, json, Value};
use near_sdk::{
    env, near, require, AccountId, Allowance, CurveType, Gas, NearToken, PanicOnDefault, Promise, PromiseOrValue,
    PublicKey,
};

pub type Addr = [u8; 20];

/// Gas attached to each `sign` call. The signer requires at least 15 Tgas (`sign_call_gas_attachment_requirement`,
/// config of v1.signer-prod.testnet 3.17.0) and reserves 7 Tgas of it for its yield callback.
const SIGN_GAS: Gas = Gas::from_tgas(30);
/// Static gas for `on_signed`; unused gas of the call is added to it by weight.
const CALLBACK_GAS: Gas = Gas::from_tgas(20);
/// `sign` takes exactly 1 yoctoNEAR (`SIGN_DEPOSIT_YOCTONEAR`); any excess is refunded by the signer.
const SIGN_DEPOSIT: NearToken = NearToken::from_yoctonear(1);
const REGISTRATION_SECS: u64 = 7 * 24 * 3600;
/// Bounds the NEAR a registered agent key can spend on gas from the vault's balance.
const AGENT_KEY_ALLOWANCE: NearToken = NearToken::from_near(2);
const AGENT_METHODS: &str = "reduce,migrate,exit";
const WAD: u128 = 1_000_000_000_000_000_000;

pub fn now_secs() -> u64 {
    env::block_timestamp() / 1_000_000_000
}

fn hex_bytes(s: &str, n: usize, what: &str) -> Vec<u8> {
    match hex::decode(s.strip_prefix("0x").unwrap_or(s)) {
        Ok(b) if b.len() == n => b,
        _ => env::panic_str(&format!("{what}: expected {n} bytes of hex")),
    }
}
fn addr(s: &str, what: &str) -> Addr {
    hex_bytes(s, 20, what).try_into().unwrap()
}
fn id32(s: &str) -> [u8; 32] {
    hex_bytes(s, 32, "market id").try_into().unwrap()
}
fn h(b: &[u8]) -> String {
    format!("0x{}", hex::encode(b))
}
fn args<T: DeserializeOwned>(v: Value) -> T {
    serde_json::from_value(v).unwrap_or_else(|e| env::panic_str(&format!("bad args: {e}")))
}

#[near(serializers = [borsh])]
#[derive(Clone)]
pub struct Market {
    pub id: [u8; 32],
    pub loan_token: Addr,
    pub collateral_token: Addr,
    pub executor: Addr,
    pub previous_executors: Vec<Addr>,
    /// Largest `reduce` amount (loan-token units) the agent may propose in one call.
    pub slice_max: u128,
    /// Set by the owner's `set_corridor`, cleared by `set_executor`: the agent is refused until then (§4.1).
    pub corridor_set: bool,
}

#[near(serializers = [borsh])]
pub struct Agent {
    pub public_key: PublicKey,
    pub expires_at: u64,
}

#[near(serializers = [json])]
pub struct MarketInit {
    pub id: String,
    pub loan_token: String,
    pub collateral_token: String,
    pub executor: String,
    pub slice_max: U128,
}

#[near(serializers = [json])]
pub struct Policy {
    pub cooldown_secs: u64,
    pub markets: Vec<MarketInit>,
}

#[near(serializers = [json])]
pub struct SignedTx {
    pub raw: String,
    pub hash: String,
    pub from: String,
    pub to: String,
    pub nonce: u64,
}

#[derive(PartialEq, Clone, Copy, Debug)]
pub enum Caller {
    Owner,
    Agent,
}

// Owner method arguments (inside the signed message's `args`).
#[derive(Deserialize)]
#[serde(crate = "near_sdk::serde", deny_unknown_fields)]
struct MarketArg {
    market: String,
}
#[derive(Deserialize)]
#[serde(crate = "near_sdk::serde", deny_unknown_fields)]
struct CorridorArgs {
    market: String,
    target: U128,
    act: U128,
    floor: U128,
    sell_budget: U128,
    migrate_allowed: bool,
    exit_allowed: bool,
}
#[derive(Deserialize)]
#[serde(crate = "near_sdk::serde", deny_unknown_fields)]
struct OpenArgs {
    market: String,
    collateral_in: U128,
    target_ltv: U128,
}
#[derive(Deserialize)]
#[serde(crate = "near_sdk::serde", deny_unknown_fields)]
struct AmountArgs {
    market: String,
    amount: U128,
}
#[derive(Deserialize)]
#[serde(crate = "near_sdk::serde", deny_unknown_fields)]
struct WithdrawArgs {
    token: String,
    amount: U128,
    destination: String,
}
#[derive(Deserialize)]
#[serde(crate = "near_sdk::serde", deny_unknown_fields)]
struct SetExecutorArgs {
    market: String,
    executor: String,
}
#[derive(Deserialize)]
#[serde(crate = "near_sdk::serde", deny_unknown_fields)]
struct NoArgs {}

#[near(contract_state)]
#[derive(PanicOnDefault)]
pub struct Vault {
    /// Owner wallet address (EVM).
    pub owner: Addr,
    pub owner_nonce: u64,
    /// Chain Signatures contract (`v1.signer-prod.testnet`; a mock in the sandbox tests).
    pub signer: AccountId,
    pub path: String,
    /// The Base address the signer derives for (this account, `path`); every signature must recover to it.
    pub base_address: Addr,
    pub morpho: Addr,
    pub cooldown_secs: u64,
    pub last_agent_action: u64,
    pub markets: Vec<Market>,
    pub approved: Option<Measurement>,
    pub agent: Option<Agent>,
}

#[near]
impl Vault {
    /// Prototype: callable once by whoever deploys (the product's factory does this in one batch, §4.1).
    #[init]
    pub fn init(
        owner: String,
        signer: AccountId,
        path: String,
        base_address: String,
        morpho: String,
        policy: Policy,
    ) -> Self {
        require!(!policy.markets.is_empty(), "no markets");
        let markets: Vec<Market> = policy
            .markets
            .iter()
            .map(|m| Market {
                id: id32(&m.id),
                loan_token: addr(&m.loan_token, "loan_token"),
                collateral_token: addr(&m.collateral_token, "collateral_token"),
                executor: addr(&m.executor, "executor"),
                previous_executors: vec![],
                slice_max: m.slice_max.0,
                corridor_set: false,
            })
            .collect();
        for (i, m) in markets.iter().enumerate() {
            require!(markets[..i].iter().all(|o| o.id != m.id), "duplicate market");
            require!(m.executor != [0u8; 20], "zero executor");
        }
        Self {
            owner: addr(&owner, "owner"),
            owner_nonce: 0,
            signer,
            path,
            base_address: addr(&base_address, "base_address"),
            morpho: addr(&morpho, "morpho"),
            cooldown_secs: policy.cooldown_secs,
            last_agent_action: 0,
            markets,
            approved: None,
            agent: None,
        }
    }

    // ------------------------------------------------------------------ owner

    /// One entry point for every owner method; `method` and `args` come from the signed message.
    /// `txs`: EVM nonce, gas limit and fees for each Base transaction the method produces, supplied by the
    /// relayer and bounded here (the vault keeps no EVM nonce, §4.1). Returns the raw signed transactions.
    pub fn owner_call(&mut self, message: String, signature: String, txs: Vec<TxParams>) -> PromiseOrValue<Vec<SignedTx>> {
        let m = owner::verify(&message, &signature, &self.owner, self.owner_nonce);
        self.owner_nonce = m.nonce;
        let dl = w_u128((now_secs() + DEADLINE_SECS) as u128);
        let calls: Vec<(Addr, Vec<u8>)> = match m.method.as_str() {
            "setup" => {
                let mk = self.market(&args::<MarketArg>(m.args).market).clone();
                vec![
                    (mk.loan_token, calldata(SEL_APPROVE, &[w_addr(&mk.executor), W_MAX])),
                    (mk.collateral_token, calldata(SEL_APPROVE, &[w_addr(&mk.executor), W_MAX])),
                    (self.morpho, calldata(SEL_SET_AUTHORIZATION, &[w_addr(&mk.executor), w_bool(true)])),
                ]
            }
            "set_corridor" => {
                let a: CorridorArgs = args(m.args);
                let i = self.market_index(&a.market);
                let w64 = |v: U128| {
                    require!(v.0 <= u64::MAX as u128, "corridor value above uint64");
                    w_u128(v.0)
                };
                let mk = &mut self.markets[i];
                mk.corridor_set = true;
                let words = [
                    mk.id,
                    w64(a.target),
                    w64(a.act),
                    w64(a.floor),
                    w64(a.sell_budget),
                    w_bool(a.migrate_allowed),
                    w_bool(a.exit_allowed),
                    w_bool(false), // `set`: the executor sets it itself
                    dl,
                ];
                vec![(mk.executor, calldata(SEL_SET_CORRIDOR, &words))]
            }
            "open" => {
                let a: OpenArgs = args(m.args);
                let mk = self.market(&a.market);
                vec![(mk.executor, calldata(SEL_OPEN, &[mk.id, w_u128(a.collateral_in.0), w_u128(a.target_ltv.0), dl]))]
            }
            "repay" => {
                let a: AmountArgs = args(m.args);
                let mk = self.market(&a.market);
                vec![(mk.executor, calldata(SEL_REPAY, &[mk.id, w_u128(a.amount.0), dl]))]
            }
            "close" => {
                let mk = self.market(&args::<MarketArg>(m.args).market);
                vec![(mk.executor, calldata(SEL_CLOSE, &[mk.id, dl]))]
            }
            "withdraw" => {
                let a: WithdrawArgs = args(m.args);
                let token = addr(&a.token, "token");
                let dest = addr(&a.destination, "destination");
                require!(self.is_token(&token), "token not in the allowlist");
                require!(dest != [0u8; 20], "zero destination");
                require!(a.amount.0 > 0, "zero amount");
                vec![(token, calldata(SEL_TRANSFER, &[w_addr(&dest), w_u128(a.amount.0)]))]
            }
            "set_executor" => {
                let a: SetExecutorArgs = args(m.args);
                let i = self.market_index(&a.market);
                let new = addr(&a.executor, "executor");
                let mk = &mut self.markets[i];
                let old = mk.executor;
                require!(new != old && new != [0u8; 20], "executor unchanged or zero");
                if !mk.previous_executors.contains(&old) {
                    mk.previous_executors.push(old);
                }
                mk.executor = new;
                mk.corridor_set = false;
                // Step 1 of the §4.1 sequence: revoke the old executor once no market uses it any more: Morpho
                // authorization off, and allowance 0 on every token of every market that used it.
                if self.markets.iter().any(|o| o.executor == old) {
                    vec![]
                } else {
                    let mut calls = vec![(self.morpho, calldata(SEL_SET_AUTHORIZATION, &[w_addr(&old), w_bool(false)]))];
                    for o in self.markets.iter().filter(|o| o.previous_executors.contains(&old)) {
                        for t in [o.loan_token, o.collateral_token] {
                            if !calls.iter().any(|(to, _)| *to == t) {
                                calls.push((t, calldata(SEL_APPROVE, &[w_addr(&old), w_u128(0)])));
                            }
                        }
                    }
                    calls
                }
            }
            "approve_measurement" => {
                let a: Measurement = args(m.args);
                a.validate().unwrap_or_else(|e| env::panic_str(&e));
                self.approved = Some(a);
                vec![]
            }
            "revoke_agent" => {
                let _: NoArgs = args(m.args);
                if let Some(agent) = self.agent.take() {
                    Promise::new(env::current_account_id()).delete_key(agent.public_key).detach();
                }
                self.approved = None;
                vec![]
            }
            other => env::panic_str(&format!("unknown method {other}")),
        };
        self.sign(calls, txs, Caller::Owner)
    }

    // ------------------------------------------------------------------ agent

    /// Sell collateral to repay `amount` (loan-token units) on `market`; the executor clamps to the corridor.
    pub fn reduce(&mut self, market: String, amount: U128, tx: TxParams) -> PromiseOrValue<Vec<SignedTx>> {
        self.agent_check();
        let mk = self.agent_market(&market);
        require!(amount.0 > 0 && amount.0 <= mk.slice_max, "amount above the slice maximum");
        let dl = w_u128((now_secs() + DEADLINE_SECS) as u128);
        self.sign(vec![(mk.executor, calldata(SEL_REDUCE, &[mk.id, w_u128(amount.0), dl]))], vec![tx], Caller::Agent)
    }

    /// Move `fraction` (WAD) of the position from one approved market to another.
    pub fn migrate(&mut self, from: String, to: String, fraction: U128, tx: TxParams) -> PromiseOrValue<Vec<SignedTx>> {
        self.agent_check();
        let a = self.agent_market(&from);
        let b = self.agent_market(&to);
        require!(a.id != b.id, "same market");
        require!(a.executor == b.executor, "markets on different executors");
        require!(fraction.0 > 0 && fraction.0 <= WAD, "fraction outside (0, 1]");
        let dl = w_u128((now_secs() + DEADLINE_SECS) as u128);
        self.sign(vec![(a.executor, calldata(SEL_MIGRATE, &[a.id, b.id, w_u128(fraction.0), dl]))], vec![tx], Caller::Agent)
    }

    /// Repay all and withdraw the collateral to the vault's Base address.
    pub fn exit(&mut self, market: String, tx: TxParams) -> PromiseOrValue<Vec<SignedTx>> {
        self.agent_check();
        let mk = self.agent_market(&market);
        let dl = w_u128((now_secs() + DEADLINE_SECS) as u128);
        self.sign(vec![(mk.executor, calldata(SEL_EXIT, &[mk.id, dl]))], vec![tx], Caller::Agent)
    }

    // ------------------------------------------------------------------ attestation

    /// Called by the agent from its implicit account (account id = hex of its ed25519 key). On success the vault
    /// adds a function-call key for that key to its own account, limited to `reduce`, `migrate`, `exit`, and records
    /// a 7-day expiry. A previous agent key is deleted.
    pub fn register_agent(&mut self, quote_hex: String, collateral: String, event_log: Vec<EventLogEntry>) -> PromiseOrValue<()> {
        let caller = env::predecessor_account_id();
        require!(caller == env::signer_account_id(), "call register_agent directly from the agent's account");
        let key: [u8; 32] = match hex::decode(caller.as_str()) {
            Ok(b) if b.len() == 32 && caller.as_str().len() == 64 => b.try_into().unwrap(),
            _ => env::panic_str("caller is not an implicit account"),
        };
        let quote = hex::decode(&quote_hex).unwrap_or_else(|_| env::panic_str("bad quote hex"));
        // Cheap checks first: a wrong report_data costs well under the 105 Tgas of the signature chain.
        let rd = attest::parse_report_data(&quote).unwrap_or_else(|e| env::panic_str(&e));
        attest::check_report_data(&rd, &key).unwrap_or_else(|e| env::panic_str(&e));
        let approved = self.approved.clone().unwrap_or_else(|| env::panic_str("no approved measurement"));
        let collateral: QuoteCollateralV3 =
            serde_json::from_str(&collateral).unwrap_or_else(|_| env::panic_str("bad collateral json"));
        let now = now_secs();
        attest::verify(&quote, &collateral, &event_log, &approved, &key, now).unwrap_or_else(|e| env::panic_str(&e));

        let pk = PublicKey::from_parts(CurveType::ED25519, key.to_vec()).unwrap();
        let me = env::current_account_id();
        let old = self.agent.replace(Agent { public_key: pk.clone(), expires_at: now + REGISTRATION_SECS });
        env::log_str(&format!("agent registered: {} until {}", String::from(&pk), now + REGISTRATION_SECS));
        let mut p = Promise::new(me.clone());
        match old {
            Some(o) if o.public_key == pk => return PromiseOrValue::Value(()), // re-attestation: expiry extended
            Some(o) => p = p.delete_key(o.public_key),
            None => {}
        }
        PromiseOrValue::Promise(p.add_access_key_allowance(
            pk,
            Allowance::limited(AGENT_KEY_ALLOWANCE).unwrap(),
            me,
            AGENT_METHODS,
        ))
    }

    // ------------------------------------------------------------------ signing callback

    /// Assembles each signature per measurement A's rule, checks it recovers to the vault's Base address and
    /// returns the raw signed transactions with their hashes.
    #[private]
    pub fn on_signed(&self, txs: Vec<BaseTx>) -> Vec<SignedTx> {
        require!(env::promise_results_count() == txs.len() as u64, "result count");
        txs.iter()
            .enumerate()
            .map(|(i, t)| {
                let bytes = env::promise_result_checked(i as u64, 1024)
                    .unwrap_or_else(|_| env::panic_str(&format!("signature request {i} failed")));
                let resp: SignResponse =
                    serde_json::from_slice(&bytes).unwrap_or_else(|_| env::panic_str("bad signature response"));
                let tx = t.omni();
                let hash = env::keccak256_array(&tx.build_for_signing());
                let (raw, rs, v) = assemble(&tx, &resp).unwrap_or_else(|e| env::panic_str(&e));
                let from = recover_address(&hash, &rs, v).unwrap_or_else(|| env::panic_str("signature does not recover"));
                require!(from == self.base_address, "signature is not from the vault's Base address");
                SignedTx {
                    hash: h(&env::keccak256_array(&raw)),
                    raw: h(&raw),
                    from: h(&from),
                    to: format!("0x{}", t.to),
                    nonce: t.nonce,
                }
            })
            .collect()
    }

    // ------------------------------------------------------------------ views

    pub fn get_state(&self) -> Value {
        json!({
            "owner": h(&self.owner),
            "owner_nonce": self.owner_nonce,
            "signer": self.signer,
            "path": self.path,
            "base_address": h(&self.base_address),
            "morpho": h(&self.morpho),
            "cooldown_secs": self.cooldown_secs,
            "last_agent_action": self.last_agent_action,
            "markets": self.markets.iter().map(|m| json!({
                "id": h(&m.id), "loan_token": h(&m.loan_token), "collateral_token": h(&m.collateral_token),
                "executor": h(&m.executor), "previous_executors": m.previous_executors.iter().map(|a| h(a)).collect::<Vec<_>>(),
                "slice_max": m.slice_max.to_string(), "corridor_set": m.corridor_set,
            })).collect::<Vec<_>>(),
            "approved": self.approved,
            "agent": self.agent.as_ref().map(|a| json!({"public_key": String::from(&a.public_key), "expires_at": a.expires_at})),
        })
    }

    /// The allowlist the vault applies before every signature, on any unsigned EIP-1559 transaction (hex of the
    /// signing bytes, 0x02 || rlp). `caller`: "owner" or "agent". Returns "ok" or fails with the reason.
    pub fn check_transaction(&self, tx_hex: String, caller: String) -> String {
        let caller = match caller.as_str() {
            "owner" => Caller::Owner,
            "agent" => Caller::Agent,
            _ => env::panic_str("caller is owner or agent"),
        };
        let bytes = hex::decode(tx_hex.strip_prefix("0x").unwrap_or(&tx_hex)).unwrap_or_else(|_| env::panic_str("bad hex"));
        let d = decode_unsigned(&bytes).unwrap_or_else(|e| env::panic_str(&e));
        self.check_tx(&d, caller, now_secs()).unwrap_or_else(|e| env::panic_str(&format!("transaction refused: {e}")));
        "ok".into()
    }
}

impl Vault {
    fn market_index(&self, id: &str) -> usize {
        let id = id32(id);
        self.markets.iter().position(|m| m.id == id).unwrap_or_else(|| env::panic_str("market not in the allowlist"))
    }

    fn market(&self, id: &str) -> &Market {
        &self.markets[self.market_index(id)]
    }

    fn agent_market(&self, id: &str) -> Market {
        let m = self.market(id);
        require!(m.corridor_set, "no corridor set for this market on its executor");
        m.clone()
    }

    fn is_token(&self, a: &Addr) -> bool {
        self.markets.iter().any(|m| &m.loan_token == a || &m.collateral_token == a)
    }

    /// §4.1: called only through the agent's function-call key on this account, by that key, before the
    /// registration expires, and after the cooldown.
    fn agent_check(&mut self) {
        let agent = self.agent.as_ref().unwrap_or_else(|| env::panic_str("no agent registered"));
        require!(
            env::predecessor_account_id() == env::current_account_id(),
            "agent methods are called through the agent key on the vault"
        );
        require!(env::signer_account_pk() == agent.public_key, "not the agent key");
        let now = now_secs();
        require!(now < agent.expires_at, "agent registration expired");
        require!(now >= self.last_agent_action + self.cooldown_secs, "cooldown");
        self.last_agent_action = now;
    }

    /// Build, decode back, check, and request one signature per transaction; `on_signed` assembles them.
    fn sign(&self, calls: Vec<(Addr, Vec<u8>)>, params: Vec<TxParams>, caller: Caller) -> PromiseOrValue<Vec<SignedTx>> {
        require!(
            params.len() == calls.len(),
            format!("expected {} transaction parameter sets, got {}", calls.len(), params.len())
        );
        if calls.is_empty() {
            return PromiseOrValue::Value(vec![]);
        }
        let now = now_secs();
        let mut txs = Vec::with_capacity(calls.len());
        let mut joint: Option<Promise> = None;
        for ((to, data), p) in calls.iter().zip(params.iter()) {
            let t = BaseTx::new(p, to, data);
            let unsigned = t.omni().build_for_signing();
            let d = decode_unsigned(&unsigned).unwrap_or_else(|e| env::panic_str(&e));
            self.check_tx(&d, caller, now).unwrap_or_else(|e| env::panic_str(&format!("transaction refused: {e}")));
            let payload = env::keccak256_array(&unsigned);
            let request = json!({"request": {"path": self.path, "payload_v2": {"Ecdsa": hex::encode(payload)}, "domain_id": 0}});
            let s = Promise::new(self.signer.clone()).function_call(
                "sign",
                serde_json::to_vec(&request).unwrap(),
                SIGN_DEPOSIT,
                SIGN_GAS,
            );
            joint = Some(match joint {
                None => s,
                Some(j) => j.and(s),
            });
            txs.push(t);
        }
        PromiseOrValue::Promise(
            joint.unwrap().then(Self::ext(env::current_account_id()).with_static_gas(CALLBACK_GAS).on_signed(txs)),
        )
    }

    /// The allowlist (§4.1): chain id, value 0, gas and fee bounds; `to` in {executor, the markets' tokens,
    /// Morpho}; selector per target and caller; approve spender and setAuthorization target; market ids;
    /// executor deadline within (now, now + 600 s].
    pub fn check_tx(&self, d: &Decoded, caller: Caller, now: u64) -> Result<(), String> {
        if d.chain_id != CHAIN_ID {
            return Err(format!("chain id {} is not {}", d.chain_id, CHAIN_ID));
        }
        if d.value != 0 {
            return Err("value must be 0".into());
        }
        if d.gas_limit == 0 || d.gas_limit > GAS_LIMIT_MAX as u128 {
            return Err(format!("gas_limit {} outside (0, {}]", d.gas_limit, GAS_LIMIT_MAX));
        }
        if d.max_fee_per_gas > MAX_FEE_PER_GAS_MAX as u128 {
            return Err(format!("max_fee_per_gas {} above {}", d.max_fee_per_gas, MAX_FEE_PER_GAS_MAX));
        }
        if d.max_priority_fee_per_gas > d.max_fee_per_gas {
            return Err("priority fee above max fee".into());
        }
        if d.data.len() < 4 {
            return Err("no selector".into());
        }
        let sel: [u8; 4] = d.data[..4].try_into().unwrap();

        if d.to == self.morpho {
            if caller != Caller::Owner {
                return Err("the agent may not call Morpho".into());
            }
            if sel != SEL_SET_AUTHORIZATION {
                return Err("selector not allowed on Morpho".into());
            }
            let w = words(&d.data, 2)?;
            let (who, on) = (word_to_addr(&w[0])?, word_to_bool(&w[1])?);
            let current = self.markets.iter().any(|m| m.executor == who);
            let previous = self.markets.iter().any(|m| m.previous_executors.contains(&who));
            return match (on, current, previous) {
                (true, true, _) | (false, false, true) => Ok(()),
                _ => Err("setAuthorization: only true for a current executor, false for a previous one".into()),
            };
        }

        let token_markets: Vec<&Market> =
            self.markets.iter().filter(|m| m.loan_token == d.to || m.collateral_token == d.to).collect();
        if !token_markets.is_empty() {
            if caller != Caller::Owner {
                return Err("the agent may not call tokens".into());
            }
            let w = words(&d.data, 2)?;
            let who = word_to_addr(&w[0])?;
            return match sel {
                SEL_APPROVE => {
                    let zero = w[1] == [0u8; 32];
                    if token_markets.iter().any(|m| m.executor == who)
                        || (zero && token_markets.iter().any(|m| m.previous_executors.contains(&who)))
                    {
                        Ok(())
                    } else {
                        Err("approve: spender is not the market's executor (or a previous one with 0)".into())
                    }
                }
                SEL_TRANSFER if who != [0u8; 20] => Ok(()),
                SEL_TRANSFER => Err("transfer to the zero address".into()),
                _ => Err("selector not allowed on a token".into()),
            };
        }

        let exec_markets: Vec<&Market> = self.markets.iter().filter(|m| m.executor == d.to).collect();
        if exec_markets.is_empty() {
            return Err("to is not the executor, a market token or Morpho".into());
        }
        let (n, ids, who) = match sel {
            SEL_SET_CORRIDOR => (9, 1, Caller::Owner),
            SEL_OPEN => (4, 1, Caller::Owner),
            SEL_REPAY => (3, 1, Caller::Owner),
            SEL_CLOSE => (2, 1, Caller::Owner),
            SEL_REDUCE => (3, 1, Caller::Agent),
            SEL_MIGRATE => (4, 2, Caller::Agent),
            SEL_EXIT => (2, 1, Caller::Agent),
            _ => return Err("selector not allowed on the executor".into()),
        };
        if who != caller {
            return Err(format!("selector not allowed for the {caller:?}"));
        }
        let w = words(&d.data, n)?;
        for id in &w[..ids] {
            if !exec_markets.iter().any(|m| &m.id == id) {
                return Err("market id not allowlisted on this executor".into());
            }
        }
        if sel == SEL_SET_CORRIDOR {
            for x in &w[1..5] {
                word_to_u64(x)?;
            }
            for x in &w[5..8] {
                word_to_bool(x)?;
            }
        }
        let deadline = word_to_u64(&w[n - 1])?;
        if deadline <= now || deadline > now + DEADLINE_SECS {
            return Err(format!("deadline {deadline} outside ({now}, {}]", now + DEADLINE_SECS));
        }
        Ok(())
    }
}
