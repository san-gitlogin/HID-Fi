//! What the flasher needs from a serial port - a real one (serialport, in the
//! Companion) or the simulated ESP32-S3 (sim.rs). Time goes through the port too,
//! so the simulation never sleeps.

use std::io;

pub trait Port {
    fn write_all(&mut self, data: &[u8]) -> io::Result<()>;
    /// Up to buf.len() bytes, waiting at most `timeout_ms` for the first one. 0 = nothing came.
    fn read(&mut self, buf: &mut [u8], timeout_ms: u32) -> io::Result<usize>;
    fn set_dtr(&mut self, on: bool) -> io::Result<()>;
    fn set_rts(&mut self, on: bool) -> io::Result<()>;
    fn set_baud(&mut self, baud: u32) -> io::Result<()>;
    fn sleep_ms(&mut self, ms: u32);
}
