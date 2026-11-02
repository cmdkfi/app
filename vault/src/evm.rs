//! Base transactions: ABI calldata, EIP-1559 build with omni-transaction, decode-back, the allowlist,
//! and the signature assembly from a Chain Signatures response (measurement A's rule).
use near_sdk::json_types::U128;
use near_sdk::serde::Deserialize;
use near_sdk::{env, near};
use omni_transaction::evm::types::Signature;
use omni_transaction::evm::EVMTransaction;
use rlp::Rlp;

use crate::Addr;

pub const CHAIN_ID: u64 = 84532; // Base Sepolia
/// Bound on gas_limit. The largest executor call measured in P1 is `migrate` at 443,948 gas; the relayer
/// sends estimate × 1.3 (P1 finding), about 580k for it, so 1M leaves room without letting a caller burn much.
pub const GAS_LIMIT_MAX: u64 = 1_000_000;
/// Bound on max_fee_per_gas: 2 gwei. Base Sepolia ran at 0.006 gwei in P1.
pub const MAX_FEE_PER_GAS_MAX: u64 = 2_000_000_000;
/// Executor calls expire this long after the vault's block time (§4.1).
pub const DEADLINE_SECS: u64 = 600;

pub const SEL_APPROVE: [u8; 4] = [0x09, 0x5e, 0xa7, 0xb3]; // approve(address,uint256)
pub const SEL_TRANSFER: [u8; 4] = [0xa9, 0x05, 0x9c, 0xbb]; // transfer(address,uint256)
pub const SEL_SET_AUTHORIZATION: [u8; 4] = [0xee, 0xce, 0xa0, 0x00]; // setAuthorization(address,bool)
pub const SEL_SET_CORRIDOR: [u8; 4] = [0x25, 0x36, 0x16, 0xb9]; // setCorridor(bytes32,(uint64,uint64,uint64,uint64,bool,bool,bool),uint256)
pub const SEL_OPEN: [u8; 4] = [0x2d, 0xc1, 0x81, 0xa4]; // open(bytes32,uint256,uint256,uint256)
pub const SEL_REPAY: [u8; 4] = [0xe4, 0x85, 0x84, 0x94]; // repay(bytes32,uint256,uint256)
pub const SEL_CLOSE: [u8; 4] = [0x77, 0xe2, 0x83, 0xd0]; // close(bytes32,uint256)
pub const SEL_REDUCE: [u8; 4] = [0x64, 0x3d, 0x3b, 0x9e]; // reduce(bytes32,uint256,uint256)
pub const SEL_MIGRATE: [u8; 4] = [0x32, 0xbd, 0xcf, 0x59]; // migrate(bytes32,bytes32,uint256,uint256)
pub const SEL_EXIT: [u8; 4] = [0x5c, 0x7d, 0xa7, 0x0e]; // exit(bytes32,uint256)

pub type Word = [u8; 32];

pub fn w_addr(a: &Addr) -> Word {
    let mut w = [0u8; 32];
    w[12..].copy_from_slice(a);
    w
}

pub fn w_u128(v: u128) -> Word {
    let mut w = [0u8; 32];
    w[16..].copy_from_slice(&v.to_be_bytes());
    w
}

pub fn w_bool(b: bool) -> Word {
    w_u128(b as u128)
}

pub const W_MAX: Word = [0xff; 32];

pub fn calldata(sel: [u8; 4], words: &[Word]) -> Vec<u8> {
    let mut d = sel.to_vec();
    for w in words {
        d.extend_from_slice(w);
    }
    d
}

/// What the relayer (or the agent) supplies; the vault bounds it and keeps no EVM nonce of its own (§4.1).
#[near(serializers = [json])]
#[derive(Clone, Debug)]
pub struct TxParams {
    pub nonce: u64,
    pub gas_limit: u64,
    pub max_fee_per_gas: u64,
    pub max_priority_fee_per_gas: u64,
}

/// An unsigned Base transaction as passed to the signing callback.
#[near(serializers = [json])]
#[derive(Clone, Debug)]
pub struct BaseTx {
    pub nonce: u64,
    pub gas_limit: u64,
    pub max_fee_per_gas: u64,
    pub max_priority_fee_per_gas: u64,
    pub to: String,
    /// Wei; non-zero only for `sweep_eth`. A decimal string in JSON (`U128`).
    pub value: U128,
    pub data: String,
}

impl BaseTx {
    pub fn new(p: &TxParams, to: &Addr, value: u128, data: &[u8]) -> Self {
        BaseTx {
            nonce: p.nonce,
            gas_limit: p.gas_limit,
            max_fee_per_gas: p.max_fee_per_gas,
            max_priority_fee_per_gas: p.max_priority_fee_per_gas,
            to: hex::encode(to),
            value: U128(value),
            data: hex::encode(data),
        }
    }

    pub fn omni(&self) -> EVMTransaction {
        let mut to = [0u8; 20];
        to.copy_from_slice(&hex::decode(&self.to).expect("to"));
        EVMTransaction {
            chain_id: CHAIN_ID,
            nonce: self.nonce,
            to: Some(to),
            value: self.value.0,
            input: hex::decode(&self.data).expect("data"),
            gas_limit: self.gas_limit as u128,
            max_fee_per_gas: self.max_fee_per_gas as u128,
            max_priority_fee_per_gas: self.max_priority_fee_per_gas as u128,
            access_list: vec![],
        }
    }
}

/// The fields of an unsigned EIP-1559 transaction, decoded from its signing bytes.
#[derive(Debug)]
pub struct Decoded {
    pub chain_id: u64,
    pub nonce: u64,
    pub max_priority_fee_per_gas: u128,
    pub max_fee_per_gas: u128,
    pub gas_limit: u128,
    pub to: Addr,
    pub value: u128,
    pub data: Vec<u8>,
}

/// Decode `0x02 || rlp([chain_id, nonce, priority, max_fee, gas, to, value, data, access_list])`.
/// Rejects anything else: other types, a contract creation, a non-empty access list, trailing bytes.
pub fn decode_unsigned(bytes: &[u8]) -> Result<Decoded, String> {
    let e = |x: rlp::DecoderError| format!("rlp: {x:?}");
    if bytes.first() != Some(&0x02) {
        return Err("not an EIP-1559 transaction".into());
    }
    let r = Rlp::new(&bytes[1..]);
    let info = r.payload_info().map_err(e)?;
    if info.header_len + info.value_len != bytes.len() - 1 {
        return Err("trailing bytes".into());
    }
    if !r.is_list() || r.item_count().map_err(e)? != 9 {
        return Err("expected 9 fields".into());
    }
    let to_bytes: Vec<u8> = r.val_at(5).map_err(e)?;
    if to_bytes.len() != 20 {
        return Err("no 20-byte recipient".into());
    }
    if r.at(8).map_err(e)?.item_count().map_err(e)? != 0 {
        return Err("access list not empty".into());
    }
    let mut to = [0u8; 20];
    to.copy_from_slice(&to_bytes);
    Ok(Decoded {
        chain_id: r.val_at(0).map_err(e)?,
        nonce: r.val_at(1).map_err(e)?,
        max_priority_fee_per_gas: r.val_at(2).map_err(e)?,
        max_fee_per_gas: r.val_at(3).map_err(e)?,
        gas_limit: r.val_at(4).map_err(e)?,
        to,
        value: r.val_at(6).map_err(e)?,
        data: r.val_at(7).map_err(e)?,
    })
}

/// Read calldata as `selector || n words` exactly.
pub fn words(data: &[u8], n: usize) -> Result<Vec<Word>, String> {
    if data.len() != 4 + 32 * n {
        return Err(format!("calldata length {} for {} words", data.len(), n));
    }
    Ok(data[4..].chunks(32).map(|c| c.try_into().unwrap()).collect())
}

pub fn word_to_addr(w: &Word) -> Result<Addr, String> {
    if w[..12].iter().any(|b| *b != 0) {
        return Err("address word not canonical".into());
    }
    Ok(w[12..].try_into().unwrap())
}

pub fn word_to_u64(w: &Word) -> Result<u64, String> {
    if w[..24].iter().any(|b| *b != 0) {
        return Err("value above u64".into());
    }
    Ok(u64::from_be_bytes(w[24..].try_into().unwrap()))
}

pub fn word_to_bool(w: &Word) -> Result<bool, String> {
    match word_to_u64(w)? {
        0 => Ok(false),
        1 => Ok(true),
        _ => Err("bool word not 0/1".into()),
    }
}

/// Chain Signatures response, as `v1.signer-prod.testnet` 3.17.0 returns it
/// (`{"scheme":"Secp256k1","big_r":{"affine_point":"02.."},"s":{"scalar":".."},"recovery_id":0}`).
#[derive(Deserialize)]
#[serde(crate = "near_sdk::serde")]
pub struct SignResponse {
    pub scheme: String,
    pub big_r: AffinePoint,
    pub s: Scalar,
    pub recovery_id: u8,
}
#[derive(Deserialize)]
#[serde(crate = "near_sdk::serde")]
pub struct AffinePoint {
    pub affine_point: String,
}
#[derive(Deserialize)]
#[serde(crate = "near_sdk::serde")]
pub struct Scalar {
    pub scalar: String,
}

fn trim(b: &[u8]) -> Vec<u8> {
    let i = b.iter().position(|x| *x != 0).unwrap_or(b.len());
    b[i..].to_vec()
}

/// Measurement A's rule: r = x(big_r), s = s.scalar, yParity = recovery_id (never the big_r prefix).
/// RLP integers must have no leading zeros, so r and s are trimmed before omni-transaction appends them as
/// byte strings (it appends them as given; about 1 in 128 signatures has a leading zero in r or s).
/// Returns (raw signed tx, the 64-byte r‖s, recovery id).
pub fn assemble(tx: &EVMTransaction, resp: &SignResponse) -> Result<(Vec<u8>, [u8; 64], u8), String> {
    if resp.scheme != "Secp256k1" {
        return Err("not a secp256k1 signature".into());
    }
    let big_r = hex::decode(&resp.big_r.affine_point).map_err(|_| "big_r hex")?;
    let s = hex::decode(&resp.s.scalar).map_err(|_| "s hex")?;
    if big_r.len() != 33 || s.len() != 32 || resp.recovery_id > 1 {
        return Err("bad signature lengths".into());
    }
    let mut rs = [0u8; 64];
    rs[..32].copy_from_slice(&big_r[1..]);
    rs[32..].copy_from_slice(&s);
    let sig = Signature { v: resp.recovery_id as u64, r: trim(&rs[..32]), s: trim(&rs[32..]) };
    Ok((tx.build_with_signature(&sig), rs, resp.recovery_id))
}

/// The EVM address that produced `rs`/`v` over `hash`, with high s rejected (as Base requires, EIP-2).
pub fn recover_address(hash: &[u8; 32], rs: &[u8; 64], v: u8) -> Option<Addr> {
    let pk = env::ecrecover(hash, rs, v, true)?;
    let h = env::keccak256_array(&pk);
    Some(h[12..].try_into().unwrap())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn tx() -> EVMTransaction {
        BaseTx::new(
            &TxParams { nonce: 7, gas_limit: 100_000, max_fee_per_gas: 1_000_000_000, max_priority_fee_per_gas: 1_000_000 },
            &[0x11; 20],
            0,
            &calldata(SEL_APPROVE, &[w_addr(&[0x22; 20]), W_MAX]),
        )
        .omni()
    }

    #[test]
    fn decode_roundtrip() {
        let d = decode_unsigned(&tx().build_for_signing()).unwrap();
        assert_eq!((d.chain_id, d.nonce, d.gas_limit, d.to, d.value), (CHAIN_ID, 7, 100_000, [0x11; 20], 0));
        assert_eq!(&d.data[..4], &SEL_APPROVE);
    }

    #[test]
    fn decode_roundtrip_value_and_empty_data() {
        let p = TxParams { nonce: 3, gas_limit: 21_000, max_fee_per_gas: 1_000_000_000, max_priority_fee_per_gas: 1 };
        let t = BaseTx::new(&p, &[0x44; 20], 1_000_000_000_000_000, &[]);
        let d = decode_unsigned(&t.omni().build_for_signing()).unwrap();
        assert_eq!((d.nonce, d.gas_limit, d.to, d.value), (3, 21_000, [0x44; 20], 1_000_000_000_000_000));
        assert!(d.data.is_empty());
        // BaseTx goes to `on_signed` as a promise argument: the value is a decimal string and round-trips at any size.
        let t = BaseTx::new(&p, &[0x44; 20], u128::MAX, &[]);
        let json = near_sdk::serde_json::to_value(&t).unwrap();
        assert_eq!(json["value"], u128::MAX.to_string());
        let back: BaseTx = near_sdk::serde_json::from_value(json).unwrap();
        assert_eq!((back.value.0, back.data.as_str()), (u128::MAX, ""));
    }

    #[test]
    fn decode_rejects_trailing_bytes_and_other_types() {
        let mut b = tx().build_for_signing();
        b.push(0);
        assert!(decode_unsigned(&b).unwrap_err().contains("trailing"));
        let mut b = tx().build_for_signing();
        b[0] = 0x01;
        assert!(decode_unsigned(&b).is_err());
    }

    #[test]
    fn leading_zero_r_is_trimmed() {
        let mut r = "00".repeat(1);
        r.push_str(&"ab".repeat(31));
        let resp = SignResponse {
            scheme: "Secp256k1".into(),
            big_r: AffinePoint { affine_point: format!("02{r}") },
            s: Scalar { scalar: "cd".repeat(32) },
            recovery_id: 1,
        };
        let (raw, rs, v) = assemble(&tx(), &resp).unwrap();
        assert_eq!((rs[0], v), (0, 1));
        // Canonical RLP: r is a 31-byte string (0x80 + 31 = 0x9f) followed by its bytes, then s (0xa0 + 32 bytes).
        let tail = &raw[raw.len() - (1 + 31 + 1 + 32)..];
        assert_eq!(tail[0], 0x9f);
        assert_eq!(tail[32], 0xa0);
        // the signed list decodes with 12 fields
        assert_eq!(Rlp::new(&raw[1..]).item_count().unwrap(), 12);
    }
}
