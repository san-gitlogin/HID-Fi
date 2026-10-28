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

/// The public keys releases are signed with (uncompressed P-256): exactly
/// tools/release_keys.json, primary then the offline backup.
pub const RELEASE_KEYS: &[[u8; 65]] = &[
    // primary, key id fa48e032c618628f
    [
        0x04, 0x49, 0x4d, 0xff, 0xb5, 0x37, 0x85, 0x0e, 0xc4, 0x15, 0x07, 0x0e, 0x75, 0x8b, 0x1a,
        0x62, 0x95, 0x97, 0x84, 0xdd, 0x22, 0xea, 0xb1, 0x75, 0x6c, 0xc6, 0x35, 0xd7, 0xed, 0xc6,
        0xe3, 0xa0, 0xaf, 0x76, 0xbb, 0x12, 0x4b, 0x6b, 0xe9, 0xe8, 0xff, 0x66, 0x4e, 0x82, 0xa2,
        0x15, 0x09, 0xbc, 0xe0, 0x35, 0xf7, 0x6b, 0x01, 0x24, 0x1c, 0xa5, 0x47, 0xd4, 0x3e, 0xcb,
        0x8b, 0x89, 0x5d, 0x7d, 0xa3,
    ],
    // backup, key id d06253869d956bec
    [
        0x04, 0xee, 0x56, 0xa3, 0x2e, 0xd7, 0x96, 0x65, 0xf6, 0xf4, 0x58, 0x0e, 0xc7, 0xcd, 0xf8,
        0x1b, 0x84, 0xbb, 0x5d, 0x28, 0xd3, 0x4b, 0x22, 0x94, 0xd2, 0x4a, 0xbd, 0x16, 0x93, 0x4d,
        0xb8, 0x1f, 0x2f, 0xed, 0xea, 0xa7, 0x7b, 0xfa, 0x9c, 0x89, 0x0f, 0xc5, 0x0c, 0xe4, 0xc4,
        0x67, 0xce, 0x14, 0x3d, 0xab, 0x88, 0xc4, 0x99, 0xec, 0x05, 0x39, 0x56, 0x97, 0xec, 0xcc,
        0x9f, 0x50, 0x57, 0xe2, 0x9a,
    ],
];

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
    let Ok(raw) = hex::decode(&sig.sig) else {
        return false;
    };
    if raw.len() != 64 || manifest.is_empty() {
        return false;
    }
    let Ok(signature) = Signature::from_slice(&raw) else {
        return false;
    };
    for k in trusted {
        if key_id(k) != sig.key_id {
            continue;
        }
        let Ok(vk) = VerifyingKey::from_sec1_bytes(k) else {
            return false;
        };
        return vk.verify(manifest, &signature).is_ok();
    }
    false
}

/// major.minor.patch from "hid_fi_v4.0.4", "v4.1.0", "4.1". Anything else is not
/// a version (the firmware's otaParseVersion, same rules).
pub fn parse_version(s: &str) -> Option<(u32, u32, u32)> {
    let t = s.rsplit_once("_v").map(|(_, r)| r).unwrap_or(s);
    let t = t
        .strip_prefix('v')
        .or_else(|| t.strip_prefix('V'))
        .unwrap_or(t);
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
pub fn verdicts(
    board: &BoardStatus,
    this_companion: &str,
    latest: Option<&Latest>,
) -> Vec<Verdict> {
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

/// One downloadable file named by a signed manifest.
#[derive(Debug, Clone, Deserialize, PartialEq, Eq)]
pub struct Asset {
    pub name: String,
    pub size: u64,
    pub sha256: String,
}

#[derive(Debug, Clone, Deserialize, PartialEq, Eq)]
pub struct ChangelogSection {
    pub version: String,
    pub text: String,
}

/// The Companion part of a release: its version and one installer per system
/// ("macos", "windows", "linux"), each pinned by size and SHA-256.
#[derive(Debug, Clone, Deserialize, PartialEq, Eq)]
pub struct CompanionRelease {
    pub version: String,
    #[serde(default)]
    pub assets: std::collections::BTreeMap<String, Asset>,
}

#[derive(Debug, Clone, Deserialize, PartialEq, Eq)]
pub struct ApiRange {
    pub min: u32,
    pub max: u32,
}

/// A release, read from a manifest whose signature was checked first.
#[derive(Debug, Clone, Deserialize, PartialEq, Eq)]
pub struct Release {
    pub format: u32,
    pub product: String,
    pub chip: String,
    pub version: String,
    pub firmware: String,
    pub released: String,
    pub support_until: String,
    pub image: Asset,
    pub min_from: String,
    #[serde(default)]
    pub security: bool,
    #[serde(default)]
    pub risk: String,
    pub comp_api: ApiRange,
    #[serde(default)]
    pub changelog: Vec<ChangelogSection>,
    pub companion: Option<CompanionRelease>,
    /// The two-slot partition table (0xC00 bytes): what a cable switch writes.
    pub table: Option<Asset>,
}

/// Why a release was not accepted. None of these is ever shown as "available".
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ReleaseError {
    /// Not signed by a key this build trusts, or not the signed bytes.
    BadSignature,
    /// Signed, but not a manifest this build understands (format, product, chip, fields).
    NotForUs(String),
    /// The image bytes are not the ones the manifest pins.
    ImageMismatch,
}

pub const MANIFEST_FORMAT: u32 = 1;
pub const MANIFEST_CAP: usize = 24 * 1024;

/// The manifest's own bytes and its signature file, as downloaded. The signature
/// is checked before a single field is believed.
pub fn read_release(
    manifest: &[u8],
    sig_file: &[u8],
    trusted: &[[u8; 65]],
) -> Result<Release, ReleaseError> {
    if manifest.len() > MANIFEST_CAP {
        return Err(ReleaseError::NotForUs("manifest too large".into()));
    }
    let sig: ManifestSig =
        serde_json::from_slice(sig_file).map_err(|_| ReleaseError::BadSignature)?;
    if !verify_manifest(manifest, &sig, trusted) {
        return Err(ReleaseError::BadSignature);
    }
    let r: Release = serde_json::from_slice(manifest)
        .map_err(|_| ReleaseError::NotForUs("missing field".into()))?;
    let bad = |why: &str| Err(ReleaseError::NotForUs(why.into()));
    if r.format != MANIFEST_FORMAT {
        return bad("unknown manifest format");
    }
    if r.product != "hid-fi" || r.chip != "esp32s3" {
        return bad("not a HID-Fi ESP32-S3 release");
    }
    let (Some(v), Some(from)) = (plain_version(&r.version), plain_version(&r.min_from)) else {
        return bad("unreadable version");
    };
    if r.firmware != format!("hid_fi_v{}", r.version) || from >= v {
        return bad("inconsistent versions");
    }
    if !is_sha256_hex(&r.image.sha256) || r.image.size == 0 || !safe_name(&r.image.name) {
        return bad("unusable image entry");
    }
    if let Some(tb) = &r.table {
        if !is_sha256_hex(&tb.sha256) || tb.size != 0xC00 || !safe_name(&tb.name) {
            return bad("unusable table entry");
        }
    }
    if let Some(c) = &r.companion {
        if plain_version(&c.version).is_none()
            || c.assets
                .values()
                .any(|a| !is_sha256_hex(&a.sha256) || a.size == 0 || !safe_name(&a.name))
        {
            return bad("unusable companion entry");
        }
    }
    Ok(r)
}

/// A manifest's versions are bare: "4.1" or "4.1.0" - no "v", no suffix.
fn plain_version(s: &str) -> Option<(u32, u32, u32)> {
    if s.is_empty() || !s.bytes().all(|b| b.is_ascii_digit() || b == b'.') {
        return None;
    }
    parse_version(s)
}

fn is_sha256_hex(s: &str) -> bool {
    s.len() == 64 && s.bytes().all(|b| matches!(b, b'0'..=b'9' | b'a'..=b'f'))
}

/// A file name, never a path: it is joined to the source URL and to a folder.
fn safe_name(s: &str) -> bool {
    !s.is_empty()
        && s.len() <= 96
        && s != "."
        && s != ".."
        && s.bytes()
            .all(|b| b.is_ascii_alphanumeric() || matches!(b, b'.' | b'-' | b'_'))
}

/// True when `bytes` are exactly the file the manifest pinned.
pub fn asset_matches(a: &Asset, bytes: &[u8]) -> bool {
    bytes.len() as u64 == a.size && hex::encode(Sha256::digest(bytes)) == a.sha256
}

impl Release {
    pub fn latest(&self) -> Latest {
        Latest {
            firmware: self.version.clone(),
            board_comp_api: (self.comp_api.min, self.comp_api.max),
            companion: self.companion.as_ref().map(|c| c.version.clone()),
        }
    }

    /// The board can take this release over the air only from min_from up.
    pub fn reachable_from(&self, running: &str) -> bool {
        match (parse_version(running), parse_version(&self.min_from)) {
            (Some(r), Some(m)) => r >= m,
            _ => false,
        }
    }

    /// The sections newer than `running`, newest first (what the person is shown).
    pub fn changes_since(&self, running: &str) -> Vec<&ChangelogSection> {
        let Some(r) = parse_version(running) else {
            return Vec::new();
        };
        self.changelog
            .iter()
            .filter(|s| parse_version(&s.version).is_some_and(|v| v > r))
            .collect()
    }

    /// This system's Companion installer, when the release has one.
    pub fn companion_asset(&self, os: &str) -> Option<&Asset> {
        self.companion.as_ref()?.assets.get(os)
    }
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
        hex::decode(v[name].as_str().unwrap())
            .unwrap()
            .try_into()
            .unwrap()
    }

    #[test]
    fn the_shared_vectors_give_the_same_answers() {
        let v = vectors();
        let trusted = [key(&v, "test_public_key")];
        assert_eq!(key_id(&trusted[0]), v["test_key_id"].as_str().unwrap());
        for c in v["cases"].as_array().unwrap() {
            let m = b64(c["manifest_b64"].as_str().unwrap());
            let sig: ManifestSig = serde_json::from_value(c["sig"].clone()).unwrap();
            assert_eq!(
                verify_manifest(&m, &sig, &trusted),
                c["valid"].as_bool().unwrap(),
                "{}",
                c["name"]
            );
        }
    }

    #[test]
    fn the_key_id_chooses_among_trusted_keys() {
        let v = vectors();
        let c = &v["cases"][0];
        let m = b64(c["manifest_b64"].as_str().unwrap());
        let sig: ManifestSig = serde_json::from_value(c["sig"].clone()).unwrap();
        assert!(verify_manifest(
            &m,
            &sig,
            &[key(&v, "untrusted_public_key"), key(&v, "test_public_key")]
        ));
        assert!(!verify_manifest(
            &m,
            &sig,
            &[key(&v, "untrusted_public_key")]
        ));
        assert!(!verify_manifest(&m, &sig, &[]));
    }

    #[test]
    fn releases_are_accepted_or_refused_as_the_release_tool_decides() {
        let v = vectors();
        let rv: serde_json::Value = serde_json::from_str(include_str!(
            "../../../../tests/vectors/ota_release_v1.json"
        ))
        .unwrap();
        let trusted = [key(&v, "test_public_key")];
        for c in rv["cases"].as_array().unwrap() {
            let m = b64(c["manifest_b64"].as_str().unwrap());
            let sig = serde_json::to_vec(&c["sig"]).unwrap();
            let got = read_release(&m, &sig, &trusted);
            match c["refused"].as_str() {
                None => assert!(got.is_ok(), "{}: {:?}", c["name"], got.err()),
                Some(why) => assert_eq!(
                    got.err(),
                    Some(ReleaseError::NotForUs(why.into())),
                    "{}",
                    c["name"]
                ),
            }
        }
    }

    #[test]
    fn a_release_is_read_only_after_its_signature() {
        let v = vectors();
        let rv: serde_json::Value = serde_json::from_str(include_str!(
            "../../../../tests/vectors/ota_release_v1.json"
        ))
        .unwrap();
        let c = &rv["cases"][0];
        let m = b64(c["manifest_b64"].as_str().unwrap());
        let sig = serde_json::to_vec(&c["sig"]).unwrap();
        assert_eq!(
            read_release(&m, &sig, &[key(&v, "untrusted_public_key")]),
            Err(ReleaseError::BadSignature)
        );
        assert_eq!(
            read_release(&m, b"not json", &[key(&v, "test_public_key")]),
            Err(ReleaseError::BadSignature)
        );
        let mut changed = m.clone();
        let at = changed.windows(5).position(|w| w == b"4.1.0").unwrap();
        changed[at] = b'9';
        assert_eq!(
            read_release(&changed, &sig, &[key(&v, "test_public_key")]),
            Err(ReleaseError::BadSignature)
        );
        let big = vec![b' '; MANIFEST_CAP + 1];
        assert!(matches!(
            read_release(&big, &sig, &[key(&v, "test_public_key")]),
            Err(ReleaseError::NotForUs(_))
        ));
    }

    #[test]
    fn what_a_release_tells_the_companion() {
        let v = vectors();
        let rv: serde_json::Value = serde_json::from_str(include_str!(
            "../../../../tests/vectors/ota_release_v1.json"
        ))
        .unwrap();
        let c = &rv["cases"][0];
        let r = read_release(
            &b64(c["manifest_b64"].as_str().unwrap()),
            &serde_json::to_vec(&c["sig"]).unwrap(),
            &[key(&v, "test_public_key")],
        )
        .unwrap();
        let image = b64(v["image_b64"].as_str().unwrap());
        assert!(asset_matches(&r.image, &image));
        let mut flipped = image.clone();
        flipped[100] ^= 1;
        assert!(!asset_matches(&r.image, &flipped), "one bit changed");
        assert!(
            !asset_matches(&r.image, &image[..image.len() - 1]),
            "one byte short"
        );
        let inst = b64(rv["installer_b64"].as_str().unwrap());
        assert!(asset_matches(r.companion_asset("macos").unwrap(), &inst));
        assert!(
            r.companion_asset("windows").is_none(),
            "no installer: never a guess at another one"
        );
        assert!(asset_matches(
            r.table.as_ref().unwrap(),
            &b64(rv["table_b64"].as_str().unwrap())
        ));
        assert!(r.reachable_from("hid_fi_v4.0.4") && !r.reachable_from("hid_fi_v4.0.3"));
        assert!(!r.reachable_from("garbage"));
        assert_eq!(
            r.changes_since("hid_fi_v4.0.4")
                .iter()
                .map(|s| s.version.as_str())
                .collect::<Vec<_>>(),
            ["4.1.0"]
        );
        assert!(r.changes_since("hid_fi_v4.1.0").is_empty());
        let l = r.latest();
        assert_eq!(
            (
                l.firmware.as_str(),
                l.companion.as_deref(),
                l.board_comp_api
            ),
            ("4.1.0", Some("4.1.0"), (1, 1))
        );
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
        Latest {
            firmware: fw.into(),
            board_comp_api: (1, 1),
            companion: companion.map(String::from),
        }
    }

    #[test]
    fn what_each_board_needs() {
        let l = latest("hid_fi_v4.1.0", Some("4.1.0"));
        // same version, same protocol
        assert_eq!(
            verdicts(
                &board("hid_fi_v4.1.0", Some(1), "two_slot", "valid"),
                "4.1.0",
                Some(&l)
            ),
            vec![Verdict::UpToDate]
        );
        // older board on two slots: over the air
        assert_eq!(
            verdicts(
                &board("hid_fi_v4.0.5", Some(1), "two_slot", "valid"),
                "4.1.0",
                Some(&l)
            ),
            vec![Verdict::BoardUpdate {
                to: "hid_fi_v4.1.0".into(),
                cable_once: false
            }]
        );
        // a v4.0.4 board reports no comp_api and no layout: protocol 1, cable once
        let old = BoardStatus {
            firmware: Some("hid_fi_v4.0.4".into()),
            ..Default::default()
        };
        assert_eq!(
            verdicts(&old, "4.1.0", Some(&l)),
            vec![Verdict::BoardUpdate {
                to: "hid_fi_v4.1.0".into(),
                cable_once: true
            }]
        );
        // older Companion
        assert_eq!(
            verdicts(
                &board("hid_fi_v4.1.0", Some(1), "two_slot", "valid"),
                "4.0.4",
                Some(&l)
            ),
            vec![Verdict::CompanionUpdate { to: "4.1.0".into() }]
        );
        // both older: both said
        assert_eq!(
            verdicts(
                &board("hid_fi_v4.0.5", Some(1), "two_slot", "valid"),
                "4.0.4",
                Some(&l)
            )
            .len(),
            2
        );
        // a newer board protocol than this Companion speaks
        assert_eq!(
            verdicts(
                &board("hid_fi_v5.0.0", Some(2), "two_slot", "valid"),
                "4.1.0",
                None
            ),
            vec![Verdict::UpdateCompanionFirst]
        );
        // an older one
        assert_eq!(
            verdicts(
                &board("hid_fi_v4.1.0", Some(0), "two_slot", "valid"),
                "4.1.0",
                None
            ),
            vec![Verdict::UpdateBoardFirst]
        );
        // proving a new firmware: say so, offer nothing
        assert_eq!(
            verdicts(
                &board("hid_fi_v4.1.0", Some(1), "two_slot", "pending_verify"),
                "4.0.4",
                Some(&l)
            ),
            vec![Verdict::Verifying]
        );
        // never guess a version
        let unknown = BoardStatus {
            firmware: Some("other_v1".into()),
            ..Default::default()
        };
        assert_eq!(
            verdicts(&unknown, "4.1.0", Some(&l)),
            vec![Verdict::Unknown]
        );
        // a newer board than the newest release known: nothing to offer
        assert_eq!(
            verdicts(
                &board("hid_fi_v4.2.0", Some(1), "two_slot", "valid"),
                "4.1.0",
                Some(&l)
            ),
            vec![Verdict::UpToDate]
        );
        // no manifest at hand: only the protocol check
        assert_eq!(
            verdicts(
                &board("hid_fi_v4.0.5", Some(1), "two_slot", "valid"),
                "4.1.0",
                None
            ),
            vec![Verdict::UpToDate]
        );
    }
}
