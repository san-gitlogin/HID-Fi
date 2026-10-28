//! The parts of the HID-Fi companion that do not depend on the operating system:
//! the protocol spoken with the board and the pairing cryptography.

pub mod discovery;
pub mod ota;
pub mod pairing;
pub mod proto;

/// Lowercase hex SHA-256, the spelling every hash on the wire uses.
pub fn sha256_hex(data: &[u8]) -> String {
    use sha2::Digest;
    hex::encode(sha2::Sha256::digest(data))
}
