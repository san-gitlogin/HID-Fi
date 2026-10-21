//! Finding the board plugged into this computer.
//!
//! The USB side gives the board's MAC (its USB serial number). From the MAC the
//! board's own network name follows: `esp-hid-` + the last three bytes in
//! lowercase hex (`startMdns()` in the firmware). That name, the address that
//! worked last time, and whatever mDNS has seen are all asked for `/api/status`,
//! and the MAC each one reports decides - see `hidfi_core::discovery`.

use crate::env::{Browse, Http, UsbList};
use hidfi_core::discovery::{self, Mac, NetBoard, Selection};
use serde::Deserialize;

#[derive(Deserialize)]
struct StatusReply {
    mac: Option<String>,
    firmware: Option<String>,
    wifi_ip: Option<String>,
}

/// The `.local` name the firmware gives itself.
pub fn mdns_url(mac: &Mac) -> String {
    format!(
        "http://esp-hid-{:02x}{:02x}{:02x}.local",
        mac.0[3], mac.0[4], mac.0[5]
    )
}

/// Asks one address what board it is. `None` for anything that is not a
/// HID-Fi board answering sensibly.
pub fn probe(http: &dyn Http, base: &str) -> Option<NetBoard> {
    let (code, body) = http.get(&format!("{base}/api/status")).ok()?;
    if code != 200 {
        return None;
    }
    let s: StatusReply = serde_json::from_str(&body).ok()?;
    let firmware = s.firmware?;
    if !firmware.starts_with("hid_fi_v") {
        return None;
    }
    // Found by its .local name: talk to its address from now on. macOS asks
    // for an IPv6 address too, the board's mDNS answers only IPv4, and every
    // lookup by name then waits 5 s for nothing (measured on a v3.11 board).
    // The name stays the way back if the address changes.
    let url = match s
        .wifi_ip
        .as_deref()
        .and_then(|ip| ip.parse::<std::net::Ipv4Addr>().ok())
    {
        Some(ip) if is_local_name(base) && !ip.is_unspecified() => format!("http://{ip}"),
        _ => base.to_string(),
    };
    Some(NetBoard {
        url,
        mac: Mac::parse(&s.mac?)?,
        firmware,
    })
}

fn is_local_name(base: &str) -> bool {
    let host = base
        .trim_start_matches("http://")
        .split(['/', ':'])
        .next()
        .unwrap_or("");
    host.ends_with(".local")
}

/// Everything worth asking, without duplicates, cheapest guess first.
pub fn candidates(
    usb_macs: &[Mac],
    remembered_url: Option<&str>,
    browsed: &[String],
) -> Vec<String> {
    let mut out: Vec<String> = Vec::new();
    let mut add = |u: String| {
        if !out.contains(&u) {
            out.push(u);
        }
    };
    // Addresses first (instant), the .local name last (a lookup by name can
    // take 5 s on macOS).
    if let Some(u) = remembered_url {
        add(u.to_string());
    }
    for b in browsed {
        add(b.clone());
    }
    for m in usb_macs {
        add(mdns_url(m));
    }
    out
}

pub struct Found {
    pub selection: Selection,
    /// MACs of Espressif devices on this USB, for the tray's diagnostics.
    pub usb: Vec<Mac>,
}

pub fn find(
    usb: &dyn UsbList,
    http: &dyn Http,
    browse: &dyn Browse,
    remembered: Option<(Mac, &str)>,
) -> Found {
    let devices = usb.list();
    let macs = discovery::usb_macs(&devices);
    if macs.is_empty() {
        // Nothing of ours on this USB: do not even touch the network.
        return Found {
            selection: Selection::NotPluggedIn { elsewhere: 0 },
            usb: macs,
        };
    }
    let mut seen: Vec<NetBoard> = Vec::new();
    for url in candidates(&macs, remembered.map(|r| r.1), &browse.boards()) {
        if let Some(b) = probe(http, &url) {
            // One board can answer on two addresses (its .local name and an IP).
            // Keep the first, which is the cheaper one to reach next time.
            if !seen.iter().any(|s| s.mac == b.mac) {
                seen.push(b);
            }
        }
    }
    Found {
        selection: discovery::select(&devices, &seen, remembered.map(|r| r.0)),
        usb: macs,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::env::NetError;
    use hidfi_core::discovery::UsbDevice;
    use std::collections::HashMap;
    use std::sync::Mutex;

    struct FakeUsb(Vec<UsbDevice>);
    impl UsbList for FakeUsb {
        fn list(&self) -> Vec<UsbDevice> {
            self.0.clone()
        }
    }
    struct FakeBrowse(Vec<String>);
    impl Browse for FakeBrowse {
        fn boards(&self) -> Vec<String> {
            self.0.clone()
        }
    }
    #[derive(Default)]
    struct FakeHttp {
        status: HashMap<String, (u16, String)>,
        asked: Mutex<Vec<String>>,
    }
    impl Http for FakeHttp {
        fn get(&self, url: &str) -> Result<(u16, String), NetError> {
            self.asked.lock().unwrap().push(url.to_string());
            self.status
                .get(url)
                .cloned()
                .ok_or(NetError::Unreachable("no".into()))
        }
        fn post_json(&self, _: &str, _: &str) -> Result<(u16, String), NetError> {
            unreachable!()
        }
    }

    const M1: &str = "DCB4D909F7C8";
    fn usb(serial: &str) -> UsbDevice {
        UsbDevice {
            vid: 0x303a,
            pid: 0x1001,
            serial: Some(serial.into()),
        }
    }
    fn status(mac: &str) -> (u16, String) {
        (
            200,
            format!(r#"{{"status":"ok","firmware":"hid_fi_v3.12","mac":"{mac}"}}"#),
        )
    }

    #[test]
    fn found_by_name_it_is_used_by_address() {
        let mut http = FakeHttp::default();
        http.status.insert(
            "http://esp-hid-09f7c8.local/api/status".into(),
            (
                200,
                r#"{"firmware":"hid_fi_v3.11","mac":"DCB4D909F7C8","wifi_ip":"192.168.0.106"}"#
                    .into(),
            ),
        );
        let f = find(&FakeUsb(vec![usb(M1)]), &http, &FakeBrowse(vec![]), None);
        assert!(
            matches!(f.selection, Selection::Board(ref b) if b.url == "http://192.168.0.106"),
            "{:?}",
            f.selection
        );
        // Not on a network yet (empty address): keep the name.
        let mut http = FakeHttp::default();
        http.status.insert(
            "http://esp-hid-09f7c8.local/api/status".into(),
            (
                200,
                r#"{"firmware":"hid_fi_v3.11","mac":"DCB4D909F7C8","wifi_ip":""}"#.into(),
            ),
        );
        let f = find(&FakeUsb(vec![usb(M1)]), &http, &FakeBrowse(vec![]), None);
        assert!(matches!(f.selection, Selection::Board(ref b) if b.url.ends_with(".local")));
    }

    #[test]
    fn the_local_name_comes_from_the_mac() {
        assert_eq!(
            mdns_url(&Mac::parse(M1).unwrap()),
            "http://esp-hid-09f7c8.local"
        );
    }

    #[test]
    fn finds_the_board_by_its_own_name_first() {
        let mut http = FakeHttp::default();
        http.status.insert(
            "http://esp-hid-09f7c8.local/api/status".into(),
            status("DC:B4:D9:09:F7:C8"),
        );
        let f = find(&FakeUsb(vec![usb(M1)]), &http, &FakeBrowse(vec![]), None);
        assert!(
            matches!(f.selection, Selection::Board(ref b) if b.url == "http://esp-hid-09f7c8.local")
        );
    }

    #[test]
    fn nothing_plugged_in_touches_no_network() {
        let http = FakeHttp::default();
        let f = find(
            &FakeUsb(vec![]),
            &http,
            &FakeBrowse(vec!["http://10.0.0.9".into()]),
            None,
        );
        assert_eq!(f.selection, Selection::NotPluggedIn { elsewhere: 0 });
        assert!(http.asked.lock().unwrap().is_empty());
    }

    #[test]
    fn something_else_answering_is_not_a_board() {
        let mut http = FakeHttp::default();
        // A router's status page, an old HID-Fi without a MAC, a 500.
        http.status.insert(
            "http://10.0.0.1/api/status".into(),
            (200, r#"{"mac":"DCB4D909F7C8"}"#.into()),
        );
        http.status.insert(
            "http://10.0.0.2/api/status".into(),
            (
                200,
                r#"{"firmware":"other_v1","mac":"DCB4D909F7C8"}"#.into(),
            ),
        );
        http.status
            .insert("http://10.0.0.3/api/status".into(), (500, String::new()));
        http.status
            .insert("http://10.0.0.4/api/status".into(), (200, "<html>".into()));
        let b = FakeBrowse((1..=4).map(|i| format!("http://10.0.0.{i}")).collect());
        let f = find(&FakeUsb(vec![usb(M1)]), &http, &b, None);
        assert!(
            matches!(f.selection, Selection::NotOnNetwork { .. }),
            "{:?}",
            f.selection
        );
    }

    #[test]
    fn the_remembered_address_is_asked_first_and_dedups() {
        let mut http = FakeHttp::default();
        http.status
            .insert("http://192.168.1.40/api/status".into(), status(M1));
        http.status
            .insert("http://esp-hid-09f7c8.local/api/status".into(), status(M1));
        let m = Mac::parse(M1).unwrap();
        let f = find(
            &FakeUsb(vec![usb(M1)]),
            &http,
            &FakeBrowse(vec!["http://192.168.1.40".into()]),
            Some((m, "http://192.168.1.40")),
        );
        assert!(matches!(f.selection, Selection::Board(ref b) if b.url == "http://192.168.1.40"));
        let asked = http.asked.lock().unwrap();
        assert_eq!(asked[0], "http://192.168.1.40/api/status");
        assert_eq!(
            asked.len(),
            2,
            "the browsed duplicate is not asked twice: {asked:?}"
        );
    }
}
