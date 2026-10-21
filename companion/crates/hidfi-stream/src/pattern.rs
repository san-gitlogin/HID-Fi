//! A synthetic screen: a moving bar over a grid, for tests and for checking a
//! viewer end to end without screen-recording permission. `still()` makes it
//! a static screen, to prove an unchanged picture costs nothing. `busy()`
//! makes it a page of small text scrolling by - as hard to compress as a real
//! desktop gets, where the grid alike would fit in any link and hide every
//! bandwidth problem.

use crate::frame::Frame;
use crate::pipeline::Source;

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
