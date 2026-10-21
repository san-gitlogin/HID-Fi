//! The outside world, behind traits, so the agent's logic runs in tests against
//! fakes - every network failure can be staged - and against the real thing in
//! the app. Real implementations live in `real.rs`.

use hidfi_core::discovery::UsbDevice;
use std::time::Instant;

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum NetError {
    /// Nothing answered: no route, refused, DNS failed, timed out.
    Unreachable(String),
    /// Something answered with a body too large to be a board.
    TooLarge,
}

impl std::fmt::Display for NetError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            NetError::Unreachable(why) => write!(f, "unreachable ({why})"),
            NetError::TooLarge => f.write_str("reply too large"),
        }
    }
}

/// Plain HTTP to the board. Every call is bounded in time and in size; a reply
/// with any HTTP status is a reply (the board refuses with 4xx and a JSON body).
pub trait Http: Send + Sync {
    fn get(&self, url: &str) -> Result<(u16, String), NetError>;
    fn post_json(&self, url: &str, body: &str) -> Result<(u16, String), NetError>;
}

pub trait UsbList: Send + Sync {
    /// Devices on this computer's USB. An OS error is an empty list: the agent
    /// then reports "no board plugged in", which is what the user can act on.
    fn list(&self) -> Vec<UsbDevice>;
}

pub trait Browse: Send + Sync {
    /// Base URLs (`http://a.b.c.d`) of HID-Fi boards seen by mDNS recently.
    fn boards(&self) -> Vec<String>;
}

pub trait Clock: Send + Sync {
    fn now(&self) -> Instant;
}

pub struct SystemClock;
impl Clock for SystemClock {
    fn now(&self) -> Instant {
        Instant::now()
    }
}
