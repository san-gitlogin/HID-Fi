//! Screen capture on Linux under X11 (Xorg), in pure Rust (x11rb).
//!
//! - XDamage says when the screen changed, so a still desktop costs a poll
//!   and nothing else - no image is read.
//! - The pointer is not in X's image: XFixes gives its picture (premultiplied
//!   ARGB) and position, and it is drawn back in on a copy.
//! - The layout of X's pixels is checked, not assumed (32 bits per pixel,
//!   little-endian), with a clear error otherwise.
//! - Wider than 2560 pixels, the picture is halved.
//! - Wayland: X11 capture would read only XWayland's own windows (a mostly
//!   black screen), so a Wayland session is refused with a message saying so.
//!
//! NOT YET RUN ON LINUX: compiled and checked from macOS only.

use crate::convert::{draw_pointer, halve_bgra, unpremultiply_argb, Pointer};
use crate::frame::Frame;
use crate::pipeline::Source;
use x11rb::connection::Connection;
use x11rb::protocol::damage::{ConnectionExt as _, ReportLevel};
use x11rb::protocol::xfixes::ConnectionExt as _;
use x11rb::protocol::xproto::{ConnectionExt as _, ImageFormat, ImageOrder, Window};
use x11rb::protocol::Event;
use x11rb::rust_connection::RustConnection;

pub const HALVE_ABOVE: usize = 2560;

/// True when the desktop session is Wayland (where X11 capture would be blank).
pub fn on_wayland() -> bool {
    std::env::var("XDG_SESSION_TYPE").is_ok_and(|v| v.eq_ignore_ascii_case("wayland"))
        || std::env::var_os("WAYLAND_DISPLAY").is_some()
}

pub struct X11Source {
    conn: RustConnection,
    root: Window,
    w: usize,
    h: usize,
    damage: u32,
    desk: Vec<u8>,
    comp: Vec<u8>,
    half: Vec<u8>,
    cursor: Vec<u8>,
    cursor_at: (i64, i64, u32, u16, u16),
    have: bool,
}

fn err(what: &str, e: impl std::fmt::Display) -> String {
    format!("{what}: {e}")
}

impl X11Source {
    pub fn start() -> Result<Self, String> {
        if on_wayland() {
            return Err(
                "this is a Wayland desktop; screen sharing on Wayland is not ready yet. \
                        Log in with an X11 (Xorg) session to use it now"
                    .into(),
            );
        }
        let (conn, screen) = x11rb::connect(None)
            .map_err(|e| err("cannot reach the X server (is DISPLAY set?)", e))?;
        let setup = conn.setup();
        if setup.image_byte_order != ImageOrder::LSB_FIRST {
            return Err("unsupported X server: big-endian images".into());
        }
        let s = &setup.roots[screen];
        let depth = s.root_depth;
        let bpp = setup
            .pixmap_formats
            .iter()
            .find(|f| f.depth == depth)
            .map(|f| f.bits_per_pixel);
        if bpp != Some(32) {
            return Err(format!(
                "unsupported X screen: depth {depth}, {bpp:?} bits per pixel (need 32)"
            ));
        }
        let (root, w, h) = (
            s.root,
            s.width_in_pixels as usize,
            s.height_in_pixels as usize,
        );
        conn.damage_query_version(1, 1)
            .map_err(|e| err("XDamage", e))?
            .reply()
            .map_err(|e| err("XDamage", e))?;
        conn.xfixes_query_version(4, 0)
            .map_err(|e| err("XFixes", e))?
            .reply()
            .map_err(|e| err("XFixes", e))?;
        let damage = conn.generate_id().map_err(|e| err("id", e))?;
        conn.damage_create(damage, root, ReportLevel::NON_EMPTY)
            .map_err(|e| err("XDamage", e))?;
        conn.flush().map_err(|e| err("flush", e))?;
        Ok(X11Source {
            conn,
            root,
            w,
            h,
            damage,
            desk: Vec::new(),
            comp: Vec::new(),
            half: Vec::new(),
            cursor: Vec::new(),
            cursor_at: (i64::MIN, 0, 0, 0, 0),
            have: false,
        })
    }

    /// Whether the screen or the pointer changed since the last call.
    fn changed(&mut self) -> Result<bool, String> {
        let mut damaged = false;
        while let Some(ev) = self
            .conn
            .poll_for_event()
            .map_err(|e| err("X connection", e))?
        {
            if matches!(ev, Event::DamageNotify(_)) {
                damaged = true;
            }
        }
        if damaged {
            // Re-arm: report the next change, not this one again.
            self.conn
                .damage_subtract(self.damage, x11rb::NONE, x11rb::NONE)
                .map_err(|e| err("XDamage", e))?;
        }
        let c = self
            .conn
            .xfixes_get_cursor_image()
            .map_err(|e| err("XFixes", e))?
            .reply()
            .map_err(|e| err("XFixes", e))?;
        let at = (
            c.x as i64 - c.xhot as i64,
            c.y as i64 - c.yhot as i64,
            c.cursor_serial,
            c.width,
            c.height,
        );
        let moved = at != self.cursor_at;
        if at.2 != self.cursor_at.2 || at.3 != self.cursor_at.3 || at.4 != self.cursor_at.4 {
            unpremultiply_argb(&c.cursor_image, &mut self.cursor);
        }
        self.cursor_at = at;
        Ok(damaged || moved)
    }

    fn read(&mut self) -> Result<(), String> {
        let img = self
            .conn
            .get_image(
                ImageFormat::Z_PIXMAP,
                self.root,
                0,
                0,
                self.w as u16,
                self.h as u16,
                !0,
            )
            .map_err(|e| err("GetImage", e))?
            .reply()
            .map_err(|e| err("GetImage", e))?;
        if img.data.len() < self.w * self.h * 4 {
            return Err("short image from the X server".into());
        }
        self.desk.clear();
        self.desk
            .extend_from_slice(&img.data[..self.w * self.h * 4]);
        self.have = true;
        Ok(())
    }
}

impl Source for X11Source {
    fn size(&self) -> (u32, u32) {
        if self.w > HALVE_ABOVE {
            ((self.w / 2) as u32, (self.h / 2) as u32)
        } else {
            (self.w as u32, self.h as u32)
        }
    }

    fn grab(&mut self, force: bool, f: &mut dyn FnMut(&Frame)) -> Result<bool, String> {
        let changed = self.changed()?;
        if !(changed || force || !self.have) {
            return Ok(false);
        }
        if changed || !self.have {
            self.read()?;
        }
        self.comp.clear();
        self.comp.extend_from_slice(&self.desk);
        // X's padding byte is not alpha; make it opaque for the encoder.
        for px in self.comp.as_chunks_mut::<4>().0 {
            px[3] = 255;
        }
        let (x, y, _, cw, ch) = self.cursor_at;
        if !self.cursor.is_empty() && self.cursor.len() == cw as usize * ch as usize * 4 {
            let p = Pointer::Color {
                w: cw as usize,
                h: ch as usize,
                pitch: cw as usize * 4,
                px: &self.cursor,
            };
            draw_pointer(&mut self.comp, self.w * 4, self.w, self.h, &p, x, y);
        }
        if self.w > HALVE_ABOVE {
            let comp = std::mem::take(&mut self.comp);
            let (nw, nh) = halve_bgra(&comp, self.w * 4, self.w, self.h, &mut self.half);
            self.comp = comp;
            f(&Frame {
                width: nw as u32,
                height: nh as u32,
                stride: nw * 4,
                bgra: &self.half,
            });
        } else {
            f(&Frame {
                width: self.w as u32,
                height: self.h as u32,
                stride: self.w * 4,
                bgra: &self.comp,
            });
        }
        Ok(true)
    }
}
