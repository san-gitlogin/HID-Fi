//! One viewer: a WebRTC peer (str0m, ICE-lite) sending H.264 to a browser.
//!
//! Sans-IO: this file owns the UDP socket and the clock. A reader thread turns
//! datagrams into messages on the same channel the pipeline sends frames on,
//! so the session thread sleeps on one channel and wakes for either - no busy
//! polling while streaming, nothing at all when the channel is quiet.

use crate::pipeline::{Control, ToViewer};
use std::net::{SocketAddr, UdpSocket};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::mpsc::{Receiver, RecvTimeoutError, SyncSender};
use std::sync::Arc;
use std::time::{Duration, Instant};
use str0m::bwe::BweKind;
use str0m::change::{SdpAnswer, SdpOffer};
use str0m::format::Codec;
use str0m::media::{Frequency, MediaKind, MediaTime, Mid};
use str0m::net::{Protocol, Receive};
use str0m::{Candidate, Event, IceConnectionState, Input, Output, Rtc, RtcConfig};

pub use str0m::crypto::dtls::DtlsCert;

/// Everything a viewer session needs from the outside.
pub enum ToSession {
    Media(ToViewer),
    Udp(Vec<u8>, SocketAddr),
}

impl From<ToViewer> for ToSession {
    fn from(v: ToViewer) -> Self {
        ToSession::Media(v)
    }
}

#[derive(Debug, thiserror::Error)]
pub enum RtcSetupError {
    #[error("the offer could not be read")]
    BadOffer,
    #[error("the offer has no H.264 video this app can send")]
    NoH264,
    #[error("network: {0}")]
    Io(#[from] std::io::Error),
    #[error("webrtc: {0}")]
    Rtc(String),
}

/// A session that has answered the offer and is ready to run.
pub struct Prepared {
    rtc: Rtc,
    socket: UdpSocket,
    pub answer: String,
}

/// Lowercase hex SHA-256 of the certificate: the `fp` the board publishes, and
/// what the dashboard checks the answer's `a=fingerprint` against.
pub fn cert_fp(cert: &DtlsCert) -> String {
    use hidfi_core::sha256_hex;
    sha256_hex(&cert.certificate)
}

/// A new certificate, made once per install and kept, so `fp` is stable.
pub fn new_cert() -> Option<DtlsCert> {
    str0m::crypto::from_feature_flags()
        .dtls_provider
        .generate_certificate()
}

/// Answer `offer`, listening for media on `local_ip` - the address the viewer
/// already reached us on, which is therefore one it can reach.
pub fn prepare(
    offer: &str,
    local_ip: std::net::IpAddr,
    cert: DtlsCert,
) -> Result<Prepared, RtcSetupError> {
    let offer = SdpOffer::from_sdp_string(offer).map_err(|_| RtcSetupError::BadOffer)?;
    let mut rtc = RtcConfig::new()
        .set_ice_lite(true)
        .set_dtls_cert(cert)
        .clear_codecs()
        .enable_h264(true)
        .enable_bwe(Some(str0m::bwe::Bitrate::mbps(3)))
        .build(Instant::now());
    let socket = bind_media(local_ip)?;
    let addr = socket.local_addr()?;
    let cand = Candidate::host(addr, "udp").map_err(|e| RtcSetupError::Rtc(e.to_string()))?;
    rtc.add_local_candidate(cand);
    let answer: SdpAnswer = rtc
        .sdp_api()
        .accept_offer(offer)
        .map_err(|e| RtcSetupError::Rtc(e.to_string()))?;
    Ok(Prepared {
        rtc,
        socket,
        answer: answer.to_sdp_string(),
    })
}

/// Media ports, fixed so a firewall that allows ports (Linux's ufw and
/// firewalld) can allow exactly these: UDP 47820-47859, next to the viewers'
/// TCP 47800-47819. One per viewer; 40 leaves room for sessions still closing.
pub const MEDIA_PORTS: std::ops::RangeInclusive<u16> = 47820..=47859;

fn bind_media(ip: std::net::IpAddr) -> std::io::Result<UdpSocket> {
    for port in MEDIA_PORTS {
        if let Ok(s) = UdpSocket::bind(SocketAddr::new(ip, port)) {
            return Ok(s);
        }
    }
    Err(std::io::Error::new(
        std::io::ErrorKind::AddrInUse,
        "no free media port in 47820-47859",
    ))
}

/// What ended a session, for the log and the dashboard.
#[derive(Debug, PartialEq, Eq)]
pub enum End {
    Stopped,
    Disconnected,
    NeverConnected,
    PipelineGone,
    Failed(String),
}

/// How long a viewer may take to connect before the session gives up.
pub const CONNECT_DEADLINE: Duration = Duration::from_secs(20);

/// What the bandwidth estimator probes towards: twice what the link carries
/// now, within these bounds, so it climbs step by step. Without a target it
/// never probed, and an estimate that fell (a moment of WiFi trouble) never
/// climbed back. A fixed high target was no better on a poor link: its probe
/// bursts overflowed the router's queue and crashed the estimate again.
pub const DESIRED_MIN_BPS: u64 = 1_500_000;
pub const DESIRED_MAX_BPS: u64 = 8_000_000;

fn desired(estimate: u64) -> u64 {
    (estimate * 2).clamp(DESIRED_MIN_BPS, DESIRED_MAX_BPS)
}

/// A viewer's own pace: a safety valve, not the rate control. The encoder
/// already aims at this viewer's link (its quality follows the slowest viewer
/// on it), so a burst - a keyframe, a sudden scroll - is let through and paid
/// for afterwards. Only a viewer a full second behind loses frames (and waits
/// for a keyframe), instead of piling up delay in the sender. Dropping at the
/// first overshoot broke the picture into keyframe after keyframe: 1 fps.
struct Pace {
    bps: f64,
    tokens: f64,
    at: Instant,
}

impl Pace {
    /// Half a second of sending, at most, in hand.
    const BURST_S: f64 = 0.5;

    fn new(bps: u64, now: Instant) -> Pace {
        Pace {
            bps: bps as f64,
            tokens: bps as f64 / 8.0 * Self::BURST_S,
            at: now,
        }
    }
    fn set_rate(&mut self, bps: u64) {
        self.bps = bps as f64;
    }
    /// Whether a frame of `len` bytes may go now. A keyframe always may (the
    /// picture cannot repair without one): the debt it leaves holds back the
    /// frames after it instead.
    fn admit(&mut self, len: usize, keyframe: bool, now: Instant) -> bool {
        let dt = now.saturating_duration_since(self.at).as_secs_f64();
        self.at = now;
        let second = self.bps / 8.0;
        self.tokens = (self.tokens + second * dt).min(second * Self::BURST_S);
        if self.tokens < -second && !keyframe {
            return false;
        }
        self.tokens -= len as f64;
        true
    }
}

/// Runs until the viewer goes away or `stop` is set. `tx` is the sender half
/// of `rx`, given to the UDP reader thread.
pub fn run(
    prepared: Prepared,
    rx: Receiver<ToSession>,
    tx: SyncSender<ToSession>,
    control: Control,
    stop: Arc<AtomicBool>,
    mut on_stats: impl FnMut(u32),
) -> End {
    let Prepared {
        mut rtc, socket, ..
    } = prepared;
    let local = match socket.local_addr() {
        Ok(a) => a,
        Err(e) => return End::Failed(e.to_string()),
    };
    let reader = match socket.try_clone() {
        Ok(s) => s,
        Err(e) => return End::Failed(e.to_string()),
    };
    let reader_stop = stop.clone();
    let _ = reader.set_read_timeout(Some(Duration::from_millis(500)));
    let reader_thread = std::thread::Builder::new()
        .name("rtc-udp".into())
        .spawn(move || {
            let mut buf = vec![0u8; 2048];
            while !reader_stop.load(Ordering::Relaxed) {
                match reader.recv_from(&mut buf) {
                    Ok((n, from)) => {
                        if tx.send(ToSession::Udp(buf[..n].to_vec(), from)).is_err() {
                            break;
                        }
                    }
                    Err(e)
                        if matches!(
                            e.kind(),
                            std::io::ErrorKind::WouldBlock | std::io::ErrorKind::TimedOut
                        ) => {}
                    Err(_) => break,
                }
            }
        });
    let started = Instant::now();
    let mut connected = false;
    let mut video: Option<(Mid, str0m::format::PayloadParams)> = None;
    let mut sent_frames = 0u32;
    let mut stats_at = Instant::now();
    let mut pace = Pace::new(3_000_000, Instant::now());
    let mut waiting_key = true;
    let (mut log_at, mut log_sent, mut log_held, mut log_bytes, mut estimate) =
        (Instant::now(), 0u32, 0u32, 0u64, 0u64);
    let end = loop {
        if stop.load(Ordering::Relaxed) {
            break End::Stopped;
        }
        if !connected && started.elapsed() > CONNECT_DEADLINE {
            break End::NeverConnected;
        }
        let deadline = match rtc.poll_output() {
            Ok(Output::Timeout(t)) => t,
            Ok(Output::Transmit(t)) => {
                let _ = socket.send_to(&t.contents, t.destination);
                continue;
            }
            Ok(Output::Event(ev)) => {
                match ev {
                    Event::Connected => {
                        connected = true;
                        rtc.bwe()
                            .set_desired_bitrate(str0m::bwe::Bitrate::bps(desired(3_000_000)));
                        control.joined();
                    }
                    Event::IceConnectionStateChange(IceConnectionState::Disconnected) => {
                        break End::Disconnected
                    }
                    Event::MediaAdded(m) if m.kind == MediaKind::Video => {
                        let params = rtc.writer(m.mid).and_then(|w| {
                            // Packetization mode 1 (fragmented NAL units) and a
                            // baseline-family profile: what the encoder makes.
                            w.payload_params()
                                .find(|p| {
                                    let s = p.spec();
                                    s.codec == Codec::H264
                                        && s.format.packetization_mode == Some(1)
                                        && s.format
                                            .profile_level_id
                                            .is_some_and(|id| id >> 16 == 0x42)
                                })
                                .cloned()
                        });
                        match params {
                            Some(p) => video = Some((m.mid, p)),
                            None => break End::Failed("no H.264 baseline in the offer".into()),
                        }
                    }
                    Event::KeyframeRequest(_) => control.request_keyframe(),
                    Event::EgressBitrateEstimate(BweKind::Twcc { estimate: b, .. }) => {
                        // Leave headroom: the estimate is the ceiling, not the target.
                        estimate = b.as_u64();
                        control.offer_bitrate((estimate as f64 * 0.85) as u32);
                        pace.set_rate(estimate);
                        rtc.bwe()
                            .set_desired_bitrate(str0m::bwe::Bitrate::bps(desired(estimate)));
                    }
                    _ => {}
                }
                continue;
            }
            Err(e) => break End::Failed(e.to_string()),
        };
        let wait = deadline
            .saturating_duration_since(Instant::now())
            .min(Duration::from_millis(500));
        let msg = match rx.recv_timeout(wait) {
            Ok(m) => m,
            Err(RecvTimeoutError::Timeout) => {
                let _ = rtc.handle_input(Input::Timeout(Instant::now()));
                continue;
            }
            Err(RecvTimeoutError::Disconnected) => break End::PipelineGone,
        };
        match msg {
            ToSession::Udp(data, from) => {
                let Ok(contents) = data.as_slice().try_into() else {
                    continue;
                };
                let r = Receive {
                    proto: Protocol::Udp,
                    source: from,
                    destination: local,
                    contents,
                };
                if let Err(e) = rtc.handle_input(Input::Receive(Instant::now(), r)) {
                    log::debug!("dropped a packet: {e}");
                }
            }
            ToSession::Media(ToViewer::Frame(frame, pts_ms)) => {
                let (Some((mid, params)), true) = (&video, connected) else {
                    continue;
                };
                // A gap before this frame (dropped on its way here) breaks the
                // picture until a keyframe: skip what cannot be decoded.
                if control.needs_key() && !frame.keyframe {
                    waiting_key = true;
                }
                if waiting_key && !frame.keyframe {
                    log_held += 1;
                    continue;
                }
                if !pace.admit(frame.data.len(), frame.keyframe, Instant::now()) {
                    waiting_key = true;
                    control.request_keyframe();
                    log_held += 1;
                    continue;
                }
                waiting_key = false;
                let pt = params.pt();
                let Some(w) = rtc.writer(*mid) else { continue };
                let t = MediaTime::new(pts_ms * 90, Frequency::NINETY_KHZ);
                if let Err(e) = w.write(pt, Instant::now(), t, frame.data.clone()) {
                    break End::Failed(e.to_string());
                }
                sent_frames += 1;
                log_sent += 1;
                log_bytes += frame.data.len() as u64;
            }
        }
        if stats_at.elapsed() >= Duration::from_secs(1) {
            on_stats(sent_frames);
            sent_frames = 0;
            stats_at = Instant::now();
        }
        if log_at.elapsed() >= Duration::from_secs(10) {
            let secs = log_at.elapsed().as_secs_f64();
            log::info!(
                "viewer {}: {:.1} fps, {} kbit/s, link estimate {} kbit/s, {} frame(s) held back",
                local.port(),
                log_sent as f64 / secs,
                (log_bytes as f64 * 8.0 / secs / 1000.0) as u64,
                estimate / 1000,
                log_held
            );
            (log_at, log_sent, log_held, log_bytes) = (Instant::now(), 0, 0, 0);
        }
    };
    stop.store(true, Ordering::Relaxed);
    rtc.disconnect();
    if let Ok(t) = reader_thread {
        let _ = t.join();
    }
    end
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_probe_target_climbs_with_the_link_within_bounds() {
        assert_eq!(desired(300_000), DESIRED_MIN_BPS);
        assert_eq!(desired(1_400_000), 2_800_000);
        assert_eq!(desired(20_000_000), DESIRED_MAX_BPS);
    }

    #[test]
    fn pace_lets_a_link_take_what_it_can_and_skips_the_rest() {
        let t0 = Instant::now();
        let mut p = Pace::new(1_000_000, t0); // 125 kB/s, 62.5 kB in hand
                                              // 30 fps of 10 kB frames = 300 kB/s: more than twice the link.
        let mut sent = 0;
        for i in 0..90 {
            let now = t0 + Duration::from_millis(i * 33);
            sent += p.admit(10_000, false, now) as u32;
        }
        // 3 s: 62.5 kB in hand + 375 kB earned + 125 kB of debt allowed: ~56.
        assert!((50..=62).contains(&sent), "{sent} of 90 frames sent");
    }

    #[test]
    fn a_keyframe_always_goes_and_the_frames_after_it_pay_for_it() {
        let t0 = Instant::now();
        let mut p = Pace::new(1_000_000, t0);
        assert!(
            p.admit(300_000, true, t0),
            "a keyframe bigger than the budget still goes"
        );
        assert!(
            !p.admit(5_000, false, t0 + Duration::from_millis(33)),
            "then the debt holds frames back"
        );
        // 237.5 kB of debt, 125 kB of it allowed, earned back at 125 kB/s: ~0.9 s.
        assert!(p.admit(5_000, false, t0 + Duration::from_millis(1000)));
    }

    #[test]
    fn media_ports_come_from_the_documented_range_and_do_not_collide() {
        let ip: std::net::IpAddr = "127.0.0.1".parse().unwrap();
        let a = bind_media(ip).unwrap();
        let b = bind_media(ip).unwrap();
        let (pa, pb) = (
            a.local_addr().unwrap().port(),
            b.local_addr().unwrap().port(),
        );
        assert!(MEDIA_PORTS.contains(&pa) && MEDIA_PORTS.contains(&pb));
        assert_ne!(pa, pb);
    }
}
