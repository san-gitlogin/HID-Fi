//! Messages on `POST /api/companion`, the board endpoint the app speaks to.
//! The flow is in `companion/DESIGN.md`; this file is its exact shape.
//!
//! Requests are built only through these types, so a field can never be
//! misspelt on the wire. Replies are parsed leniently in what they may *add*
//! (unknown fields are ignored, so a newer board does not break an older app)
//! and strictly in what they carry: every hex value is length-checked here,
//! before anything uses it.

use serde::{Deserialize, Serialize};

pub const PROTOCOL: u32 = 1;
/// The board refuses longer bodies before copying them.
pub const MAX_BODY: usize = 768;
pub const MAX_NAME: usize = 32;

#[derive(Serialize, Debug, Clone, PartialEq, Eq)]
#[serde(tag = "op", rename_all = "snake_case")]
pub enum Request {
    Hello {
        v: u32,
        id: String,
        name: String,
        a_pub: String,
        commit: String,
    },
    State {
        id: String,
    },
    Reveal {
        id: String,
        n_a: String,
    },
    Chal {
        id: String,
    },
    Beat {
        id: String,
        port: u16,
        fp: String,
        tn: String,
        chal: String,
        mac: String,
    },
    /// An update action for the board this computer is paired with
    /// (hid_fi/ota.h, compOta): check, from (relay), install, cancel, apply,
    /// download_mode. `url` is empty except for `from`.
    Ota {
        id: String,
        act: String,
        url: String,
        chal: String,
        mac: String,
    },
}

impl Request {
    pub fn to_body(&self) -> String {
        let s = serde_json::to_string(self).expect("plain structs always serialise");
        debug_assert!(
            s.len() <= MAX_BODY,
            "request over the board's limit: {}",
            s.len()
        );
        s
    }
}

/// A computer name the board can store and the dashboard can show: printable,
/// at most 32 bytes, cut on a character boundary. Never empty.
pub fn clean_name(raw: &str) -> String {
    let mut out = String::new();
    for c in raw.chars() {
        let c = if c.is_control() { ' ' } else { c };
        if out.len() + c.len_utf8() > MAX_NAME {
            break;
        }
        out.push(c);
    }
    let t = out.trim();
    if t.is_empty() {
        "Computer".to_string()
    } else {
        t.to_string()
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum PairState {
    /// The board has never heard of this id, or it expired.
    None,
    /// Waiting for someone to tap Pair on the dashboard.
    Pending,
    /// The board has chosen its key; the app should reveal and show the code.
    Confirm,
    /// The code is on both screens; waiting for it to be typed on the phone.
    CodeShown,
    Paired,
    /// Too many wrong codes, or the user declined. Start again.
    Aborted,
}

#[derive(Debug, Clone, Deserialize)]
pub struct RawReply {
    pub status: String,
    #[serde(default)]
    pub reply: Option<String>,
    #[serde(default)]
    pub message: Option<String>,
    #[serde(default)]
    pub state: Option<PairState>,
    #[serde(default)]
    pub b_pub: Option<String>,
    #[serde(default)]
    pub n_b: Option<String>,
    #[serde(default)]
    pub chal: Option<String>,
    #[serde(default)]
    pub retry_ms: Option<u64>,
    /// An update request's answer (op "ota"): what was done, if anything.
    #[serde(default)]
    pub done: Option<String>,
}

/// Why the board said no, in words the tray can show.
#[derive(Debug, Clone, PartialEq, Eq, thiserror::Error)]
pub enum Refusal {
    #[error("set an access PIN on the board first (dashboard, Settings)")]
    PinRequired,
    #[error("the board already has the most computers it can pair; remove one in the dashboard")]
    Full,
    #[error("the board asked us to slow down")]
    RateLimited { retry_ms: u64 },
    #[error("the board no longer knows this computer; pair again")]
    UnknownPc,
    #[error("the board rejected our proof")]
    BadProof,
    #[error("the challenge expired")]
    StaleChallenge,
    #[error("this board's firmware does not support the companion app; update it")]
    Unsupported,
    #[error("the board refused: {0}")]
    Other(String),
}

#[derive(Debug, thiserror::Error)]
pub enum ReplyError {
    #[error(transparent)]
    Refused(#[from] Refusal),
    #[error("the board sent something we could not read")]
    Malformed,
}

fn hex_n<const N: usize>(s: &Option<String>) -> Result<[u8; N], ReplyError> {
    let s = s.as_deref().ok_or(ReplyError::Malformed)?;
    if s.len() != N * 2 {
        return Err(ReplyError::Malformed);
    }
    let mut out = [0u8; N];
    hex::decode_to_slice(s, &mut out).map_err(|_| ReplyError::Malformed)?;
    Ok(out)
}

impl RawReply {
    /// As [`RawReply::parse`], knowing the HTTP status: a 404 without a `reply`
    /// is firmware older than the companion endpoint (its catch-all 404).
    pub fn parse_http(code: u16, body: &str) -> Result<RawReply, ReplyError> {
        let r = Self::parse(body);
        if code == 404 {
            if let Err(ReplyError::Refused(Refusal::Other(_))) | Err(ReplyError::Malformed) = r {
                return Err(ReplyError::Refused(Refusal::Unsupported));
            }
        }
        r
    }

    pub fn parse(body: &str) -> Result<RawReply, ReplyError> {
        let r: RawReply = serde_json::from_str(body).map_err(|_| ReplyError::Malformed)?;
        if r.status == "ok" {
            return Ok(r);
        }
        Err(ReplyError::Refused(match r.reply.as_deref() {
            Some("pin_required") => Refusal::PinRequired,
            Some("full") => Refusal::Full,
            Some("rate_limited") => Refusal::RateLimited {
                retry_ms: r.retry_ms.unwrap_or(5000).min(60_000),
            },
            Some("unknown_pc") => Refusal::UnknownPc,
            Some("bad_mac") => Refusal::BadProof,
            Some("stale_chal") => Refusal::StaleChallenge,
            _ => Refusal::Other(
                r.reply
                    .or(r.message)
                    .unwrap_or_default()
                    .chars()
                    .take(80)
                    .collect(),
            ),
        }))
    }

    pub fn pair_state(&self) -> Result<PairState, ReplyError> {
        self.state.ok_or(ReplyError::Malformed)
    }

    /// `b_pub` and `nB`, only meaningful in [`PairState::Confirm`].
    pub fn board_key(&self) -> Result<([u8; 32], [u8; 16]), ReplyError> {
        Ok((hex_n::<32>(&self.b_pub)?, hex_n::<16>(&self.n_b)?))
    }

    pub fn challenge(&self) -> Result<[u8; 16], ReplyError> {
        hex_n::<16>(&self.chal)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn requests_are_tagged_by_op() {
        let r = Request::Chal { id: "ab".into() };
        assert_eq!(r.to_body(), r#"{"op":"chal","id":"ab"}"#);
    }

    #[test]
    fn the_largest_request_fits() {
        let r = Request::Beat {
            id: "a".repeat(32),
            port: 65535,
            fp: "f".repeat(64),
            tn: "1".repeat(32),
            chal: "c".repeat(32),
            mac: "d".repeat(64),
        };
        assert!(r.to_body().len() <= MAX_BODY);
        let h = Request::Hello {
            v: 1,
            id: "a".repeat(32),
            name: "\u{1F600}".repeat(8), // 32 bytes
            a_pub: "b".repeat(64),
            commit: "c".repeat(64),
        };
        assert!(h.to_body().len() <= MAX_BODY);
    }

    #[test]
    fn names_are_bounded_and_printable() {
        assert_eq!(clean_name("  Santhu's MacBook\n"), "Santhu's MacBook");
        assert_eq!(clean_name(""), "Computer");
        assert_eq!(clean_name("\u{7}\u{8}"), "Computer");
        let long = "é".repeat(40);
        let c = clean_name(&long);
        assert!(c.len() <= MAX_NAME && c.chars().all(|ch| ch == 'é'));
        assert_eq!(clean_name("a\tb"), "a b");
    }

    #[test]
    fn refusals_are_named() {
        let e = RawReply::parse(r#"{"status":"error","reply":"pin_required"}"#).unwrap_err();
        assert!(matches!(e, ReplyError::Refused(Refusal::PinRequired)));
        let e = RawReply::parse(r#"{"status":"error","reply":"rate_limited","retry_ms":999999}"#)
            .unwrap_err();
        assert!(matches!(
            e,
            ReplyError::Refused(Refusal::RateLimited { retry_ms: 60_000 })
        ));
        let e = RawReply::parse(r#"{"status":"error","message":"Not found."}"#).unwrap_err();
        assert!(matches!(e, ReplyError::Refused(Refusal::Other(m)) if m == "Not found."));
    }

    #[test]
    fn hex_is_length_checked() {
        let ok = format!(
            r#"{{"status":"ok","state":"confirm","b_pub":"{}","n_b":"{}"}}"#,
            "ab".repeat(32),
            "cd".repeat(16)
        );
        let r = RawReply::parse(&ok).unwrap();
        assert_eq!(r.pair_state().unwrap(), PairState::Confirm);
        assert!(r.board_key().is_ok());
        for bad in [
            format!(
                r#"{{"status":"ok","b_pub":"{}","n_b":"{}"}}"#,
                "ab".repeat(31),
                "cd".repeat(16)
            ),
            format!(
                r#"{{"status":"ok","b_pub":"{}","n_b":"{}"}}"#,
                "zz".repeat(32),
                "cd".repeat(16)
            ),
            format!(r#"{{"status":"ok","b_pub":"{}"}}"#, "ab".repeat(32)),
        ] {
            assert!(RawReply::parse(&bad).unwrap().board_key().is_err(), "{bad}");
        }
    }

    #[test]
    fn garbage_is_malformed_not_a_panic() {
        for b in ["", "null", "[]", "{", r#"{"status":1}"#, "<html>"] {
            assert!(
                matches!(RawReply::parse(b), Err(ReplyError::Malformed)),
                "{b}"
            );
        }
        // An unknown state from a newer board is malformed for this app, not a crash.
        assert!(RawReply::parse(r#"{"status":"ok","state":"later"}"#).is_err());
    }

    #[test]
    fn old_firmware_is_named() {
        let body =
            r#"{"status":"error","message":"Not found. Dashboard at / or API at /api/command"}"#;
        assert!(matches!(
            RawReply::parse_http(404, body),
            Err(ReplyError::Refused(Refusal::Unsupported))
        ));
        assert!(matches!(
            RawReply::parse_http(404, "<html>"),
            Err(ReplyError::Refused(Refusal::Unsupported))
        ));
        // A 404 that names itself (unknown_pc) keeps its meaning.
        let r = r#"{"status":"error","reply":"unknown_pc"}"#;
        assert!(matches!(
            RawReply::parse_http(404, r),
            Err(ReplyError::Refused(Refusal::UnknownPc))
        ));
    }

    #[test]
    fn unknown_fields_are_ignored() {
        assert!(RawReply::parse(r#"{"status":"ok","state":"paired","new":1}"#).is_ok());
    }
}
