//! Updates, the Companion's side of the decisions (docs/OTA.md): is a release
//! manifest really ours, which versions are newer, and what each board on this
//! computer needs - nothing, a board update, a Companion update, or one of them
//! first because the two no longer speak the same protocol.
//!
//! The signature check must give exactly the answers of tools/ota_release.py and
//! the firmware on tests/vectors/ota_v1.json (the test below reads that file).

use p256::ecdsa::{signature::Verifier, Signature, VerifyingKey};
use serde::Deserialize;
use sha2_011::{Digest, Sha256};

/// The Companion protocol range this build speaks (the board reports `comp_api`;
/// boards that report none speak 1).
pub const COMP_API_MIN: u32 = 1;
pub const COMP_API_MAX: u32 = 1;

/// `{"key_id": "<16 hex>", "sig": "<128 hex: r || s>"}`
#[derive(Debug, Clone, Deserialize, PartialEq, Eq)]
pub struct ManifestSig {
    pub key_id: String,
    pub sig: String,
}

/// The id a manifest signature names: the first 8 bytes of SHA-256 of the
/// uncompressed public key, as hex.
pub fn key_id(public_key: &[u8; 65]) -> String {
    hex::encode(&Sha256::digest(public_key)[..8])
}

/// True only when `sig` was made over exactly `manifest` by one of `trusted`.
/// Everything malformed is simply false - never a panic, never "probably".
pub fn verify_manifest(manifest: &[u8], sig: &ManifestSig, trusted: &[[u8; 65]]) -> bool {
    let Ok(raw) = hex::decode(&sig.sig) else { return false };
    if raw.len() != 64 || manifest.is_empty() {
        return false;
    }
    let Ok(signature) = Signature::from_slice(&raw) else { return false };
    for k in trusted {
        if key_id(k) != sig.key_id {
            continue;
        }
        let Ok(vk) = VerifyingKey::from_sec1_bytes(k) else { return false };
        return vk.verify(manifest, &signature).is_ok();
    }
    false
}

/// major.minor.patch from "hid_fi_v4.0.4", "v4.1.0", "4.1". Anything else is not
/// a version (the firmware's otaParseVersion, same rules).
pub fn parse_version(s: &str) -> Option<(u32, u32, u32)> {
    let t = s.rsplit_once("_v").map(|(_, r)| r).unwrap_or(s);
    let t = t.strip_prefix('v').or_else(|| t.strip_prefix('V')).unwrap_or(t);
    let core = t.split(['-', '+']).next()?;
    let parts: Vec<&str> = core.split('.').collect();
    if parts.len() < 2 || parts.len() > 3 {
        return None;
    }
    let mut n = [0u32; 3];
    for (i, p) in parts.iter().enumerate() {
        if p.is_empty() || p.len() > 6 || !p.bytes().all(|b| b.is_ascii_digit()) {
            return None;
        }
        n[i] = p.parse().ok()?;
    }
    Some((n[0], n[1], n[2]))
}

/// What the Companion knows about one board from its /api/status.
#[derive(Debug, Clone, Default, Deserialize)]
pub struct BoardStatus {
    pub firmware: Option<String>,
    pub comp_api: Option<u32>,
    pub ota_layout: Option<String>,
    pub ota_state: Option<String>,
    pub ota_result: Option<String>,
}

/// The newest release, as read from its verified manifest.
#[derive(Debug, Clone)]
pub struct Latest {
    pub firmware: String,
    pub board_comp_api: (u32, u32),
    pub companion: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Verdict {
    /// Board and Companion speak the same protocol and nothing newer is known.
    UpToDate,
    /// The board is busy proving a new firmware - say so, change nothing.
    Verifying,
    /// A newer firmware exists; `cable_once` when the board still has one slot.
    BoardUpdate { to: String, cable_once: bool },
    /// A newer Companion exists.
    CompanionUpdate { to: String },
    /// The board's protocol is older than this Companion speaks: update the board first.
    UpdateBoardFirst,
    /// The board speaks a newer protocol: update the Companion first.
    UpdateCompanionFirst,
    /// The status does not say which firmware runs - never guess.
    Unknown,
}

/// What one board needs, most urgent first. `this_companion` is this build's
/// version; `latest` is None when no verified manifest is at hand.
pub fn verdicts(board: &BoardStatus, this_companion: &str, latest: Option<&Latest>) -> Vec<Verdict> {
    let Some(fw) = board.firmware.as_deref().and_then(parse_version) else {
        return vec![Verdict::Unknown];
    };
    if board.ota_state.as_deref() == Some("pending_verify") {
        return vec![Verdict::Verifying];
    }
    let api = board.comp_api.unwrap_or(1);
    let mut out = Vec::new();
    if api < COMP_API_MIN {
        out.push(Verdict::UpdateBoardFirst);
    } else if api > COMP_API_MAX {
        out.push(Verdict::UpdateCompanionFirst);
    }
    if let Some(l) = latest {
        if let Some(to) = parse_version(&l.firmware) {
            if to > fw {
                out.push(Verdict::BoardUpdate {
                    to: l.firmware.clone(),
                    cable_once: board.ota_layout.as_deref() != Some("two_slot"),
                });
            }
        }
        if let (Some(c), Some(me)) = (l.companion.as_deref(), parse_version(this_companion)) {
            if parse_version(c).is_some_and(|c| c > me) {
                out.push(Verdict::CompanionUpdate { to: c.to_string() });
            }
        }
    }
    if out.is_empty() {
        out.push(Verdict::UpToDate);
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    // A tiny base64 decoder for the vectors file (no extra dependency for a test).
    fn b64(s: &str) -> Vec<u8> {
        let table = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
        let mut out = Vec::new();
        let (mut buf, mut bits) = (0u32, 0);
        for c in s.bytes().filter(|c| *c != b'=') {
            let v = table.iter().position(|t| *t == c).expect("base64") as u32;
            buf = (buf << 6) | v;
            bits += 6;
            if bits >= 8 {
                bits -= 8;
                out.push((buf >> bits) as u8);
            }
        }
        out
    }

    fn vectors() -> serde_json::Value {
        serde_json::from_str(include_str!("../../../../tests/vectors/ota_v1.json")).unwrap()
    }

    fn key(v: &serde_json::Value, name: &str) -> [u8; 65] {
        hex::decode(v[name].as_str().unwrap()).unwrap().try_into().unwrap()
    }

    #[test]
    fn the_shared_vectors_give_the_same_answers() {
        let v = vectors();
        let trusted = [key(&v, "test_public_key")];
        assert_eq!(key_id(&trusted[0]), v["test_key_id"].as_str().unwrap());
        for c in v["cases"].as_array().unwrap() {
            let m = b64(c["manifest_b64"].as_str().unwrap());
            let sig: ManifestSig = serde_json::from_value(c["sig"].clone()).unwrap();
            assert_eq!(verify_manifest(&m, &sig, &trusted), c["valid"].as_bool().unwrap(), "{}", c["name"]);
        }
    }

    #[test]
    fn the_key_id_chooses_among_trusted_keys() {
        let v = vectors();
        let c = &v["cases"][0];
        let m = b64(c["manifest_b64"].as_str().unwrap());
        let sig: ManifestSig = serde_json::from_value(c["sig"].clone()).unwrap();
        assert!(verify_manifest(&m, &sig, &[key(&v, "untrusted_public_key"), key(&v, "test_public_key")]));
        assert!(!verify_manifest(&m, &sig, &[key(&v, "untrusted_public_key")]));
        assert!(!verify_manifest(&m, &sig, &[]));
    }

    #[test]
    fn versions_follow_the_firmware_rules() {
        assert_eq!(parse_version("hid_fi_v4.0.4"), Some((4, 0, 4)));
        assert_eq!(parse_version("v4.1.0"), Some((4, 1, 0)));
        assert_eq!(parse_version("4.1"), Some((4, 1, 0)));
        assert_eq!(parse_version("hid_fi_v3.10"), Some((3, 10, 0)));
        assert_eq!(parse_version("hid_fi_v4.0.4-test"), Some((4, 0, 4)));
        for bad in ["4", "v4.x", "4.0.4junk", "", "hid_fi_v1234567.0", "4.0.4.1"] {
            assert_eq!(parse_version(bad), None, "{bad}");
        }
        assert!(parse_version("hid_fi_v3.10") > parse_version("hid_fi_v3.9"));
    }

    fn board(fw: &str, api: Option<u32>, layout: &str, state: &str) -> BoardStatus {
        BoardStatus {
            firmware: Some(fw.into()),
            comp_api: api,
            ota_layout: Some(layout.into()),
            ota_state: Some(state.into()),
            ota_result: None,
        }
    }

    fn latest(fw: &str, companion: Option<&str>) -> Latest {
        Latest { firmware: fw.into(), board_comp_api: (1, 1), companion: companion.map(String::from) }
    }

    #[test]
    fn what_each_board_needs() {
        let l = latest("hid_fi_v4.1.0", Some("4.1.0"));
        // same version, same protocol
        assert_eq!(verdicts(&board("hid_fi_v4.1.0", Some(1), "two_slot", "valid"), "4.1.0", Some(&l)),
                   vec![Verdict::UpToDate]);
        // older board on two slots: over the air
        assert_eq!(verdicts(&board("hid_fi_v4.0.5", Some(1), "two_slot", "valid"), "4.1.0", Some(&l)),
                   vec![Verdict::BoardUpdate { to: "hid_fi_v4.1.0".into(), cable_once: false }]);
        // a v4.0.4 board reports no comp_api and no layout: protocol 1, cable once
        let old = BoardStatus { firmware: Some("hid_fi_v4.0.4".into()), ..Default::default() };
        assert_eq!(verdicts(&old, "4.1.0", Some(&l)),
                   vec![Verdict::BoardUpdate { to: "hid_fi_v4.1.0".into(), cable_once: true }]);
        // older Companion
        assert_eq!(verdicts(&board("hid_fi_v4.1.0", Some(1), "two_slot", "valid"), "4.0.4", Some(&l)),
                   vec![Verdict::CompanionUpdate { to: "4.1.0".into() }]);
        // both older: both said
        assert_eq!(verdicts(&board("hid_fi_v4.0.5", Some(1), "two_slot", "valid"), "4.0.4", Some(&l)).len(), 2);
        // a newer board protocol than this Companion speaks
        assert_eq!(verdicts(&board("hid_fi_v5.0.0", Some(2), "two_slot", "valid"), "4.1.0", None),
                   vec![Verdict::UpdateCompanionFirst]);
        // an older one
        assert_eq!(verdicts(&board("hid_fi_v4.1.0", Some(0), "two_slot", "valid"), "4.1.0", None),
                   vec![Verdict::UpdateBoardFirst]);
        // proving a new firmware: say so, offer nothing
        assert_eq!(verdicts(&board("hid_fi_v4.1.0", Some(1), "two_slot", "pending_verify"), "4.0.4", Some(&l)),
                   vec![Verdict::Verifying]);
        // never guess a version
        let unknown = BoardStatus { firmware: Some("other_v1".into()), ..Default::default() };
        assert_eq!(verdicts(&unknown, "4.1.0", Some(&l)), vec![Verdict::Unknown]);
        // a newer board than the newest release known: nothing to offer
        assert_eq!(verdicts(&board("hid_fi_v4.2.0", Some(1), "two_slot", "valid"), "4.1.0", Some(&l)),
                   vec![Verdict::UpToDate]);
        // no manifest at hand: only the protocol check
        assert_eq!(verdicts(&board("hid_fi_v4.0.5", Some(1), "two_slot", "valid"), "4.1.0", None),
                   vec![Verdict::UpToDate]);
    }
}
