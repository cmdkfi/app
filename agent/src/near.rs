//! NEAR testnet: JSON-RPC reads and one transaction kind, a single FunctionCall signed with the agent's ed25519 key.
//! The transaction is borsh-encoded by hand (Transaction V0: signer, key, nonce, receiver, block hash, actions),
//! so the agent needs no NEAR SDK. Hash = sha256(borsh(tx)); the signature is ed25519 over that hash.
//! Several RPC providers, tried in order (9 Nov: rpc.testnet.near.org answered the enclave's shared egress address
//! `-429 … DEPRECATED` on about 95% of calls): a call that the provider could not serve (transport error, non-JSON,
//! rate limit, internal error) goes to the next one; an answer about the request itself (`HANDLER_ERROR`,
//! `REQUEST_VALIDATION_ERROR`: unknown account, contract panic, timeout of a sent transaction) is returned as it is.
//! The next call starts at the provider that answered last.
use anyhow::{anyhow, bail, Result};
use base64::{engine::general_purpose::STANDARD as B64, Engine};
use ed25519_dalek::{Signer, SigningKey};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::sync::atomic::{AtomicUsize, Ordering};
use std::time::Duration;

/// how long one provider gets for a read; `send_tx` and `tx` wait for execution and get SEND_TIMEOUT
const READ_TIMEOUT: Duration = Duration::from_secs(20);
const SEND_TIMEOUT: Duration = Duration::from_secs(90);

pub struct Near {
    http: reqwest::Client,
    pub rpcs: Vec<String>,
    /// the provider that answered last; the next call starts there
    good: AtomicUsize,
}

/// True when an RPC `error` object is about the provider (rate limit, deprecation notice, internal error, any shape
/// without a request-level name), so the next provider may answer; false for answers about the request itself.
pub fn provider_trouble(e: &Value) -> bool {
    !matches!(e["name"].as_str(), Some("HANDLER_ERROR") | Some("REQUEST_VALIDATION_ERROR"))
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
    pub fn new(rpcs: Vec<String>) -> Self {
        assert!(!rpcs.is_empty(), "at least one NEAR RPC provider");
        Near { http: reqwest::Client::builder().timeout(SEND_TIMEOUT).build().unwrap(), rpcs, good: AtomicUsize::new(0) }
    }

    /// One provider's answer: Ok(result), Err((provider trouble?, message)).
    async fn call_one(&self, url: &str, method: &str, body: &Value) -> std::result::Result<Value, (bool, String)> {
        let t = if method == "send_tx" || method == "tx" { SEND_TIMEOUT } else { READ_TIMEOUT };
        let r: Value = match self.http.post(url).header("User-Agent", "cmdk-agent").timeout(t).json(body).send().await {
            Ok(x) => match x.json().await {
                Ok(v) => v,
                Err(e) => return Err((true, format!("{url}: not JSON: {e}"))),
            },
            Err(e) => return Err((true, format!("{url}: {e}"))),
        };
        match r.get("error") {
            Some(e) => Err((provider_trouble(e), format!("NEAR RPC {method}: {}", e.to_string().chars().take(800).collect::<String>()))),
            None => Ok(r["result"].clone()),
        }
    }

    pub async fn call(&self, method: &str, params: Value) -> Result<Value> {
        let body = json!({"jsonrpc": "2.0", "id": "p3", "method": method, "params": params});
        let n = self.rpcs.len();
        let start = self.good.load(Ordering::Relaxed) % n;
        let mut last = String::new();
        for k in 0..n {
            let i = (start + k) % n;
            match self.call_one(&self.rpcs[i], method, &body).await {
                Ok(v) => {
                    self.good.store(i, Ordering::Relaxed);
                    return Ok(v);
                }
                Err((false, m)) => {
                    self.good.store(i, Ordering::Relaxed);
                    bail!("{m}");
                }
                Err((true, m)) => last = m,
            }
        }
        bail!("{last} (every NEAR RPC provider failed: {})", self.rpcs.join(", "))
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
        let n = Near::new(vec![rpc]);
        let id = k["account_id"].as_str().unwrap();
        let (h, r) = n.function_call(&sk, id, id, "no_such_method", b"{\"x\":1}", 30_000_000_000_000, 0).await.unwrap();
        let g = gas_summary(&r);
        println!("{h} {}", serde_json::to_string_pretty(&g).unwrap());
        assert!(r["status"]["Failure"].to_string().contains("CodeDoesNotExist"), "{}", r["status"]);
        assert_eq!(r["transaction"]["hash"].as_str().unwrap(), h);
    }

    /// A local HTTP endpoint that answers every request with `body` (JSON); returns its URL and a request counter.
    fn mock(body: &'static str) -> (String, std::sync::Arc<AtomicUsize>) {
        use std::io::{Read, Write};
        let l = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
        let url = format!("http://{}", l.local_addr().unwrap());
        let hits = std::sync::Arc::new(AtomicUsize::new(0));
        let h = hits.clone();
        std::thread::spawn(move || {
            for mut c in l.incoming().flatten() {
                let mut buf = [0u8; 8192];
                let mut got = Vec::new();
                // the head, then the body by Content-Length
                loop {
                    let k = c.read(&mut buf).unwrap_or(0);
                    if k == 0 {
                        break;
                    }
                    got.extend_from_slice(&buf[..k]);
                    let text = String::from_utf8_lossy(&got).to_string();
                    if let Some(p) = text.find("\r\n\r\n") {
                        let len = text[..p]
                            .lines()
                            .find_map(|l| l.to_ascii_lowercase().strip_prefix("content-length:").map(|v| v.trim().parse::<usize>().unwrap_or(0)))
                            .unwrap_or(0);
                        if got.len() >= p + 4 + len {
                            break;
                        }
                    }
                }
                h.fetch_add(1, Ordering::SeqCst);
                let head = format!("HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n", body.len());
                let _ = c.write_all(head.as_bytes()).and_then(|_| c.write_all(body.as_bytes()));
            }
        });
        (url, hits)
    }

    const THROTTLED: &str = r#"{"jsonrpc":"2.0","id":"p3","error":{"code":-429,"message":"WARNING! THIS ENDPOINT IS DEPRECATED!"}}"#;
    const UNKNOWN: &str = r#"{"jsonrpc":"2.0","id":"p3","error":{"name":"HANDLER_ERROR","cause":{"name":"UNKNOWN_ACCOUNT","info":{}},"code":-32000,"message":"Server error"}}"#;
    const BALANCE: &str = r#"{"jsonrpc":"2.0","id":"p3","result":{"amount":"5","locked":"0","code_hash":"11111111111111111111111111111111","storage_usage":1,"storage_paid_at":0,"block_height":1,"block_hash":"x"}}"#;

    #[test]
    fn provider_trouble_separates_the_provider_from_the_request() {
        assert!(provider_trouble(&serde_json::from_str::<Value>(THROTTLED).unwrap()["error"]));
        assert!(provider_trouble(&json!({"name": "INTERNAL_ERROR", "cause": {"name": "INTERNAL_ERROR"}})));
        assert!(provider_trouble(&json!({"code": -32005, "message": "Rate limit exceeded"})));
        assert!(!provider_trouble(&serde_json::from_str::<Value>(UNKNOWN).unwrap()["error"]));
        assert!(!provider_trouble(&json!({"name": "REQUEST_VALIDATION_ERROR", "cause": {"name": "PARSE_ERROR"}})));
    }

    #[tokio::test]
    async fn a_throttled_provider_hands_the_call_to_the_next_and_the_next_call_starts_there() {
        let (bad, bad_hits) = mock(THROTTLED);
        let (ok, ok_hits) = mock(BALANCE);
        let n = Near::new(vec![bad, ok]);
        assert_eq!(n.balance("a.testnet").await.unwrap(), Some(5));
        assert_eq!(n.balance("a.testnet").await.unwrap(), Some(5));
        assert_eq!(bad_hits.load(Ordering::SeqCst), 1, "the second call starts at the provider that answered");
        assert_eq!(ok_hits.load(Ordering::SeqCst), 2);
    }

    #[tokio::test]
    async fn an_answer_about_the_request_is_not_retried_elsewhere() {
        let (first, first_hits) = mock(UNKNOWN);
        let (second, second_hits) = mock(BALANCE);
        let n = Near::new(vec![first, second]);
        assert_eq!(n.balance("nobody.testnet").await.unwrap(), None, "UNKNOWN_ACCOUNT is the answer");
        assert_eq!((first_hits.load(Ordering::SeqCst), second_hits.load(Ordering::SeqCst)), (1, 0));
    }

    #[tokio::test]
    async fn every_provider_failing_names_the_last_error_and_the_list() {
        let (a, _) = mock(THROTTLED);
        let dead = "http://127.0.0.1:9".to_string(); // discard port: connection refused
        let n = Near::new(vec![a, dead.clone()]);
        let e = n.view("x.testnet", "get_state", &json!({})).await.unwrap_err().to_string();
        assert!(e.contains("every NEAR RPC provider failed") && e.contains(&dead), "{e}");
    }
}
