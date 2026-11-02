//! cmdk vault factory on NEAR (hackathon build, item V4). Spec: docs/01-design-and-feasibility.md §4.1 (the
//! `init` row) and §8 ("The factory is a keyless immutable contract with the vault code hash fixed inside it").
//!
//! Deployed once to its own account and initialised once; after deployment every key of that account is deleted,
//! so the factory is keyless: nobody, including us, can redeploy it, change its state or move its balance. There
//! is no owner and no upgrade method. The vault code hash is fixed at `init` and cannot change; a new vault
//! version means a new factory.
//!
//! `create_vault(owner, policy)`, callable by anyone with the storage deposit attached:
//! 1. asks the Chain Signatures contract for `derived_public_key(path, predecessor = new vault id, domain_id 0)`
//!    (the key the signer will use when that vault calls `sign`);
//! 2. in `on_derived_key`, turns it into the vault's Base address (`keccak256(x || y)[12..]`) and sends one batch to
//!    `v{n}.<factory>`: `CreateAccount`, `Transfer(deposit)`, `UseGlobalContract(code hash)`, `FunctionCall(init)`.
//!    The vault account gets no keys. The vault's `init` validates the owner and the policy.
//!
//! Before any state change or promise, `create_vault` checks everything the vault's `init` checks that the
//! factory can check too (hex lengths, a non-zero executor, at least one market, no duplicate market ids); a
//! refusal there panics in the entry call, so the protocol returns the deposit to the caller and no vault number
//! is used. The vault re-checks and stays the final judge. If the signer call fails, `on_derived_key` returns the
//! deposit to the caller. After these checks, the only way for a deposit to stay in the factory is an `init`
//! failure the factory could not foresee: the protocol then returns the batch's `Transfer` to the factory, and a
//! keyless factory has no way to pass it on.
use near_sdk::json_types::{Base58CryptoHash, U128};
use near_sdk::serde_json::json;
use near_sdk::{env, near, require, AccountId, CurveType, Gas, NearToken, PanicOnDefault, Promise, PublicKey};

/// Attached to `derived_public_key`: research H measured 5.2-5.7 Tgas burnt on testnet.
const DERIVE_GAS: Gas = Gas::from_tgas(15);
/// Attached to the vault's `init` inside the batch.
const INIT_GAS: Gas = Gas::from_tgas(30);
/// Static gas for `on_derived_key`: its own work plus the batch, including `INIT_GAS`.
const CALLBACK_GAS: Gas = Gas::from_tgas(60);

/// Same JSON shape as the vault's `MarketInit`; forwarded unchanged, the vault validates it.
#[near(serializers = [json])]
pub struct MarketInit {
    pub id: String,
    pub loan_token: String,
    pub collateral_token: String,
    pub executor: String,
    pub slice_max: U128,
}

/// Same JSON shape as the vault's `Policy`; forwarded unchanged, the vault validates it.
#[near(serializers = [json])]
pub struct Policy {
    pub cooldown_secs: u64,
    pub markets: Vec<MarketInit>,
}

#[near(serializers = [json])]
pub struct Config {
    /// The vault Global Contract's code hash, base58.
    pub code_hash: Base58CryptoHash,
    pub signer: AccountId,
    pub path: String,
    pub morpho: String,
    /// Vaults created so far; the next one is `v{count + 1}.<factory>`.
    pub count: u64,
}

#[near(contract_state)]
#[derive(PanicOnDefault)]
pub struct Factory {
    /// sha256 of the vault wasm, published as a Global Contract by hash; fixed at `init`.
    pub code_hash: [u8; 32],
    /// Chain Signatures contract (`v1.signer-prod.testnet`; a mock in the sandbox tests).
    pub signer: AccountId,
    /// Derivation path every vault uses with the signer.
    pub path: String,
    /// Morpho Blue on Base, `0x` hex.
    pub morpho: String,
    pub count: u64,
}

fn hex_bytes(s: &str, n: usize, what: &str) -> Vec<u8> {
    match hex::decode(s.strip_prefix("0x").unwrap_or(s)) {
        Ok(b) if b.len() == n => b,
        _ => env::panic_str(&format!("{what}: expected {n} bytes of hex")),
    }
}

/// The checks of the vault's `init` that do not need the vault: run in the entry call, so a refusal refunds.
fn validate(owner: &str, policy: &Policy) {
    hex_bytes(owner, 20, "owner");
    require!(!policy.markets.is_empty(), "no markets");
    let mut ids: Vec<Vec<u8>> = vec![];
    for m in &policy.markets {
        let id = hex_bytes(&m.id, 32, "market id");
        hex_bytes(&m.loan_token, 20, "loan_token");
        hex_bytes(&m.collateral_token, 20, "collateral_token");
        require!(hex_bytes(&m.executor, 20, "executor") != [0u8; 20], "zero executor");
        require!(!ids.contains(&id), "duplicate market");
        ids.push(id);
    }
}

#[near]
impl Factory {
    #[init]
    pub fn init(code_hash: Base58CryptoHash, signer: AccountId, path: String, morpho: String) -> Self {
        hex_bytes(&morpho, 20, "morpho");
        Self { code_hash: code_hash.into(), signer, path, morpho, count: 0 }
    }

    /// Creates `v{n}.<factory>` for `owner` (EVM address) with `policy`. The attached deposit becomes the vault's
    /// balance and must cover its storage. Refuses, before any state change, what the vault's `init` would refuse.
    #[payable]
    pub fn create_vault(&mut self, owner: String, policy: Policy) -> Promise {
        validate(&owner, &policy);
        self.count += 1;
        let vault_id = self.vault_id(self.count);
        let args = json!({"path": self.path, "predecessor": vault_id, "domain_id": 0});
        Promise::new(self.signer.clone())
            .function_call("derived_public_key", args.to_string().into_bytes(), NearToken::from_near(0), DERIVE_GAS)
            .then(Self::ext(env::current_account_id()).with_static_gas(CALLBACK_GAS).on_derived_key(
                vault_id,
                owner,
                policy,
                env::attached_deposit(),
                env::predecessor_account_id(),
            ))
    }

    /// Receives the derived key and creates the vault in one batch; if the signer call failed, returns the
    /// deposit to the caller instead (no panic, so that the refund is not rolled back).
    #[private]
    pub fn on_derived_key(
        &self,
        vault_id: AccountId,
        owner: String,
        policy: Policy,
        deposit: NearToken,
        caller: AccountId,
    ) -> Promise {
        let bytes = match env::promise_result_checked(0, 256) {
            Ok(b) => b,
            Err(_) => {
                env::log_str(&format!("derived_public_key failed for {vault_id}; deposit returned to {caller}"));
                return Promise::new(caller).transfer(deposit);
            }
        };
        let pk: PublicKey = near_sdk::serde_json::from_slice(&bytes)
            .unwrap_or_else(|_| env::panic_str("bad derived_public_key result"));
        require!(pk.curve_type() == CurveType::SECP256K1, "derived key is not secp256k1");
        let h = env::keccak256_array(&pk.as_bytes()[1..]);
        let base_address = format!("0x{}", hex::encode(&h[12..]));
        let init = json!({"owner": owner, "signer": self.signer, "path": self.path, "base_address": base_address,
                          "morpho": self.morpho, "policy": policy});
        env::log_str(&format!("creating vault: {vault_id} {base_address}"));
        let init = init.to_string().into_bytes();
        let batch = Promise::new(vault_id).create_account().transfer(deposit).use_global_contract(self.code_hash);
        batch.function_call("init", init, NearToken::from_near(0), INIT_GAS)
    }

    pub fn get_config(&self) -> Config {
        Config {
            code_hash: self.code_hash.into(),
            signer: self.signer.clone(),
            path: self.path.clone(),
            morpho: self.morpho.clone(),
            count: self.count,
        }
    }

    /// The account id of vault number `n` (1-based).
    pub fn vault_id(&self, n: u64) -> AccountId {
        format!("v{n}.{}", env::current_account_id()).parse().unwrap_or_else(|_| env::panic_str("bad vault id"))
    }
}
