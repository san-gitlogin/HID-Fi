//! HID-Fi: updating a board over the cable (docs/OTA.md, "Over the cable").
//!
//! - [`proto`]: the serial loader protocol, closed: nothing here can write a
//!   register (eFuses), erase a whole chip or erase outside a checked write.
//! - [`flasher`]: the client - every write read back by MD5.
//! - [`update`]: the two update paths, the journal, cancel and resume.
//! - [`sim`]: a simulated ESP32-S3 the fault sweep (tests) runs everything against.

pub mod flasher;
pub mod image;
pub mod port;
pub mod proto;
pub mod sim;
pub mod slip;
pub mod stub;
pub mod update;

#[cfg(test)]
mod sweep;
