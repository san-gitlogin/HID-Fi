//! Pins the pairing wire format to fixed bytes. The board's mbedTLS
//! implementation and an independent Python check (`companion/vectors/check.py`)
//! read the same file, so all three must agree byte for byte.
//!
//! The file is golden: a mismatch is a protocol change. Regenerate only on
//! purpose, with HIDFI_WRITE_VECTORS=1, and bump the protocol version.

use hidfi_core::pairing::*;
use serde_json::{json, Value};

fn compute() -> Value {
    let a = Initiator::from_parts([0x01; 32], [0x0a; 16]);
    let b = Responder::from_parts([0x02; 32], [0x0b; 16]);
    let c = a.complete(&b.b_pub, &b.n_b).unwrap();
    let (code, key) = b.finish(&a.a_pub, &a.commit(), &c.n_a).unwrap();
    assert_eq!(code, c.code);
    let id = "00112233445566778899aabbccddeeff";
    let fp = "f0".repeat(32);
    let tn = "1c".repeat(16);
    let chal = "c4".repeat(16);
    let vn = "5e".repeat(16);
    json!({
        "v": 1,
        "a_secret": hex::encode([0x01u8; 32]),
        "n_a": hex::encode([0x0au8; 16]),
        "b_secret": hex::encode([0x02u8; 32]),
        "n_b": hex::encode([0x0bu8; 16]),
        "a_pub": hex::encode(a.a_pub),
        "b_pub": hex::encode(b.b_pub),
        "commit": hex::encode(a.commit()),
        "code": format_code(code),
        "key": hex::encode(key.as_bytes()),
        "beat": {"id": id, "port": 47800, "fp": fp, "tn": tn, "chal": chal,
                 "msg": beat_message(id, 47800, &fp, &tn, &chal),
                 "mac": hex::encode(beat_mac(&key, id, 47800, &fp, &tn, &chal))},
        "ticket": {"tn": tn, "vn": vn, "mac": hex::encode(ticket(&key, &tn, &vn))},
    })
}

#[test]
fn vectors_are_unchanged() {
    let path = concat!(env!("CARGO_MANIFEST_DIR"), "/../../vectors/pairing_v1.json");
    let got = compute();
    if std::env::var_os("HIDFI_WRITE_VECTORS").is_some() {
        std::fs::write(path, serde_json::to_string_pretty(&got).unwrap() + "\n").unwrap();
    }
    let want: Value =
        serde_json::from_str(&std::fs::read_to_string(path).expect("vectors file")).unwrap();
    assert_eq!(got, want, "pairing wire format changed");
}
