//! The vaults this agent watches (decided 3 Nov; design §4.4: one shared enclave serves every vault that approved our
//! compose hash). Pure logic only; the reads, registrations and tick loops are in `main.rs`.
//!
//! Source: `--factory <account>` (the factory's vaults `v1..vN.<factory>`, N = `get_config().count`) or, for dry and
//! twin runs and tests, one or more `--vault <account>`. Each vault has a slot: its factory index, or its position in
//! the `--vault` list. Discovery is cached: an index whose vault was read once is known and never resolved again; an
//! index the factory counts but whose account did not answer yet (its `create_vault` is still in flight, or it
//! failed and never will) is retried on every refresh. Membership is re-read on every refresh: a vault is watched
//! while its `approved` equals our measurement (a `--no-tee` run has none and watches every vault, read-only).
use serde_json::{json, Value};
use std::collections::{BTreeMap, BTreeSet};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex};

/// Where the vaults come from.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Source {
    Factory(String),
    List(Vec<String>),
}

/// The account id of factory vault number `n` (1-based), as the factory's `vault_id(n)` builds it.
pub fn factory_vault_id(n: u64, factory: &str) -> String {
    format!("v{n}.{factory}")
}

/// The short name of a slot in logs and file names: `v<n>` (a factory vault: the account's first label) or
/// `vault<n>` (the n-th `--vault`).
pub fn tag(source: &Source, slot: u64) -> String {
    match source {
        Source::Factory(_) => format!("v{slot}"),
        Source::List(_) => format!("vault{slot}"),
    }
}

/// A tag as `tag` makes it: `v` or `vault` and 1-6 digits. Served file names are built from it, so nothing else.
pub fn valid_tag(t: &str) -> bool {
    let d = t.strip_prefix("vault").or_else(|| t.strip_prefix('v')).unwrap_or("");
    !d.is_empty() && d.len() <= 6 && d.bytes().all(|b| b.is_ascii_digit())
}

/// The discovery cache: which slots are known (read once, with their account id) and which the factory counted but
/// did not answer yet.
#[derive(Debug, Default, Clone, PartialEq, Eq)]
pub struct Scan {
    pub known: BTreeMap<u64, String>,
    pub missing: BTreeSet<u64>,
    /// every index up to here was planned once (known or missing)
    pub scanned: u64,
}

impl Scan {
    /// The slots this refresh must resolve: the indices the factory added since the last scan, and the missing ones.
    /// Known slots are never in it.
    pub fn plan(&self, count: u64) -> Vec<u64> {
        self.missing.iter().copied().chain(self.scanned + 1..=count).collect()
    }

    /// The slots to resolve with their account ids, for either source. A `--vault` list is planned once, whole.
    pub fn plan_ids(&self, source: &Source, count: u64) -> Vec<(u64, String)> {
        match source {
            Source::Factory(f) => self.plan(count).into_iter().map(|n| (n, factory_vault_id(n, f))).collect(),
            Source::List(l) => {
                let all = (1..=l.len() as u64).zip(l.iter().cloned());
                all.filter(|(n, _)| !self.known.contains_key(n) && (*n > self.scanned || self.missing.contains(n))).collect()
            }
        }
    }

    /// Records what one planned read found: `true` = the vault answered `get_state`.
    pub fn resolve(&mut self, slot: u64, id: &str, answered: bool) {
        if answered {
            self.missing.remove(&slot);
            self.known.insert(slot, id.to_string());
        } else {
            self.missing.insert(slot);
        }
        self.scanned = self.scanned.max(slot);
    }
}

/// Whether this vault approves `measurement` (`None` = a `--no-tee` run: no measurement, every vault is watched).
pub fn approves(state: &Value, measurement: Option<&Value>) -> bool {
    match measurement {
        Some(m) => !m.is_null() && state["approved"] == *m,
        None => true,
    }
}

/// The watched set after a refresh. `fresh` holds the states read this refresh; a slot whose read failed keeps its
/// previous membership (a read error never drops a vault). Returns (watched, joined, left).
pub fn membership(prev: &BTreeSet<u64>, fresh: &BTreeMap<u64, Value>, measurement: Option<&Value>) -> (BTreeSet<u64>, Vec<u64>, Vec<u64>) {
    let mut now = prev.clone();
    for (slot, st) in fresh {
        if approves(st, measurement) {
            now.insert(*slot);
        } else {
            now.remove(slot);
        }
    }
    let joined = now.difference(prev).copied().collect();
    let left = prev.difference(&now).copied().collect();
    (now, joined, left)
}

/// Where a watched vault's registration stands for this boot.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Phase {
    /// joined; `register_agent` not yet accepted. `attempts` counts calls made (a wait for funds is not one)
    Pending { attempts: u32 },
    /// the vault named our key after one of our registrations
    Registered,
    /// REGISTER_ATTEMPTS calls failed in this boot; retried only if the vault leaves and joins again
    GaveUp,
    /// a `--no-tee` run: no quote, no registration; ticks read-only
    NoTee,
}

impl Phase {
    pub fn label(&self) -> &'static str {
        match self {
            Phase::Pending { .. } => "registering",
            Phase::Registered => "registered",
            Phase::GaveUp => "registration failed (gave up for this boot)",
            Phase::NoTee => "not registered (no TEE)",
        }
    }
    /// Whether the vault's tick loop runs: never unregistered in the TEE.
    pub fn ticks(&self) -> bool {
        matches!(self, Phase::Registered | Phase::NoTee)
    }
}

/// What the refresh does for one watched vault about its registration.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum RegAction {
    Nothing,
    /// the vault already names our key (an earlier call of ours succeeded although its outcome was not seen)
    MarkRegistered,
    Register,
    GiveUp,
}

/// Registration is wanted only for a vault that joined in this boot and does not name our key yet. Once registered,
/// another key on the vault is left alone (re-registering would displace it; re-attestation is `reregistration_due`).
pub fn reg_action(phase: Phase, state: &Value, our_pk: &str, max_attempts: u32) -> RegAction {
    match phase {
        Phase::Pending { attempts } => {
            if state["agent"]["public_key"].as_str() == Some(our_pk) {
                RegAction::MarkRegistered
            } else if attempts >= max_attempts {
                RegAction::GiveUp
            } else {
                RegAction::Register
            }
        }
        _ => RegAction::Nothing,
    }
}

/// The boot's exit rule (as with one vault before 3 Nov): exit non-zero, so the restart policy gives a fresh key, only
/// when some vault gave up and none is registered or still registering.
pub fn should_exit(phases: &[Phase]) -> bool {
    phases.iter().any(|p| *p == Phase::GaveUp) && !phases.iter().any(|p| matches!(p, Phase::Registered | Phase::Pending { .. }))
}

/// The feed items a vault has not taken yet. Feed items are appended to one shared list; each vault keeps its own
/// cursor, so every vault sees every new item once and no vault takes another's.
pub fn take_new_items(items: &[String], cursor: &mut usize) -> Vec<String> {
    let from = (*cursor).min(items.len());
    *cursor = items.len();
    items[from..].to_vec()
}

/// What a vault's tick reads on Base: its derived address, its executor and its market ids, from its `get_state`.
pub fn watch_target(vault: &Value) -> Result<(String, String, Vec<String>), String> {
    let base_address = vault["base_address"].as_str().unwrap_or("").to_string();
    let markets = vault["markets"].as_array().cloned().unwrap_or_default();
    let market_ids: Vec<String> = markets.iter().filter_map(|m| m["id"].as_str().map(|s| s.to_string())).collect();
    let executor = markets.first().and_then(|m| m["executor"].as_str()).unwrap_or("").to_string();
    if market_ids.is_empty() || base_address.is_empty() || executor.is_empty() {
        return Err("vault state lacks markets, base address or executor".into());
    }
    Ok((base_address, executor, market_ids))
}

// ------------------------------------------------------------------ the public listing (evidence endpoint)

/// Per-vault progress the tick loop updates and the evidence endpoint reads.
#[derive(Default, Debug)]
pub struct Progress {
    pub ticks: AtomicU64,
    pub last_tick_unix: AtomicU64,
}

impl Progress {
    pub fn tick(&self, n: u64, now: u64) {
        self.ticks.store(n, Ordering::SeqCst);
        self.last_tick_unix.store(now, Ordering::SeqCst);
    }
}

/// One row of the listing: what the refresh last wrote about a vault, plus its live progress.
#[derive(Clone, Debug)]
pub struct Row {
    pub info: Value,
    pub progress: Arc<Progress>,
}

/// The listing shared with the evidence endpoint, keyed by slot.
pub type Board = Arc<Mutex<BTreeMap<u64, Row>>>;

pub fn board_json(board: &Board) -> Value {
    let g = board.lock().unwrap_or_else(|e| e.into_inner());
    let rows: Vec<Value> = g
        .values()
        .map(|r| {
            let mut v = r.info.clone();
            let last = r.progress.last_tick_unix.load(Ordering::SeqCst);
            v["ticks"] = json!(r.progress.ticks.load(Ordering::SeqCst));
            v["last_tick_unix"] = if last == 0 { Value::Null } else { json!(last) };
            v
        })
        .collect();
    json!(rows)
}

#[cfg(test)]
mod tests {
    use super::*;

    const M: &str = "{\"compose_hash\":\"aa\",\"mrtd\":\"bb\"}";

    fn st(approved: Option<&str>, agent: Option<&str>) -> Value {
        json!({"approved": approved.map(|a| serde_json::from_str::<Value>(a).unwrap()), "agent": agent.map(|k| json!({"public_key": k, "expires_at": 1}))})
    }

    #[test]
    fn the_scan_reads_only_new_and_missing_indices() {
        let src = Source::Factory("f.testnet".into());
        let mut s = Scan::default();
        let p = s.plan_ids(&src, 3);
        assert_eq!(p, vec![(1, "v1.f.testnet".into()), (2, "v2.f.testnet".into()), (3, "v3.f.testnet".into())]);
        s.resolve(1, "v1.f.testnet", true);
        s.resolve(2, "v2.f.testnet", false); // create_vault still in flight
        s.resolve(3, "v3.f.testnet", true);
        // same count: only the missing index is read again; known ones never
        assert_eq!(s.plan_ids(&src, 3), vec![(2, "v2.f.testnet".into())]);
        // the factory created two more
        assert_eq!(s.plan_ids(&src, 5).iter().map(|x| x.0).collect::<Vec<_>>(), vec![2, 4, 5]);
        s.resolve(2, "v2.f.testnet", true);
        s.resolve(4, "v4.f.testnet", true);
        s.resolve(5, "v5.f.testnet", true);
        assert!(s.plan_ids(&src, 5).is_empty());
        assert_eq!(s.known.len(), 5);
        assert!(s.missing.is_empty());
        // a --vault list is planned once, whole
        let l = Source::List(vec!["a.testnet".into(), "b.testnet".into()]);
        let mut s = Scan::default();
        assert_eq!(s.plan_ids(&l, 0), vec![(1, "a.testnet".into()), (2, "b.testnet".into())]);
        s.resolve(1, "a.testnet", true);
        s.resolve(2, "b.testnet", false);
        assert_eq!(s.plan_ids(&l, 0), vec![(2, "b.testnet".into())]);
        assert_eq!((tag(&src, 4), tag(&l, 2)), ("v4".into(), "vault2".into()));
        for good in ["v1", "v123456", "vault2"] {
            assert!(valid_tag(good), "{good}");
        }
        for bad in ["", "v", "vault", "v1234567", "v1.f", "x1", "v-1", "../v1"] {
            assert!(!valid_tag(bad), "{bad}");
        }
    }

    #[test]
    fn vaults_join_on_approval_leave_on_revoke_and_a_mismatch_is_ignored() {
        let m: Value = serde_json::from_str(M).unwrap();
        let other = "{\"compose_hash\":\"cc\",\"mrtd\":\"bb\"}";
        let mut fresh = BTreeMap::new();
        fresh.insert(1, st(Some(M), None));
        fresh.insert(2, st(Some(other), None)); // approves another measurement: ignored
        fresh.insert(3, st(None, None)); // approves nothing
        let (w, joined, left) = membership(&BTreeSet::new(), &fresh, Some(&m));
        assert_eq!((w.iter().copied().collect::<Vec<_>>(), joined, left), (vec![1], vec![1], vec![]));
        // vault 3 approves later and joins; vault 1 revokes (revoke_agent clears `approved`) and leaves
        let mut fresh = BTreeMap::new();
        fresh.insert(1, st(None, None));
        fresh.insert(3, st(Some(M), None));
        let (w2, joined, left) = membership(&w, &fresh, Some(&m));
        assert_eq!((w2.iter().copied().collect::<Vec<_>>(), joined, left), (vec![3], vec![3], vec![1]));
        // a failed read keeps the previous membership
        let (w3, joined, left) = membership(&w2, &BTreeMap::new(), Some(&m));
        assert_eq!((w3, joined, left), (w2.clone(), vec![], vec![]));
        // a --no-tee run has no measurement: every vault, approved or not
        let mut fresh = BTreeMap::new();
        fresh.insert(5, st(None, None));
        assert_eq!(membership(&BTreeSet::new(), &fresh, None).1, vec![5]);
        // a null measurement never matches a vault that approves nothing
        assert!(!approves(&st(None, None), Some(&Value::Null)));
    }

    #[test]
    fn registration_is_per_vault_and_never_displaces_another_key_once_registered() {
        let ours = "ed25519:ours";
        let v_none = st(Some(M), None);
        let v_ours = st(Some(M), Some(ours));
        let v_other = st(Some(M), Some("ed25519:other"));
        assert_eq!(reg_action(Phase::Pending { attempts: 0 }, &v_none, ours, 4), RegAction::Register);
        assert_eq!(reg_action(Phase::Pending { attempts: 0 }, &v_other, ours, 4), RegAction::Register, "a joining vault: the previous boot's key is replaced");
        assert_eq!(reg_action(Phase::Pending { attempts: 2 }, &v_ours, ours, 4), RegAction::MarkRegistered);
        assert_eq!(reg_action(Phase::Pending { attempts: 4 }, &v_none, ours, 4), RegAction::GiveUp);
        assert_eq!(reg_action(Phase::Registered, &v_other, ours, 4), RegAction::Nothing);
        assert_eq!(reg_action(Phase::GaveUp, &v_none, ours, 4), RegAction::Nothing);
        assert_eq!(reg_action(Phase::NoTee, &v_none, ours, 4), RegAction::Nothing);
        assert!(Phase::Registered.ticks() && Phase::NoTee.ticks());
        assert!(!Phase::Pending { attempts: 1 }.ticks() && !Phase::GaveUp.ticks());
        // exit only when nothing is registered or registering and something gave up
        assert!(should_exit(&[Phase::GaveUp]));
        assert!(!should_exit(&[Phase::GaveUp, Phase::Registered]));
        assert!(!should_exit(&[Phase::GaveUp, Phase::Pending { attempts: 1 }]));
        assert!(!should_exit(&[]), "no vault approves us yet: keep scanning");
    }

    #[test]
    fn per_vault_keying_feed_cursors_and_targets() {
        // every vault takes every new feed item once, independently of the others
        let mut items: Vec<String> = vec!["a".into(), "b".into()];
        let (mut c1, mut c2) = (0usize, 0usize);
        assert_eq!(take_new_items(&items, &mut c1), vec!["a", "b"]);
        items.push("c".into());
        assert_eq!(take_new_items(&items, &mut c1), vec!["c"]);
        assert_eq!(take_new_items(&items, &mut c2), vec!["a", "b", "c"]);
        assert!(take_new_items(&items, &mut c1).is_empty() && take_new_items(&items, &mut c2).is_empty());
        // a vault that joins later starts at the end of the list
        let mut c3 = items.len();
        assert!(take_new_items(&items, &mut c3).is_empty());
        // each vault reads its own Base position from its own state
        let v = |addr: &str, exec: &str| json!({"base_address": addr, "markets": [{"id": "0x01", "executor": exec}, {"id": "0x02", "executor": exec}]});
        assert_eq!(watch_target(&v("0xa1", "0xe1")).unwrap(), ("0xa1".into(), "0xe1".into(), vec!["0x01".into(), "0x02".into()]));
        assert_eq!(watch_target(&v("0xa2", "0xe2")).unwrap().0, "0xa2");
        assert!(watch_target(&json!({"base_address": "0xa1", "markets": []})).is_err());
        // the listing is keyed by slot, and progress is per vault
        let board: Board = Default::default();
        let (p1, p2) = (Arc::new(Progress::default()), Arc::new(Progress::default()));
        board.lock().unwrap().insert(2, Row { info: json!({"vault": "v2.f"}), progress: p2.clone() });
        board.lock().unwrap().insert(1, Row { info: json!({"vault": "v1.f"}), progress: p1.clone() });
        p1.tick(7, 1_800_000_000);
        let j = board_json(&board);
        assert_eq!(j[0]["vault"], "v1.f");
        assert_eq!((j[0]["ticks"].as_u64(), j[0]["last_tick_unix"].as_u64()), (Some(7), Some(1_800_000_000)));
        assert_eq!((j[1]["ticks"].as_u64(), j[1]["last_tick_unix"].is_null()), (Some(0), true));
    }
}
