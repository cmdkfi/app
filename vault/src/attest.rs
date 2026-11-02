//! `register_agent`'s checks (§4.4): measurement F's dcap-qvl verification, plus the checks F lacked:
//! report_data equals the calling implicit account's key, debug bit off, TCB status UpToDate,
//! MRTD/RTMR0-2 and the RTMR3 event log (compose hash, OS image hash) equal the owner-approved measurement.
//! The collateral's validity windows (TCB info, QE identity, CRLs, certificates) are checked by dcap-qvl
//! against `now`, which is always the block time here.
use dcap_qvl::quote::Quote;
use dcap_qvl::verify::QuoteVerifier;
use dcap_qvl::QuoteCollateralV3;
use near_sdk::near;
use sha2::{Digest, Sha384};

/// The enclave code the owner accepts (`approve_measurement`).
#[near(serializers = [borsh, json])]
#[derive(Clone, PartialEq, Debug)]
pub struct Measurement {
    pub mrtd: String,
    pub rtmr0: String,
    pub rtmr1: String,
    pub rtmr2: String,
    pub compose_hash: String,
    pub os_image_hash: String,
}

impl Measurement {
    pub fn validate(&self) -> Result<(), String> {
        for (name, v, n) in [
            ("mrtd", &self.mrtd, 48),
            ("rtmr0", &self.rtmr0, 48),
            ("rtmr1", &self.rtmr1, 48),
            ("rtmr2", &self.rtmr2, 48),
            ("compose_hash", &self.compose_hash, 32),
            ("os_image_hash", &self.os_image_hash, 32),
        ] {
            match hex::decode(v) {
                Ok(b) if b.len() == n && v.chars().all(|c| !c.is_ascii_uppercase()) => {}
                _ => return Err(format!("{name}: expected {n} bytes of lowercase hex")),
            }
        }
        Ok(())
    }
}

/// One entry of dstack's event log (`event_log.json` from the guest agent's `Info`). `digest` is ignored:
/// the vault recomputes every digest from the event and its payload.
#[near(serializers = [json])]
#[derive(Clone, Debug)]
pub struct EventLogEntry {
    pub imr: u32,
    pub event_type: u32,
    #[serde(default)]
    pub digest: String,
    pub event: String,
    pub event_payload: String,
}

/// dstack cc-eventlog: digest = sha384(event_type LE u32 || ":" || event || ":" || payload);
/// rtmr3 = fold sha384(rtmr3 || digest) from 48 zero bytes (measurement H).
/// Returns rtmr3 and the payloads of the `compose-hash` and `os-image-hash` events.
pub fn replay_rtmr3(events: &[EventLogEntry]) -> Result<([u8; 48], String, String), String> {
    let mut mr = [0u8; 48];
    let (mut compose, mut os_image) = (None, None);
    for e in events.iter().filter(|e| e.imr == 3) {
        let payload = hex::decode(&e.event_payload).map_err(|_| format!("event {}: payload not hex", e.event))?;
        let mut h = Sha384::new();
        h.update(e.event_type.to_le_bytes());
        h.update(b":");
        h.update(e.event.as_bytes());
        h.update(b":");
        h.update(&payload);
        let digest = h.finalize();
        let mut h = Sha384::new();
        h.update(mr);
        h.update(digest);
        mr.copy_from_slice(&h.finalize());
        let slot = match e.event.as_str() {
            "compose-hash" => &mut compose,
            "os-image-hash" => &mut os_image,
            _ => continue,
        };
        if slot.is_some() {
            return Err(format!("duplicate {} event", e.event));
        }
        *slot = Some(hex::encode(payload));
    }
    Ok((mr, compose.ok_or("no compose-hash event")?, os_image.ok_or("no os-image-hash event")?))
}

/// The cheap check, before the 105 Tgas verification: report_data = key || 32 zero bytes,
/// where key is the ed25519 key of the calling implicit account.
pub fn check_report_data(report_data: &[u8; 64], caller_key: &[u8; 32]) -> Result<(), String> {
    if &report_data[..32] != caller_key {
        return Err("report_data does not match the caller's key".into());
    }
    if report_data[32..].iter().any(|b| *b != 0) {
        return Err("report_data tail is not zero".into());
    }
    Ok(())
}

pub fn parse_report_data(quote: &[u8]) -> Result<[u8; 64], String> {
    let q = Quote::parse(quote).map_err(|e| format!("bad quote: {e:?}"))?;
    let td = q.report.as_td10().ok_or("not a TDX quote")?;
    Ok(td.report_data)
}

/// Full verification against the approved measurement at `now` (block time, seconds).
pub fn verify(
    quote: &[u8],
    collateral: &QuoteCollateralV3,
    events: &[EventLogEntry],
    approved: &Measurement,
    caller_key: &[u8; 32],
    now: u64,
) -> Result<(), String> {
    let report = QuoteVerifier::new_prod()
        .with_config::<dcap_qvl::configs::RustCryptoConfig>()
        .verify(quote, collateral, now)
        .map_err(|e| format!("quote verification failed: {e:?}"))?;
    if report.status != "UpToDate" {
        return Err(format!("TCB status {} is not UpToDate", report.status));
    }
    let td = report.report.as_td10().ok_or("not a TDX quote")?;
    check_report_data(&td.report_data, caller_key)?;
    if td.td_attributes[0] & 1 != 0 {
        return Err("debug bit set".into());
    }
    for (name, got, want) in [
        ("mrtd", &td.mr_td, &approved.mrtd),
        ("rtmr0", &td.rt_mr0, &approved.rtmr0),
        ("rtmr1", &td.rt_mr1, &approved.rtmr1),
        ("rtmr2", &td.rt_mr2, &approved.rtmr2),
    ] {
        if &hex::encode(got) != want {
            return Err(format!("{name} differs from the approved measurement"));
        }
    }
    let (rtmr3, compose, os_image) = replay_rtmr3(events)?;
    if rtmr3 != td.rt_mr3 {
        return Err("event log does not replay to the quote's RTMR3".into());
    }
    if compose != approved.compose_hash {
        return Err("compose hash differs from the approved measurement".into());
    }
    if os_image != approved.os_image_hash {
        return Err("OS image hash differs from the approved measurement".into());
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    //! Native checks on measurement E's quote, event log and F's collateral (copies in tests/fixtures/).
    use super::*;
    const E: &str = "tests/fixtures";
    const F: &str = "tests/fixtures";

    fn inputs() -> (Vec<u8>, QuoteCollateralV3, Vec<EventLogEntry>) {
        let q = hex::decode(std::fs::read_to_string(format!("{E}/tdx_quote_prime-e-test.hex")).unwrap().trim()).unwrap();
        let c = near_sdk::serde_json::from_str(&std::fs::read_to_string(format!("{F}/collateral_phala.json")).unwrap()).unwrap();
        let ev = near_sdk::serde_json::from_str(&std::fs::read_to_string(format!("{E}/event_log.json")).unwrap()).unwrap();
        (q, c, ev)
    }

    pub fn e_measurement() -> Measurement {
        Measurement {
            mrtd: "7fe60787222bd394cb516abca2435f22f035ab2cc9c0a4b4b3b148e46297f3d931a237b72f359052c6e657d8c1409173".into(),
            rtmr0: "68102e7b524af310f7b7d426ce75481e36c40f5d513a9009c046e9d37e31551f0134d954b496a3357fd61d03f07ffe96".into(),
            rtmr1: "4ae609807c765916a31d724786ed74bcb97e168a9d937b04936aeef2015c907eba5ac352c6bcd749c246fd62ffb659ec".into(),
            rtmr2: "80e945df96ec417b68d574dba18d199a7448d66c3d0e56f073167a9513d2381601f5d614853646d2c49c5d8d58a7daf5".into(),
            compose_hash: "78eee1165c9da208d460157cb625519e6422c1a2b833ad678b10556355b769e2".into(),
            os_image_hash: "e866e987221db357129ebc3f066b6706ef955889ea95e5510acbdf3d3055186b".into(),
        }
    }

    const OCT_27: u64 = 1_793_059_200; // 2026-10-27 00:00:00 UTC
    const NOV_26: u64 = 1_795_651_200; // 2026-11-26 00:00:00 UTC, after the collateral's nextUpdate (25 Nov)

    #[test]
    fn e_quote_passes_with_its_own_key() {
        let (q, c, ev) = inputs();
        let rd = parse_report_data(&q).unwrap();
        let key: [u8; 32] = rd[..32].try_into().unwrap();
        verify(&q, &c, &ev, &e_measurement(), &key, OCT_27).unwrap();
    }

    #[test]
    fn wrong_key_rejected() {
        let (q, c, ev) = inputs();
        assert!(verify(&q, &c, &ev, &e_measurement(), &[7u8; 32], OCT_27).unwrap_err().contains("report_data"));
    }

    #[test]
    fn expired_collateral_rejected() {
        let (q, c, ev) = inputs();
        let key: [u8; 32] = parse_report_data(&q).unwrap()[..32].try_into().unwrap();
        let err = verify(&q, &c, &ev, &e_measurement(), &key, NOV_26).unwrap_err();
        assert!(err.contains("expired"), "{err}");
    }

    #[test]
    fn tampered_event_log_rejected() {
        let (q, c, mut ev) = inputs();
        let key: [u8; 32] = parse_report_data(&q).unwrap()[..32].try_into().unwrap();
        let i = ev.iter().position(|e| e.event == "compose-hash").unwrap();
        ev[i].event_payload = "00".repeat(32);
        assert!(verify(&q, &c, &ev, &e_measurement(), &key, OCT_27).unwrap_err().contains("RTMR3"));
    }

    #[test]
    fn other_measurement_rejected() {
        let (q, c, ev) = inputs();
        let key: [u8; 32] = parse_report_data(&q).unwrap()[..32].try_into().unwrap();
        let mut m = e_measurement();
        m.compose_hash = "11".repeat(32);
        assert!(verify(&q, &c, &ev, &m, &key, OCT_27).unwrap_err().contains("compose hash"));
        let mut m = e_measurement();
        m.rtmr0 = "22".repeat(48);
        assert!(verify(&q, &c, &ev, &m, &key, OCT_27).unwrap_err().contains("rtmr0"));
    }
}
