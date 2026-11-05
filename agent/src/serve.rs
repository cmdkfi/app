//! Read-only HTTP endpoint for the measurement evidence (build item 38). It serves, for this boot, the files a
//! third party needs to rebuild and check the measurement: the guest agent's `app-compose.json` and `vm_config.json`,
//! the quote, the event log, `info.json` (app certificate removed) and the registration records, one per vault
//! (`registration-<tag>.json`, `reregistration-<tag>-<n>.json`; tag `v<n>` = the factory's vault n). `GET /vaults`
//! lists the watched vaults (3 Nov): account, registration, expiry, ticks and the last tick's time. Transcripts are
//! public in the build (decided 5 Nov, so the judges can verify them; private to the vault's owner is a production
//! release item): `GET /transcripts` lists every transcript on the evidence volume, all boots and all vaults, and
//! `GET /transcripts/<name>` serves one, for a name that passes `transcript_name` only.
//! GET only; fixed names only, no paths from the request reach the file system; one short-lived thread per
//! connection, at most MAX_CONN at once. std::net only, so the measured binary gains no HTTP server dependency.
use crate::vaults;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::io::{Read, Write};
use std::net::{TcpListener, TcpStream};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::Arc;
use std::time::Duration;

/// The measurement evidence files of a boot, in the order the index lists them.
pub const PUBLIC_FILES: [&str; 5] = ["app-compose.json", "vm_config.json", "tdx_quote.hex", "event_log.json", "info.json"];
const MAX_CONN: usize = 16;
const MAX_REQUEST: usize = 8 * 1024;

/// A served name: one of PUBLIC_FILES, `registration-<tag>.json`, or `reregistration-<tag>-<digits>.json`, with a
/// tag as `vaults::valid_tag` accepts it. Nothing else.
pub fn is_public_name(name: &str) -> bool {
    if PUBLIC_FILES.contains(&name) {
        return true;
    }
    let digits = |d: &str| !d.is_empty() && d.len() <= 6 && d.bytes().all(|b| b.is_ascii_digit());
    if let Some(t) = name.strip_prefix("registration-").and_then(|r| r.strip_suffix(".json")) {
        return vaults::valid_tag(t);
    }
    name.strip_prefix("reregistration-")
        .and_then(|r| r.strip_suffix(".json"))
        .and_then(|r| r.rsplit_once('-'))
        .map(|(t, n)| vaults::valid_tag(t) && digits(n))
        .unwrap_or(false)
}

/// What a transcript's name says, for a name the agent writes (`main.rs`: `transcripts/<boot>-<tag>[-twin]-<loop>.json`,
/// boot `boot-<unix seconds>-<first 8 hex of the implicit account>`, loop `{:04}`), checked by hand as the pattern
/// `^boot-[0-9]{1,12}-[0-9a-f]{8}-(v|vault)[0-9]{1,6}(-twin)?-[0-9]{4,6}\.json$` (tag as `vaults::valid_tag`).
/// Nothing else is a transcript name; in particular no `/`, no `..`.
#[derive(Debug, PartialEq, Eq)]
pub struct TranscriptName {
    pub boot: String,
    pub vault: String,
    pub twin: bool,
    pub loop_n: u64,
}

pub fn transcript_name(name: &str) -> Option<TranscriptName> {
    let digits = |d: &str, lo: usize, hi: usize| (lo..=hi).contains(&d.len()) && d.bytes().all(|b| b.is_ascii_digit());
    let rest = name.strip_prefix("boot-")?.strip_suffix(".json")?;
    let (head, n) = rest.rsplit_once('-')?;
    if !digits(n, 4, 6) {
        return None;
    }
    let (head, twin) = match head.strip_suffix("-twin") {
        Some(h) => (h, true),
        None => (head, false),
    };
    let mut it = head.splitn(3, '-');
    let (ts, id, tag) = (it.next()?, it.next()?, it.next()?);
    let hex8 = id.len() == 8 && id.bytes().all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b));
    if !digits(ts, 1, 12) || !hex8 || !vaults::valid_tag(tag) {
        return None;
    }
    Some(TranscriptName { boot: format!("boot-{ts}-{id}"), vault: tag.to_string(), twin, loop_n: n.parse().ok()? })
}

/// The transcript index: every transcript file on the evidence volume (all boots, since the volume persists across
/// restarts), sorted by name, with its size, SHA-256 (the same as its `RESULT transcript` log line) and what its name says.
pub fn transcripts(dir: &Path, boot_id: &str) -> Value {
    let base = dir.join("transcripts");
    let mut names: Vec<String> = std::fs::read_dir(&base)
        .map(|rd| {
            rd.filter_map(|e| e.ok())
                .filter(|e| e.file_type().map(|t| t.is_file()).unwrap_or(false))
                .filter_map(|e| e.file_name().into_string().ok())
                .filter(|n| transcript_name(n).is_some())
                .collect()
        })
        .unwrap_or_default();
    names.sort();
    let list: Vec<Value> = names
        .iter()
        .filter_map(|n| {
            let t = transcript_name(n)?;
            let b = std::fs::read(base.join(n)).ok()?;
            Some(json!({"name": n, "bytes": b.len(), "sha256": hex::encode(Sha256::digest(&b)), "boot": t.boot, "vault": t.vault,
                "twin": t.twin, "loop": t.loop_n, "path": format!("/transcripts/{n}")}))
        })
        .collect();
    json!({"boot": boot_id, "count": list.len(), "transcripts": list,
        "note": "every decision-loop transcript on this agent's evidence volume, all boots and vaults; public in the testnet build (5 Nov), private to the vault's owner in the production release; check one with agent/verify-transcript.sh"})
}

/// The index: every public file present for this boot with its size and SHA-256, and the watched vaults.
pub fn index(dir: &Path, boot_id: &str, operator: &str, vaults: &Value) -> Value {
    let base = dir.join(boot_id);
    let mut names: Vec<String> = std::fs::read_dir(&base)
        .map(|rd| rd.filter_map(|e| e.ok()).filter_map(|e| e.file_name().into_string().ok()).filter(|n| is_public_name(n)).collect())
        .unwrap_or_default();
    names.sort_by_key(|n| (PUBLIC_FILES.iter().position(|p| p == n).unwrap_or(PUBLIC_FILES.len()), n.clone()));
    let files: Vec<Value> = names
        .iter()
        .filter_map(|n| std::fs::read(base.join(n)).ok().map(|b| json!({"name": n, "bytes": b.len(), "sha256": hex::encode(Sha256::digest(&b)), "path": format!("/evidence/{n}")})))
        .collect();
    json!({"boot": boot_id, "operator": operator, "files": files, "vaults": vaults,
        "note": "measurement evidence of this boot and the vaults it watches (also GET /vaults); the decision-loop transcripts of every boot are listed at GET /transcripts"})
}

/// The response for one request line: (status, content type, body).
pub fn route(dir: &Path, boot_id: &str, operator: &str, vaults: &Value, request: &str) -> (u16, &'static str, Vec<u8>) {
    let mut parts = request.split_whitespace();
    let (method, target) = (parts.next().unwrap_or(""), parts.next().unwrap_or(""));
    if method != "GET" {
        return (405, "text/plain", b"GET only\n".to_vec());
    }
    let path = target.split('?').next().unwrap_or("");
    if path == "/" || path == "/evidence" || path == "/evidence/" {
        return (200, "application/json", serde_json::to_vec_pretty(&index(dir, boot_id, operator, vaults)).unwrap_or_default());
    }
    if path == "/vaults" {
        return (200, "application/json", serde_json::to_vec_pretty(&json!({"boot": boot_id, "operator": operator, "vaults": vaults})).unwrap_or_default());
    }
    if path == "/transcripts" || path == "/transcripts/" {
        return (200, "application/json", serde_json::to_vec_pretty(&transcripts(dir, boot_id)).unwrap_or_default());
    }
    if let Some(name) = path.strip_prefix("/transcripts/") {
        // the file path is built only from a name that passed transcript_name
        if transcript_name(name).is_some() {
            if let Ok(b) = std::fs::read(dir.join("transcripts").join(name)) {
                return (200, "application/json", b);
            }
        }
    }
    if let Some(name) = path.strip_prefix("/evidence/") {
        if is_public_name(name) {
            if let Ok(b) = std::fs::read(dir.join(boot_id).join(name)) {
                let ct = if name.ends_with(".json") { "application/json" } else { "text/plain" };
                return (200, ct, b);
            }
        }
    }
    (404, "text/plain", b"not found\n".to_vec())
}

fn handle(mut c: TcpStream, dir: &Path, boot_id: &str, operator: &str, board: &vaults::Board) {
    let _ = c.set_read_timeout(Some(Duration::from_secs(5)));
    let _ = c.set_write_timeout(Some(Duration::from_secs(10)));
    let mut buf = vec![0u8; MAX_REQUEST];
    let mut n = 0;
    while n < buf.len() {
        match c.read(&mut buf[n..]) {
            Ok(0) | Err(_) => break,
            Ok(k) => {
                n += k;
                if buf[..n].windows(4).any(|w| w == b"\r\n\r\n") || buf[..n].contains(&b'\n') {
                    break;
                }
            }
        }
    }
    let req = String::from_utf8_lossy(&buf[..n]);
    let line = req.lines().next().unwrap_or("");
    let (code, ct, body) = route(dir, boot_id, operator, &vaults::board_json(board), line);
    let reason = match code {
        200 => "OK",
        404 => "Not Found",
        _ => "Method Not Allowed",
    };
    let head = format!("HTTP/1.1 {code} {reason}\r\nContent-Type: {ct}\r\nContent-Length: {}\r\nConnection: close\r\n\r\n", body.len());
    let _ = c.write_all(head.as_bytes()).and_then(|_| c.write_all(&body));
}

/// Binds `addr` and serves in a background thread. A bind failure is returned (and logged by the caller); it never
/// stops the agent.
pub fn spawn(addr: &str, dir: PathBuf, boot_id: String, operator: String, board: vaults::Board) -> std::io::Result<std::net::SocketAddr> {
    let l = TcpListener::bind(addr)?;
    let local = l.local_addr()?;
    let live = Arc::new(AtomicUsize::new(0));
    std::thread::Builder::new().name("evidence-http".into()).spawn(move || {
        for c in l.incoming().flatten() {
            if live.load(Ordering::SeqCst) >= MAX_CONN {
                continue; // dropped: closes the connection
            }
            live.fetch_add(1, Ordering::SeqCst);
            let (d, b, o, lv, bd) = (dir.clone(), boot_id.clone(), operator.clone(), live.clone(), board.clone());
            let r = std::thread::Builder::new().spawn(move || {
                handle(c, &d, &b, &o, &bd);
                lv.fetch_sub(1, Ordering::SeqCst);
            });
            if r.is_err() {
                live.fetch_sub(1, Ordering::SeqCst);
            }
        }
    })?;
    Ok(local)
}

#[cfg(test)]
mod tests {
    use super::*;

    const T1: &str = "boot-1-0123abcd-v3-0001.json";
    const T2: &str = "boot-1-0123abcd-v3-twin-0002.json";
    const T3: &str = "boot-1792000000-89abcdef-vault2-12345.json";

    fn dir(name: &str) -> PathBuf {
        let d = std::env::temp_dir().join(format!("agent-serve-test-{name}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&d);
        let b = d.join("boot-1");
        std::fs::create_dir_all(b.join("x")).unwrap();
        std::fs::create_dir_all(d.join("transcripts").join("boot-1-0123abcd-v3-0005.json")).unwrap(); // a directory, not a file
        std::fs::write(b.join("app-compose.json"), b"{}").unwrap();
        std::fs::write(b.join("tdx_quote.hex"), b"abcd").unwrap();
        std::fs::write(b.join("reregistration-v3-2.json"), b"{}").unwrap();
        std::fs::write(b.join("registration-v3.json"), b"{}").unwrap();
        std::fs::write(b.join("registration.json"), b"{}").unwrap();
        std::fs::write(b.join("boot-1-0123abcd-v3-0009.json"), b"{\"elsewhere\":1}").unwrap(); // valid name, wrong directory
        let t = d.join("transcripts");
        std::fs::write(t.join("boot-1-0001.json"), b"{\"untagged\":1}").unwrap();
        std::fs::write(t.join("notes.json"), b"{}").unwrap();
        std::fs::write(t.join(T2), b"{\"twin\":true}\n").unwrap();
        std::fs::write(t.join(T1), b"{\n  \"vault\": \"v3.f.testnet\",\n  \"x\": [1, 2]\n}\n").unwrap();
        std::fs::write(t.join(T3), b"{}").unwrap();
        d
    }

    #[test]
    fn serves_measurement_evidence() {
        let d = dir("evidence");
        let vs = json!([{"vault": "v3.f.testnet", "tag": "v3", "registration": "registered"}]);
        let (c, _, b) = route(&d, "boot-1", "cmdk", &vs, "GET /evidence/app-compose.json HTTP/1.1");
        assert_eq!((c, b.as_slice()), (200, b"{}".as_slice()));
        assert_eq!(route(&d, "boot-1", "cmdk", &vs, "GET /evidence/tdx_quote.hex HTTP/1.1").0, 200);
        assert_eq!(route(&d, "boot-1", "cmdk", &vs, "GET /evidence/reregistration-v3-2.json HTTP/1.1").0, 200);
        assert_eq!(route(&d, "boot-1", "cmdk", &vs, "GET /evidence/registration-v3.json HTTP/1.1").0, 200);
        for bad in ["GET /evidence/../transcripts/boot-1-0001.json HTTP/1.1", "GET /evidence/transcripts/boot-1-0001.json HTTP/1.1",
                    "GET /evidence/vm_config.json HTTP/1.1", "GET /evidence/x HTTP/1.1",
                    "GET /evidence/%2e%2e%2ftranscripts HTTP/1.1", "GET /evidence/reregistration-.json HTTP/1.1",
                    "GET /evidence/registration.json HTTP/1.1", "GET /evidence/registration-x.json HTTP/1.1", "GET /evidence/reregistration-v3-.json HTTP/1.1",
                    "GET /evidence/registration-v3.f.json HTTP/1.1", "GET /evidence/reregistration-2.json HTTP/1.1", "GET /vaults/x HTTP/1.1",
                    "GET /evidence/boot-1-0123abcd-v3-0001.json HTTP/1.1", "GET /evidence/../transcripts/boot-1-0123abcd-v3-0001.json HTTP/1.1"] {
            assert_eq!(route(&d, "boot-1", "cmdk", &vs, bad).0, 404, "{bad}");
        }
        assert_eq!(route(&d, "boot-1", "cmdk", &vs, "POST /evidence/app-compose.json HTTP/1.1").0, 405);
        let (c, _, b) = route(&d, "boot-1", "cmdk", &vs, "GET / HTTP/1.1");
        assert_eq!(c, 200);
        let v: Value = serde_json::from_slice(&b).unwrap();
        let names: Vec<&str> = v["files"].as_array().unwrap().iter().map(|f| f["name"].as_str().unwrap()).collect();
        assert_eq!(names, vec!["app-compose.json", "tdx_quote.hex", "registration-v3.json", "reregistration-v3-2.json"]);
        assert_eq!(v["vaults"], vs);
        assert_eq!(v["operator"], "cmdk");
        assert!(!String::from_utf8_lossy(&b).contains("boot-1-0001"));
        assert!(v["note"].as_str().unwrap().contains("GET /transcripts"));
        let (c, _, b) = route(&d, "boot-1", "cmdk", &vs, "GET /vaults HTTP/1.1");
        let v: Value = serde_json::from_slice(&b).unwrap();
        assert_eq!((c, &v["vaults"], &v["boot"]), (200, &vs, &json!("boot-1")));
        let _ = std::fs::remove_dir_all(&d);
    }

    #[test]
    fn transcript_names() {
        let t = transcript_name(T2).unwrap();
        assert_eq!(t, TranscriptName { boot: "boot-1-0123abcd".into(), vault: "v3".into(), twin: true, loop_n: 2 });
        let t = transcript_name("boot-1793856948-f21cf44b-v1-0042.json").unwrap();
        assert_eq!((t.boot.as_str(), t.vault.as_str(), t.twin, t.loop_n), ("boot-1793856948-f21cf44b", "v1", false, 42));
        assert_eq!(transcript_name(T3).unwrap().vault, "vault2");
        for bad in ["boot-1-0001.json", "boot-1793051811-26cc5edf-0001.json", "boot-1-0123abcd-v3-001.json", "boot-1-0123abcd-v3-0001234.json",
                    "boot-1-0123ABCD-v3-0001.json", "boot-1-0123abc-v3-0001.json", "boot-1-0123abcd-x3-0001.json", "boot-1-0123abcd-v-0001.json",
                    "boot-1-0123abcd-v3-twin-twin-0001.json", "boot-1-0123abcd-v3-0001.JSON", "boot-1-0123abcd-v3-0001.json/", "/boot-1-0123abcd-v3-0001.json",
                    "../boot-1-0123abcd-v3-0001.json", "boot-1-0123abcd-v3/../0001.json", "boot--0123abcd-v3-0001.json", "boot-1-0123abcd-v3-0001",
                    "notes.json", "app-compose.json", "registration-v3.json", ""] {
            assert!(transcript_name(bad).is_none(), "{bad}");
        }
    }

    #[test]
    fn serves_every_transcript_and_nothing_else() {
        let d = dir("transcripts");
        let vs = json!([]);
        let (c, ct, b) = route(&d, "boot-7", "cmdk", &vs, "GET /transcripts HTTP/1.1");
        assert_eq!((c, ct), (200, "application/json"));
        let v: Value = serde_json::from_slice(&b).unwrap();
        assert_eq!(v["boot"], "boot-7");
        let list = v["transcripts"].as_array().unwrap();
        let names: Vec<&str> = list.iter().map(|f| f["name"].as_str().unwrap()).collect();
        assert_eq!(names, vec![T1, T2, T3]); // sorted by name, all boots; untagged, other and directory entries left out
        assert_eq!(v["count"], 3);
        let body1 = std::fs::read(d.join("transcripts").join(T1)).unwrap();
        assert_eq!(list[0], json!({"name": T1, "bytes": body1.len(), "sha256": hex::encode(Sha256::digest(&body1)), "boot": "boot-1-0123abcd",
            "vault": "v3", "twin": false, "loop": 1, "path": format!("/transcripts/{T1}")}));
        assert_eq!((&list[1]["twin"], &list[1]["loop"]), (&json!(true), &json!(2)));
        assert_eq!((&list[2]["boot"], &list[2]["vault"], &list[2]["loop"]), (&json!("boot-1792000000-89abcdef"), &json!("vault2"), &json!(12345)));
        assert_eq!(route(&d, "boot-7", "cmdk", &vs, "GET /transcripts/ HTTP/1.1").1, "application/json");
        for name in [T1, T2, T3] {
            let (c, ct, b) = route(&d, "boot-7", "cmdk", &vs, &format!("GET /transcripts/{name} HTTP/1.1"));
            assert_eq!((c, ct), (200, "application/json"));
            assert_eq!(b, std::fs::read(d.join("transcripts").join(name)).unwrap(), "{name} byte for byte");
        }
        for bad in ["GET /transcripts/boot-1-0001.json HTTP/1.1", "GET /transcripts/notes.json HTTP/1.1",
                    "GET /transcripts/../boot-1/app-compose.json HTTP/1.1", "GET /transcripts/..%2fboot-1%2fapp-compose.json HTTP/1.1",
                    "GET /transcripts/../transcripts/boot-1-0123abcd-v3-0001.json HTTP/1.1", "GET /transcripts/./boot-1-0123abcd-v3-0001.json HTTP/1.1",
                    "GET /transcripts//boot-1-0123abcd-v3-0001.json HTTP/1.1", "GET /transcripts/boot-1-0123abcd-v3-0001.json/ HTTP/1.1",
                    "GET /transcripts/app-compose.json HTTP/1.1", "GET /transcripts/registration-v3.json HTTP/1.1",
                    "GET /transcripts/boot-1-0123abcd-v3-0009.json HTTP/1.1", "GET /transcripts/boot-1-0123abcd-v3-0005.json HTTP/1.1",
                    "GET /transcripts/boot-1-0123abcd-v3-0003.json HTTP/1.1", "GET /transcripts/boot-1-0123ABCD-v3-0001.json HTTP/1.1",
                    "GET /transcripts/x/boot-1-0123abcd-v3-0001.json HTTP/1.1", "GET /transcriptsboot-1-0123abcd-v3-0001.json HTTP/1.1"] {
            assert_eq!(route(&d, "boot-7", "cmdk", &vs, bad).0, 404, "{bad}");
        }
        assert_eq!(route(&d, "boot-7", "cmdk", &vs, &format!("POST /transcripts/{T1} HTTP/1.1")).0, 405);
        assert_eq!(route(&d, "boot-7", "cmdk", &vs, "HEAD /transcripts HTTP/1.1").0, 405);
        let empty = std::env::temp_dir().join(format!("agent-serve-test-empty-{}", std::process::id()));
        let v: Value = serde_json::from_slice(&route(&empty, "boot-7", "cmdk", &vs, "GET /transcripts HTTP/1.1").2).unwrap();
        assert_eq!((&v["count"], &v["transcripts"]), (&json!(0), &json!([])));
        // over a real socket: status line, Content-Type and the bytes as written
        let addr = spawn("127.0.0.1:0", d.clone(), "boot-7".into(), "cmdk".into(), vaults::Board::default()).unwrap();
        let get = |target: &str| {
            let mut c = TcpStream::connect(addr).unwrap();
            c.write_all(format!("GET {target} HTTP/1.1\r\nHost: x\r\n\r\n").as_bytes()).unwrap();
            let mut r = vec![];
            c.read_to_end(&mut r).unwrap();
            let i = r.windows(4).position(|w| w == b"\r\n\r\n").unwrap();
            (String::from_utf8_lossy(&r[..i]).into_owned(), r[i + 4..].to_vec())
        };
        let (head, body) = get(&format!("/transcripts/{T1}"));
        assert!(head.starts_with("HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"), "{head}");
        assert_eq!(body, std::fs::read(d.join("transcripts").join(T1)).unwrap());
        assert!(get("/transcripts/../boot-1/app-compose.json").0.starts_with("HTTP/1.1 404 "));
        let (head, body) = get("/transcripts");
        assert!(head.starts_with("HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"), "{head}");
        assert_eq!(serde_json::from_slice::<Value>(&body).unwrap()["count"], 3);
        let _ = std::fs::remove_dir_all(&d);
    }
}
