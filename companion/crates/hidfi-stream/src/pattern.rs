//! A synthetic screen: a moving bar over a grid, for tests and for checking a
//! viewer end to end without screen-recording permission. `still()` makes it
//! a static screen, to prove an unchanged picture costs nothing.

use crate::frame::Frame;
use crate::pipeline::Source;

pub struct Pattern {
    w: u32,
    h: u32,
    buf: Vec<u8>,
    t: u32,
    moving: bool,
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
            drawn: false,
        }
    }
    pub fn still(mut self) -> Self {
        self.moving = false;
        self
    }
    fn draw(&mut self) {
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
