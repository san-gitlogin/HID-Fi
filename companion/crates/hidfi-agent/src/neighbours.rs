//! Finding a board on this computer's own network without mDNS (D-14).
//!
//! mDNS does not cross from one router's network to another's, some routers drop it
//! between WiFi clients, and the address that worked last time goes stale when the
//! board's DHCP lease or network changes. The board on USB gives its MAC (its USB
//! serial number is its WiFi MAC); the operating system's ARP table maps MACs it has
//! heard to addresses. When the MAC is not there yet, one tiny UDP datagram to each
//! address of this computer's own subnets (its real netmasks, never a guessed /24)
//! makes the OS ask "who has" for every one of them, and the board answers. The
//! caller still asks each address for /api/status and the MAC it reports decides, as
//! for every other candidate (hidfi_core::discovery).

use hidfi_core::discovery::Mac;
use std::net::{Ipv4Addr, UdpSocket};
use std::time::Duration;

/// The most addresses one sweep nudges: a /22. A bigger network is not swept.
pub const MAX_HOSTS: u32 = 1024;

/// `(MAC, IPv4)` pairs from the OS's ARP table, in the three formats it comes in:
///   macOS / BSD `arp -an`:   `? (192.168.0.184) at dc:b4:d9:9:f7:c8 on en0 ifscope [ethernet]`
///   Linux `/proc/net/arp`:   `192.168.0.184  0x1  0x2  dc:b4:d9:09:f7:c8  *  wlan0`
///   Windows `arp -a`:        `  192.168.0.184         dc-b4-d9-09-f7-c8     dynamic`
/// macOS drops leading zeros in each byte (`9` for `09`); entries without a MAC
/// (`(incomplete)`, `00:00:00:00:00:00`) are skipped.
pub fn parse_arp(text: &str) -> Vec<(Mac, Ipv4Addr)> {
    let mut out = Vec::new();
    for line in text.lines() {
        let words: Vec<&str> = line
            .split_whitespace()
            .map(|w| w.trim_matches(|c| c == '(' || c == ')'))
            .collect();
        let ip = words.iter().find_map(|w| w.parse::<Ipv4Addr>().ok());
        let mac = words.iter().find_map(|w| mac_of(w));
        if let (Some(ip), Some(mac)) = (ip, mac) {
            if !out.contains(&(mac, ip)) {
                out.push((mac, ip));
            }
        }
    }
    out
}

/// A MAC written with `:` or `-` and one or two hex digits per byte.
fn mac_of(w: &str) -> Option<Mac> {
    let sep = if w.contains(':') {
        ':'
    } else if w.contains('-') {
        '-'
    } else {
        return None;
    };
    let parts: Vec<&str> = w.split(sep).collect();
    if parts.len() != 6
        || parts
            .iter()
            .any(|p| p.is_empty() || p.len() > 2 || !p.bytes().all(|c| c.is_ascii_hexdigit()))
    {
        return None;
    }
    Mac::parse(&parts.iter().map(|p| format!("{p:0>2}")).collect::<String>())
}

/// Every address of one subnet a sweep may nudge: not the network or broadcast
/// address, not this computer's own; nothing for a subnet over MAX_HOSTS or a /31-/32.
pub fn hosts(own: Ipv4Addr, mask: Ipv4Addr) -> Vec<Ipv4Addr> {
    let (a, m) = (u32::from(own), u32::from(mask));
    if m == 0 || m.leading_ones() + m.trailing_zeros() != 32 || m.trailing_zeros() < 2 {
        return Vec::new(); // not a contiguous mask, or a point-to-point link
    }
    let size = 1u32 << m.trailing_zeros();
    if size > MAX_HOSTS {
        return Vec::new();
    }
    let net = a & m;
    (1..size - 1)
        .map(|i| net + i)
        .filter(|&h| h != a)
        .map(Ipv4Addr::from)
        .collect()
}

/// This computer's own IPv4 subnets, by each interface's real netmask: not loopback,
/// not link-local (169.254/16, where no board's DHCP address lives).
pub fn local_subnets() -> Vec<(Ipv4Addr, Ipv4Addr)> {
    let mut out = Vec::new();
    for i in if_addrs::get_if_addrs().unwrap_or_default() {
        if let if_addrs::IfAddr::V4(v4) = i.addr {
            if v4.ip.is_loopback() || v4.ip.is_link_local() {
                continue;
            }
            if !out.contains(&(v4.ip, v4.netmask)) {
                out.push((v4.ip, v4.netmask));
            }
        }
    }
    out
}

/// The OS's ARP table as text (nothing parsed here; empty on any error).
pub fn arp_text() -> String {
    #[cfg(target_os = "linux")]
    {
        std::fs::read_to_string("/proc/net/arp").unwrap_or_default()
    }
    #[cfg(not(target_os = "linux"))]
    {
        let args: &[&str] = if cfg!(windows) { &["-a"] } else { &["-an"] };
        let mut c = std::process::Command::new("arp");
        c.args(args);
        #[cfg(windows)]
        {
            // the Companion is a windowed app: a console child would flash a window
            use std::os::windows::process::CommandExt;
            c.creation_flags(0x0800_0000); // CREATE_NO_WINDOW
        }
        c.output()
            .map(|o| String::from_utf8_lossy(&o.stdout).into_owned())
            .unwrap_or_default()
    }
}

/// One datagram to the discard port of every host: each makes the OS resolve that
/// address (ARP), so the board's MAC lands in the table. Nothing waits for a reply.
pub fn nudge(subnets: &[(Ipv4Addr, Ipv4Addr)]) -> usize {
    let Ok(s) = UdpSocket::bind("0.0.0.0:0") else {
        return 0;
    };
    let mut sent = 0;
    for (own, mask) in subnets {
        for h in hosts(*own, *mask) {
            if s.send_to(&[0], (h, 9)).is_ok() {
                sent += 1;
            }
        }
    }
    sent
}

/// "a.b.c.d/m.m.m.m" (as the board's heartbeat lists its saved networks) -> (address, mask).
pub fn parse_subnet(s: &str) -> Option<(Ipv4Addr, Ipv4Addr)> {
    let (a, m) = s.split_once('/')?;
    Some((a.parse().ok()?, m.parse().ok()?))
}

/// Addresses in these subnets answering on port 80 within `wait` (TCP connect, many at
/// once): what can be asked for /api/status. For a network the board was on that this
/// computer reaches only through a router, where ARP sees nothing. Bounded by MAX_HOSTS
/// per subnet and by the caller's rate limit.
pub fn tcp_sweep(subnets: &[(Ipv4Addr, Ipv4Addr)], wait: Duration) -> Vec<Ipv4Addr> {
    let hosts: Vec<Ipv4Addr> = subnets.iter().flat_map(|(a, m)| hosts(*a, *m)).collect();
    let found = std::sync::Arc::new(std::sync::Mutex::new(Vec::new()));
    for chunk in hosts.chunks(64) {
        let handles: Vec<_> = chunk
            .iter()
            .map(|&h| {
                let f = found.clone();
                std::thread::spawn(move || {
                    if std::net::TcpStream::connect_timeout(&(h, 80).into(), wait).is_ok() {
                        f.lock().unwrap_or_else(|e| e.into_inner()).push(h);
                    }
                })
            })
            .collect();
        for t in handles {
            let _ = t.join();
        }
    }
    let mut v = found.lock().unwrap_or_else(|e| e.into_inner()).clone();
    v.sort();
    v
}

/// Base URLs for these boards from an ARP table: EVERY address listed for each MAC - a
/// stale entry for the board's old address can sit beside its new one for minutes, and
/// the caller asks each one who it is.
pub fn lookup(macs: &[Mac], table: &[(Mac, Ipv4Addr)]) -> Vec<String> {
    let mut out: Vec<String> = Vec::new();
    for m in macs {
        for (tm, ip) in table {
            let u = format!("http://{ip}");
            if tm == m && !out.contains(&u) {
                out.push(u);
            }
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    const BOARD: &str = "DCB4D909F7C8";

    #[test]
    fn the_arp_table_is_read_in_every_os_format() {
        let mac = Mac::parse(BOARD).unwrap();
        let ip: Ipv4Addr = "192.168.0.184".parse().unwrap();
        for text in [
            "? (192.168.0.184) at dc:b4:d9:9:f7:c8 on en0 ifscope [ethernet]",
            "IP address       HW type     Flags       HW address            Mask     Device\n192.168.0.184    0x1         0x2         dc:b4:d9:09:f7:c8     *        wlan0",
            "Interface: 192.168.0.248 --- 0x11\n  Internet Address      Physical Address      Type\n  192.168.0.184         dc-b4-d9-09-f7-c8     dynamic",
        ] {
            assert!(parse_arp(text).contains(&(mac, ip)), "{text}");
        }
    }

    #[test]
    fn every_address_listed_for_the_board_is_tried_not_just_the_first() {
        // a lease changed: the old entry lingers beside the new one
        let t = parse_arp("? (192.168.0.184) at dc:b4:d9:9:f7:c8 on en0\n? (192.168.0.190) at dc:b4:d9:9:f7:c8 on en0\n? (192.168.0.1) at 0:11:22:33:44:55 on en0");
        assert_eq!(
            lookup(&[Mac::parse(BOARD).unwrap()], &t),
            ["http://192.168.0.184", "http://192.168.0.190"]
        );
    }

    #[test]
    fn incomplete_and_empty_entries_are_not_boards() {
        assert!(parse_arp("? (192.168.0.9) at (incomplete) on en0 ifscope [ethernet]").is_empty());
        assert!(parse_arp("192.168.0.9 0x1 0x0 00:00:00:00:00:00 * wlan0").is_empty());
        assert!(
            parse_arp("? (192.168.0.9) at dc:b4:d9:9:f7 on en0").is_empty(),
            "five bytes"
        );
    }

    #[test]
    fn a_subnet_from_the_board_is_read_exactly_or_not_at_all() {
        assert_eq!(
            parse_subnet("192.168.1.0/255.255.255.0"),
            Some((
                "192.168.1.0".parse().unwrap(),
                "255.255.255.0".parse().unwrap()
            ))
        );
        assert_eq!(
            parse_subnet("192.168.1.0/24"),
            None,
            "the board sends the mask itself"
        );
        assert_eq!(parse_subnet("nonsense"), None);
    }

    #[test]
    fn a_sweep_covers_the_real_subnet_and_never_a_guessed_or_huge_one() {
        let ip = |s: &str| s.parse::<Ipv4Addr>().unwrap();
        let h = hosts(ip("192.168.0.248"), ip("255.255.255.0"));
        assert_eq!(h.len(), 253, "254 hosts minus this computer");
        assert!(h.contains(&ip("192.168.0.184")) && !h.contains(&ip("192.168.0.248")));
        assert!(!h.contains(&ip("192.168.0.0")) && !h.contains(&ip("192.168.0.255")));
        assert!(
            !h.contains(&ip("192.168.1.11")),
            "the other router's network is not this subnet"
        );
        assert_eq!(
            hosts(ip("10.1.2.3"), ip("255.255.252.0")).len(),
            1021,
            "a /22 is the most swept"
        );
        assert!(
            hosts(ip("10.1.2.3"), ip("255.255.0.0")).is_empty(),
            "a /16 is never swept"
        );
        assert!(hosts(ip("10.0.0.1"), ip("255.255.255.254")).is_empty());
        assert!(
            hosts(ip("10.0.0.1"), ip("255.0.255.0")).is_empty(),
            "not a contiguous mask"
        );
    }
}
