//! Which board is plugged into *this* computer.
//!
//! HID-Fi enumerates as Espressif `303A:1001`. That is the default for every
//! generic ESP32-S3 and also the ID of the chip's own USB-Serial-JTAG port in
//! the bootloader, so a VID/PID match proves nothing. What does: the ESP32 core
//! sets the USB serial number to the chip's efuse MAC (`USB.cpp`,
//! `ESPUSB::begin`, `"__MAC__"` → `%02X` x6), and the board reports the same MAC
//! as `mac` in `/api/status`. A board is selected only when a device on this
//! computer's USB carries the MAC of a board found on the network.
//!
//! Pure logic only; the OS listing and the network lookup feed it.

use serde::{Deserialize, Serialize};

pub const ESPRESSIF_VID: u16 = 0x303a;

/// Six bytes, compared exactly. Built only through [`Mac::parse`].
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash, PartialOrd, Ord, Serialize, Deserialize)]
pub struct Mac(pub [u8; 6]);

impl Mac {
    /// Accepts `DCB4D909F7C8`, `dc:b4:d9:09:f7:c8` and `DC-B4-D9-09-F7-C8`.
    /// Anything else, including the right digits with stray characters, is not
    /// a MAC: a lenient parser is how two different devices end up "equal".
    pub fn parse(s: &str) -> Option<Mac> {
        let s = s.trim();
        let digits: String = match s.len() {
            12 => s.to_string(),
            17 => {
                let sep = s.as_bytes()[2];
                if sep != b':' && sep != b'-' {
                    return None;
                }
                for (i, c) in s.bytes().enumerate() {
                    if i % 3 == 2 && c != sep {
                        return None;
                    }
                }
                s.chars().filter(|&c| c as u8 != sep).collect()
            }
            _ => return None,
        };
        if digits.len() != 12 || !digits.bytes().all(|c| c.is_ascii_hexdigit()) {
            return None;
        }
        let mut out = [0u8; 6];
        for (i, b) in out.iter_mut().enumerate() {
            *b = u8::from_str_radix(&digits[i * 2..i * 2 + 2], 16).ok()?;
        }
        if out == [0; 6] || out == [0xff; 6] {
            return None;
        }
        Some(Mac(out))
    }

    pub fn hex(&self) -> String {
        hex::encode_upper(self.0)
    }
}

/// One USB device as the OS lists it. Only what matching needs.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct UsbDevice {
    pub vid: u16,
    pub pid: u16,
    pub serial: Option<String>,
}

/// One board found on the network, from its `/api/status`.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct NetBoard {
    /// Base URL that answered, e.g. `http://192.168.1.40`.
    pub url: String,
    pub mac: Mac,
    pub firmware: String,
}

#[derive(Debug, PartialEq, Eq)]
pub enum Selection {
    /// Exactly one board, or the one the user chose before.
    Board(NetBoard),
    /// No HID-Fi board on this computer's USB.
    NotPluggedIn {
        /// HID-Fi boards seen on the network that are plugged in elsewhere.
        elsewhere: usize,
    },
    /// A HID-Fi board is on this USB but not reachable on the network: it has
    /// not joined the home WiFi, or this computer is on another network.
    NotOnNetwork { mac: Mac },
    /// Several boards on this computer, and no remembered choice among them.
    Choose(Vec<NetBoard>),
}

/// MACs of Espressif devices on this computer's USB.
pub fn usb_macs(devices: &[UsbDevice]) -> Vec<Mac> {
    let mut v: Vec<Mac> = devices
        .iter()
        .filter(|d| d.vid == ESPRESSIF_VID)
        .filter_map(|d| d.serial.as_deref().and_then(Mac::parse))
        .collect();
    v.sort();
    v.dedup();
    v
}

pub fn select(usb: &[UsbDevice], net: &[NetBoard], remembered: Option<Mac>) -> Selection {
    let local = usb_macs(usb);
    let mut here: Vec<NetBoard> = Vec::new();
    for b in net {
        if local.contains(&b.mac) && !here.iter().any(|h| h.mac == b.mac) {
            here.push(b.clone());
        }
    }
    here.sort_by_key(|b| b.mac);
    match here.len() {
        1 => Selection::Board(here.remove(0)),
        0 => {
            // An Espressif device on USB with no network twin is either a
            // HID-Fi off the network or some other ESP32. We cannot tell, so
            // only say "not on the network" when there is exactly one such
            // device - with several, which one would we be talking about?
            let unmatched: Vec<Mac> = local
                .iter()
                .copied()
                .filter(|m| !net.iter().any(|b| b.mac == *m))
                .collect();
            if unmatched.len() == 1 && net.is_empty() {
                Selection::NotOnNetwork { mac: unmatched[0] }
            } else {
                Selection::NotPluggedIn {
                    elsewhere: net.len(),
                }
            }
        }
        _ => match remembered.and_then(|m| here.iter().find(|b| b.mac == m)) {
            Some(b) => Selection::Board(b.clone()),
            None => Selection::Choose(here),
        },
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const M1: &str = "DCB4D909F7C8";
    const M2: &str = "5CBC40994200";

    fn usb(vid: u16, serial: &str) -> UsbDevice {
        UsbDevice {
            vid,
            pid: 0x1001,
            serial: Some(serial.into()),
        }
    }
    fn net(mac: &str) -> NetBoard {
        NetBoard {
            url: format!("http://{mac}"),
            mac: Mac::parse(mac).unwrap(),
            firmware: "hid_fi_v3.11".into(),
        }
    }

    #[test]
    fn mac_forms() {
        let m = Mac::parse(M1).unwrap();
        assert_eq!(Mac::parse("dc:b4:d9:09:f7:c8"), Some(m));
        assert_eq!(Mac::parse("DC-B4-D9-09-F7-C8"), Some(m));
        assert_eq!(Mac::parse(" DCB4D909F7C8\n"), Some(m));
        assert_eq!(m.hex(), M1);
        for bad in [
            "",
            "DCB4D909F7C",
            "DCB4D909F7C8A",
            "DC:B4-D9:09:F7:C8",
            "DC:B4:D9:09:F7:CX",
            "DCB4D909F7G8",
            "000000000000",
            "FFFFFFFFFFFF",
            "DC:B4:D9:09:F7:C8:",
            "+CB4D909F7C8",
        ] {
            assert_eq!(Mac::parse(bad), None, "{bad:?}");
        }
    }

    #[test]
    fn the_board_on_this_usb_is_chosen() {
        let s = select(&[usb(0x303a, M1)], &[net(M2), net(M1)], None);
        assert_eq!(s, Selection::Board(net(M1)));
    }

    #[test]
    fn a_board_elsewhere_on_the_network_is_never_chosen() {
        let s = select(&[], &[net(M1)], None);
        assert_eq!(s, Selection::NotPluggedIn { elsewhere: 1 });
    }

    #[test]
    fn another_espressif_device_does_not_match() {
        // A generic S3 dev board on USB, and a HID-Fi plugged into another PC.
        let s = select(&[usb(0x303a, M2)], &[net(M1)], None);
        assert_eq!(s, Selection::NotPluggedIn { elsewhere: 1 });
    }

    #[test]
    fn same_serial_other_vendor_does_not_match() {
        let s = select(&[usb(0x1a86, M1)], &[net(M1)], None);
        assert_eq!(s, Selection::NotPluggedIn { elsewhere: 1 });
    }

    #[test]
    fn the_bootloader_port_with_colons_matches_its_own_board() {
        // USB-Serial-JTAG reports the MAC with colons. Same chip, same board.
        let s = select(&[usb(0x303a, "dc:b4:d9:09:f7:c8")], &[net(M1)], None);
        assert_eq!(s, Selection::Board(net(M1)));
    }

    #[test]
    fn a_board_off_the_network_is_named() {
        let s = select(&[usb(0x303a, M1)], &[], None);
        assert_eq!(
            s,
            Selection::NotOnNetwork {
                mac: Mac::parse(M1).unwrap()
            }
        );
    }

    #[test]
    fn two_unknown_espressif_devices_are_not_guessed_at() {
        let s = select(&[usb(0x303a, M1), usb(0x303a, M2)], &[], None);
        assert_eq!(s, Selection::NotPluggedIn { elsewhere: 0 });
    }

    #[test]
    fn two_boards_here_ask_unless_remembered() {
        let u = [usb(0x303a, M1), usb(0x303a, M2)];
        let n = [net(M1), net(M2)];
        assert!(matches!(select(&u, &n, None), Selection::Choose(v) if v.len() == 2));
        assert_eq!(select(&u, &n, Mac::parse(M2)), Selection::Board(net(M2)));
        // A remembered board that is no longer here does not win by default.
        let other = Mac::parse("112233445566");
        assert!(matches!(select(&u, &n, other), Selection::Choose(_)));
    }

    #[test]
    fn a_board_answering_on_two_addresses_counts_once() {
        let mut b = net(M1);
        b.url = "http://192.168.4.1".into();
        let s = select(&[usb(0x303a, M1), usb(0x303a, M1)], &[net(M1), b], None);
        assert!(matches!(s, Selection::Board(_)));
    }

    #[test]
    fn missing_serial_is_ignored() {
        let d = UsbDevice {
            vid: 0x303a,
            pid: 0x1001,
            serial: None,
        };
        assert_eq!(
            select(&[d], &[net(M1)], None),
            Selection::NotPluggedIn { elsewhere: 1 }
        );
    }
}
