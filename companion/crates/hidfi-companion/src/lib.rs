//! HID-Fi Companion: the app's service and storage, used by the binary and by
//! the system tests.

pub mod browser;
pub mod menu;
pub mod opener;
#[cfg(any(target_os = "macos", windows))]
pub mod panel;
pub mod service;
pub mod store;
