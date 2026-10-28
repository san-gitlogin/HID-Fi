//! The real serial port, and which ports can be a HID-Fi board's loader.

use std::io::{self, Read, Write};
use std::time::Duration;

use crate::port::Port;

/// The COM port's chip on the boards HID-Fi supports (WCH CH343) - the same rule
/// as tools/flash_ports.py: never guess at another kind of device.
pub const CH343: (u16, u16) = (0x1A86, 0x55D3);
/// The ESP32-S3's own USB-Serial-JTAG - present only while the chip is in its ROM
/// loader (HID-Fi's firmware runs the native port as HID). Its USB serial number
/// is the MAC, "B8:1F:3F:DA:1A:2C".
pub const USB_JTAG: (u16, u16) = (0x303A, 0x1001);

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Kind {
    /// A COM port: which board it belongs to is only known after reading the
    /// MAC from its ROM (which resets the board) - done only for an update the
    /// person asked for.
    Com,
    /// The native port in the ROM loader: the MAC is its serial number.
    Loader { mac: String },
}

#[derive(Debug, Clone)]
pub struct Candidate {
    pub path: String,
    pub kind: Kind,
}

pub fn candidates() -> Vec<Candidate> {
    let Ok(ports) = serialport::available_ports() else { return Vec::new() };
    let mut out = Vec::new();
    for p in ports {
        if let serialport::SerialPortType::UsbPort(u) = &p.port_type {
            if (u.vid, u.pid) == CH343 {
                out.push(Candidate { path: p.port_name.clone(), kind: Kind::Com });
            } else if (u.vid, u.pid) == USB_JTAG {
                let mac = u.serial_number.clone().unwrap_or_default().to_uppercase();
                out.push(Candidate { path: p.port_name.clone(), kind: Kind::Loader { mac } });
            }
        }
    }
    // macOS lists every port twice (tty.* and cu.*); the cu.* one does not wait for carrier.
    out.retain(|c| !c.path.contains("/tty."));
    out
}

pub struct Serial {
    port: Box<dyn serialport::SerialPort>,
}

impl Serial {
    /// Opens with DTR and RTS released first (AGENTS.md: set them before the
    /// open, or the open itself resets the board on some systems).
    pub fn open(path: &str, baud: u32) -> io::Result<Serial> {
        let mut port = serialport::new(path, baud)
            .timeout(Duration::from_millis(50))
            .dtr_on_open(false)
            .open()
            .map_err(io::Error::other)?;
        let _ = port.write_request_to_send(false);
        Ok(Serial { port })
    }
}

impl Port for Serial {
    fn write_all(&mut self, data: &[u8]) -> io::Result<()> {
        self.port.write_all(data)?;
        self.port.flush()
    }

    fn read(&mut self, buf: &mut [u8], timeout_ms: u32) -> io::Result<usize> {
        self.port.set_timeout(Duration::from_millis(timeout_ms.max(1) as u64)).map_err(io::Error::other)?;
        match self.port.read(buf) {
            Ok(n) => Ok(n),
            Err(e) if e.kind() == io::ErrorKind::TimedOut => Ok(0),
            Err(e) => Err(e),
        }
    }

    fn set_dtr(&mut self, on: bool) -> io::Result<()> {
        self.port.write_data_terminal_ready(on).map_err(io::Error::other)
    }

    fn set_rts(&mut self, on: bool) -> io::Result<()> {
        self.port.write_request_to_send(on).map_err(io::Error::other)
    }

    fn set_baud(&mut self, baud: u32) -> io::Result<()> {
        self.port.set_baud_rate(baud).map_err(io::Error::other)
    }

    fn sleep_ms(&mut self, ms: u32) {
        std::thread::sleep(Duration::from_millis(ms as u64));
    }
}
