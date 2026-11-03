//! Sandbox tests (near-workspaces 0.23, nearcore sandbox 2.13.4): owner messages, policy, the agent's
//! function-call key, cancel and sweep_eth, the transaction allowlist, Create vault in one `setup` message, and
//! the keyless factory (`factory/`) creating vaults from the vault published as a Global Contract. The Chain Signatures contract is replaced by
//! `mock-signer/` (same `sign` and `derived_public_key` arguments and response JSON); the real signer path runs on
//! testnet (research_notes, prototype results).
//!
//! Build first:  (cd mock-signer && cargo near build non-reproducible-wasm) && cargo near build non-reproducible-wasm
//!               (cd factory && cargo near build non-reproducible-wasm)
//! Run:          cargo test --test sandbox -- --nocapture
//! Owner messages are signed with `cast wallet sign` from throwaway keystores made per test in the temp dir
//! (deleted at the end); no key is stored in the repository or passed on a command line.
//! Agent calls: the E quote's key was made inside a CVM that no longer exists, so the registration test calls
//! from E's implicit account by giving it a test key in the sandbox (state patch); the agent methods then run
//! with a test function-call key patched in the same way. Neither patch is possible on a real network.
use anyhow::Result;
use cmdk_vault::evm::*;
use cmdk_vault::{Agent, Vault};
use near_sdk::borsh::{self, BorshDeserialize};
use near_sdk::serde_json::{self, json, Value};
use near_workspaces::network::Sandbox;
use near_workspaces::result::ExecutionFinalResult;
use near_workspaces::types::{AccessKey, AccessKeyPermission, Gas, KeyType, NearToken, SecretKey};
use near_workspaces::{Account, AccountId, Contract, Worker};
use omni_transaction::evm::EVMTransaction;
use rlp::{Rlp, RlpStream};
use std::path::PathBuf;
use std::process::Command;
use std::str::FromStr;

const LIVE: &str = "0xa25ac5171b7e500b82653d3750b91bb44e2409c32ca3c9e80b0db077e8a4535c";
const BACKUP: &str = "0x99d43c0f2a0eb6172774598a4e34e7eee09caa502692c2b2df0f40e1e3c5ea75";
const DEMO: &str = "0x55872b53dba6147b6badf9d4277bc166dfb7d5f8ac8d491da093960dbef6d585";
const UNKNOWN_MARKET: &str = "0x00000000000000000000000000000000000000000000000000000000000000aa";
const USDC: &str = "0x036CbD53842c5426634e7929541eC2318f3dCF7e";
const WETH: &str = "0x4200000000000000000000000000000000000006";
const DUSDC: &str = "0xeb68Fd5bAAAfD4e46b43bAa7fEAb3216B87b10d5";
const MORPHO: &str = "0xBBBBBbbBBb9cC5e90e3b3Af64bdAF62C37EEFFCb";
const EXEC: &str = "0xB23e0d7e1D0182Db7f6f2637EC4Ac51c10290f40";
const EXEC2: &str = "0x2222222222222222222222222222222222222222";
const STRANGER: &str = "0x3333333333333333333333333333333333333333";
const PATH: &str = "prime-base";
const SLICE: u128 = 1_000_000_000; // 1,000 USDC (6 decimals)
const COOLDOWN: u64 = 300;
const WAD: u128 = 1_000_000_000_000_000_000;
const E_DIR: &str = "tests/fixtures";
const F_COLLATERAL: &str = "tests/fixtures/collateral_phala.json";
// secp256k1 group order n, big-endian
const N: [u8; 32] = [
    0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xfe, 0xba, 0xae, 0xdc,
    0xe6, 0xaf, 0x48, 0xa0, 0x3b, 0xbf, 0xd2, 0x5e, 0x8c, 0xd0, 0x36, 0x41, 0x41,
];

fn a20(s: &str) -> [u8; 20] {
    hex::decode(&s[2..]).unwrap().try_into().unwrap()
}
fn a32(s: &str) -> [u8; 32] {
    hex::decode(&s[2..]).unwrap().try_into().unwrap()
}
fn cast() -> String {
    format!("{}/.foundry/bin/cast", std::env::var("HOME").unwrap())
}

/// A throwaway EVM wallet: an encrypted keystore in a temp dir, used only through `cast wallet sign`.
struct Wallet {
    keystore: String,
    address: String,
}
const PW: &str = "cmdk-sandbox-throwaway";
impl Wallet {
    fn new(dir: &PathBuf) -> Wallet {
        let out = Command::new(cast())
            .args(["wallet", "new", dir.to_str().unwrap(), "--unsafe-password", PW, "--json"])
            .output()
            .unwrap();
        let v: Value = serde_json::from_slice(&out.stdout).unwrap();
        let d = &v["data"][0];
        Wallet { keystore: d["path"].as_str().unwrap().into(), address: d["address"].as_str().unwrap().into() }
    }
    fn sign(&self, message: &str) -> String {
        let out = Command::new(cast())
            .args(["wallet", "sign", "--keystore", &self.keystore, "--password", PW, message])
            .output()
            .unwrap();
        assert!(out.status.success(), "{}", String::from_utf8_lossy(&out.stderr));
        String::from_utf8(out.stdout).unwrap().trim().into()
    }
}

/// keccak256 of hex bytes with `cast keccak` (independent of the vault's host keccak).
fn keccak(hex_bytes: &str) -> String {
    let out = Command::new(cast()).args(["keccak", hex_bytes]).output().unwrap();
    assert!(out.status.success(), "{}", String::from_utf8_lossy(&out.stderr));
    String::from_utf8(out.stdout).unwrap().trim().into()
}

/// Decode a raw signed transaction with `cast decode-transaction` (independent of the vault's own code).
fn decode_signed(raw: &str) -> Value {
    let out = Command::new(cast()).args(["decode-transaction", raw]).output().unwrap();
    assert!(out.status.success(), "{}", String::from_utf8_lossy(&out.stderr));
    serde_json::from_slice(&out.stdout).unwrap()
}

fn params(first_nonce: u64, n: usize) -> Vec<Value> {
    (0..n as u64)
        .map(|i| json!({"nonce": first_nonce + i, "gas_limit": 100_000, "max_fee_per_gas": 1_000_000_000u64, "max_priority_fee_per_gas": 1_000_000}))
        .collect()
}

fn fail(r: ExecutionFinalResult) -> String {
    assert!(!r.is_success(), "expected a failure, got success: {:?}", r.logs());
    format!("{:?}", r.into_result().unwrap_err())
}

fn expect_fail(r: ExecutionFinalResult, needle: &str) {
    let e = fail(r);
    assert!(e.contains(needle), "expected '{needle}' in: {e}");
}

fn signed(r: &ExecutionFinalResult) -> Vec<Value> {
    assert!(r.is_success(), "call failed: {:?}", r.clone().into_result().err());
    r.clone().json().unwrap()
}

struct Env {
    worker: Worker<Sandbox>,
    vault: Contract,
    signer: Contract,
    relayer: Account,
    owner: Wallet,
    other: Wallet,
    base: String,
    nonce: std::cell::Cell<u64>,
    dir: PathBuf,
    gas: std::cell::RefCell<Vec<(String, u64)>>,
}

impl Drop for Env {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.dir);
    }
}

async fn setup(tag: &str) -> Result<Env> {
    let worker = near_workspaces::sandbox().await?;
    let signer = worker.dev_deploy(&std::fs::read("mock-signer/target/near/cmdk_mock_signer.wasm")?).await?;
    let root = worker.root_account()?;
    let vault_acct =
        root.create_subaccount("vault").initial_balance(NearToken::from_near(30)).transact().await?.into_result()?;
    let vault = vault_acct.deploy(&std::fs::read("target/near/cmdk_vault.wasm")?).await?.into_result()?;
    let base: String = signer.view("derived_address").args_json(json!({"predecessor": vault.id(), "path": PATH})).await?.json()?;
    let dir = std::env::temp_dir().join(format!("cmdk-vault-sandbox-{tag}-{}", std::process::id()));
    std::fs::create_dir_all(&dir)?;
    let (owner, other) = (Wallet::new(&dir), Wallet::new(&dir));
    let m = |id: &str, loan: &str| json!({"id": id, "loan_token": loan, "collateral_token": WETH, "executor": EXEC, "slice_max": SLICE.to_string()});
    let r = vault
        .call("init")
        .args_json(json!({"owner": owner.address, "signer": signer.id(), "path": PATH, "base_address": base, "morpho": MORPHO,
            "policy": {"cooldown_secs": COOLDOWN, "markets": [m(LIVE, USDC), m(BACKUP, USDC), m(DEMO, DUSDC)]}}))
        .transact()
        .await?;
    assert!(r.is_success(), "{:?}", r.into_result().err());
    let relayer =
        root.create_subaccount("relayer").initial_balance(NearToken::from_near(20)).transact().await?.into_result()?;
    let (nonce, gas) = (Default::default(), Default::default());
    Ok(Env { worker, vault, signer, relayer, owner, other, base, nonce, dir, gas })
}

impl Env {
    async fn now(&self) -> Result<u64> {
        Ok(self.worker.view_block().await?.timestamp() / 1_000_000_000)
    }

    fn message(&self, method: &str, args: Value, nonce: u64, expiry: u64) -> String {
        json!({"protocol": "cmdk-vault/1", "network": "testnet", "vault": self.vault.id(), "method": method,
               "args": args, "nonce": nonce, "expiry": expiry})
        .to_string()
    }

    async fn submit(&self, message: &str, signature: &str, txs: Vec<Value>) -> Result<ExecutionFinalResult> {
        Ok(self
            .relayer
            .call(self.vault.id(), "owner_call")
            .args_json(json!({"message": message, "signature": signature, "txs": txs}))
            .gas(Gas::from_tgas(300))
            .transact()
            .await?)
    }

    /// Sign the next owner message and submit it with `ntx` transaction parameter sets.
    async fn owner(&self, method: &str, args: Value, ntx: usize) -> Result<ExecutionFinalResult> {
        let expiry = self.now().await? + 600;
        let m = self.message(method, args, self.nonce.get() + 1, expiry);
        let r = self.submit(&m, &self.owner.sign(&m), params(0, ntx)).await?;
        if r.is_success() {
            self.nonce.set(self.nonce.get() + 1);
        }
        Ok(r)
    }

    fn record(&self, what: &str, r: &ExecutionFinalResult) {
        let receipts: Vec<String> =
            r.receipt_outcomes().iter().map(|o| format!("{}:{:.3}", o.executor_id, o.gas_burnt.as_gas() as f64 / 1e12)).collect();
        self.gas.borrow_mut().push((format!("{what} | receipts {}", receipts.join(" ")), r.total_gas_burnt.as_gas()));
    }

    fn print_gas(&self, test: &str) {
        for (w, g) in self.gas.borrow().iter() {
            println!("GAS {test} | {w} | {g} | {:.3} Tgas", *g as f64 / 1e12);
        }
    }

    async fn state(&self) -> Result<Vault> {
        let s = self.worker.view_state(self.vault.id()).await?;
        Ok(Vault::try_from_slice(s.get(b"STATE".as_slice()).expect("STATE"))?)
    }

    async fn set_state(&self, v: &Vault) -> Result<()> {
        self.worker.patch(self.vault.id()).state(b"STATE", &borsh::to_vec(v)?).transact().await?;
        Ok(())
    }

    /// Sandbox only: give the vault a function-call key we hold, with the permission the vault itself grants,
    /// and point the vault's agent record at it.
    async fn patch_agent(&self, expires_at: u64) -> Result<Account> {
        let sk = SecretKey::from_random(KeyType::ED25519);
        let ak = AccessKey::function_call_access(self.vault.id(), &["reduce", "migrate", "exit"], Some(NearToken::from_near(2)));
        self.worker.patch(self.vault.id()).access_key(sk.public_key(), ak).transact().await?;
        let mut st = self.state().await?;
        st.agent = Some(Agent { public_key: near_sdk::PublicKey::from_str(&sk.public_key().to_string()).unwrap(), expires_at });
        self.set_state(&st).await?;
        Ok(Account::from_secret_key(self.vault.id().clone(), sk, &self.worker))
    }

    async fn reset_cooldown(&self) -> Result<()> {
        let mut st = self.state().await?;
        st.last_agent_action = 0;
        self.set_state(&st).await
    }

    /// An implicit account (id = hex of an ed25519 key) holding `sk` as its full-access key.
    async fn implicit(&self, key_hex: &str, sk: &SecretKey) -> Result<Account> {
        let id = AccountId::from_str(key_hex)?;
        self.worker
            .patch(&id)
            .account(near_workspaces::types::AccountDetailsPatch::default().balance(NearToken::from_near(5)))
            .access_key(sk.public_key(), AccessKey::full_access())
            .transact()
            .await?;
        Ok(Account::from_secret_key(id, sk.clone(), &self.worker))
    }

    async fn check(&self, tx: &EVMTransaction, caller: &str) -> Result<String> {
        self.check_hex(&hex::encode(tx.build_for_signing()), caller).await
    }

    /// `check_transaction` on arbitrary bytes (hex): "ok", or "ERR ..." with the view's panic message.
    async fn check_hex(&self, tx_hex: &str, caller: &str) -> Result<String> {
        let r = self.vault.view("check_transaction").args_json(json!({"tx_hex": tx_hex, "caller": caller})).await;
        Ok(match r {
            Ok(v) => v.json::<String>()?,
            Err(e) => format!("ERR {e:?}"),
        })
    }
}

fn e_measurement() -> Value {
    json!({
        "mrtd": "7fe60787222bd394cb516abca2435f22f035ab2cc9c0a4b4b3b148e46297f3d931a237b72f359052c6e657d8c1409173",
        "rtmr0": "68102e7b524af310f7b7d426ce75481e36c40f5d513a9009c046e9d37e31551f0134d954b496a3357fd61d03f07ffe96",
        "rtmr1": "4ae609807c765916a31d724786ed74bcb97e168a9d937b04936aeef2015c907eba5ac352c6bcd749c246fd62ffb659ec",
        "rtmr2": "80e945df96ec417b68d574dba18d199a7448d66c3d0e56f073167a9513d2381601f5d614853646d2c49c5d8d58a7daf5",
        "compose_hash": "78eee1165c9da208d460157cb625519e6422c1a2b833ad678b10556355b769e2",
        "os_image_hash": "e866e987221db357129ebc3f066b6706ef955889ea95e5510acbdf3d3055186b",
    })
}

fn word_hex(data: &str, i: usize) -> String {
    // data: 0x + selector(8 hex) + words
    data[10 + 64 * i..10 + 64 * (i + 1)].to_string()
}

fn u256_of(data: &str, i: usize) -> u128 {
    u128::from_str_radix(&word_hex(data, i)[32..], 16).unwrap()
}

// ======================================================================== owner messages

#[tokio::test]
async fn owner_messages() -> Result<()> {
    let env = setup("owner").await?;

    // A valid message: setup() produces three signed transactions from the vault's Base address.
    let a = json!({"markets": [{"market": LIVE, "approve": true, "authorize": true}]});
    let r = env.owner("setup", a, 3).await?;
    env.record("owner_call setup, 3 mock signatures", &r);
    let txs = signed(&r);
    assert_eq!(txs.len(), 3);
    let want = [(USDC, "0x095ea7b3"), (WETH, "0x095ea7b3"), (MORPHO, "0xeecea000")];
    for (i, (t, (to, sel))) in txs.iter().zip(want).enumerate() {
        assert_eq!(t["from"].as_str().unwrap(), env.base.to_lowercase());
        let d = decode_signed(t["raw"].as_str().unwrap());
        assert_eq!(d["to"].as_str().unwrap().to_lowercase(), to.to_lowercase(), "{d}");
        assert_eq!(&d["input"].as_str().unwrap()[..10], sel);
        assert_eq!(u64::from_str_radix(d["chainId"].as_str().unwrap().trim_start_matches("0x"), 16)?, 84532);
        assert_eq!(u64::from_str_radix(d["nonce"].as_str().unwrap().trim_start_matches("0x"), 16)?, i as u64);
        assert_eq!(t["hash"].as_str().unwrap(), d["hash"].as_str().unwrap(), "hash-to-be equals cast's");
    }
    assert_eq!(env.state().await?.owner_nonce, 1);

    // Replay of the same message and signature.
    let expiry = env.now().await? + 600;
    let m = env.message("close", json!({"market": LIVE}), 2, expiry);
    let s = env.owner.sign(&m);
    let r = env.submit(&m, &s, params(3, 1)).await?;
    env.record("owner_call close, 1 mock signature", &r);
    signed(&r);
    expect_fail(env.submit(&m, &s, params(3, 1)).await?, "nonce not above the last one");
    // A lower nonce, then a gap (nonce 9 after 2) is accepted: strictly increasing, not consecutive.
    let m1 = env.message("close", json!({"market": LIVE}), 1, expiry);
    expect_fail(env.submit(&m1, &env.owner.sign(&m1), params(3, 1)).await?, "nonce not above the last one");
    let m9 = env.message("close", json!({"market": LIVE}), 9, expiry);
    signed(&env.submit(&m9, &env.owner.sign(&m9), params(3, 1)).await?);
    assert_eq!(env.state().await?.owner_nonce, 9);
    env.nonce.set(9);

    // Expired (checked against block time).
    let now = env.now().await?;
    let m = env.message("close", json!({"market": LIVE}), 10, now - 1);
    expect_fail(env.submit(&m, &env.owner.sign(&m), params(3, 1)).await?, "message expired");
    // Expiry more than an hour after block time (V6); the unit tests in owner.rs check the exact bound.
    let m = env.message("close", json!({"market": LIVE}), 10, now + 3600 + 60);
    expect_fail(env.submit(&m, &env.owner.sign(&m), params(3, 1)).await?, "message expiry too far ahead");

    // Signed by another wallet.
    let m = env.message("close", json!({"market": LIVE}), 10, now + 600);
    expect_fail(env.submit(&m, &env.other.sign(&m), params(3, 1)).await?, "signature is not the owner's");

    // Message changed after signing (the amount): recovers to another address.
    let m = env.message("repay", json!({"market": LIVE, "amount": "1"}), 10, now + 600);
    let s = env.owner.sign(&m);
    let tampered = m.replace("\"1\"", "\"1000000000\"");
    expect_fail(env.submit(&tampered, &s, params(3, 1)).await?, "signature is not the owner's");

    // High-s twin of a valid signature (s' = n - s, v flipped): rejected by the host's malleability check.
    let sig = hex::decode(&s[2..])?;
    let mut s2 = [0u8; 32];
    let mut borrow = 0i16;
    for i in (0..32).rev() {
        let d = N[i] as i16 - sig[32 + i] as i16 - borrow;
        borrow = (d < 0) as i16;
        s2[i] = (d + 256 * borrow) as u8;
    }
    let mut high = sig.clone();
    high[32..64].copy_from_slice(&s2);
    high[64] = if sig[64] == 27 { 28 } else { 27 };
    expect_fail(env.submit(&m, &format!("0x{}", hex::encode(&high)), params(3, 1)).await?, "bad owner signature");
    // ... while the original low-s signature still works.
    signed(&env.submit(&m, &s, params(3, 1)).await?);
    env.nonce.set(10);

    // 64-byte signature.
    let m = env.message("close", json!({"market": LIVE}), 11, now + 600);
    let short = env.owner.sign(&m)[..130].to_string();
    expect_fail(env.submit(&m, &short, params(3, 1)).await?, "bad owner signature");

    // Wrong vault, network, protocol; unknown method; unknown field.
    let other_vault = m.replace(env.vault.id().as_str(), "other.test.near");
    expect_fail(env.submit(&other_vault, &env.owner.sign(&other_vault), params(3, 1)).await?, "message is for another vault");
    let mainnet = m.replace("\"testnet\"", "\"mainnet\"");
    expect_fail(env.submit(&mainnet, &env.owner.sign(&mainnet), params(3, 1)).await?, "wrong network");
    let v2 = m.replace("cmdk-vault/1", "cmdk-vault/2");
    expect_fail(env.submit(&v2, &env.owner.sign(&v2), params(3, 1)).await?, "wrong protocol version");
    let unknown = env.message("drain", json!({}), 11, now + 600);
    expect_fail(env.submit(&unknown, &env.owner.sign(&unknown), vec![]).await?, "unknown method drain");
    let extra = m.replacen('{', "{\"to\":\"0x00\",", 1);
    expect_fail(env.submit(&extra, &env.owner.sign(&extra), params(3, 1)).await?, "bad owner message");
    let extra_arg = env.message("close", json!({"market": LIVE, "recipient": STRANGER}), 11, now + 600);
    expect_fail(env.submit(&extra_arg, &env.owner.sign(&extra_arg), params(3, 1)).await?, "bad args");
    // Nonce unchanged by all the failures.
    assert_eq!(env.state().await?.owner_nonce, 10);

    // setup() without the Morpho authorization (the relayer read it as already set): the two approvals only.
    let a = json!({"markets": [{"market": LIVE, "approve": true, "authorize": false}]});
    let r = env.owner("setup", a, 2).await?;
    env.record("owner_call setup, 2 mock signatures", &r);
    let txs = signed(&r);
    assert_eq!(txs.len(), 2);
    for (t, to) in txs.iter().zip([USDC, WETH]) {
        let d = decode_signed(t["raw"].as_str().unwrap());
        assert_eq!(d["to"].as_str().unwrap().to_lowercase(), to.to_lowercase(), "{d}");
        assert_eq!(&d["input"].as_str().unwrap()[..10], "0x095ea7b3");
    }
    assert_eq!(env.state().await?.owner_nonce, 11);
    // setup() without `authorize` is refused; the nonce stays.
    expect_fail(env.owner("setup", json!({"markets": [{"market": LIVE, "approve": true}]}), 3).await?, "bad args");
    assert_eq!(env.state().await?.owner_nonce, 11);

    // An expiry exactly an hour after the chain time read here is accepted (block time only moves forward).
    let m = env.message("close", json!({"market": LIVE}), 12, env.now().await? + 3600);
    signed(&env.submit(&m, &env.owner.sign(&m), params(3, 1)).await?);
    assert_eq!(env.state().await?.owner_nonce, 12);
    env.nonce.set(12);

    env.print_gas("owner_messages");
    Ok(())
}

// ======================================================================== policy

#[tokio::test]
async fn policy() -> Result<()> {
    let env = setup("policy").await?;
    let now = env.now().await?;

    // set_corridor: one executor call; the vault records that the market's corridor is set.
    let r = env
        .owner("set_corridor", json!({"market": LIVE, "target": "500000000000000000", "act": "700000000000000000",
            "floor": "300000000000000000", "sell_budget": "200000000000000000", "migrate_allowed": true, "exit_allowed": true}), 1)
        .await?;
    env.record("owner_call set_corridor, 1 mock signature", &r);
    let t = &signed(&r)[0];
    let d = decode_signed(t["raw"].as_str().unwrap());
    let data = d["input"].as_str().unwrap();
    assert_eq!(d["to"].as_str().unwrap().to_lowercase(), EXEC.to_lowercase());
    assert_eq!(&data[..10], "0x253616b9");
    assert_eq!(word_hex(data, 0), LIVE[2..]);
    assert_eq!(u256_of(data, 1), 5 * WAD / 10);
    assert_eq!(u256_of(data, 7), 0, "`set` is left to the executor");
    let dl = u256_of(data, 8) as u64;
    assert!(dl > now && dl <= env.now().await? + 600, "deadline {dl} is block time + 600 s");
    assert!(env.state().await?.markets[0].corridor_set);

    // open / repay / close: market id, arguments and deadline in place.
    let r = env.owner("open", json!({"market": LIVE, "collateral_in": "2000000000000000", "target_ltv": "500000000000000000"}), 1).await?;
    env.record("owner_call open, 1 mock signature", &r);
    let data = decode_signed(signed(&r)[0]["raw"].as_str().unwrap())["input"].as_str().unwrap().to_string();
    assert_eq!((&data[..10], u256_of(&data, 1), u256_of(&data, 2)), ("0x2dc181a4", 2_000_000_000_000_000, 5 * WAD / 10));
    // open_from_stables (V6): one executor call, openFromStables(id, stablesIn, targetLtv, deadline), owner only.
    let before = env.now().await?;
    let r = env.owner("open_from_stables", json!({"market": LIVE, "stables_in": "250000000", "target_ltv": "400000000000000000"}), 1).await?;
    env.record("owner_call open_from_stables, 1 mock signature", &r);
    let txs = signed(&r);
    assert_eq!(txs.len(), 1);
    let d = decode_signed(txs[0]["raw"].as_str().unwrap());
    let data = d["input"].as_str().unwrap();
    assert_eq!(d["to"].as_str().unwrap().to_lowercase(), EXEC.to_lowercase());
    assert_eq!(hex_u128(&d["value"]), 0);
    assert_eq!(data.len(), 10 + 4 * 64, "selector and four words");
    assert_eq!((&data[..10], word_hex(data, 0)), ("0x5f447773", LIVE[2..].to_string()));
    assert_eq!((u256_of(data, 1), u256_of(data, 2)), (250_000_000, 4 * WAD / 10));
    let dl = u256_of(data, 3) as u64;
    assert!(dl > before && dl <= env.now().await? + 600, "deadline {dl} is block time + 600 s");
    let unsigned = unsigned_hex(txs[0]["raw"].as_str().unwrap())?;
    assert_eq!(env.check_hex(&unsigned, "owner").await?, "ok");
    assert!(env.check_hex(&unsigned, "agent").await?.contains("not allowed for the Agent"));
    // Unknown or missing fields, a market outside the allowlist; the nonce stays.
    let n = env.state().await?.owner_nonce;
    let extra = json!({"market": LIVE, "stables_in": "1", "target_ltv": "1", "recipient": STRANGER});
    expect_fail(env.owner("open_from_stables", extra, 1).await?, "bad args");
    expect_fail(env.owner("open_from_stables", json!({"market": LIVE, "collateral_in": "1", "target_ltv": "1"}), 1).await?, "bad args");
    expect_fail(env.owner("open_from_stables", json!({"market": UNKNOWN_MARKET, "stables_in": "1", "target_ltv": "1"}), 1).await?, "market not in the allowlist");
    // stables_in 0 is refused (decided 3 Nov); open with collateral_in 0 stays allowed (borrow more).
    expect_fail(env.owner("open_from_stables", json!({"market": LIVE, "stables_in": "0", "target_ltv": "1"}), 1).await?, "zero stables_in");
    assert_eq!(env.state().await?.owner_nonce, n);
    let r = env.owner("repay", json!({"market": DEMO, "amount": "500000"}), 1).await?;
    let data = decode_signed(signed(&r)[0]["raw"].as_str().unwrap())["input"].as_str().unwrap().to_string();
    assert_eq!((&data[..10], word_hex(&data, 0), u256_of(&data, 1)), ("0xe4858494", DEMO[2..].to_string(), 500_000));
    let r = env.owner("close", json!({"market": DEMO}), 1).await?;
    assert_eq!(&decode_signed(signed(&r)[0]["raw"].as_str().unwrap())["input"].as_str().unwrap()[..10], "0x77e283d0");

    // A market outside the allowlist.
    expect_fail(env.owner("open", json!({"market": UNKNOWN_MARKET, "collateral_in": "1", "target_ltv": "1"}), 1).await?, "market not in the allowlist");

    // withdraw: a plain transfer of an allowlisted token to the owner-named destination.
    let r = env.owner("withdraw", json!({"token": WETH, "amount": "1000", "destination": STRANGER}), 1).await?;
    env.record("owner_call withdraw, 1 mock signature", &r);
    let d = decode_signed(signed(&r)[0]["raw"].as_str().unwrap());
    let data = d["input"].as_str().unwrap();
    assert_eq!(d["to"].as_str().unwrap().to_lowercase(), WETH.to_lowercase());
    assert_eq!((&data[..10], &word_hex(data, 0)[24..], u256_of(data, 1)), ("0xa9059cbb", &STRANGER[2..], 1000));
    expect_fail(env.owner("withdraw", json!({"token": STRANGER, "amount": "1", "destination": STRANGER}), 1).await?, "token not in the allowlist");
    expect_fail(env.owner("withdraw", json!({"token": USDC, "amount": "1", "destination": "0x0000000000000000000000000000000000000000"}), 1).await?, "zero destination");

    // Transaction parameters: count, gas limit and fee bounds. 1,000,000 gas is accepted (migrate 443,948 × 1.3 fits).
    expect_fail(env.owner("close", json!({"market": LIVE}), 0).await?, "expected 1 transaction parameter sets, got 0");
    let expiry = env.now().await? + 600;
    for (gas, fee, needle) in [
        (1_000_001u64, 1_000_000_000u64, "gas_limit 1000001 outside"),
        (100_000, 2_000_000_001, "max_fee_per_gas 2000000001 above"),
    ] {
        let m = env.message("close", json!({"market": LIVE}), env.nonce.get() + 1, expiry);
        let p = vec![json!({"nonce": 0, "gas_limit": gas, "max_fee_per_gas": fee, "max_priority_fee_per_gas": 1})];
        expect_fail(env.submit(&m, &env.owner.sign(&m), p).await?, needle);
    }
    let m = env.message("close", json!({"market": LIVE}), env.nonce.get() + 1, expiry);
    let p = vec![json!({"nonce": 0, "gas_limit": 1_000_000, "max_fee_per_gas": 2_000_000_000u64, "max_priority_fee_per_gas": 2_000_000_000u64})];
    signed(&env.submit(&m, &env.owner.sign(&m), p).await?);
    env.nonce.set(env.nonce.get() + 1);

    // set_executor: a pointer change per market; the old executor is revoked once no market uses it, on every token.
    // Without `revoke_authorization` the message is refused and the nonce stays.
    let n = env.state().await?.owner_nonce;
    let no_flag = json!({"market": LIVE, "executor": EXEC2});
    expect_fail(env.owner("set_executor", no_flag, 0).await?, "bad args");
    assert_eq!(env.state().await?.owner_nonce, n);
    let move_to = |m: &str, e: &str, revoke: bool| json!({"market": m, "executor": e, "revoke_authorization": revoke});
    let r = env.owner("set_executor", move_to(LIVE, EXEC2, true), 0).await?;
    assert_eq!(signed(&r).len(), 0, "EXEC still serves backup and demo");
    let st = env.state().await?;
    assert_eq!((st.markets[0].executor, st.markets[0].previous_executors.clone(), st.markets[0].corridor_set), (a20(EXEC2), vec![a20(EXEC)], false));
    signed(&env.owner("set_executor", move_to(BACKUP, EXEC2, true), 0).await?);
    let r = env.owner("set_executor", move_to(DEMO, EXEC2, true), 4).await?;
    env.record("owner_call set_executor (last market), 4 mock signatures", &r);
    let txs = signed(&r);
    let got: Vec<(String, String)> = txs
        .iter()
        .map(|t| {
            let d = decode_signed(t["raw"].as_str().unwrap());
            (d["to"].as_str().unwrap().to_lowercase(), d["input"].as_str().unwrap().to_string())
        })
        .collect();
    let revoke_auth = format!("0xeecea000{:0>64}{:0>64}", &EXEC[2..].to_lowercase(), "0");
    let approve0 = format!("0x095ea7b3{:0>64}{:0>64}", &EXEC[2..].to_lowercase(), "0");
    assert_eq!(got[0], (MORPHO.to_lowercase(), revoke_auth));
    let mut tokens: Vec<String> = got[1..].iter().map(|(to, data)| { assert_eq!(data, &approve0); to.clone() }).collect();
    tokens.sort();
    let mut want = vec![USDC.to_lowercase(), WETH.to_lowercase(), DUSDC.to_lowercase()];
    want.sort();
    assert_eq!(tokens, want, "allowance 0 on USDC, WETH and dUSDC");
    // setup() for the new executor, then the agent waits for set_corridor (agent_key test covers the refusal).
    let a = json!({"markets": [{"market": LIVE, "approve": true, "authorize": true}]});
    let r = env.owner("setup", a, 3).await?;
    let d = decode_signed(signed(&r)[0]["raw"].as_str().unwrap());
    assert_eq!(&word_hex(d["input"].as_str().unwrap(), 0)[24..], &EXEC2[2..].to_lowercase());
    // Every market back to EXEC, the last move with `revoke_authorization: false` (the relayer read EXEC2 as not
    // authorized on Morpho): the allowance resets only, on every token of every market EXEC2 served.
    signed(&env.owner("set_executor", move_to(LIVE, EXEC, true), 0).await?);
    signed(&env.owner("set_executor", move_to(BACKUP, EXEC, true), 0).await?);
    let r = env.owner("set_executor", move_to(DEMO, EXEC, false), 3).await?;
    env.record("owner_call set_executor (no revoke), 3 mock signatures", &r);
    let txs = signed(&r);
    assert_eq!(txs.len(), 3);
    let approve0 = format!("0x095ea7b3{:0>64}{:0>64}", &EXEC2[2..].to_lowercase(), "0");
    let mut tokens = vec![];
    for t in &txs {
        let d = decode_signed(t["raw"].as_str().unwrap());
        assert_eq!(d["input"].as_str().unwrap(), approve0, "{d}");
        tokens.push(d["to"].as_str().unwrap().to_lowercase());
    }
    tokens.sort();
    assert_eq!(tokens, want, "allowance 0 on USDC, WETH and dUSDC; no setAuthorization");

    // approve_measurement: validated and stored; revoke_agent clears it.
    expect_fail(env.owner("approve_measurement", json!({"mrtd": "00", "rtmr0": "", "rtmr1": "", "rtmr2": "", "compose_hash": "", "os_image_hash": ""}), 0).await?, "mrtd: expected 48 bytes");
    let r = env.owner("approve_measurement", e_measurement(), 0).await?;
    env.record("owner_call approve_measurement", &r);
    assert!(r.is_success());
    assert!(env.state().await?.approved.is_some());
    let r = env.owner("revoke_agent", json!({}), 0).await?;
    env.record("owner_call revoke_agent (no agent key)", &r);
    assert!(r.is_success());
    assert!(env.state().await?.approved.is_none());

    env.print_gas("policy");
    Ok(())
}

// ======================================================================== agent key

#[tokio::test]
async fn agent_key() -> Result<()> {
    let env = setup("agent").await?;
    let quote_hex = std::fs::read_to_string(format!("{E_DIR}/tdx_quote_prime-e-test.hex"))?.trim().to_string();
    let collateral = std::fs::read_to_string(F_COLLATERAL)?;
    let event_log: Value = serde_json::from_str(&std::fs::read_to_string(format!("{E_DIR}/event_log.json"))?)?;
    // report_data = E's agent key || 32 zero bytes (TD report body offset 520, after the 48-byte header).
    let q = hex::decode(&quote_hex)?;
    let e_key = hex::encode(&q[48 + 520..48 + 552]);
    let reg_args = |quote: &str, log: &Value| json!({"quote_hex": quote, "collateral": collateral, "event_log": log});

    // E's implicit account, given a key we hold (sandbox only).
    let e_acct = env.implicit(&e_key, &SecretKey::from_random(KeyType::ED25519)).await?;
    let call_reg = |acct: &Account, args: Value| {
        acct.call(env.vault.id(), "register_agent").args_json(args).gas(Gas::from_tgas(300)).transact()
    };

    // Before the owner approves a measurement.
    expect_fail(call_reg(&e_acct, reg_args(&quote_hex, &event_log)).await?, "no approved measurement");
    signed(&env.owner("approve_measurement", e_measurement(), 0).await?);

    // From a named account, and from another implicit account (report_data is not its key).
    expect_fail(call_reg(&env.relayer, reg_args(&quote_hex, &event_log)).await?, "caller is not an implicit account");
    let sk = SecretKey::from_random(KeyType::ED25519);
    let pk_hex = hex::encode(near_sdk::PublicKey::from_str(&sk.public_key().to_string()).unwrap().as_bytes()[1..].to_vec());
    let stranger = env.implicit(&pk_hex, &sk).await?;
    let r = call_reg(&stranger, reg_args(&quote_hex, &event_log)).await?;
    env.record("register_agent, wrong report_data (rejected)", &r);
    expect_fail(r, "report_data does not match the caller's key");

    // Tampered event log; tampered quote signature (byte 640, as in F).
    let mut bad_log = event_log.clone();
    for e in bad_log.as_array_mut().unwrap() {
        if e["event"] == "compose-hash" {
            e["event_payload"] = json!("00".repeat(32));
        }
    }
    expect_fail(call_reg(&e_acct, reg_args(&quote_hex, &bad_log)).await?, "event log does not replay");
    let mut bad_q = q.clone();
    bad_q[640] ^= 1;
    let r = call_reg(&e_acct, reg_args(&hex::encode(&bad_q), &event_log)).await?;
    env.record("register_agent, tampered quote signature (rejected)", &r);
    expect_fail(r, "quote verification failed");

    // Registration: the vault adds a function-call key for E's key, limited to reduce/migrate/exit, 7-day expiry.
    let r = call_reg(&e_acct, reg_args(&quote_hex, &event_log)).await?;
    env.record("register_agent, success", &r);
    assert!(r.is_success(), "{:?}", r.clone().into_result().err());
    let now = env.now().await?;
    let keys = env.worker.view_access_keys(env.vault.id()).await?;
    let e_pk = near_workspaces::types::PublicKey::from_str(&String::from(
        &near_sdk::PublicKey::from_parts(near_sdk::CurveType::ED25519, hex::decode(&e_key)?).unwrap(),
    ))?;
    let k = keys.iter().find(|k| k.public_key == e_pk).expect("agent key added");
    match &k.access_key.permission {
        AccessKeyPermission::FunctionCall(p) => {
            assert_eq!(p.receiver_id, env.vault.id().to_string());
            assert_eq!(p.method_names, vec!["reduce", "migrate", "exit"]);
            assert_eq!(p.allowance, Some(NearToken::from_near(2)));
        }
        other => panic!("not a function-call key: {other:?}"),
    }
    let exp = env.state().await?.agent.unwrap().expires_at;
    assert!(exp >= now + 7 * 86400 - 5 && exp <= now + 7 * 86400, "expiry {exp} = registration + 7 days");
    // Re-attestation with the same key extends the expiry without touching keys.
    let r = call_reg(&e_acct, reg_args(&quote_hex, &event_log)).await?;
    assert!(r.is_success());

    // From here the agent is a key we hold (E's private key no longer exists).
    let agent = env.patch_agent(now + 7 * 86400).await?;
    let tx = params(0, 1)[0].clone();
    let reduce = |acct: &Account, market: &str, amount: u128| {
        acct.call(env.vault.id(), "reduce")
            .args_json(json!({"market": market, "amount": amount.to_string(), "tx": tx}))
            .gas(Gas::from_tgas(150))
            .transact()
    };

    // No corridor yet on any market.
    expect_fail(reduce(&agent, LIVE, SLICE).await?, "no corridor set");
    for m in [LIVE, BACKUP] {
        signed(&env.owner("set_corridor", json!({"market": m, "target": "500000000000000000", "act": "700000000000000000",
            "floor": "300000000000000000", "sell_budget": "200000000000000000", "migrate_allowed": true, "exit_allowed": true}), 1).await?);
    }

    // reduce at the slice maximum: one executor call with the vault's deadline.
    let r = reduce(&agent, LIVE, SLICE).await?;
    env.record("reduce, 1 mock signature", &r);
    let t = &signed(&r)[0];
    let d = decode_signed(t["raw"].as_str().unwrap());
    let data = d["input"].as_str().unwrap();
    assert_eq!(d["to"].as_str().unwrap().to_lowercase(), EXEC.to_lowercase());
    assert_eq!((&data[..10], word_hex(data, 0), u256_of(data, 1)), ("0x643d3b9e", LIVE[2..].to_string(), SLICE));
    assert_eq!(t["from"].as_str().unwrap(), env.base.to_lowercase());

    // Cooldown, slice maximum, allowlist, corridor.
    expect_fail(reduce(&agent, LIVE, 1).await?, "cooldown");
    env.reset_cooldown().await?;
    expect_fail(reduce(&agent, LIVE, SLICE + 1).await?, "amount above the slice maximum");
    expect_fail(reduce(&agent, LIVE, 0).await?, "amount above the slice maximum");
    expect_fail(reduce(&agent, UNKNOWN_MARKET, 1).await?, "market not in the allowlist");
    expect_fail(reduce(&agent, DEMO, 1).await?, "no corridor set");

    // migrate and exit.
    let mig = |from: &str, to: &str, f: u128| {
        agent.call(env.vault.id(), "migrate")
            .args_json(json!({"from": from, "to": to, "fraction": f.to_string(), "tx": tx}))
            .gas(Gas::from_tgas(150))
            .transact()
    };
    expect_fail(mig(LIVE, LIVE, WAD).await?, "same market");
    expect_fail(mig(LIVE, BACKUP, 0).await?, "fraction outside");
    expect_fail(mig(LIVE, BACKUP, WAD + 1).await?, "fraction outside");
    expect_fail(mig(LIVE, DEMO, WAD).await?, "no corridor set");
    let r = mig(LIVE, BACKUP, 4 * WAD / 10).await?;
    env.record("migrate, 1 mock signature", &r);
    let data = decode_signed(signed(&r)[0]["raw"].as_str().unwrap())["input"].as_str().unwrap().to_string();
    assert_eq!((&data[..10], word_hex(&data, 1), u256_of(&data, 2)), ("0x32bdcf59", BACKUP[2..].to_string(), 4 * WAD / 10));
    env.reset_cooldown().await?;
    let r = agent
        .call(env.vault.id(), "exit")
        .args_json(json!({"market": BACKUP, "tx": tx}))
        .gas(Gas::from_tgas(150))
        .transact()
        .await?;
    env.record("exit, 1 mock signature", &r);
    assert_eq!(&decode_signed(signed(&r)[0]["raw"].as_str().unwrap())["input"].as_str().unwrap()[..10], "0x5c7da70e");
    env.reset_cooldown().await?;

    // The function-call key cannot reach owner or registration methods (the protocol refuses before the vault runs).
    let m = env.message("withdraw", json!({"token": WETH, "amount": "1", "destination": STRANGER}), env.nonce.get() + 1, env.now().await? + 600);
    let as_agent = agent
        .call(env.vault.id(), "owner_call")
        .args_json(json!({"message": m, "signature": env.owner.sign(&m), "txs": params(0, 1)}))
        .transact()
        .await;
    let e = format!("{as_agent:?}");
    assert!(e.contains("MethodNameMismatch") || e.contains("not allowed"), "agent key on owner_call: {e}");
    let as_agent = agent.call(env.vault.id(), "register_agent").args_json(reg_args(&quote_hex, &event_log)).transact().await;
    assert!(format!("{as_agent:?}").contains("MethodNameMismatch"), "agent key on register_agent: {as_agent:?}");
    // Nor call another contract.
    let as_agent = agent.call(env.relayer.id(), "reduce").transact().await;
    assert!(format!("{as_agent:?}").contains("ReceiverMismatch"), "agent key on another receiver: {as_agent:?}");

    // Another key on the vault account (its full-access key), and another account, are refused by the vault.
    let fak = env.vault.as_account();
    expect_fail(reduce(fak, LIVE, 1).await?, "not the agent key");
    expect_fail(reduce(&env.relayer, LIVE, 1).await?, "agent methods are called through the agent key on the vault");

    // Expired registration.
    let mut st = env.state().await?;
    st.agent.as_mut().unwrap().expires_at = env.now().await? - 1;
    env.set_state(&st).await?;
    expect_fail(reduce(&agent, LIVE, 1).await?, "agent registration expired");
    let mut st = env.state().await?;
    st.agent.as_mut().unwrap().expires_at = env.now().await? + 86400;
    env.set_state(&st).await?;
    signed(&reduce(&agent, LIVE, 1).await?);

    // revoke_agent deletes the key: the protocol then refuses the agent's transactions.
    let r = env.owner("revoke_agent", json!({}), 0).await?;
    env.record("owner_call revoke_agent (deletes the key)", &r);
    assert!(r.is_success());
    let st = env.state().await?;
    assert!(st.agent.is_none() && st.approved.is_none());
    env.reset_cooldown().await?;
    let after = reduce(&agent, LIVE, 1).await;
    let e = format!("{after:?}");
    assert!(e.contains("AccessKeyNotFound") || e.contains("does not exist"), "after revoke: {e}");

    env.print_gas("agent_key");
    Ok(())
}

// ======================================================================== cancel and sweep_eth

fn hex_u128(v: &Value) -> u128 {
    u128::from_str_radix(v.as_str().unwrap().trim_start_matches("0x"), 16).unwrap()
}

/// The unsigned transaction (hex, 0x02 || rlp of the first 9 fields) inside a raw signed type-2 transaction.
fn unsigned_hex(raw: &str) -> Result<String> {
    let raw = hex::decode(&raw[2..])?;
    let r = Rlp::new(&raw[1..]);
    let mut s = RlpStream::new_list(9);
    for k in 0..9 {
        s.append_raw(r.at(k)?.as_raw(), 1);
    }
    Ok(format!("0x02{}", hex::encode(s.out())))
}

#[tokio::test]
async fn cancel_and_sweep() -> Result<()> {
    let env = setup("cancel").await?;

    // cancel: a zero-value self-transfer from the vault's Base address, empty data.
    let r = env.owner("cancel", json!({}), 1).await?;
    env.record("owner_call cancel, 1 mock signature", &r);
    let txs = signed(&r);
    assert_eq!(txs.len(), 1);
    let d = decode_signed(txs[0]["raw"].as_str().unwrap());
    assert_eq!(d["to"].as_str().unwrap().to_lowercase(), env.base.to_lowercase(), "{d}");
    assert_eq!((hex_u128(&d["value"]), d["input"].as_str().unwrap()), (0, "0x"));
    assert_eq!(d["signer"].as_str().unwrap().to_lowercase(), env.base.to_lowercase(), "cast recovers the vault");
    assert_eq!(txs[0]["from"].as_str().unwrap(), env.base.to_lowercase());
    assert_eq!(txs[0]["value"].as_str().unwrap(), "0");
    assert_eq!(txs[0]["hash"].as_str().unwrap(), d["hash"].as_str().unwrap());

    // sweep_eth: 1e15 wei to the owner's wallet address, empty data.
    let r = env.owner("sweep_eth", json!({"amount": "1000000000000000"}), 1).await?;
    env.record("owner_call sweep_eth, 1 mock signature", &r);
    let txs = signed(&r);
    assert_eq!(txs.len(), 1);
    let d = decode_signed(txs[0]["raw"].as_str().unwrap());
    assert_eq!(d["to"].as_str().unwrap().to_lowercase(), env.owner.address.to_lowercase(), "{d}");
    assert_eq!((hex_u128(&d["value"]), d["input"].as_str().unwrap()), (1_000_000_000_000_000, "0x"));
    assert_eq!(d["signer"].as_str().unwrap().to_lowercase(), env.base.to_lowercase(), "cast recovers the vault");
    assert_eq!(txs[0]["from"].as_str().unwrap(), env.base.to_lowercase());
    assert_eq!(txs[0]["value"].as_str().unwrap(), "1000000000000000");
    assert_eq!(txs[0]["hash"].as_str().unwrap(), d["hash"].as_str().unwrap());

    // Refusals: zero amount, an extra field (e.g. a destination), a cancel with arguments.
    expect_fail(env.owner("sweep_eth", json!({"amount": "0"}), 1).await?, "zero amount");
    let extra = json!({"amount": "1", "destination": STRANGER});
    expect_fail(env.owner("sweep_eth", extra, 1).await?, "bad args");
    expect_fail(env.owner("cancel", json!({"nonce": 3}), 1).await?, "bad args");
    assert_eq!(env.state().await?.owner_nonce, 2);

    env.print_gas("cancel_and_sweep");
    Ok(())
}

// ======================================================================== transaction allowlist

#[tokio::test]
async fn allowlist() -> Result<()> {
    let env = setup("allowlist").await?;
    let now = env.now().await?;
    let p = TxParams { nonce: 0, gas_limit: 100_000, max_fee_per_gas: 1_000_000_000, max_priority_fee_per_gas: 1_000_000 };
    let tx = |to: &str, data: Vec<u8>| BaseTx::new(&p, &a20(to), 0, &data).omni();
    let dl = w_u128((now + 300) as u128);
    let (live, backup, unknown) = (a32(LIVE), a32(BACKUP), a32(UNKNOWN_MARKET));
    let ok = "ok".to_string();

    // Tokens and Morpho (owner only).
    assert_eq!(env.check(&tx(USDC, calldata(SEL_APPROVE, &[w_addr(&a20(EXEC)), W_MAX])), "owner").await?, ok);
    assert!(env.check(&tx(USDC, calldata(SEL_APPROVE, &[w_addr(&a20(STRANGER)), W_MAX])), "owner").await?.contains("approve: spender is not"));
    assert!(env.check(&tx(USDC, calldata(SEL_APPROVE, &[w_addr(&a20(EXEC)), W_MAX])), "agent").await?.contains("agent may not call tokens"));
    assert_eq!(env.check(&tx(WETH, calldata(SEL_TRANSFER, &[w_addr(&a20(STRANGER)), w_u128(5)])), "owner").await?, ok);
    assert!(env.check(&tx(WETH, calldata(SEL_TRANSFER, &[w_addr(&[0u8; 20]), w_u128(5)])), "owner").await?.contains("zero address"));
    let transfer_from = calldata([0x23, 0xb8, 0x72, 0xdd], &[w_addr(&a20(STRANGER)), w_addr(&a20(EXEC))]);
    assert!(env.check(&tx(WETH, transfer_from), "owner").await?.contains("selector not allowed on a token"));
    assert_eq!(env.check(&tx(MORPHO, calldata(SEL_SET_AUTHORIZATION, &[w_addr(&a20(EXEC)), w_bool(true)])), "owner").await?, ok);
    assert!(env.check(&tx(MORPHO, calldata(SEL_SET_AUTHORIZATION, &[w_addr(&a20(STRANGER)), w_bool(true)])), "owner").await?.contains("setAuthorization"));
    assert!(env.check(&tx(MORPHO, calldata(SEL_SET_AUTHORIZATION, &[w_addr(&a20(EXEC)), w_bool(false)])), "owner").await?.contains("setAuthorization"));
    assert!(env.check(&tx(MORPHO, calldata(SEL_SET_AUTHORIZATION, &[w_addr(&a20(EXEC)), w_bool(true)])), "agent").await?.contains("agent may not call Morpho"));
    let mut dirty = w_addr(&a20(EXEC));
    dirty[0] = 1;
    assert!(env.check(&tx(MORPHO, calldata(SEL_SET_AUTHORIZATION, &[dirty, w_bool(true)])), "owner").await?.contains("not canonical"));

    // Executor: selector per caller, market ids, deadline window, exact calldata length.
    assert_eq!(env.check(&tx(EXEC, calldata(SEL_OPEN, &[live, w_u128(1), w_u128(1), dl])), "owner").await?, ok);
    assert!(env.check(&tx(EXEC, calldata(SEL_OPEN, &[live, w_u128(1), w_u128(1), dl])), "agent").await?.contains("not allowed for the Agent"));
    assert_eq!(env.check(&tx(EXEC, calldata(SEL_REDUCE, &[live, w_u128(1), dl])), "agent").await?, ok);
    assert!(env.check(&tx(EXEC, calldata(SEL_REDUCE, &[live, w_u128(1), dl])), "owner").await?.contains("not allowed for the Owner"));
    assert!(env.check(&tx(EXEC, calldata(SEL_REDUCE, &[unknown, w_u128(1), dl])), "agent").await?.contains("market id not allowlisted"));
    assert_eq!(env.check(&tx(EXEC, calldata(SEL_MIGRATE, &[live, backup, w_u128(WAD), dl])), "agent").await?, ok);
    assert!(env.check(&tx(EXEC, calldata(SEL_MIGRATE, &[live, unknown, w_u128(WAD), dl])), "agent").await?.contains("market id not allowlisted"));
    assert!(env.check(&tx(EXEC, calldata(SEL_EXIT, &[live, w_u128((now + 900) as u128)])), "agent").await?.contains("deadline"));
    assert!(env.check(&tx(EXEC, calldata(SEL_EXIT, &[live, w_u128((now - 10) as u128)])), "agent").await?.contains("deadline"));
    assert!(env.check(&tx(EXEC, calldata(SEL_EXIT, &[live, dl, dl])), "agent").await?.contains("calldata length"));
    // openFromStables, selector from `cast sig "openFromStables(bytes32,uint256,uint256,uint256)"`: owner only (V6).
    let open_from_stables = calldata([0x5f, 0x44, 0x77, 0x73], &[live, w_u128(1), w_u128(1), dl]);
    assert_eq!(env.check(&tx(EXEC, open_from_stables.clone()), "owner").await?, ok);
    assert!(env.check(&tx(EXEC, open_from_stables), "agent").await?.contains("not allowed for the Agent"));
    let ofs_unknown = calldata([0x5f, 0x44, 0x77, 0x73], &[unknown, w_u128(1), w_u128(1), dl]);
    assert!(env.check(&tx(EXEC, ofs_unknown), "owner").await?.contains("market id not allowlisted"));
    let ofs_short = calldata([0x5f, 0x44, 0x77, 0x73], &[live, w_u128(1), dl]);
    assert!(env.check(&tx(EXEC, ofs_short), "owner").await?.contains("calldata length"));
    let deadline_late = calldata([0x5f, 0x44, 0x77, 0x73], &[live, w_u128(1), w_u128(1), w_u128((now + 900) as u128)]);
    assert!(env.check(&tx(EXEC, deadline_late), "owner").await?.contains("deadline"));
    let mut corridor = vec![live, w_u128(1), w_u128(2), w_u128(3), w_u128(4), w_bool(true), w_bool(true), w_bool(false), dl];
    assert_eq!(env.check(&tx(EXEC, calldata(SEL_SET_CORRIDOR, &corridor)), "owner").await?, ok);
    corridor[5] = w_u128(2);
    assert!(env.check(&tx(EXEC, calldata(SEL_SET_CORRIDOR, &corridor)), "owner").await?.contains("bool word"));

    // Transaction fields.
    let good = tx(EXEC, calldata(SEL_EXIT, &[live, dl]));
    let mut t = good.clone();
    t.chain_id = 8453;
    assert!(env.check(&t, "agent").await?.contains("chain id 8453"));
    let mut t = good.clone();
    t.value = 1;
    assert!(env.check(&t, "agent").await?.contains("value must be 0"));
    let mut t = good.clone();
    t.access_list = vec![(a20(EXEC), vec![[0u8; 32]])];
    assert!(env.check(&t, "agent").await?.contains("access list"));
    let mut t = good.clone();
    t.gas_limit = 1_000_001;
    assert!(env.check(&t, "agent").await?.contains("gas_limit"));
    let mut t = good.clone();
    t.max_fee_per_gas = 2_000_000_001;
    assert!(env.check(&t, "agent").await?.contains("max_fee_per_gas"));
    let mut t = good.clone();
    t.max_priority_fee_per_gas = t.max_fee_per_gas + 1;
    assert!(env.check(&t, "agent").await?.contains("priority fee"));
    let mut t = good.clone();
    t.to = None;
    assert!(env.check(&t, "agent").await?.contains("20-byte recipient"));
    assert!(env.check(&tx(STRANGER, calldata(SEL_EXIT, &[live, dl])), "agent").await?.contains("to is not the executor"));

    // Owner-only shapes with empty data: cancel (self-transfer, value 0) and sweep_eth (ETH to the owner only).
    let cancel = tx(&env.base, vec![]);
    assert_eq!(env.check(&cancel, "owner").await?, ok);
    assert!(env.check(&cancel, "agent").await?.contains("the agent may not cancel"));
    let mut t = cancel.clone();
    t.value = 1;
    assert!(env.check(&t, "owner").await?.contains("value must be 0"), "a self-transfer carries no value");
    let mut sweep = tx(&env.owner.address, vec![]);
    sweep.value = 5;
    assert_eq!(env.check(&sweep, "owner").await?, ok);
    assert!(env.check(&sweep, "agent").await?.contains("the agent may not send ETH to the owner"));
    let mut t = tx(STRANGER, vec![]);
    t.value = 5;
    assert!(env.check(&t, "owner").await?.contains("value must be 0"));
    let mut t = tx(&env.owner.address, vec![0xde, 0xad, 0xbe, 0xef]);
    t.value = 5;
    assert!(env.check(&t, "owner").await?.contains("value must be 0"));
    let mut t = tx(EXEC, calldata(SEL_CLOSE, &[live, dl]));
    t.value = 5;
    assert!(env.check(&t, "owner").await?.contains("value must be 0"));
    // The chain id and fee bounds apply to these shapes too.
    let mut t = sweep.clone();
    t.chain_id = 8453;
    assert!(env.check(&t, "owner").await?.contains("chain id 8453"));
    let mut t = cancel.clone();
    t.max_fee_per_gas = 2_000_000_001;
    assert!(env.check(&t, "owner").await?.contains("max_fee_per_gas"));

    // After the owner moves every market to a new executor: the old one may only be revoked.
    for m in [LIVE, BACKUP, DEMO] {
        let n = if m == DEMO { 4 } else { 0 };
        let a = json!({"market": m, "executor": EXEC2, "revoke_authorization": true});
        signed(&env.owner("set_executor", a, n).await?);
    }
    assert_eq!(env.check(&tx(USDC, calldata(SEL_APPROVE, &[w_addr(&a20(EXEC)), w_u128(0)])), "owner").await?, ok);
    assert!(env.check(&tx(USDC, calldata(SEL_APPROVE, &[w_addr(&a20(EXEC)), w_u128(1)])), "owner").await?.contains("approve: spender is not"));
    assert_eq!(env.check(&tx(MORPHO, calldata(SEL_SET_AUTHORIZATION, &[w_addr(&a20(EXEC)), w_bool(false)])), "owner").await?, ok);
    assert!(env.check(&tx(MORPHO, calldata(SEL_SET_AUTHORIZATION, &[w_addr(&a20(EXEC)), w_bool(true)])), "owner").await?.contains("setAuthorization"));
    assert!(env.check(&tx(EXEC, calldata(SEL_EXIT, &[live, dl])), "agent").await?.contains("to is not the executor"));
    assert_eq!(env.check(&tx(EXEC2, calldata(SEL_EXIT, &[live, dl])), "agent").await?, ok);

    env.print_gas("allowlist");
    Ok(())
}

// ======================================================================== signing path

/// The contract's panic message inside a failed view's error ("ERR ..." from `check_hex`), or the whole string.
fn panic_msg(e: &str) -> &str {
    let k = "panic_msg: \\\"";
    e.find(k).and_then(|i| e[i + k.len()..].split('\\').next()).unwrap_or(e)
}

/// V3's check on the transactions one owner call returned and the payloads the mock signer recorded for them:
/// each payload is keccak256(0x02 || rlp(unsigned)) of the returned transaction at the same position, checked
/// independently of the vault's code (rlp re-encoding of the signed list, `cast keccak`). Returns the unsigned
/// bytes (hex, 0x02 || rlp) of each transaction.
async fn check_payloads(env: &Env, txs: &[Value], payloads: &[String]) -> Result<Vec<String>> {
    assert_eq!(payloads.len(), txs.len(), "one signer payload per returned transaction");
    let mut out = vec![];
    for (i, (t, payload)) in txs.iter().zip(payloads).enumerate() {
        let raw = hex::decode(&t["raw"].as_str().unwrap()[2..])?;
        assert_eq!(raw[0], 0x02, "tx {i}: type-2 envelope");
        let r = Rlp::new(&raw[1..]);
        let info = r.payload_info()?;
        assert_eq!(info.header_len + info.value_len, raw.len() - 1, "tx {i}: no trailing bytes");
        assert!(r.is_list() && r.item_count()? == 12, "tx {i}: 12 fields");
        assert!(r.val_at::<u8>(9)? <= 1, "tx {i}: y_parity");
        // The signed fields are the ones the vault returned.
        assert_eq!(r.val_at::<u64>(1)?, t["nonce"].as_u64().unwrap(), "tx {i}: nonce");
        let to = format!("0x{}", hex::encode(r.val_at::<Vec<u8>>(5)?));
        assert_eq!(to, t["to"].as_str().unwrap().to_lowercase(), "tx {i}: to");
        assert_eq!(r.val_at::<u128>(6)?.to_string(), t["value"].as_str().unwrap(), "tx {i}: value");
        // 0x02 || rlp(first 9 fields) is the unsigned transaction; its keccak is what the signer was given.
        let mut s = RlpStream::new_list(9);
        for k in 0..9 {
            s.append_raw(r.at(k)?.as_raw(), 1);
        }
        let unsigned = format!("0x02{}", hex::encode(s.out()));
        assert_eq!(keccak(&unsigned), format!("0x{payload}"), "tx {i}: signer payload = keccak(unsigned)");
        assert_eq!(env.check_hex(&unsigned, "owner").await?, "ok", "tx {i}: the signed bytes pass check_tx");
        // The returned hash is the hash of the raw signed transaction.
        let d = decode_signed(t["raw"].as_str().unwrap());
        assert_eq!(t["hash"].as_str().unwrap(), d["hash"].as_str().unwrap(), "tx {i}: hash");
        assert_eq!(t["hash"].as_str().unwrap(), keccak(t["raw"].as_str().unwrap()), "tx {i}: hash = keccak(raw)");
        out.push(unsigned);
    }
    Ok(out)
}

// What this proves: every 32-byte payload the vault sends to the signer is keccak256(0x02 || rlp(the 9 unsigned
// EIP-1559 fields)) of a transaction the vault returns in the same call, at the same position, and nothing else
// is sent for signing. It holds by construction: `sign` builds `unsigned = build_for_signing()` once, decodes
// those bytes back and runs `check_tx` on them, then hashes the same `unsigned` bytes into `payload_v2.Ecdsa`;
// `on_signed` signs nothing and only assembles the returned signatures onto the same `BaseTx`. So no raw hash, no
// legacy or type-4 (EIP-7702) transaction, and no bytes that did not pass `check_tx` reach the signer. The test
// checks the payloads the mock signer recorded against the signed transactions, independently of the vault's
// code (rlp re-encoding of the signed list, `cast keccak`), and shows the decoder refusing the other shapes.
#[tokio::test]
async fn signing_path() -> Result<()> {
    let env = setup("signing").await?;

    // A mix of owner calls: 3 + 1 + 1 + 1 + 1 transactions.
    let corridor = json!({"market": LIVE, "target": "500000000000000000", "act": "700000000000000000",
        "floor": "300000000000000000", "sell_budget": "200000000000000000",
        "migrate_allowed": true, "exit_allowed": true});
    let calls = [
        ("setup", json!({"markets": [{"market": LIVE, "approve": true, "authorize": true}]}), 3),
        ("set_corridor", corridor, 1),
        ("withdraw", json!({"token": WETH, "amount": "1000", "destination": STRANGER}), 1),
        ("cancel", json!({}), 1),
        ("sweep_eth", json!({"amount": "1000000000000000"}), 1),
    ];
    let mut txs = vec![];
    for (method, args, n) in calls {
        let r = env.owner(method, args, n).await?;
        env.record(&format!("owner_call {method}, {n} mock signature{}", if n == 1 { "" } else { "s" }), &r);
        let got = signed(&r);
        assert_eq!(got.len(), n, "{method}");
        txs.extend(got);
    }
    assert_eq!(txs.len(), 7);

    // Nothing was signed that was not returned.
    let payloads: Vec<String> = env.signer.view("payloads").await?.json()?;
    assert_eq!(payloads.len(), txs.len(), "one signer payload per returned transaction");

    check_payloads(&env, &txs, &payloads).await?;

    // Other shapes are refused by the decoder before any check or signature.
    let p =
        TxParams { nonce: 0, gas_limit: 100_000, max_fee_per_gas: 1_000_000_000, max_priority_fee_per_gas: 1_000_000 };
    let approve = calldata(SEL_APPROVE, &[w_addr(&a20(EXEC)), W_MAX]);
    let good = BaseTx::new(&p, &a20(USDC), 0, &approve).omni().build_for_signing();
    assert_eq!(env.check_hex(&hex::encode(&good), "owner").await?, "ok");
    // Legacy (type 0, EIP-155 signing form): rlp([nonce, gas_price, gas, to, value, data, chain_id, 0, 0]).
    let mut s = RlpStream::new_list(9);
    s.append(&0u64).append(&1_000_000_000u64).append(&100_000u64).append(&a20(USDC).to_vec()).append(&0u8);
    s.append(&approve).append(&84_532u64).append(&0u8).append(&0u8);
    let legacy = hex::encode(s.out());
    // Type 4 (EIP-7702) prefix on otherwise valid fields.
    let mut type4 = good.clone();
    type4[0] = 0x04;
    // A 32-byte hash in place of a transaction: an actual signing payload from above, and one starting with 0x02.
    let mut hash02 = a32(&keccak("0x"));
    hash02[0] = 0x02;
    let not_1559 = "not an EIP-1559 transaction";
    let cases = [
        ("legacy", legacy, not_1559),
        ("type4", hex::encode(&type4), not_1559),
        ("hash", payloads[0].clone(), not_1559),
        ("hash02", hex::encode(hash02), "trailing bytes"),
    ];
    for (name, tx_hex, want) in cases {
        let e = env.check_hex(&tx_hex, "owner").await?;
        assert!(e.starts_with("ERR "), "{name}: refused, got {e}");
        println!("signing_path refusal | {name} | {}", panic_msg(&e));
        assert_eq!(panic_msg(&e), want, "{name}");
    }

    env.print_gas("signing_path");
    Ok(())
}

// ======================================================================== Create vault in one signature

/// The six corridor values of one `setup` market part (the same words `set_corridor` takes, without `market`).
fn corridor(target: &str) -> Value {
    json!({"target": target, "act": "700000000000000000", "floor": "300000000000000000",
           "sell_budget": "200000000000000000", "migrate_allowed": true, "exit_allowed": false})
}

#[tokio::test]
async fn create_vault_setup() -> Result<()> {
    let env = setup("create").await?;
    let st: Value = env.vault.view("get_state").await?.json()?;
    assert!(st["markets"].as_array().unwrap().iter().all(|m| m["corridor_set"] == false));
    assert!(st["approved"].is_null());

    // The full Create vault message: live set up and authorized, backup on the same tokens and executor (nothing
    // to approve or authorize again), corridors on both, the measurement. One signature, five Base transactions.
    let full = json!({"markets": [
        {"market": LIVE, "approve": true, "authorize": true, "corridor": corridor("500000000000000000")},
        {"market": BACKUP, "approve": false, "authorize": false, "corridor": corridor("450000000000000000")}],
        "measurement": e_measurement()});
    let before = env.now().await?;
    let r = env.owner("setup", full, 5).await?;
    env.record("owner_call setup (Create vault: live + backup, measurement), 5 mock signatures", &r);
    let txs = signed(&r);
    assert_eq!(txs.len(), 5);
    let auth = format!("0xeecea000{:0>64}{:0>64}", &EXEC[2..].to_lowercase(), "1");
    let approve = format!("0x095ea7b3{:0>64}{}", &EXEC[2..].to_lowercase(), "f".repeat(64));
    let now = env.now().await?;
    for (i, t) in txs.iter().enumerate() {
        assert_eq!(t["from"].as_str().unwrap(), env.base.to_lowercase());
        assert_eq!(t["nonce"].as_u64().unwrap(), i as u64);
        let d = decode_signed(t["raw"].as_str().unwrap());
        let (to, data) = (d["to"].as_str().unwrap().to_lowercase(), d["input"].as_str().unwrap().to_string());
        match i {
            0 | 1 => assert_eq!((to, data), ([USDC, WETH][i].to_lowercase(), approve.clone()), "tx {i}"),
            2 => assert_eq!((to, data), (MORPHO.to_lowercase(), auth.clone()), "tx {i}"),
            _ => {
                assert_eq!((to, &data[..10]), (EXEC.to_lowercase(), "0x253616b9"), "tx {i}");
                assert_eq!(word_hex(&data, 0), [LIVE, BACKUP][i - 3][2..], "tx {i}: market");
                assert_eq!(u256_of(&data, 1), [5 * WAD / 10, 45 * WAD / 100][i - 3], "tx {i}: target");
                assert_eq!((u256_of(&data, 2), u256_of(&data, 3), u256_of(&data, 4)), (7 * WAD / 10, 3 * WAD / 10, 2 * WAD / 10));
                assert_eq!((u256_of(&data, 5), u256_of(&data, 6), u256_of(&data, 7)), (1, 0, 0), "tx {i}: flags, `set`");
                let dl = u256_of(&data, 8) as u64;
                assert!(dl > before && dl <= now + 600, "tx {i}: deadline {dl} is block time + 600 s");
            }
        }
    }
    // Every signer payload is the hash of the type-2 transaction returned at its position (V3), and nothing else.
    let payloads: Vec<String> = env.signer.view("payloads").await?.json()?;
    let unsigned = check_payloads(&env, &txs, &payloads).await?;
    // None of the five could be signed for the agent.
    for (i, u) in unsigned.iter().enumerate() {
        assert!(env.check_hex(u, "agent").await?.starts_with("ERR "), "tx {i}: refused for the agent");
    }
    // The vault's state: corridor set on both markets (not on demo), the measurement stored as approve_measurement does.
    let st: Value = env.vault.view("get_state").await?.json()?;
    let set: Vec<bool> = st["markets"].as_array().unwrap().iter().map(|m| m["corridor_set"].as_bool().unwrap()).collect();
    assert_eq!(set, vec![true, true, false]);
    assert_eq!(st["approved"], e_measurement());
    assert_eq!(st["owner_nonce"], 1);

    // De-duplication: approve and authorize on all three markets. Live and backup share USDC, WETH and EXEC; demo
    // shares WETH and EXEC. One approval per (token, executor), one authorization: USDC, WETH, Morpho, dUSDC.
    let all = |m: &str| json!({"market": m, "approve": true, "authorize": true});
    let r = env.owner("setup", json!({"markets": [all(LIVE), all(BACKUP), all(DEMO)]}), 4).await?;
    env.record("owner_call setup (three markets, de-duplicated), 4 mock signatures", &r);
    let got: Vec<(String, String)> = signed(&r)
        .iter()
        .map(|t| {
            let d = decode_signed(t["raw"].as_str().unwrap());
            (d["to"].as_str().unwrap().to_lowercase(), d["input"].as_str().unwrap().to_string())
        })
        .collect();
    let want: Vec<(String, String)> = [(USDC, &approve), (WETH, &approve), (MORPHO, &auth), (DUSDC, &approve)]
        .iter()
        .map(|(to, data)| (to.to_lowercase(), data.to_string()))
        .collect();
    assert_eq!(got, want);
    // Approve on both live and backup, no authorization: one approval pair.
    let pair = |m: &str| json!({"market": m, "approve": true, "authorize": false});
    let r = env.owner("setup", json!({"markets": [pair(LIVE), pair(BACKUP)]}), 2).await?;
    let got: Vec<String> = signed(&r).iter().map(|t| decode_signed(t["raw"].as_str().unwrap())["to"].as_str().unwrap().to_lowercase()).collect();
    assert_eq!(got, vec![USDC.to_lowercase(), WETH.to_lowercase()]);
    // The measurement alone: stored, no Base transaction.
    let r = env.owner("setup", json!({"markets": [], "measurement": e_measurement()}), 0).await?;
    assert!(signed(&r).is_empty());
    let n = env.state().await?.owner_nonce;
    assert_eq!(n, 4);

    // Refusals; the nonce stays.
    let none = json!({"market": LIVE, "approve": false, "authorize": false});
    expect_fail(env.owner("setup", json!({"markets": []}), 0).await?, "empty setup");
    expect_fail(env.owner("setup", json!({"markets": [none]}), 0).await?, "empty setup");
    let twice = json!({"markets": [pair(LIVE), {"market": format!("0x{}", LIVE[2..].to_uppercase()), "approve": false,
        "authorize": false, "corridor": corridor("500000000000000000")}]});
    expect_fail(env.owner("setup", twice, 3).await?, "market listed twice");
    expect_fail(env.owner("setup", json!({"markets": [pair(UNKNOWN_MARKET)]}), 2).await?, "market not in the allowlist");
    let bad_m = json!({"markets": [], "measurement": {"mrtd": "00", "rtmr0": "", "rtmr1": "", "rtmr2": "", "compose_hash": "", "os_image_hash": ""}});
    expect_fail(env.owner("setup", bad_m, 0).await?, "mrtd: expected 48 bytes");
    let mut high = corridor("500000000000000000");
    high["floor"] = json!("18446744073709551616");
    expect_fail(env.owner("setup", json!({"markets": [{"market": LIVE, "approve": false, "authorize": false, "corridor": high}]}), 1).await?, "corridor value above uint64");
    let extra = json!({"markets": [{"market": LIVE, "approve": true, "authorize": true, "executor": STRANGER}]});
    expect_fail(env.owner("setup", extra, 3).await?, "bad args");
    expect_fail(env.owner("setup", json!({"markets": [pair(LIVE)], "to": STRANGER}), 2).await?, "bad args");
    expect_fail(env.owner("setup", json!({"markets": [{"market": LIVE, "authorize": true}]}), 1).await?, "bad args");
    expect_fail(env.owner("setup", json!({"market": LIVE, "authorize": true}), 3).await?, "bad args");
    // The parameter count is the de-duplicated one.
    expect_fail(env.owner("setup", json!({"markets": [all(LIVE), all(BACKUP)]}), 6).await?, "expected 3 transaction parameter sets, got 6");
    assert_eq!(env.state().await?.owner_nonce, n);

    // The agent key cannot call setup: owner_call is outside its permission, so the protocol refuses it.
    let agent = env.patch_agent(env.now().await? + 86400).await?;
    let m = env.message("setup", json!({"markets": [all(LIVE)]}), n + 1, env.now().await? + 600);
    let as_agent = agent
        .call(env.vault.id(), "owner_call")
        .args_json(json!({"message": m, "signature": env.owner.sign(&m), "txs": params(0, 3)}))
        .transact()
        .await;
    let e = format!("{as_agent:?}");
    assert!(e.contains("MethodNameMismatch"), "agent key on owner_call setup: {e}");
    assert_eq!(env.state().await?.owner_nonce, n);

    env.print_gas("create_vault_setup");
    Ok(())
}

// ======================================================================== factory

const FACTORY_PATH: &str = "cmdk-base";

/// Sandbox only: publish `code` as a Global Contract by hash with a raw signed `DeployGlobalContract` transaction
/// from `root` (near-workspaces 0.23 has no API for it; research H, recipe (a)). Runs before any near-workspaces
/// transaction from `root`, because near-workspaces caches the nonce of each key it signs with. Returns sha256(code).
async fn publish_global(worker: &Worker<Sandbox>, root: &Account, code: Vec<u8>) -> Result<[u8; 32]> {
    use near_jsonrpc_client::{methods, JsonRpcClient};
    use near_primitives::action::{Action, DeployGlobalContractAction, GlobalContractDeployMode};
    use near_primitives::transaction::{SignedTransaction, Transaction, TransactionV0};
    use near_primitives::views::TxExecutionStatus::{self, Final};
    use near_primitives::views::{FinalExecutionOutcomeViewEnum as E, FinalExecutionStatus};
    let size = code.len();
    let code_hash = near_primitives::hash::hash(&code);
    let sk = near_crypto::SecretKey::from_str(&root.secret_key().to_string())?;
    let nonce = worker.view_access_key(root.id(), &root.secret_key().public_key()).await?.nonce + 1;
    let block_hash = near_primitives::hash::CryptoHash(worker.view_block().await?.hash().0);
    let before = root.view_account().await?.balance;
    let action = DeployGlobalContractAction { code: code.into(), deploy_mode: GlobalContractDeployMode::CodeHash };
    let tx = Transaction::V0(TransactionV0 {
        signer_id: root.id().clone(),
        public_key: sk.public_key(),
        nonce,
        receiver_id: root.id().clone(),
        block_hash,
        actions: vec![Action::DeployGlobalContract(action)],
    });
    let signature = sk.sign(tx.get_hash_and_size().0.as_ref());
    let signed_transaction = SignedTransaction::new(signature, tx);
    let tx_hash = signed_transaction.get_hash();
    // Submitted without waiting (a long server-side wait can time out on a slow run), then polled to FINAL.
    let client = JsonRpcClient::connect(worker.rpc_addr());
    let send = methods::send_tx::RpcSendTransactionRequest { signed_transaction, wait_until: TxExecutionStatus::None };
    client.call(send).await?;
    let mut outcome = None;
    for _ in 0..WAIT_BLOCKS {
        let info = methods::tx::TransactionInfo::TransactionId { tx_hash, sender_account_id: root.id().clone() };
        let status = methods::tx::RpcTransactionStatusRequest { transaction_info: info, wait_until: Final };
        if let Ok(r) = client.call(status).await {
            if let Some(o) = r.final_execution_outcome {
                outcome = Some(o);
                break;
            }
        }
        worker.fast_forward(1).await?;
    }
    let o = match outcome.expect("publish: no FINAL outcome within the wait") {
        E::FinalExecutionOutcome(o) => o,
        E::FinalExecutionOutcomeWithReceipt(o) => o.final_outcome,
    };
    assert!(matches!(o.status, FinalExecutionStatus::SuccessValue(_)), "publish: {:?}", o.status);
    // The distribution receipt is asynchronous (research H): wait until the node serves the code by its hash.
    let hash58 = near_sdk::bs58::encode(code_hash.0).into_string();
    let code = rpc_wait(worker, json!({"request_type": "view_global_contract_code", "finality": "final",
                                       "code_hash": hash58}), "global contract code").await?;
    assert_eq!(code["hash"].as_str().unwrap(), hash58);
    let after = settled_balance(worker, root.id()).await?;
    let outcomes: Vec<_> = std::iter::once(&o.transaction_outcome).chain(o.receipts_outcome.iter()).collect();
    let gas: u64 = outcomes.iter().map(|x| x.outcome.gas_burnt.as_gas()).sum();
    // tokens_burnt includes the per-byte code burn (0.0001 NEAR per byte, research H), not only gas.
    let burnt: u128 = outcomes.iter().map(|x| x.outcome.tokens_burnt.as_yoctonear()).sum();
    let receipts =
        outcomes.iter().map(|x| format!("{}:{:.3}", x.outcome.executor_id, x.outcome.gas_burnt.as_gas() as f64 / 1e12));
    let receipts = receipts.collect::<Vec<_>>().join(" ");
    let delta = before.as_yoctonear() - after.as_yoctonear();
    let tgas = gas as f64 / 1e12;
    let what = format!("publish vault Global Contract, {size} bytes");
    println!("GAS factory | {what} | receipts {receipts} | {gas} | {tgas:.3} Tgas");
    let code_burn = size as u128 * 100_000_000_000_000_000_000;
    println!(
        "factory publish burn | root balance -{:.6} NEAR | tokens_burnt {:.6} NEAR | code 0.0001 NEAR x {size} = \
         {:.4} NEAR | rest (gas) {:.6} NEAR",
        delta as f64 / 1e24, burnt as f64 / 1e24, code_burn as f64 / 1e24, (delta - code_burn) as f64 / 1e24
    );
    Ok(code_hash.0)
}

/// Upper bound, in blocks, for every wait in the factory test.
const WAIT_BLOCKS: usize = 20;

/// A JSON-RPC `query` with curl, the same request DEPLOY.md runs against testnet: `result`, or `Err` with the
/// RPC error (for example an account not yet visible at finality "final").
fn rpc_query(worker: &Worker<Sandbox>, params: &Value) -> std::result::Result<Value, Value> {
    let body = json!({"jsonrpc": "2.0", "id": "cmdk", "method": "query", "params": params}).to_string();
    let out = Command::new("curl")
        .args(["-s", &worker.rpc_addr(), "-H", "Content-Type: application/json", "-d", &body])
        .output()
        .unwrap();
    let v: Value = serde_json::from_slice(&out.stdout).unwrap();
    match v.get("error") {
        Some(e) => Err(e.clone()),
        None => Ok(v["result"].clone()),
    }
}

/// `rpc_query` until it answers, one block between tries, at most `WAIT_BLOCKS` blocks.
async fn rpc_wait(worker: &Worker<Sandbox>, params: Value, what: &str) -> Result<Value> {
    let mut last = Value::Null;
    for _ in 0..WAIT_BLOCKS {
        match rpc_query(worker, &params) {
            Ok(v) => return Ok(v),
            Err(e) => last = e,
        }
        worker.fast_forward(1).await?;
    }
    panic!("{what}: not served within {WAIT_BLOCKS} blocks: {last}");
}

/// The account once a view at the default (optimistic) finality shows it, one block between tries.
async fn visible_account(worker: &Worker<Sandbox>, id: &AccountId) -> Result<near_workspaces::types::AccountDetails> {
    for _ in 0..WAIT_BLOCKS {
        if let Ok(a) = worker.view_account(id).await {
            return Ok(a);
        }
        worker.fast_forward(1).await?;
    }
    panic!("{id}: account not visible within {WAIT_BLOCKS} blocks");
}

/// The account's balance once it has stopped changing over two blocks (gas refunds of earlier calls landed).
async fn settled_balance(worker: &Worker<Sandbox>, id: &AccountId) -> Result<NearToken> {
    let mut b = worker.view_account(id).await?.balance;
    for _ in 0..WAIT_BLOCKS / 2 {
        worker.fast_forward(2).await?;
        let next = worker.view_account(id).await?.balance;
        if next == b {
            return Ok(b);
        }
        b = next;
    }
    panic!("{id}: balance still changing after {WAIT_BLOCKS} blocks");
}

async fn create_vault(relayer: &Account, factory: &AccountId, owner: &str, policy: Value)
    -> Result<ExecutionFinalResult> {
    Ok(relayer
        .call(factory, "create_vault")
        .args_json(json!({"owner": owner, "policy": policy}))
        .deposit(NearToken::from_near(2))
        .gas(Gas::from_tgas(150))
        .transact()
        .await?)
}

fn print_receipts(what: &str, r: &ExecutionFinalResult) {
    let receipts =
        r.receipt_outcomes().iter().map(|o| format!("{}:{:.3}", o.executor_id, o.gas_burnt.as_gas() as f64 / 1e12));
    let receipts = receipts.collect::<Vec<_>>().join(" ");
    let g = r.total_gas_burnt.as_gas();
    println!("GAS factory | {what} | receipts {receipts} | {g} | {:.3} Tgas", g as f64 / 1e12);
}

#[tokio::test]
async fn factory() -> Result<()> {
    let worker = near_workspaces::sandbox().await?;
    let root = worker.root_account()?;
    // 1. The vault code as a Global Contract by hash.
    let code_hash = publish_global(&worker, &root, std::fs::read("target/near/cmdk_vault.wasm")?).await?;

    // 2. Mock signer, factory, init, then the factory's only key is deleted.
    let signer = worker.dev_deploy(&std::fs::read("mock-signer/target/near/cmdk_mock_signer.wasm")?).await?;
    let fac = root.create_subaccount("factory").initial_balance(NearToken::from_near(10)).transact().await?;
    let fac = fac.into_result()?;
    let factory = fac.deploy(&std::fs::read("factory/target/near/cmdk_factory.wasm")?).await?.into_result()?;
    // DEPLOY.md step 2's check (the code is served under its hash) ran inside `publish_global`.
    let hash58 = near_sdk::bs58::encode(code_hash).into_string();
    let r = factory
        .call("init")
        .args_json(json!({"code_hash": hash58, "signer": signer.id(), "path": FACTORY_PATH, "morpho": MORPHO}))
        .transact()
        .await?;
    assert!(r.is_success(), "{:?}", r.into_result().err());
    let r = fac.batch(fac.id()).delete_key(fac.secret_key().public_key()).transact().await?;
    assert!(r.is_success(), "{:?}", r.into_result().err());
    assert!(worker.view_access_keys(factory.id()).await?.is_empty(), "factory is keyless");
    let fa = worker.view_account(factory.id()).await?;
    let near = fa.balance.as_yoctonear() as f64 / 1e24;
    println!("factory | {} | keys 0 | storage_usage {} bytes | balance {near:.6} NEAR | code hash {hash58}",
        factory.id(), fa.storage_usage);

    let relayer = root.create_subaccount("relayer").initial_balance(NearToken::from_near(50)).transact().await?;
    let relayer = relayer.into_result()?;
    // init cannot run a second time, from anyone.
    let again = json!({"code_hash": hash58, "signer": relayer.id(), "path": "other", "morpho": MORPHO});
    expect_fail(relayer.call(factory.id(), "init").args_json(again).transact().await?, "already been initialized");

    // 3. create_vault from the relayer.
    let dir = std::env::temp_dir().join(format!("cmdk-vault-sandbox-factory-{}", std::process::id()));
    std::fs::create_dir_all(&dir)?;
    let (owner, other) = (Wallet::new(&dir), Wallet::new(&dir));
    let m = |id: &str, loan: &str, exec: &str| {
        json!({"id": id, "loan_token": loan, "collateral_token": WETH, "executor": exec,
               "slice_max": SLICE.to_string()})
    };
    let policy = json!({"cooldown_secs": COOLDOWN, "markets": [m(LIVE, USDC, EXEC), m(BACKUP, USDC, EXEC)]});
    let r = create_vault(&relayer, factory.id(), &owner.address, policy.clone()).await?;
    assert!(r.is_success(), "{:?}", r.clone().into_result().err());
    print_receipts("create_vault v1 (derived_public_key, on_derived_key, batch with init)", &r);
    for l in r.logs() {
        println!("factory log | {l}");
    }
    let v1: AccountId = factory.view("vault_id").args_json(json!({"n": 1})).await?.json()?;
    assert_eq!(v1.as_str(), format!("v1.{}", factory.id()));
    let a = visible_account(&worker, &v1).await?;
    assert_eq!(a.contract_state, near_workspaces::ContractState::GlobalHash(near_workspaces::CryptoHash(code_hash)));
    assert!(worker.view_access_keys(&v1).await?.is_empty(), "vault is keyless");
    // DEPLOY.md step 7's checks, as raw RPC at finality "final": waits until the final block includes v1.
    let q = json!({"request_type": "view_account", "finality": "final", "account_id": v1});
    let va = rpc_wait(&worker, q, "v1 at finality final").await?;
    assert_eq!(va["global_contract_hash"].as_str().unwrap(), hash58, "{va}");
    assert_eq!(va["code_hash"].as_str().unwrap(), "11111111111111111111111111111111", "{va}");
    for id in [&v1, factory.id()] {
        let q = json!({"request_type": "view_access_key_list", "finality": "final", "account_id": id});
        let keys = rpc_wait(&worker, q, "access key list").await?;
        assert_eq!(keys["keys"], json!([]), "{id}: {keys}");
    }
    let near = a.balance.as_yoctonear() as f64 / 1e24;
    println!("factory vault | {v1} | storage_usage {} bytes | balance {near:.6} NEAR | keys 0", a.storage_usage);
    let q = json!({"predecessor": v1, "path": FACTORY_PATH});
    let base: String = signer.view("derived_address").args_json(q).await?.json()?;
    let st: Value = worker.view(&v1, "get_state").await?.json()?;
    assert_eq!(st["owner"].as_str().unwrap(), owner.address.to_lowercase());
    assert_eq!(st["base_address"].as_str().unwrap(), base.to_lowercase());
    assert_eq!(st["path"].as_str().unwrap(), FACTORY_PATH);
    assert_eq!(st["signer"].as_str().unwrap(), signer.id().as_str());
    assert_eq!(st["morpho"].as_str().unwrap(), MORPHO.to_lowercase());
    assert!(r.logs().contains(&format!("creating vault: {v1} {}", base.to_lowercase()).as_str()));

    // 4. The created vault works: one owner message, three transactions from its Base address.
    let vault = Contract::from_secret_key(v1.clone(), SecretKey::from_random(KeyType::ED25519), &worker);
    let (nonce, gas) = (Default::default(), Default::default());
    let (worker2, signer2, relayer2) = (worker.clone(), signer.clone(), relayer.clone());
    let env = Env { worker: worker2, vault, signer: signer2, relayer: relayer2, owner, other, base, nonce, dir, gas };
    let a = json!({"markets": [{"market": LIVE, "approve": true, "authorize": true}]});
    let r = env.owner("setup", a, 3).await?;
    env.record("owner_call setup on v1, 3 mock signatures", &r);
    let txs = signed(&r);
    assert_eq!(txs.len(), 3);
    for t in &txs {
        assert_eq!(t["from"].as_str().unwrap(), env.base.to_lowercase());
        let d = decode_signed(t["raw"].as_str().unwrap());
        assert_eq!(t["hash"].as_str().unwrap(), d["hash"].as_str().unwrap());
    }
    env.print_gas("factory");

    // 5. A second vault: another account and another Base address.
    let r = create_vault(&relayer, factory.id(), &env.other.address, policy.clone()).await?;
    assert!(r.is_success(), "{:?}", r.clone().into_result().err());
    let v2: AccountId = factory.view("vault_id").args_json(json!({"n": 2})).await?.json()?;
    visible_account(&worker, &v2).await?;
    let st2: Value = worker.view(&v2, "get_state").await?.json()?;
    assert_ne!(st2["base_address"], st["base_address"]);
    let cfg: Value = factory.view("get_config").await?.json()?;
    assert_eq!(cfg["count"], 2);
    assert_eq!(cfg["code_hash"].as_str().unwrap(), hash58);
    println!("factory vault | {v2} | base {} | config {cfg}", st2["base_address"]);

    // 6. A policy the vault would refuse is refused by the factory in the entry call: the protocol returns the
    // deposit, no promise is made, no vault number is used.
    let zero = "0x0000000000000000000000000000000000000000";
    let bad = json!({"cooldown_secs": COOLDOWN, "markets": [m(LIVE, USDC, zero)]});
    // Balances are read once they have settled, so that refunds of earlier and of this call are inside the window.
    let (rb, fb) = (settled_balance(&worker, relayer.id()).await?, settled_balance(&worker, factory.id()).await?);
    let r = create_vault(&relayer, factory.id(), &env.owner.address, bad).await?;
    print_receipts("create_vault, zero executor (refused in the entry call)", &r);
    assert_eq!(r.receipt_outcomes().iter().filter(|o| o.executor_id == *signer.id()).count(), 0, "no signer call");
    let (ra, fa) = (settled_balance(&worker, relayer.id()).await?, settled_balance(&worker, factory.id()).await?);
    let e = fail(r);
    assert!(e.contains("zero executor"), "{e}");
    let v3: AccountId = factory.view("vault_id").args_json(json!({"n": 3})).await?.json()?;
    let v3_exists = worker.view_account(&v3).await.is_ok();
    let cfg: Value = factory.view("get_config").await?.json()?;
    let d = |a: NearToken, b: NearToken| (a.as_yoctonear() as f64 - b.as_yoctonear() as f64) / 1e24;
    println!(
        "factory refused | relayer {:+.6} NEAR | factory {:+.6} NEAR | {v3} exists: {v3_exists} | count {}",
        d(ra, rb), d(fa, fb), cfg["count"]
    );
    assert!(!v3_exists, "no account");
    assert_eq!(cfg["count"], 2, "no vault number used");
    // Bounds, not equalities: the 2 NEAR deposit is back; what is gone is gas (150 Tgas prepaid is 0.015 NEAR).
    assert!(d(ra, rb) < 0.0 && d(ra, rb) > -0.1, "deposit returned, only gas spent: {}", d(ra, rb));
    assert!(d(fa, fb) < 0.1, "nothing stays in the factory: {}", d(fa, fb));
    // The other checks the factory repeats from the vault's init, each refused the same way.
    let dup = json!({"cooldown_secs": COOLDOWN, "markets": [m(LIVE, USDC, EXEC), m(LIVE, USDC, EXEC)]});
    let short = json!({"cooldown_secs": COOLDOWN, "markets": [m("0x01", USDC, EXEC)]});
    let token = json!({"cooldown_secs": COOLDOWN, "markets": [m(LIVE, "0x12", EXEC)]});
    let cases = [
        ("bad owner", "0x1234", policy.clone(), "owner: expected 20 bytes of hex"),
        ("no markets", env.owner.address.as_str(), json!({"cooldown_secs": COOLDOWN, "markets": []}), "no markets"),
        ("duplicate", env.owner.address.as_str(), dup, "duplicate market"),
        ("short id", env.owner.address.as_str(), short, "market id: expected 32 bytes of hex"),
        ("bad token", env.owner.address.as_str(), token, "loan_token: expected 20 bytes of hex"),
    ];
    for (name, owner, p, want) in cases {
        let e = fail(create_vault(&relayer, factory.id(), owner, p).await?);
        assert!(e.contains(want), "{name}: {e}");
        println!("factory refused | {name} | {want}");
    }
    let cfg: Value = factory.view("get_config").await?.json()?;
    assert_eq!(cfg["count"], 2, "no vault number used");

    // 7. The signer call fails (a second factory whose `signer` has no contract): the deposit goes back to the
    // caller and no account is created.
    let f2 = root.create_subaccount("factory2").initial_balance(NearToken::from_near(5)).transact().await?;
    let f2 = f2.into_result()?.deploy(&std::fs::read("factory/target/near/cmdk_factory.wasm")?).await?.into_result()?;
    let args = json!({"code_hash": hash58, "signer": relayer.id(), "path": FACTORY_PATH, "morpho": MORPHO});
    let r = f2.call("init").args_json(args).transact().await?;
    assert!(r.is_success(), "{:?}", r.into_result().err());
    let cfg2: Value = f2.view("get_config").await?.json()?;
    assert_eq!(cfg2["signer"].as_str().unwrap(), relayer.id().as_str(), "factory2 initialised before use");
    let rb = settled_balance(&worker, relayer.id()).await?;
    let policy = json!({"cooldown_secs": COOLDOWN, "markets": [m(LIVE, USDC, EXEC)]});
    let r = create_vault(&relayer, f2.id(), &env.owner.address, policy).await?;
    print_receipts("create_vault on factory2, signer call fails", &r);
    assert!(r.is_success(), "{:?}", r.clone().into_result().err());
    let f2v1 = format!("v1.{}", f2.id());
    let want = format!("derived_public_key failed for {f2v1}; deposit returned to {}", relayer.id());
    assert!(r.logs().contains(&want.as_str()), "{:?}", r.logs());
    // The returned deposit is a Transfer receipt from `on_derived_key`; wait until it and the refunds landed.
    let ra = settled_balance(&worker, relayer.id()).await?;
    let exists = worker.view_account(&AccountId::from_str(&f2v1)?).await.is_ok();
    println!("factory signer failure | relayer {:+.6} NEAR | {f2v1} exists: {exists}", d(ra, rb));
    assert!(!exists);
    assert!(d(ra, rb) < 0.0 && d(ra, rb) > -0.1, "deposit returned, only gas spent: {}", d(ra, rb));
    Ok(())
}
