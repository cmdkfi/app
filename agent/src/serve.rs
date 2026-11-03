//! Read-only HTTP endpoint for the measurement evidence (build item 38). It serves, for this boot only, the files a
//! third party needs to rebuild and check the measurement: the guest agent's `app-compose.json` and `vm_config.json`,
//! the quote, the event log, `info.json` (app certificate removed) and the registration records. Transcripts are
//! never served (they stay in the evidence volume, private to the vault's owner). GET only; fixed names only, no
//! paths from the request reach the file system; one short-lived thread per connection, at most MAX_CONN at once.
//! std::net only, so the measured binary gains no HTTP server dependency.
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

/// A served name: one of PUBLIC_FILES, `registration.json`, or `reregistration-<digits>.json`. Nothing else.
pub fn is_public_name(name: &str) -> bool {
    if PUBLIC_FILES.contains(&name) || name == "registration.json" {
        return true;
    }
    name.strip_prefix("reregistration-")
        .and_then(|r| r.strip_suffix(".json"))
        .map(|d| !d.is_empty() && d.len() <= 6 && d.bytes().all(|b| b.is_ascii_digit()))
        .unwrap_or(false)
}

/// The index: every public file present for this boot with its size and SHA-256.
pub fn index(dir: &Path, boot_id: &str, operator: &str) -> Value {
    let base = dir.join(boot_id);
    let mut names: Vec<String> = std::fs::read_dir(&base)
        .map(|rd| rd.filter_map(|e| e.ok()).filter_map(|e| e.file_name().into_string().ok()).filter(|n| is_public_name(n)).collect())
        .unwrap_or_default();
    names.sort_by_key(|n| (PUBLIC_FILES.iter().position(|p| p == n).unwrap_or(PUBLIC_FILES.len()), n.clone()));
    let files: Vec<Value> = names
        .iter()
        .filter_map(|n| std::fs::read(base.join(n)).ok().map(|b| json!({"name": n, "bytes": b.len(), "sha256": hex::encode(Sha256::digest(&b)), "path": format!("/evidence/{n}")})))
        .collect();
    json!({"boot": boot_id, "operator": operator, "files": files,
        "note": "measurement evidence of this boot; transcripts are private and are not served"})
}

/// The response for one request line: (status, content type, body).
pub fn route(dir: &Path, boot_id: &str, operator: &str, request: &str) -> (u16, &'static str, Vec<u8>) {
    let mut parts = request.split_whitespace();
    let (method, target) = (parts.next().unwrap_or(""), parts.next().unwrap_or(""));
    if method != "GET" {
        return (405, "text/plain", b"GET only\n".to_vec());
    }
    let path = target.split('?').next().unwrap_or("");
    if path == "/" || path == "/evidence" || path == "/evidence/" {
        return (200, "application/json", serde_json::to_vec_pretty(&index(dir, boot_id, operator)).unwrap_or_default());
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

fn handle(mut c: TcpStream, dir: &Path, boot_id: &str, operator: &str) {
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
    let (code, ct, body) = route(dir, boot_id, operator, line);
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
pub fn spawn(addr: &str, dir: PathBuf, boot_id: String, operator: String) -> std::io::Result<std::net::SocketAddr> {
    let l = TcpListener::bind(addr)?;
    let local = l.local_addr()?;
    let live = Arc::new(AtomicUsize::new(0));
    std::thread::Builder::new().name("evidence-http".into()).spawn(move || {
        for c in l.incoming().flatten() {
            if live.load(Ordering::SeqCst) >= MAX_CONN {
                continue; // dropped: closes the connection
            }
            live.fetch_add(1, Ordering::SeqCst);
            let (d, b, o, lv) = (dir.clone(), boot_id.clone(), operator.clone(), live.clone());
            let r = std::thread::Builder::new().spawn(move || {
                handle(c, &d, &b, &o);
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

    fn dir() -> PathBuf {
        let d = std::env::temp_dir().join(format!("agent-serve-test-{}", std::process::id()));
        let b = d.join("boot-1");
        std::fs::create_dir_all(b.join("x")).unwrap();
        std::fs::create_dir_all(d.join("transcripts")).unwrap();
        std::fs::write(b.join("app-compose.json"), b"{}").unwrap();
        std::fs::write(b.join("tdx_quote.hex"), b"abcd").unwrap();
        std::fs::write(b.join("reregistration-2.json"), b"{}").unwrap();
        std::fs::write(d.join("transcripts").join("boot-1-0001.json"), b"{\"private\":1}").unwrap();
        d
    }

    #[test]
    fn serves_measurement_evidence_and_never_transcripts() {
        let d = dir();
        let (c, _, b) = route(&d, "boot-1", "cmdk", "GET /evidence/app-compose.json HTTP/1.1");
        assert_eq!((c, b.as_slice()), (200, b"{}".as_slice()));
        assert_eq!(route(&d, "boot-1", "cmdk", "GET /evidence/tdx_quote.hex HTTP/1.1").0, 200);
        assert_eq!(route(&d, "boot-1", "cmdk", "GET /evidence/reregistration-2.json HTTP/1.1").0, 200);
        for bad in ["GET /evidence/../transcripts/boot-1-0001.json HTTP/1.1", "GET /evidence/transcripts/boot-1-0001.json HTTP/1.1",
                    "GET /transcripts/boot-1-0001.json HTTP/1.1", "GET /evidence/vm_config.json HTTP/1.1", "GET /evidence/x HTTP/1.1",
                    "GET /evidence/%2e%2e%2ftranscripts HTTP/1.1", "GET /evidence/reregistration-.json HTTP/1.1"] {
            assert_eq!(route(&d, "boot-1", "cmdk", bad).0, 404, "{bad}");
        }
        assert_eq!(route(&d, "boot-1", "cmdk", "POST /evidence/app-compose.json HTTP/1.1").0, 405);
        let (c, _, b) = route(&d, "boot-1", "cmdk", "GET / HTTP/1.1");
        assert_eq!(c, 200);
        let v: Value = serde_json::from_slice(&b).unwrap();
        let names: Vec<&str> = v["files"].as_array().unwrap().iter().map(|f| f["name"].as_str().unwrap()).collect();
        assert_eq!(names, vec!["app-compose.json", "tdx_quote.hex", "reregistration-2.json"]);
        assert_eq!(v["operator"], "cmdk");
        assert!(!String::from_utf8_lossy(&b).contains("boot-1-0001"));
        let _ = std::fs::remove_dir_all(&d);
    }
}
