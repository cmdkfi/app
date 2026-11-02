//! Sandbox-only stand-in for `v1.signer-prod.testnet` (the cmdk vault's sandbox tests). Same `sign` arguments,
//! same 1 yoctoNEAR deposit rule, same response JSON; the key is sha256("prime-mock-signer:" + predecessor + ","
//! + path), so the tests can compute the derived address with `derived_address`. Answers synchronously (no
//! yield/resume). Records every payload it is asked to sign (`payloads`), so the tests can compare them with the
//! transactions the vault returns.
use k256::ecdsa::SigningKey;
use near_sdk::serde_json::{json, Value};
use near_sdk::{env, near, require, AccountId, NearToken};

#[near(contract_state)]
#[derive(Default)]
pub struct MockSigner {
    payloads: Vec<String>,
}

fn key(predecessor: &str, path: &str) -> SigningKey {
    let seed = env::sha256_array(format!("prime-mock-signer:{predecessor},{path}").as_bytes());
    SigningKey::from_bytes(&seed.into()).expect("key")
}

#[near]
impl MockSigner {
    #[payable]
    pub fn sign(&mut self, request: Value) -> Value {
        require!(env::attached_deposit() >= NearToken::from_yoctonear(1), "deposit");
        let path = request["path"].as_str().expect("path");
        let payload_hex = request["payload_v2"]["Ecdsa"].as_str().expect("Ecdsa payload");
        self.payloads.push(payload_hex.to_string());
        let payload = hex::decode(payload_hex).expect("hex");
        require!(payload.len() == 32, "payload length");
        let sk = key(env::predecessor_account_id().as_str(), path);
        // k256 signs with RFC 6979 nonces and returns low-s, as the MPC does.
        let (sig, recid) = sk.sign_prehash_recoverable(&payload).expect("sign");
        let (r, s) = sig.split_bytes();
        let mut big_r = vec![0x02 | (recid.is_y_odd() as u8)];
        big_r.extend_from_slice(&r);
        json!({"scheme": "Secp256k1", "big_r": {"affine_point": hex::encode(big_r)},
               "s": {"scalar": hex::encode(s)}, "recovery_id": recid.to_byte()})
    }

    /// Every payload `sign` was called with (hex, as received), in call order.
    pub fn payloads(&self) -> Vec<String> {
        self.payloads.clone()
    }

    /// The EVM address of the key `sign` uses for (predecessor, path).
    pub fn derived_address(&self, predecessor: AccountId, path: String) -> String {
        let pk = key(predecessor.as_str(), &path).verifying_key().to_encoded_point(false);
        let h = env::keccak256_array(&pk.as_bytes()[1..]);
        format!("0x{}", hex::encode(&h[12..]))
    }
}
