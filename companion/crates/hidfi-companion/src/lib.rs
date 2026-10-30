//! HID-Fi Companion: the app's service and storage, used by the binary and by
//! the system tests.

pub mod browser;
pub mod handover;
pub mod health;
pub mod menu;
pub mod opener;
#[cfg(any(target_os = "macos", windows))]
pub mod panel;
#[cfg(target_os = "macos")]
pub mod permissions;
pub mod service;
pub mod soundcheck;
pub mod store;
pub mod updates;
pub mod window;
