//! Owner messages: ERC-191 personal-sign over a JSON message, recovered with NEAR's `ecrecover`
//! (measurement G's check, unchanged), then the message's own fields checked by the vault.
use near_sdk::env;
use near_sdk::serde::Deserialize;
use near_sdk::serde_json::{self, Value};

use crate::Addr;

pub const PROTOCOL: &str = "prime-vault/1";
pub const NETWORK: &str = "testnet";

/// What the owner's wallet signs (the exact bytes of the JSON text are what is signed).
/// Example: {"protocol":"prime-vault/1","network":"testnet","vault":"p2-vault.x.testnet",
///           "method":"open","args":{...},"nonce":3,"expiry":1793100000}
#[derive(Deserialize)]
#[serde(crate = "near_sdk::serde", deny_unknown_fields)]
pub struct OwnerMessage {
    pub protocol: String,
    pub network: String,
    pub vault: String,
    pub method: String,
    pub args: Value,
    pub nonce: u64,
    pub expiry: u64,
}

fn decode_hex(s: &str) -> Option<Vec<u8>> {
    hex::decode(s.strip_prefix("0x").unwrap_or(s)).ok()
}

/// G's `verify_owner`, returning the recovered address instead of comparing it.
/// `signature_hex`: 65 bytes r‖s‖v (v = 27/28 or 0/1). High s is rejected by the host (malleability flag).
pub fn recover(message: &str, signature_hex: &str) -> Option<Addr> {
    let sig = decode_hex(signature_hex).filter(|s| s.len() == 65)?;
    let v = match sig[64] {
        27 | 28 => sig[64] - 27,
        0 | 1 => sig[64],
        _ => return None,
    };
    let prefixed = format!("\x19Ethereum Signed Message:\n{}{}", message.len(), message);
    let hash = env::keccak256_array(prefixed.as_bytes());
    let pubkey = env::ecrecover(&hash, &sig[..64], v, true)?;
    let h = env::keccak256_array(&pubkey);
    let mut a = [0u8; 20];
    a.copy_from_slice(&h[12..]);
    Some(a)
}

/// Recover the signer, require the owner, and check protocol, network, vault id, nonce and expiry.
/// Returns the parsed message; the caller stores the new nonce.
pub fn verify(message: &str, signature_hex: &str, owner: &Addr, last_nonce: u64) -> OwnerMessage {
    let signer = recover(message, signature_hex).unwrap_or_else(|| env::panic_str("bad owner signature"));
    if &signer != owner {
        env::panic_str("signature is not the owner's");
    }
    let m: OwnerMessage =
        serde_json::from_str(message).unwrap_or_else(|e| env::panic_str(&format!("bad owner message: {e}")));
    if m.protocol != PROTOCOL {
        env::panic_str("wrong protocol version");
    }
    if m.network != NETWORK {
        env::panic_str("wrong network");
    }
    if m.vault != env::current_account_id().as_str() {
        env::panic_str("message is for another vault");
    }
    if m.nonce <= last_nonce {
        env::panic_str("nonce not above the last one");
    }
    if crate::now_secs() > m.expiry {
        env::panic_str("message expired");
    }
    m
}
