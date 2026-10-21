//! The displays a viewer can choose from, on each OS.
//!
//! - macOS: CoreGraphics' active displays (no permission needed to list them);
//!   the id is the CGDirectDisplayID ScreenCaptureKit captures by.
//! - Windows: every output of every graphics card - on a laptop with two GPUs
//!   an external monitor often hangs off the other one, and Desktop
//!   Duplication works only through the card that owns the output. The id is
//!   `card << 8 | output`.
//! - Linux (X11): RandR monitors; the id is the monitor's index, and the
//!   capture is that monitor's part of the X screen.
//!
//! The main display is listed first. Listing never fails: an error is an empty
//! list, and the main display is always there to fall back to.

use serde::Serialize;

#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
pub struct Display {
    pub id: u32,
    pub name: String,
    pub width: u32,
    pub height: u32,
    pub main: bool,
}

/// Names a display that has no name of its own: "Display 2" and so on.
fn numbered(i: usize) -> String {
    format!("Display {}", i + 1)
}

/// Main first, the rest in the order the OS gave them. With none flagged main
/// (an X server without a primary monitor), the first is.
fn main_first(mut v: Vec<Display>) -> Vec<Display> {
    v.sort_by_key(|d| !d.main);
    if !v.iter().any(|d| d.main) {
        if let Some(d) = v.first_mut() {
            d.main = true;
        }
    }
    v
}

#[cfg(target_os = "macos")]
pub fn list() -> Vec<Display> {
    use std::ffi::c_void;
    #[link(name = "CoreGraphics", kind = "framework")]
    extern "C" {
        fn CGGetActiveDisplayList(max: u32, ids: *mut u32, count: *mut u32) -> i32;
        fn CGMainDisplayID() -> u32;
        fn CGDisplayIsBuiltin(id: u32) -> u32;
        fn CGDisplayCopyDisplayMode(id: u32) -> *mut c_void;
        fn CGDisplayModeGetPixelWidth(mode: *mut c_void) -> usize;
        fn CGDisplayModeGetPixelHeight(mode: *mut c_void) -> usize;
        fn CGDisplayModeRelease(mode: *mut c_void);
    }
    let mut ids = [0u32; 16];
    let mut n = 0u32;
    // SAFETY: a buffer of 16 ids and its length; CoreGraphics fills at most that.
    if unsafe { CGGetActiveDisplayList(16, ids.as_mut_ptr(), &mut n) } != 0 {
        return Vec::new();
    }
    // SAFETY: no arguments.
    let main = unsafe { CGMainDisplayID() };
    let mut out = Vec::new();
    for (i, &id) in ids.iter().take(n as usize).enumerate() {
        // SAFETY: a display id the system just listed; the mode is released.
        let (w, h, builtin) = unsafe {
            let m = CGDisplayCopyDisplayMode(id);
            let (w, h) = if m.is_null() {
                (0, 0)
            } else {
                let s = (CGDisplayModeGetPixelWidth(m), CGDisplayModeGetPixelHeight(m));
                CGDisplayModeRelease(m);
                s
            };
            (w as u32, h as u32, CGDisplayIsBuiltin(id) != 0)
        };
        out.push(Display {
            id,
            name: if builtin { "Built-in display".into() } else { numbered(i) },
            width: w,
            height: h,
            main: id == main,
        });
    }
    main_first(out)
}

#[cfg(windows)]
pub fn list() -> Vec<Display> {
    use windows::Win32::Graphics::Dxgi::{CreateDXGIFactory1, IDXGIFactory1};
    // SAFETY: COM calls on live objects; descriptors are plain data.
    unsafe {
        let Ok(f) = CreateDXGIFactory1::<IDXGIFactory1>() else {
            return Vec::new();
        };
        let mut out = Vec::new();
        let mut a = 0u32;
        while let Ok(adapter) = f.EnumAdapters1(a) {
            let mut o = 0u32;
            while let Ok(output) = adapter.EnumOutputs(o) {
                if let Ok(d) = output.GetDesc() {
                    if d.AttachedToDesktop.as_bool() {
                        let r = d.DesktopCoordinates;
                        let dev = String::from_utf16_lossy(&d.DeviceName);
                        let dev = dev.trim_end_matches('\0');
                        // "\\.\DISPLAY2" -> "Display 2", as Windows Settings names it.
                        let name = dev
                            .rsplit("DISPLAY")
                            .next()
                            .filter(|n| !n.is_empty() && n.chars().all(|c| c.is_ascii_digit()))
                            .map(|n| format!("Display {n}"))
                            .unwrap_or_else(|| numbered(out.len()));
                        out.push(Display {
                            id: a << 8 | o,
                            name,
                            width: (r.right - r.left).max(0) as u32,
                            height: (r.bottom - r.top).max(0) as u32,
                            main: r.left == 0 && r.top == 0,
                        });
                    }
                }
                o += 1;
            }
            a += 1;
        }
        main_first(out)
    }
}

#[cfg(target_os = "linux")]
pub fn list() -> Vec<Display> {
    crate::x11::monitors()
        .map(|m| {
            main_first(
                m.into_iter()
                    .enumerate()
                    .map(|(i, m)| Display {
                        id: i as u32,
                        name: if m.name.is_empty() { numbered(i) } else { m.name },
                        width: m.w as u32,
                        height: m.h as u32,
                        main: m.primary,
                    })
                    .collect(),
            )
        })
        .unwrap_or_default()
}

#[cfg(not(any(target_os = "macos", windows, target_os = "linux")))]
pub fn list() -> Vec<Display> {
    Vec::new()
}

/// The main display's id in `list`, if it has one.
pub fn main_id(list: &[Display]) -> Option<u32> {
    list.iter().find(|d| d.main).or(list.first()).map(|d| d.id)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn d(id: u32, main: bool) -> Display {
        Display { id, name: numbered(id as usize), width: 1920, height: 1080, main }
    }

    #[test]
    fn the_main_display_comes_first() {
        let v = main_first(vec![d(1, false), d(2, true), d(3, false)]);
        assert_eq!(v.iter().map(|x| x.id).collect::<Vec<_>>(), [2, 1, 3]);
        assert_eq!(main_id(&v), Some(2));
        assert_eq!(main_id(&[d(5, false)]), Some(5), "no main flagged: the first");
        assert!(main_first(vec![d(7, false), d(8, false)])[0].main, "and it is called main");
        assert_eq!(main_id(&[]), None);
    }

    /// Opt-in, at a Mac: run with --ignored to see this computer's displays.
    #[test]
    #[ignore]
    fn lists_this_computers_displays() {
        let v = list();
        eprintln!("{v:#?}");
        assert!(!v.is_empty() && v[0].main);
    }
}
