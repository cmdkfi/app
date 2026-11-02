//! NEAR testnet: JSON-RPC reads and one transaction kind, a single FunctionCall signed with the agent's ed25519 key.
//! The transaction is borsh-encoded by hand (Transaction V0: signer, key, nonce, receiver, block hash, actions),
//! so the agent needs no NEAR SDK. Hash = sha256(borsh(tx)); the signature is ed25519 over that hash.
use anyhow::{anyhow, bail, Result};
use base64::{engine::general_purpose::STANDARD as B64, Engine};
use ed25519_dalek::{Signer, SigningKey};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::time::Duration;

pub struct Near {
    http: reqwest::Client,
    pub rpc: String,
}

pub fn pk_string(sk: &SigningKey) -> String {
    format!("ed25519:{}", bs58::encode(sk.verifying_key().to_bytes()).into_string())
}

/// The implicit account id of a key: 64 lowercase hex characters of the public key.
pub fn implicit_account(sk: &SigningKey) -> String {
    hex::encode(sk.verifying_key().to_bytes())
}

fn put_bytes(b: &mut Vec<u8>, x: &[u8]) {
    b.extend_from_slice(&(x.len() as u32).to_le_bytes());
    b.extend_from_slice(x);
}

/// borsh(Transaction V0) with one FunctionCall action (action enum index 2).
pub fn encode_function_call(
    signer_id: &str,
    pk: &[u8; 32],
    nonce: u64,
    receiver_id: &str,
    block_hash: &[u8; 32],
    method: &str,
    args: &[u8],
    gas: u64,
    deposit: u128,
) -> Vec<u8> {
    let mut b = Vec::with_capacity(args.len() + 256);
    put_bytes(&mut b, signer_id.as_bytes());
    b.push(0); // KeyType::ED25519
    b.extend_from_slice(pk);
    b.extend_from_slice(&nonce.to_le_bytes());
    put_bytes(&mut b, receiver_id.as_bytes());
    b.extend_from_slice(block_hash);
    b.extend_from_slice(&1u32.to_le_bytes()); // one action
    b.push(2); // Action::FunctionCall
    put_bytes(&mut b, method.as_bytes());
    put_bytes(&mut b, args);
    b.extend_from_slice(&gas.to_le_bytes());
    b.extend_from_slice(&deposit.to_le_bytes());
    b
}

impl Near {
    pub fn new(rpc: &str) -> Self {
        Near { http: reqwest::Client::builder().timeout(Duration::from_secs(90)).build().unwrap(), rpc: rpc.to_string() }
    }

    pub async fn call(&self, method: &str, params: Value) -> Result<Value> {
        let body = json!({"jsonrpc": "2.0", "id": "p3", "method": method, "params": params});
        let r: Value = self.http.post(&self.rpc).json(&body).send().await?.json().await?;
        if let Some(e) = r.get("error") {
            bail!("NEAR RPC {method}: {}", e.to_string().chars().take(800).collect::<String>());
        }
        Ok(r["result"].clone())
    }

    pub async fn view(&self, account: &str, method: &str, args: &Value) -> Result<Value> {
        let r = self
            .call(
                "query",
                json!({"request_type": "call_function", "finality": "final", "account_id": account,
                       "method_name": method, "args_base64": B64.encode(args.to_string())}),
            )
            .await?;
        let bytes: Vec<u8> = serde_json::from_value(r["result"].clone())?;
        Ok(serde_json::from_slice(&bytes)?)
    }

    /// Balance in yoctoNEAR, or None while the account does not exist.
    pub async fn balance(&self, account: &str) -> Result<Option<u128>> {
        match self.call("query", json!({"request_type": "view_account", "finality": "final", "account_id": account})).await {
            Ok(r) => Ok(Some(r["amount"].as_str().unwrap_or("0").parse()?)),
            Err(e) if e.to_string().contains("UNKNOWN_ACCOUNT") || e.to_string().contains("does not exist") => Ok(None),
            Err(e) => Err(e),
        }
    }

    async fn access_key(&self, account: &str, pk: &str) -> Result<(u64, [u8; 32])> {
        let r = self
            .call(
                "query",
                json!({"request_type": "view_access_key", "finality": "final", "account_id": account, "public_key": pk}),
            )
            .await?;
        let nonce = r["nonce"].as_u64().ok_or_else(|| anyhow!("no nonce"))?;
        let bh: [u8; 32] = bs58::decode(r["block_hash"].as_str().unwrap_or(""))
            .into_vec()?
            .try_into()
            .map_err(|_| anyhow!("bad block hash"))?;
        Ok((nonce, bh))
    }

    /// Signs and sends one FunctionCall from `signer_id` with `sk`, waits for the final outcome.
    /// Returns (tx hash, final outcome JSON).
    pub async fn function_call(
        &self,
        sk: &SigningKey,
        signer_id: &str,
        receiver: &str,
        method: &str,
        args: &[u8],
        gas: u64,
        deposit: u128,
    ) -> Result<(String, Value)> {
        let pk = sk.verifying_key().to_bytes();
        let (nonce, block_hash) = self.access_key(signer_id, &pk_string(sk)).await?;
        let tx = encode_function_call(signer_id, &pk, nonce + 1, receiver, &block_hash, method, args, gas, deposit);
        let hash: [u8; 32] = Sha256::digest(&tx).into();
        let sig = sk.sign(&hash).to_bytes();
        let mut signed = tx;
        signed.push(0); // Signature::ED25519
        signed.extend_from_slice(&sig);
        let h = bs58::encode(hash).into_string();
        let sent = self
            .call("send_tx", json!({"signed_tx_base64": B64.encode(&signed), "wait_until": "EXECUTED_OPTIMISTIC"}))
            .await;
        if let Err(e) = &sent {
            // a timeout still leaves the transaction in flight; anything else is a real failure
            if !e.to_string().contains("TIMEOUT") {
                bail!("send_tx {h}: {e}");
            }
        }
        for _ in 0..30 {
            match self.call("tx", json!({"tx_hash": h, "sender_account_id": signer_id, "wait_until": "FINAL"})).await {
                Ok(r) => return Ok((h, r)),
                Err(e) if e.to_string().contains("TIMEOUT") || e.to_string().contains("UNKNOWN_TRANSACTION") => {
                    tokio::time::sleep(Duration::from_secs(2)).await
                }
                Err(e) => return Err(e),
            }
        }
        bail!("no final outcome for {h}")
    }
}

/// Gas and NEAR burnt per receipt of a final outcome, as relayer.py's `near-gas` prints it.
pub fn gas_summary(r: &Value) -> Value {
    let t = &r["transaction_outcome"]["outcome"];
    let mut rows = vec![json!({"id": "tx", "executor": t["executor_id"], "gas": t["gas_burnt"], "tokens_burnt": t["tokens_burnt"], "status": "conversion"})];
    let (mut gas, mut burnt) = (t["gas_burnt"].as_u64().unwrap_or(0), t["tokens_burnt"].as_str().unwrap_or("0").parse::<u128>().unwrap_or(0));
    for ro in r["receipts_outcome"].as_array().into_iter().flatten() {
        let o = &ro["outcome"];
        gas += o["gas_burnt"].as_u64().unwrap_or(0);
        burnt += o["tokens_burnt"].as_str().unwrap_or("0").parse::<u128>().unwrap_or(0);
        let st = o["status"].as_object().and_then(|m| m.keys().next().cloned()).unwrap_or_default();
        let failure = if st == "Failure" { o["status"]["Failure"].to_string() } else { String::new() };
        rows.push(json!({"id": ro["id"], "executor": o["executor_id"], "gas": o["gas_burnt"], "tokens_burnt": o["tokens_burnt"],
                         "status": st, "failure": failure, "logs": o["logs"]}));
    }
    json!({"final_status": r["status"], "gas_total": gas, "tgas_total": gas as f64 / 1e12, "near_burnt": burnt as f64 / 1e24, "receipts": rows})
}

/// The function call's return value (SuccessValue, base64 JSON), if any.
pub fn success_value(r: &Value) -> Option<Value> {
    let s = r["status"]["SuccessValue"].as_str()?;
    let b = B64.decode(s).ok()?;
    if b.is_empty() {
        return Some(Value::Null);
    }
    serde_json::from_slice(&b).ok()
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Local check of the hand-written borsh encoding against a near-sandbox node (no testnet funds):
    /// SANDBOX_RPC=http://127.0.0.1:3030 SANDBOX_KEY=<home>/validator_key.json cargo test -- --ignored
    /// The call targets an account without code, so the transaction must be accepted and its receipt must fail
    /// with CodeDoesNotExist.
    #[tokio::test]
    #[ignore]
    async fn function_call_is_accepted_by_a_sandbox_node() {
        let rpc = std::env::var("SANDBOX_RPC").unwrap();
        let k: Value = serde_json::from_str(&std::fs::read_to_string(std::env::var("SANDBOX_KEY").unwrap()).unwrap()).unwrap();
        let raw = bs58::decode(k["secret_key"].as_str().unwrap().trim_start_matches("ed25519:")).into_vec().unwrap();
        let sk = SigningKey::from_bytes(&raw[..32].try_into().unwrap());
        assert_eq!(pk_string(&sk), k["public_key"].as_str().unwrap());
        let n = Near::new(&rpc);
        let id = k["account_id"].as_str().unwrap();
        let (h, r) = n.function_call(&sk, id, id, "no_such_method", b"{\"x\":1}", 30_000_000_000_000, 0).await.unwrap();
        let g = gas_summary(&r);
        println!("{h} {}", serde_json::to_string_pretty(&g).unwrap());
        assert!(r["status"]["Failure"].to_string().contains("CodeDoesNotExist"), "{}", r["status"]);
        assert_eq!(r["transaction"]["hash"].as_str().unwrap(), h);
    }
}
