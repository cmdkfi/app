//! Owner messages: ERC-191 personal-sign over a JSON message, recovered with NEAR's `ecrecover`
//! (measurement G's check, unchanged), then the message's own fields checked by the vault.
use near_sdk::env;
use near_sdk::serde::Deserialize;
use near_sdk::serde_json::{self, Value};

use crate::Addr;

pub const PROTOCOL: &str = "cmdk-vault/1";
pub const NETWORK: &str = "testnet";
/// Upper bound on an owner message's `expiry`: at most this long after the vault's block time (§4.1, owner
/// messages; decided 3 Nov, V6). A message signed to stay valid for days is refused, so a signed but unsubmitted
/// message cannot be held back and used much later.
pub const MAX_EXPIRY_AHEAD_SECS: u64 = 3600;

/// What the owner's wallet signs (the exact bytes of the JSON text are what is signed).
/// Example: {"protocol":"cmdk-vault/1","network":"testnet","vault":"vault.cmdk.testnet",
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
    let now = crate::now_secs();
    if now > m.expiry {
        env::panic_str("message expired");
    }
    if m.expiry > now + MAX_EXPIRY_AHEAD_SECS {
        env::panic_str("message expiry too far ahead");
    }
    m
}

#[cfg(test)]
mod tests {
    use super::*;
    use near_crypto::{KeyType, SecretKey, Signature};
    use near_sdk::test_utils::VMContextBuilder;
    use near_sdk::testing_env;

    const VAULT: &str = "vault.test.near";
    const NOW: u64 = 1_793_000_000;

    /// A throwaway secp256k1 key and its EVM address (keccak of the 64-byte public key, last 20 bytes).
    fn wallet() -> (SecretKey, Addr) {
        let sk = SecretKey::from_random(KeyType::SECP256K1);
        let h = env::keccak256_array(sk.public_key().key_data());
        (sk, h[12..].try_into().unwrap())
    }

    /// ERC-191 personal-sign, r‖s‖v with v = 27/28 as a wallet returns it.
    fn sign(sk: &SecretKey, message: &str) -> String {
        let prefixed = format!("\x19Ethereum Signed Message:\n{}{}", message.len(), message);
        let mut sig: [u8; 65] = match sk.sign(&env::keccak256_array(prefixed.as_bytes())) {
            Signature::SECP256K1(s) => s.into(),
            _ => unreachable!(),
        };
        sig[64] += 27;
        format!("0x{}", hex::encode(sig))
    }

    fn message(expiry: u64) -> String {
        format!(
            r#"{{"protocol":"{PROTOCOL}","network":"{NETWORK}","vault":"{VAULT}","method":"cancel","args":{{}},"nonce":1,"expiry":{expiry}}}"#
        )
    }

    /// Verify a message with the given expiry at block time NOW.
    fn verify_at_now(expiry: u64) -> OwnerMessage {
        testing_env!(VMContextBuilder::new()
            .current_account_id(VAULT.parse().unwrap())
            .block_timestamp(NOW * 1_000_000_000)
            .build());
        let (sk, owner) = wallet();
        let m = message(expiry);
        verify(&m, &sign(&sk, &m), &owner, 0)
    }

    #[test]
    fn expiry_at_the_bound_is_accepted() {
        assert_eq!(verify_at_now(NOW + MAX_EXPIRY_AHEAD_SECS).expiry, NOW + 3600);
        assert_eq!(verify_at_now(NOW).expiry, NOW);
    }

    #[test]
    #[should_panic(expected = "message expiry too far ahead")]
    fn expiry_one_second_over_the_bound_is_refused() {
        verify_at_now(NOW + MAX_EXPIRY_AHEAD_SECS + 1);
    }

    #[test]
    #[should_panic(expected = "message expired")]
    fn expiry_one_second_past_is_refused() {
        verify_at_now(NOW - 1);
    }
}
