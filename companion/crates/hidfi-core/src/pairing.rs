//! Pairing and the proofs that follow it. The wire format is fixed in
//! `companion/DESIGN.md` ("Pairing", "Heartbeat", "Viewer tickets"); the board
//! implements the same functions with mbedTLS, and `vectors/pairing_v1.json`
//! pins both to identical bytes.
//!
//! Everything here is pure: no clock, no network, no randomness except the one
//! explicit [`Initiator::new`] constructor, so it can be tested exhaustively.

use hkdf::Hkdf;
use hmac::{Hmac, Mac};
use sha2::{Digest, Sha256};
use subtle::ConstantTimeEq;
use x25519_dalek::{PublicKey, StaticSecret};
use zeroize::{Zeroize, ZeroizeOnDrop};

type HmacSha256 = Hmac<Sha256>;

pub const NONCE_LEN: usize = 16;
pub const KEY_LEN: usize = 32;
pub const HKDF_INFO: &[u8] = b"hidfi-companion-v1";

pub type Nonce = [u8; NONCE_LEN];

#[derive(Debug, thiserror::Error, PartialEq, Eq)]
pub enum PairError {
    /// The peer's public key is a low-order point: the shared secret would be
    /// all zeros, so anyone could compute it. Refuse rather than pair weakly.
    #[error("the board sent an unusable public key")]
    WeakPeerKey,
    #[error("the board's reply does not match the commitment")]
    CommitMismatch,
}

/// The pairing key. Never printed, never serialised except into the OS
/// keystore, wiped from memory on drop.
#[derive(Clone, Zeroize, ZeroizeOnDrop, PartialEq, Eq)]
pub struct PairKey([u8; KEY_LEN]);

impl PairKey {
    pub fn from_bytes(b: [u8; KEY_LEN]) -> Self {
        Self(b)
    }
    pub fn as_bytes(&self) -> &[u8; KEY_LEN] {
        &self.0
    }
}

impl std::fmt::Debug for PairKey {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str("PairKey(..)")
    }
}

/// `H("hidfi-commit" || a_pub || nA)`: sent in `hello` before the board has
/// chosen its key, so the app cannot pick `nA` after seeing it.
pub fn commit(a_pub: &[u8; 32], n_a: &Nonce) -> [u8; 32] {
    let mut h = Sha256::new();
    h.update(b"hidfi-commit");
    h.update(a_pub);
    h.update(n_a);
    h.finalize().into()
}

/// The six-digit code both sides show/compare, in `0..1_000_000`.
pub fn sas(a_pub: &[u8; 32], b_pub: &[u8; 32], n_a: &Nonce, n_b: &Nonce) -> u32 {
    let mut h = Sha256::new();
    h.update(b"hidfi-sas");
    h.update(a_pub);
    h.update(b_pub);
    h.update(n_a);
    h.update(n_b);
    let d = h.finalize();
    u32::from_be_bytes([d[0], d[1], d[2], d[3]]) % 1_000_000
}

/// Zero-padded, so 42 reads "000042" on both screens.
pub fn format_code(code: u32) -> String {
    format!("{:06}", code % 1_000_000)
}

pub fn derive_key(
    shared: &[u8; 32],
    a_pub: &[u8; 32],
    b_pub: &[u8; 32],
    n_a: &Nonce,
    n_b: &Nonce,
) -> PairKey {
    let mut salt = Sha256::new();
    salt.update(a_pub);
    salt.update(b_pub);
    salt.update(n_a);
    salt.update(n_b);
    let salt: [u8; 32] = salt.finalize().into();
    let hk = Hkdf::<Sha256>::new(Some(&salt), shared);
    let mut out = [0u8; KEY_LEN];
    // 32 bytes is far below HKDF-SHA256's 8160-byte limit; expand cannot fail.
    hk.expand(HKDF_INFO, &mut out)
        .expect("HKDF length is valid");
    PairKey(out)
}

fn hmac(k: &PairKey, msg: &[u8]) -> [u8; 32] {
    let mut m =
        <HmacSha256 as Mac>::new_from_slice(k.as_bytes()).expect("HMAC takes any key length");
    m.update(msg);
    m.finalize().into_bytes().into()
}

/// The exact ASCII the heartbeat proof covers. Hex is lowercase, port decimal.
pub fn beat_message(id: &str, port: u16, fp: &str, tn: &str, chal: &str) -> String {
    format!("beat|{id}|{port}|{fp}|{tn}|{chal}")
}

pub fn beat_mac(k: &PairKey, id: &str, port: u16, fp: &str, tn: &str, chal: &str) -> [u8; 32] {
    hmac(k, beat_message(id, port, fp, tn, chal).as_bytes())
}

/// The exact ASCII an update request's proof covers (hid_fi/ota.h, compOta).
pub fn ota_message(id: &str, act: &str, url: &str, chal: &str) -> String {
    format!("ota|{id}|{act}|{url}|{chal}")
}

pub fn ota_mac(k: &PairKey, id: &str, act: &str, url: &str, chal: &str) -> [u8; 32] {
    hmac(k, ota_message(id, act, url, chal).as_bytes())
}

pub fn ticket(k: &PairKey, tn: &str, vn: &str) -> [u8; 32] {
    hmac(k, format!("tkt|{tn}|{vn}").as_bytes())
}

/// Constant time, so the time taken does not tell an attacker how many leading
/// bytes of a forged ticket were right.
pub fn ticket_ok(k: &PairKey, tn: &str, vn: &str, presented: &[u8]) -> bool {
    let want = ticket(k, tn, vn);
    presented.len() == want.len() && bool::from(want.ct_eq(presented))
}

/// The app's side of one pairing attempt. Created fresh for every attempt, so
/// an abandoned attempt can never be resumed with old nonces.
pub struct Initiator {
    secret: StaticSecret,
    pub a_pub: [u8; 32],
    n_a: Nonce,
}

/// What the app holds once the board has answered: the code to show, and the
/// key to keep if the user confirms it on the dashboard.
pub struct Confirmed {
    pub code: u32,
    pub key: PairKey,
    pub n_a: Nonce,
}

impl Initiator {
    /// Fresh keys and nonce from the OS random source.
    pub fn new() -> Result<Self, getrandom::Error> {
        let mut seed = [0u8; 32];
        let mut n_a = [0u8; NONCE_LEN];
        getrandom::fill(&mut seed)?;
        getrandom::fill(&mut n_a)?;
        let me = Self::from_parts(seed, n_a);
        seed.zeroize();
        Ok(me)
    }

    /// Deterministic construction, for test vectors only.
    pub fn from_parts(secret: [u8; 32], n_a: Nonce) -> Self {
        let secret = StaticSecret::from(secret);
        let a_pub = PublicKey::from(&secret).to_bytes();
        Self { secret, a_pub, n_a }
    }

    pub fn commit(&self) -> [u8; 32] {
        commit(&self.a_pub, &self.n_a)
    }

    /// Called with the board's `b_pub` and `nB`. Only after this does the app
    /// reveal `nA` (returned in [`Confirmed`]), which is what makes grinding a
    /// matching code impossible for someone in the middle.
    pub fn complete(&self, b_pub: &[u8; 32], n_b: &Nonce) -> Result<Confirmed, PairError> {
        let shared = self.secret.diffie_hellman(&PublicKey::from(*b_pub));
        if !shared.was_contributory() {
            return Err(PairError::WeakPeerKey);
        }
        Ok(Confirmed {
            code: sas(&self.a_pub, b_pub, &self.n_a, n_b),
            key: derive_key(shared.as_bytes(), &self.a_pub, b_pub, &self.n_a, n_b),
            n_a: self.n_a,
        })
    }
}

/// The board's side, as the firmware does it. Lives here so tests can run the
/// whole exchange, and so the vectors have one reference implementation.
pub struct Responder {
    secret: StaticSecret,
    pub b_pub: [u8; 32],
    pub n_b: Nonce,
}

impl Responder {
    pub fn from_parts(secret: [u8; 32], n_b: Nonce) -> Self {
        let secret = StaticSecret::from(secret);
        let b_pub = PublicKey::from(&secret).to_bytes();
        Self { secret, b_pub, n_b }
    }

    /// Checks the revealed `nA` against the commitment from `hello`, then
    /// derives the same code and key as the app.
    pub fn finish(
        &self,
        a_pub: &[u8; 32],
        commitment: &[u8; 32],
        n_a: &Nonce,
    ) -> Result<(u32, PairKey), PairError> {
        if !bool::from(commit(a_pub, n_a).ct_eq(commitment)) {
            return Err(PairError::CommitMismatch);
        }
        let shared = self.secret.diffie_hellman(&PublicKey::from(*a_pub));
        if !shared.was_contributory() {
            return Err(PairError::WeakPeerKey);
        }
        Ok((
            sas(a_pub, &self.b_pub, n_a, &self.n_b),
            derive_key(shared.as_bytes(), a_pub, &self.b_pub, n_a, &self.n_b),
        ))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn pair(seed_a: u8, seed_b: u8) -> (Initiator, Responder) {
        (
            Initiator::from_parts([seed_a; 32], [seed_a ^ 0x55; 16]),
            Responder::from_parts([seed_b; 32], [seed_b ^ 0xaa; 16]),
        )
    }

    #[test]
    fn both_sides_agree_on_code_and_key() {
        let (a, b) = pair(1, 2);
        let c = a.complete(&b.b_pub, &b.n_b).unwrap();
        let (code, key) = b.finish(&a.a_pub, &a.commit(), &c.n_a).unwrap();
        assert_eq!(c.code, code);
        assert!(c.key == key);
        assert!(code < 1_000_000);
    }

    #[test]
    fn a_swapped_key_changes_the_code() {
        // Someone in the middle gives the board their own key (m) instead of
        // the app's: the codes must disagree, which is what the user sees.
        let (a, b) = pair(3, 4);
        let m = Initiator::from_parts([9; 32], [7; 16]);
        let app = a.complete(&b.b_pub, &b.n_b).unwrap();
        let (board_code, _) = b.finish(&m.a_pub, &m.commit(), &m.n_a).unwrap();
        assert_ne!(app.code, board_code);
    }

    #[test]
    fn a_wrong_reveal_is_refused() {
        let (a, b) = pair(5, 6);
        let bad = [0u8; 16];
        assert_eq!(
            b.finish(&a.a_pub, &a.commit(), &bad).err(),
            Some(PairError::CommitMismatch)
        );
    }

    #[test]
    fn low_order_points_are_refused() {
        let (a, _) = pair(7, 8);
        // The identity point: X25519 with it gives an all-zero secret.
        assert_eq!(
            a.complete(&[0u8; 32], &[1; 16]).err(),
            Some(PairError::WeakPeerKey)
        );
        let mut one = [0u8; 32];
        one[0] = 1;
        assert_eq!(
            a.complete(&one, &[1; 16]).err(),
            Some(PairError::WeakPeerKey)
        );
    }

    #[test]
    fn tickets_check_every_byte_and_length() {
        let k = PairKey::from_bytes([0x11; 32]);
        let t = ticket(&k, "aa", "bb");
        assert!(ticket_ok(&k, "aa", "bb", &t));
        assert!(!ticket_ok(&k, "aa", "bc", &t));
        assert!(!ticket_ok(&PairKey::from_bytes([0x12; 32]), "aa", "bb", &t));
        assert!(!ticket_ok(&k, "aa", "bb", &t[..31]));
        for i in 0..32 {
            let mut f = t;
            f[i] ^= 1;
            assert!(!ticket_ok(&k, "aa", "bb", &f));
        }
    }

    #[test]
    fn codes_are_six_digits() {
        assert_eq!(format_code(42), "000042");
        assert_eq!(format_code(999_999), "999999");
    }

    #[test]
    fn the_key_never_prints() {
        assert_eq!(
            format!("{:?}", PairKey::from_bytes([0x41; 32])),
            "PairKey(..)"
        );
    }

    #[test]
    fn beat_message_is_the_documented_ascii() {
        assert_eq!(
            beat_message("id", 47800, "fp", "tn", "ch"),
            "beat|id|47800|fp|tn|ch"
        );
    }
}
