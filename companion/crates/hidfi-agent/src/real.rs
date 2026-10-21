//! The real world: HTTP with ureq, USB listing with nusb, mDNS with mdns-sd.

use crate::env::{Browse, Http, NetError, UsbList};
use hidfi_core::discovery::UsbDevice;
use std::collections::HashMap;
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

/// A board's reply is a couple of kilobytes. Anything far bigger is not a board.
const MAX_REPLY: u64 = 32 * 1024;

pub struct UreqHttp {
    agent: ureq::Agent,
}

impl UreqHttp {
    /// `timeout` bounds the whole request: connect, send and read. The board
    /// answers in milliseconds; a long wait is a board that is not there.
    pub fn new(timeout: Duration) -> Self {
        let agent = ureq::Agent::config_builder()
            .timeout_global(Some(timeout))
            .http_status_as_error(false)
            .max_redirects(0)
            .build()
            .new_agent();
        UreqHttp { agent }
    }

    fn finish(
        r: Result<ureq::http::Response<ureq::Body>, ureq::Error>,
    ) -> Result<(u16, String), NetError> {
        let mut resp = r.map_err(|e| NetError::Unreachable(e.to_string()))?;
        let code = resp.status().as_u16();
        let body = resp
            .body_mut()
            .with_config()
            .limit(MAX_REPLY)
            .read_to_string()
            .map_err(|e| match e {
                ureq::Error::BodyExceedsLimit(_) => NetError::TooLarge,
                other => NetError::Unreachable(other.to_string()),
            })?;
        Ok((code, body))
    }
}

impl Http for UreqHttp {
    fn get(&self, url: &str) -> Result<(u16, String), NetError> {
        Self::finish(self.agent.get(url).call())
    }

    fn post_json(&self, url: &str, body: &str) -> Result<(u16, String), NetError> {
        Self::finish(
            self.agent
                .post(url)
                .header("Content-Type", "application/json")
                .send(body),
        )
    }
}

pub struct NusbList;

impl UsbList for NusbList {
    fn list(&self) -> Vec<UsbDevice> {
        use nusb::MaybeFuture;
        match nusb::list_devices().wait() {
            Ok(it) => it
                .map(|d| UsbDevice {
                    vid: d.vendor_id(),
                    pid: d.product_id(),
                    serial: d.serial_number().map(str::to_string),
                })
                .collect(),
            Err(e) => {
                log::warn!("could not list USB devices: {e}");
                Vec::new()
            }
        }
    }
}

/// Browses `_hidfi._tcp` in the background and remembers what answered in the
/// last few minutes. If mDNS cannot start (no multicast on this network, a
/// locked-down firewall), it stays empty: the board's `.local` name and the
/// remembered address still work.
pub struct MdnsBrowse {
    seen: Arc<Mutex<HashMap<String, Instant>>>,
    _daemon: Option<mdns_sd::ServiceDaemon>,
}

const MDNS_FORGET: Duration = Duration::from_secs(300);

impl MdnsBrowse {
    pub fn start() -> Self {
        let seen: Arc<Mutex<HashMap<String, Instant>>> = Arc::default();
        let daemon = match mdns_sd::ServiceDaemon::new() {
            Ok(d) => d,
            Err(e) => {
                log::warn!("mDNS unavailable: {e}");
                return MdnsBrowse {
                    seen,
                    _daemon: None,
                };
            }
        };
        match daemon.browse("_hidfi._tcp.local.") {
            Ok(rx) => {
                let s = seen.clone();
                let spawned = std::thread::Builder::new()
                    .name("mdns".into())
                    .spawn(move || {
                        while let Ok(ev) = rx.recv() {
                            if let mdns_sd::ServiceEvent::ServiceResolved(info) = ev {
                                let mut m = s.lock().unwrap_or_else(|e| e.into_inner());
                                for a in info.get_addresses_v4() {
                                    m.insert(format!("http://{a}"), Instant::now());
                                }
                                // Bounded: a hostile network cannot grow this forever.
                                if m.len() > 32 {
                                    let now = Instant::now();
                                    m.retain(|_, t| now.duration_since(*t) < MDNS_FORGET);
                                    while m.len() > 32 {
                                        let oldest = m
                                            .iter()
                                            .min_by_key(|(_, t)| **t)
                                            .map(|(k, _)| k.clone());
                                        match oldest {
                                            Some(k) => m.remove(&k),
                                            None => break,
                                        };
                                    }
                                }
                            }
                        }
                    });
                if let Err(e) = spawned {
                    log::warn!("mDNS thread failed to start: {e}");
                }
            }
            Err(e) => log::warn!("mDNS browse failed: {e}"),
        }
        MdnsBrowse {
            seen,
            _daemon: Some(daemon),
        }
    }
}

impl Browse for MdnsBrowse {
    fn boards(&self) -> Vec<String> {
        let now = Instant::now();
        let m = self.seen.lock().unwrap_or_else(|e| e.into_inner());
        let mut v: Vec<(String, Instant)> = m
            .iter()
            .filter(|(_, t)| now.duration_since(**t) < MDNS_FORGET)
            .map(|(k, t)| (k.clone(), *t))
            .collect();
        v.sort_by_key(|e| std::cmp::Reverse(e.1));
        v.into_iter().map(|(k, _)| k).collect()
    }
}
