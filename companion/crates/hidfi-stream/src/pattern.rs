//! A synthetic screen: a moving bar over a grid, for tests and for checking a
//! viewer end to end without screen-recording permission. `still()` makes it
//! a static screen, to prove an unchanged picture costs nothing. `busy()`
//! makes it a page of small text scrolling by - as hard to compress as a real
//! desktop gets, where the grid alike would fit in any link and hide every
//! bandwidth problem.

use crate::display::Display;
use crate::frame::Frame;
use crate::pipeline::Source;
use std::sync::{Arc, Mutex};

pub struct Pattern {
    w: u32,
    h: u32,
    buf: Vec<u8>,
    t: u32,
    moving: bool,
    busy: bool,
    drawn: bool,
}

impl Pattern {
    pub fn new(w: u32, h: u32) -> Self {
        Pattern {
            w,
            h,
            buf: vec![0; (w * h * 4) as usize],
            t: 0,
            moving: true,
            busy: false,
            drawn: false,
        }
    }
    pub fn still(mut self) -> Self {
        self.moving = false;
        self
    }
    pub fn busy(mut self) -> Self {
        self.busy = true;
        self
    }
    /// Text-like: 6x10 glyph cells of hashed dots, light on dark, scrolling.
    fn draw_busy(&mut self) {
        let (w, h) = (self.w, self.h);
        let scroll = self.t * 3;
        for y in 0..h {
            let row = (y + scroll) / 10;
            let gy = (y + scroll) % 10;
            for x in 0..w {
                let (col, gx) = (x / 6, x % 6);
                let mut v = (row.wrapping_mul(0x9E37_79B9) ^ col.wrapping_mul(0x85EB_CA6B)).rotate_left(13);
                v ^= v >> 15;
                let blank = v.is_multiple_of(7) || col % 40 > 34; // spaces and a margin
                let on = !blank && gx < 5 && gy < 8 && (v.rotate_left(gx * 5 + gy) & 1) == 1;
                let i = ((y * w + x) * 4) as usize;
                let (b, g, r) = if on { (230, 230, 220) } else { (25, 17, 13) };
                self.buf[i] = b;
                self.buf[i + 1] = g;
                self.buf[i + 2] = r;
                self.buf[i + 3] = 255;
            }
        }
    }
    fn draw(&mut self) {
        if self.busy {
            return self.draw_busy();
        }
        let (w, h) = (self.w, self.h);
        let bar = (self.t * 8) % w;
        for y in 0..h {
            for x in 0..w {
                let i = ((y * w + x) * 4) as usize;
                let grid = x % 64 == 0 || y % 64 == 0;
                let on_bar = x >= bar && x < bar + 40;
                let (b, g, r) = if on_bar {
                    (255, 210, 0) // the dashboard's accent, #00d2ff
                } else if grid {
                    (60, 50, 40)
                } else {
                    (25, 17, 13)
                };
                self.buf[i] = b;
                self.buf[i + 1] = g;
                self.buf[i + 2] = r;
                self.buf[i + 3] = 255;
            }
        }
    }
}

impl Source for Pattern {
    fn size(&self) -> (u32, u32) {
        (self.w, self.h)
    }
    fn grab(&mut self, force: bool, f: &mut dyn FnMut(&Frame)) -> Result<bool, String> {
        let changed = self.moving || !self.drawn;
        if changed {
            self.t = self.t.wrapping_add(1);
            self.draw();
            self.drawn = true;
        }
        if changed || force {
            f(&Frame {
                width: self.w,
                height: self.h,
                stride: (self.w * 4) as usize,
                bgra: &self.buf,
            });
            return Ok(true);
        }
        Ok(false)
    }
}

/// Pretend displays, for tests: `HIDFI_DISPLAYS=n` gives n of them (main
/// first, at `w`x`h`; then 1280x1024, then a portrait 1080x1920...), each
/// streaming a pattern of its own size. `unplug(id)` takes one away: it leaves
/// the list, and capturing it fails, as a monitor pulled out does.
#[derive(Clone)]
pub struct FakeDisplays(Arc<Mutex<Vec<Display>>>);

impl FakeDisplays {
    pub fn from_env(w: u32, h: u32) -> FakeDisplays {
        let n: usize = std::env::var("HIDFI_DISPLAYS").ok().and_then(|s| s.parse().ok()).unwrap_or(1);
        let sizes = [(w, h), (1280, 1024), (1080, 1920), (1600, 900)];
        let v = (0..n.clamp(1, 4))
            .map(|i| Display {
                id: i as u32 + 1,
                name: if i == 0 { "Built-in display".into() } else { format!("Display {}", i + 1) },
                width: sizes[i].0,
                height: sizes[i].1,
                main: i == 0,
            })
            .collect();
        FakeDisplays(Arc::new(Mutex::new(v)))
    }
    pub fn list(&self) -> Vec<Display> {
        self.0.lock().unwrap_or_else(|e| e.into_inner()).clone()
    }
    pub fn unplug(&self, id: u32) {
        self.0.lock().unwrap_or_else(|e| e.into_inner()).retain(|d| d.id != id || d.main);
    }
    /// A moving pattern at the display's size; `busy` for scrolling text.
    pub fn source(&self, display: Option<u32>, busy: bool) -> Result<Box<dyn Source>, String> {
        let l = self.list();
        let d = match display {
            None => l.iter().find(|d| d.main),
            Some(id) => l.iter().find(|d| d.id == id),
        }
        .ok_or("no such display")?;
        let p = Pattern::new(d.width, d.height);
        Ok(Box::new(if busy { p.busy() } else { p }))
    }
}
