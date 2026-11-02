//! Key, quote and evidence (§4.4). The ed25519 key is made inside the enclave and never leaves it; the quote's
//! report_data is its public key followed by 32 zero bytes. At startup the agent writes into the evidence volume
//! what a third party needs to rebuild the measurement (measurement H): the guest agent's `app_compose` and
//! `vm_config`, the quote and the event log; and prints each file with its SHA-256 to the (public) log.
use anyhow::{anyhow, Result};
use base64::{engine::general_purpose::STANDARD as B64, Engine};
use dstack_sdk::dstack_client::DstackClient;
use ed25519_dalek::SigningKey;
use serde_json::{json, Value};
use sha2::{Digest, Sha256, Sha384};
use std::path::{Path, PathBuf};

pub struct Boot {
    pub quote: Vec<u8>,
    pub event_log: Vec<Value>,
    /// The six values the owner approves: MRTD, RTMR0-2 from the quote; compose and OS image hash from the
    /// replayed RTMR3 event log.
    pub measurement: Value,
    pub rtmr3_replayed_matches: bool,
    pub debug: bool,
}

pub fn sha256_hex(b: &[u8]) -> String {
    hex::encode(Sha256::digest(b))
}

/// Writes `name` into `dir` and prints it to the log as `EVIDENCE <name> <sha256> <i>/<n> <base64 chunk>`.
pub fn save_evidence(dir: &Path, name: &str, bytes: &[u8]) -> Result<String> {
    let path = dir.join(name);
    if let Some(p) = path.parent() {
        std::fs::create_dir_all(p)?;
    }
    std::fs::write(&path, bytes)?;
    let h = sha256_hex(bytes);
    let b = B64.encode(bytes);
    let chunks: Vec<&str> = b.as_bytes().chunks(6000).map(|c| std::str::from_utf8(c).unwrap()).collect();
    for (i, c) in chunks.iter().enumerate() {
        println!("EVIDENCE {name} {h} {}/{} {c}", i + 1, chunks.len());
    }
    println!("RESULT evidence {name} bytes={} sha256={h}", bytes.len());
    Ok(h)
}

/// dstack cc-eventlog replay of the imr-3 events (as the vault's attest.rs and measurement H do).
fn replay_rtmr3(events: &[Value]) -> ([u8; 48], String, String) {
    let mut mr = [0u8; 48];
    let (mut compose, mut os) = (String::new(), String::new());
    for e in events.iter().filter(|e| e["imr"] == 3) {
        let payload = hex::decode(e["event_payload"].as_str().unwrap_or("")).unwrap_or_default();
        let ev = e["event"].as_str().unwrap_or("");
        let mut h = Sha384::new();
        h.update((e["event_type"].as_u64().unwrap_or(0) as u32).to_le_bytes());
        h.update(b":");
        h.update(ev.as_bytes());
        h.update(b":");
        h.update(&payload);
        let d = h.finalize();
        let mut h = Sha384::new();
        h.update(mr);
        h.update(d);
        mr.copy_from_slice(&h.finalize());
        match ev {
            "compose-hash" => compose = hex::encode(&payload),
            "os-image-hash" => os = hex::encode(&payload),
            _ => {}
        }
    }
    (mr, compose, os)
}

pub fn measurement_of(quote: &[u8], events: &[Value]) -> Result<(Value, bool, bool)> {
    let q = dcap_qvl::quote::Quote::parse(quote).map_err(|e| anyhow!("quote: {e:?}"))?;
    let td = q.report.as_td10().ok_or_else(|| anyhow!("not a TDX quote"))?;
    let (rtmr3, compose, os) = replay_rtmr3(events);
    let m = json!({
        "mrtd": hex::encode(td.mr_td), "rtmr0": hex::encode(td.rt_mr0), "rtmr1": hex::encode(td.rt_mr1),
        "rtmr2": hex::encode(td.rt_mr2), "compose_hash": compose, "os_image_hash": os,
    });
    Ok((m, rtmr3 == td.rt_mr3, td.td_attributes[0] & 1 == 1))
}

/// Inside the CVM: Info and GetQuote from the guest agent, evidence written. `dir` is the evidence volume.
pub async fn boot(sk: &SigningKey, dir: &Path, boot_id: &str) -> Result<Boot> {
    let client = DstackClient::new(None);
    let t = std::time::Instant::now();
    let info = client.info().await?;
    let mut rd = sk.verifying_key().to_bytes().to_vec();
    rd.extend_from_slice(&[0u8; 32]);
    let q = client.get_quote(rd).await?;
    println!("RESULT quote_ms={} (info + quote)", t.elapsed().as_millis());
    let quote = hex::decode(&q.quote)?;
    let event_log: Vec<Value> = serde_json::from_str(&q.event_log)?;
    let info_v = serde_json::to_value(&info)?;
    let sub: PathBuf = PathBuf::from(boot_id);
    save_evidence(dir, &sub.join("app-compose.json").to_string_lossy(), info.tcb_info.app_compose.as_bytes())?;
    save_evidence(dir, &sub.join("vm_config.json").to_string_lossy(), info.vm_config.as_bytes())?;
    save_evidence(dir, &sub.join("tdx_quote.hex").to_string_lossy(), q.quote.as_bytes())?;
    save_evidence(dir, &sub.join("event_log.json").to_string_lossy(), q.event_log.as_bytes())?;
    let mut info_slim = info_v.clone();
    info_slim["app_cert"] = json!("(omitted)");
    info_slim["tcb_info"]["event_log"] = json!("(see event_log.json)");
    save_evidence(dir, &sub.join("info.json").to_string_lossy(), serde_json::to_string_pretty(&info_slim)?.as_bytes())?;
    let (measurement, replay_ok, debug) = measurement_of(&quote, &event_log)?;
    Ok(Boot { quote, event_log, measurement, rtmr3_replayed_matches: replay_ok, debug })
}

/// Fresh Intel collateral for our own quote (P2 finding: the vault checks its validity window against block time),
/// verified locally first so a bad status is reported before 120 Tgas are spent.
pub async fn collateral(quote: &[u8]) -> Result<(String, Value)> {
    let coll = dcap_qvl::collateral::CollateralClient::with_default_http(crate::judge::PCCS)?.fetch(quote).await?;
    let now = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH)?.as_secs();
    let tcb: Value = serde_json::from_str(&coll.tcb_info).unwrap_or_default();
    let local = match dcap_qvl::verify::verify(quote, &coll, now) {
        Ok(v) => json!({"verified": true, "status": v.status, "advisory_ids": v.advisory_ids}),
        Err(e) => json!({"verified": false, "error": format!("{e:?}")}),
    };
    let s = serde_json::to_string(&coll)?;
    Ok((s.clone(), json!({"bytes": s.len(), "sha256": sha256_hex(s.as_bytes()), "tcb_issue": tcb["issueDate"],
        "tcb_next_update": tcb["nextUpdate"], "fmspc": tcb["fmspc"], "local_verify": local})))
}
