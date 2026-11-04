//! Recovery mode (docs/RECOVERY_MODE.md): a board whose BOOT was held for 8
//! seconds restarts once with a serial link on its own USB, beside HID, for 5
//! minutes. Through it - and only through it, or a COM cable - a forgotten WiFi
//! password can be shown or replaced and the access PIN reset: holding the board is
//! what proves ownership. The same steps, words and safeguards as tools/recover.py.
//!
//! The link is opened for one action and closed again: another tool (the recovery
//! script) may want it, and the serial library opens ports exclusively. The board's
//! USB serial only answers a host that has raised DTR, so it is raised after the
//! open (a COM port is never opened here: there DTR held is BOOT pressed).
//!
//! Nothing here keeps a secret: the WiFi password is read only when the person asks
//! to see it, handed to the caller, and not logged or stored.

use hidfi_core::discovery::Mac;
use hidfi_flash::port::Port;
use serde_json::{json, Value};
use std::time::{Duration, Instant};

/// A running HID-Fi's own USB, and the ROM's USB-Serial-JTAG: told apart by asking.
pub const ESP_NATIVE: (u16, u16) = (0x303A, 0x1001);
/// How long recovery mode stays open after its last command (RECOVERY_IDLE_MS).
pub const WINDOW: Duration = Duration::from_secs(300);

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum RecError {
    /// The port could not be opened (in use, gone, or no permission).
    Open(String),
    /// Nothing answered in time - recovery mode may have just closed.
    NoAnswer,
    /// It answered, but it is not a board in recovery mode.
    NotInRecovery,
    /// The board refused, with its own message.
    Refused(String),
    /// What the person typed cannot be used; nothing was sent.
    Invalid(&'static str),
    /// Resetting the PIN would erase this many stored passwords: ask first.
    WouldErase(usize),
    /// The board answered but did not take it: nothing changed.
    NotTaken,
}

impl RecError {
    /// What the window says - the same words as tools/recover.py where it has them.
    pub fn text(&self) -> String {
        match self {
            RecError::Open(e) if cfg!(target_os = "linux") && e.to_lowercase().contains("permission") => {
                "This computer will not let the Companion open the board's USB serial port. Add yourself to the \
                 'dialout' group (sudo usermod -aG dialout $USER), log out and in, then try again."
                    .into()
            }
            RecError::Open(e) => format!("Could not open the board's recovery link: {e}. Close anything else using it and try again."),
            RecError::NoAnswer => "The board did not answer. Recovery mode may have just closed: hold BOOT 8 s again.".into(),
            RecError::NotInRecovery => "This board is not in recovery mode.".into(),
            RecError::Refused(m) => format!("The board refused: {m}"),
            RecError::Invalid(m) => (*m).into(),
            RecError::WouldErase(n) => format!(
                "{n} stored password(s) WILL BE ERASED - they were guarded by the access PIN. Type ERASE to go ahead."
            ),
            RecError::NotTaken => "The board did not take it. Nothing changed.".into(),
        }
    }
}

/// One line-based JSON session over a port, as the COM port speaks it.
pub struct Link<P: Port> {
    port: P,
}

impl<P: Port> Link<P> {
    /// The port must be open; DTR is raised here (and left raised until it closes).
    pub fn new(mut port: P) -> Result<Link<P>, RecError> {
        port.set_dtr(true).map_err(|e| RecError::Open(e.to_string()))?;
        port.sleep_ms(200);
        let mut junk = [0u8; 512];
        while port.read(&mut junk, 1).unwrap_or(0) > 0 {}
        Ok(Link { port })
    }

    /// Sends one command; returns the first reply (not an event).
    pub fn cmd(&mut self, body: &Value, timeout: Duration) -> Result<Value, RecError> {
        let mut line = body.to_string();
        line.push('\n');
        self.port.write_all(line.as_bytes()).map_err(|e| RecError::Open(e.to_string()))?;
        let end = Instant::now() + timeout;
        let mut buf = Vec::new();
        let mut chunk = [0u8; 512];
        while Instant::now() < end {
            let n = self.port.read(&mut chunk, 50).map_err(|e| RecError::Open(e.to_string()))?;
            buf.extend_from_slice(&chunk[..n]);
            while let Some(i) = buf.iter().position(|&b| b == b'\n') {
                let l: Vec<u8> = buf.drain(..=i).collect();
                let l = String::from_utf8_lossy(&l);
                let l = l.trim();
                if !l.starts_with('{') {
                    continue;
                }
                if let Ok(v) = serde_json::from_str::<Value>(l) {
                    if v.get("status").is_some() && v.get("event").is_none() {
                        return Ok(v);
                    }
                }
            }
        }
        Err(RecError::NoAnswer)
    }

    /// Sends a command the board does not answer (ap_configure restarts its WiFi).
    fn send(&mut self, body: &Value) -> Result<(), RecError> {
        let mut line = body.to_string();
        line.push('\n');
        self.port.write_all(line.as_bytes()).map_err(|e| RecError::Open(e.to_string()))
    }

    fn sleep(&mut self, ms: u32) {
        self.port.sleep_ms(ms);
    }
}

const T: Duration = Duration::from_secs(6);

fn ok(v: Value) -> Result<Value, RecError> {
    if v["status"] == "ok" {
        Ok(v)
    } else {
        Err(RecError::Refused(v["message"].as_str().unwrap_or("no reason given").to_string()))
    }
}

/// What the window shows about a board in recovery mode: never a secret.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Info {
    pub mac: String,
    pub firmware: String,
    pub left_s: u64,
    pub ssid: String,
    pub open: bool,
    pub generated: bool,
    pub pin_set: bool,
    pub stored: usize,
    pub vault_pin: bool,
}

impl Info {
    /// The same words as tools/recover.py's vault_fate().
    pub fn vault_fate(&self) -> String {
        match (self.stored, self.vault_pin) {
            (0, _) => "nothing stored, nothing to lose".into(),
            (n, true) => format!("{n} stored password(s) kept - the vault has its own vault PIN"),
            (n, false) => format!("{n} stored password(s) WILL BE ERASED - they were guarded by the access PIN"),
        }
    }
}

/// The handshake and the summary: proves it is a board in recovery mode.
pub fn info<P: Port>(link: &mut Link<P>) -> Result<Info, RecError> {
    let st = link.cmd(&json!({"cmd": "status"}), Duration::from_millis(1500))?;
    if st["recovery"] != true {
        return Err(RecError::NotInRecovery);
    }
    let ap = ok(link.cmd(&json!({"cmd": "ap_password"}), T)?)?;
    let vl = ok(link.cmd(&json!({"cmd": "vault_list"}), T)?)?;
    Ok(Info {
        mac: st["mac"].as_str().unwrap_or("").replace(':', "").to_uppercase(),
        firmware: st["firmware"].as_str().unwrap_or("").to_string(),
        left_s: st["recovery_left_s"].as_u64().unwrap_or(0),
        ssid: ap["ssid"].as_str().unwrap_or("").to_string(),
        open: ap["open"] == true,
        generated: ap["generated"] == true,
        pin_set: st["auth_set"] == true,
        stored: vl["entries"].as_array().map_or(0, |a| a.len()),
        vault_pin: vl["vault_pin_set"] == true,
    })
}

/// The WiFi name and password - only when the person asked to see them.
pub fn reveal<P: Port>(link: &mut Link<P>) -> Result<(String, Option<String>), RecError> {
    let ap = ok(link.cmd(&json!({"cmd": "ap_password"}), T)?)?;
    let ssid = ap["ssid"].as_str().unwrap_or("").to_string();
    if ap["open"] == true {
        return Ok((ssid, None));
    }
    Ok((ssid, Some(ap["password"].as_str().unwrap_or("").to_string())))
}

/// A new WiFi password, read back afterwards (as tools/recover.py does).
pub fn set_password<P: Port>(link: &mut Link<P>, new: &str) -> Result<String, RecError> {
    if !(8..=63).contains(&new.len()) {
        return Err(RecError::Invalid("A WiFi password needs 8 to 63 characters. Nothing changed."));
    }
    let ap = ok(link.cmd(&json!({"cmd": "ap_password"}), T)?)?;
    let ssid = ap["ssid"].as_str().unwrap_or("").to_string();
    link.send(&json!({"cmd": "ap_configure", "ssid": ssid, "password": new, "open": false}))?;
    link.sleep(1500);
    let again = link.cmd(&json!({"cmd": "ap_password"}), T)?;
    if again["password"].as_str() != Some(new) {
        return Err(RecError::NotTaken);
    }
    Ok(ssid)
}

/// The access PIN: four digits, or "" to turn it off. An unguarded vault is never
/// erased unless `erase_ok` (the person typed ERASE).
pub fn reset_pin<P: Port>(link: &mut Link<P>, pin: &str, erase_ok: bool) -> Result<bool, RecError> {
    if !pin.is_empty() && !(pin.len() == 4 && pin.bytes().all(|b| b.is_ascii_digit())) {
        return Err(RecError::Invalid("A PIN is four digits. Nothing changed."));
    }
    let vl = ok(link.cmd(&json!({"cmd": "vault_list"}), T)?)?;
    let n = vl["entries"].as_array().map_or(0, |a| a.len());
    if n > 0 && vl["vault_pin_set"] != true && !erase_ok {
        return Err(RecError::WouldErase(n));
    }
    let r = ok(link.cmd(&json!({"cmd": "set_auth", "token": pin}), T)?)?;
    let st = link.cmd(&json!({"cmd": "status"}), T)?;
    if (st["auth_set"] == true) != !pin.is_empty() {
        return Err(RecError::NotTaken);
    }
    Ok(r["vault_wiped"] == true)
}

/// Closes recovery mode: the board restarts normally.
pub fn close<P: Port>(link: &mut Link<P>) -> Result<(), RecError> {
    ok(link.cmd(&json!({"cmd": "recovery_exit"}), T)?).map(|_| ())
}

/// The board's recovery link, by its MAC: an Espressif native port whose USB serial
/// number is the MAC (and not the ROM's USB-Serial-JTAG). Whether it really is in
/// recovery mode is only known by asking (`info`).
pub fn find_port(mac: &Mac) -> Option<String> {
    let ports = serialport::available_ports().ok()?;
    ports.into_iter().find_map(|p| match &p.port_type {
        serialport::SerialPortType::UsbPort(u)
            if (u.vid, u.pid) == ESP_NATIVE
                && !u.product.as_deref().unwrap_or("").to_lowercase().contains("jtag")
                && u.serial_number.as_deref().and_then(Mac::parse).as_ref() == Some(mac)
                && !p.port_name.contains("/tty.") =>
        {
            Some(p.port_name.clone())
        }
        _ => None,
    })
}

/// Opens the board's recovery link for one action.
pub fn open(path: &str) -> Result<Link<hidfi_flash::serial::Serial>, RecError> {
    let port = hidfi_flash::serial::Serial::open(path, 115_200).map_err(|e| RecError::Open(e.to_string()))?;
    Link::new(port)
}

// ---- the worker: all serial I/O on its own thread, never the service loop ----

/// What the window shows about one board in recovery mode. Never a secret.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct RecView {
    pub mac: String,
    /// The handshake's summary, once it answered.
    pub info: Option<Info>,
    /// When the board closes recovery mode by itself (5 minutes after its last command).
    pub closes: Option<Instant>,
    /// An action is running.
    pub busy: bool,
    /// The last thing to say: (good, text). Never a password.
    pub note: Option<(bool, String)>,
}

/// What the person asked for, on one board.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Op {
    Reveal,
    SetPassword(String),
    /// Four digits, or "" to turn the PIN off; `erase`: the person typed ERASE.
    ResetPin { pin: String, erase: bool },
    Close,
}

/// The answer to an action - the WiFi password only for `Reveal`, and only to the caller.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Reply {
    Shown { ssid: String, password: Option<String> },
    Done(String),
    Failed(String),
}

/// How the worker reaches ports: the real ones, or a fake in tests.
pub trait Ports: Send {
    fn find(&self, mac: &Mac) -> Option<String>;
    fn open(&self, path: &str) -> Result<Link<DynPort>, RecError>;
}

/// A port of any kind (the real serial port, or a fake).
pub struct DynPort(pub Box<dyn Port + Send>);

impl Port for DynPort {
    fn write_all(&mut self, d: &[u8]) -> std::io::Result<()> {
        self.0.write_all(d)
    }
    fn read(&mut self, b: &mut [u8], t: u32) -> std::io::Result<usize> {
        self.0.read(b, t)
    }
    fn set_dtr(&mut self, on: bool) -> std::io::Result<()> {
        self.0.set_dtr(on)
    }
    fn set_rts(&mut self, on: bool) -> std::io::Result<()> {
        self.0.set_rts(on)
    }
    fn set_baud(&mut self, b: u32) -> std::io::Result<()> {
        self.0.set_baud(b)
    }
    fn sleep_ms(&mut self, ms: u32) {
        self.0.sleep_ms(ms)
    }
}

pub struct RealPorts;

impl Ports for RealPorts {
    fn find(&self, mac: &Mac) -> Option<String> {
        find_port(mac)
    }
    fn open(&self, path: &str) -> Result<Link<DynPort>, RecError> {
        let port = hidfi_flash::serial::Serial::open(path, 115_200).map_err(|e| RecError::Open(e.to_string()))?;
        Link::new(DynPort(Box::new(port)))
    }
}

type Done = Box<dyn FnOnce(Reply) + Send>;

enum Msg {
    /// The boards with a recovery link on this computer's USB now.
    Present(Vec<Mac>),
    Do(Mac, Op, Done),
}

/// The handle the service and the window hold.
#[derive(Clone)]
pub struct Worker {
    tx: std::sync::mpsc::Sender<Msg>,
}

impl Worker {
    /// `state` is the window's view of recovery mode; `changed` is called after each change.
    pub fn start(
        ports: Box<dyn Ports>,
        state: std::sync::Arc<std::sync::Mutex<Vec<RecView>>>,
        changed: impl Fn() + Send + 'static,
    ) -> Worker {
        let (tx, rx) = std::sync::mpsc::channel::<Msg>();
        std::thread::Builder::new()
            .name("recovery".into())
            .spawn(move || run(ports, rx, state, changed))
            .expect("recovery thread");
        Worker { tx }
    }

    pub fn present(&self, macs: Vec<Mac>) {
        let _ = self.tx.send(Msg::Present(macs));
    }

    pub fn act(&self, mac: Mac, op: Op, done: impl FnOnce(Reply) + Send + 'static) {
        let _ = self.tx.send(Msg::Do(mac, op, Box::new(done)));
    }
}

fn run(
    ports: Box<dyn Ports>,
    rx: std::sync::mpsc::Receiver<Msg>,
    state: std::sync::Arc<std::sync::Mutex<Vec<RecView>>>,
    changed: impl Fn(),
) {
    let lock = |f: &mut dyn FnMut(&mut Vec<RecView>)| {
        f(&mut state.lock().unwrap_or_else(|e| e.into_inner()));
        changed();
    };
    let mut present: Vec<Mac> = Vec::new();
    while let Ok(msg) = rx.recv() {
        match msg {
            Msg::Present(now) => {
                if now == present {
                    continue;
                }
                lock(&mut |v| v.retain(|r| now.iter().any(|m| m.hex() == r.mac)));
                for m in now.iter().filter(|m| !present.contains(m)) {
                    // A new link: ask it once what it is (a board in recovery, or not).
                    lock(&mut |v| v.push(RecView { mac: m.hex(), info: None, closes: None, busy: true, note: None }));
                    let r = with_link(ports.as_ref(), m, |l| info(l));
                    lock(&mut |v| {
                        if let Some(rv) = v.iter_mut().find(|r| r.mac == m.hex()) {
                            rv.busy = false;
                            match &r {
                                Ok(i) => {
                                    rv.closes = Some(Instant::now() + Duration::from_secs(i.left_s));
                                    rv.info = Some(i.clone());
                                }
                                Err(e) => rv.note = Some((false, e.text())),
                            }
                        }
                    });
                }
                present = now;
            }
            Msg::Do(m, op, done) => {
                lock(&mut |v| {
                    if let Some(rv) = v.iter_mut().find(|r| r.mac == m.hex()) {
                        rv.busy = true;
                        rv.note = None;
                    }
                });
                let reply = match with_link(ports.as_ref(), &m, |l| perform(l, &op)) {
                    Ok(r) => r,
                    Err(e) => Reply::Failed(e.text()),
                };
                let note = match &reply {
                    Reply::Shown { .. } => None,
                    Reply::Done(t) => Some((true, t.clone())),
                    Reply::Failed(t) => Some((false, t.clone())),
                };
                let refreshed = (op != Op::Close).then(|| with_link(ports.as_ref(), &m, |l| info(l)).ok()).flatten();
                lock(&mut |v| {
                    if let Some(rv) = v.iter_mut().find(|r| r.mac == m.hex()) {
                        rv.busy = false;
                        rv.note = note.clone();
                        if let Some(i) = &refreshed {
                            rv.closes = Some(Instant::now() + Duration::from_secs(i.left_s));
                            rv.info = Some(i.clone());
                        }
                    }
                });
                done(reply);
            }
        }
    }
}

/// Opens the link for one job and closes it again (another tool may want it).
fn with_link<T>(
    ports: &dyn Ports,
    mac: &Mac,
    job: impl FnOnce(&mut Link<DynPort>) -> Result<T, RecError>,
) -> Result<T, RecError> {
    let path = ports.find(mac).ok_or(RecError::NoAnswer)?;
    let mut link = ports.open(&path)?;
    job(&mut link)
}

fn perform(l: &mut Link<DynPort>, op: &Op) -> Result<Reply, RecError> {
    Ok(match op {
        Op::Reveal => {
            let (ssid, password) = reveal(l)?;
            Reply::Shown { ssid, password }
        }
        Op::SetPassword(p) => {
            let ssid = set_password(l, p)?;
            Reply::Done(format!("Done. Join '{ssid}' with the new password. Phones on it now will drop off."))
        }
        Op::ResetPin { pin, erase } => {
            let wiped = reset_pin(l, pin, *erase)?;
            let mut t = if pin.is_empty() {
                "Access PIN turned off.".to_string()
            } else {
                "Access PIN set. Phones will ask for it on their next connection.".to_string()
            };
            if wiped {
                t.push_str(" The stored passwords were erased, as warned.");
            }
            Reply::Done(t)
        }
        Op::Close => {
            close(l)?;
            Reply::Done("Recovery mode closed: the board restarts normally.".into())
        }
    })
}

// ---- the page's messages: one parser and one reply for every transport ----

/// A recovery action from the window: `rec:{"mac":"DCB4D909F7C8","op":"reveal"}`,
/// `"set_password","value":...`, `"reset_pin","value":"1357","erase":false`, `"close"`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RecMsg {
    pub mac: Mac,
    pub op: Op,
}

/// Anything not exactly that is ignored: short, a known op, what the person typed
/// within the limits the board accepts (checked again before anything is sent).
pub fn parse_msg(msg: &str) -> Option<RecMsg> {
    let j = msg.strip_prefix("rec:")?;
    if j.len() > 256 {
        return None;
    }
    let v: Value = serde_json::from_str(j).ok()?;
    let mac = Mac::parse(v["mac"].as_str()?)?;
    let text = |k: &str| v[k].as_str().map(str::to_string);
    let op = match v["op"].as_str()? {
        "reveal" => Op::Reveal,
        "close" => Op::Close,
        "set_password" => Op::SetPassword(text("value").filter(|p| p.len() <= 63)?),
        "reset_pin" => Op::ResetPin {
            pin: text("value").filter(|p| p.len() <= 4)?,
            erase: v["erase"].as_bool().unwrap_or(false),
        },
        _ => return None,
    };
    Some(RecMsg { mac, op })
}

/// Only for a board in recovery mode now, that answered, and is not mid-action.
pub fn admit(views: &[RecView], m: &RecMsg) -> bool {
    views.iter().any(|r| r.mac == m.mac.hex() && r.info.is_some() && !r.busy)
}

/// What goes back to the page that asked (and only to it).
pub fn reply_json(m: &RecMsg, r: &Reply) -> String {
    let op = match m.op {
        Op::Reveal => "reveal",
        Op::SetPassword(_) => "set_password",
        Op::ResetPin { .. } => "reset_pin",
        Op::Close => "close",
    };
    let mac = m.mac.hex();
    match r {
        Reply::Shown { ssid, password } => {
            json!({"mac": mac, "op": op, "ok": true, "ssid": ssid, "password": password, "open": password.is_none()})
        }
        Reply::Done(t) => json!({"mac": mac, "op": op, "ok": true, "text": t}),
        Reply::Failed(t) => json!({"mac": mac, "op": op, "ok": false, "text": t}),
    }
    .to_string()
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::VecDeque;
    use std::io;

    /// A board in recovery mode, answering as the firmware does (board_sim.py).
    struct FakeBoard {
        recovery: bool,
        pin: String,
        ap_pass: String,
        open_ap: bool,
        stored: usize,
        vault_pin: bool,
        dtr: bool,
        out: VecDeque<u8>,
        inbuf: Vec<u8>,
        seen: Vec<String>,
        ignore_ap_configure: bool,
    }

    impl FakeBoard {
        fn new() -> FakeBoard {
            FakeBoard {
                recovery: true,
                pin: "4826".into(),
                ap_pass: "k7m2-x9qp-4tre".into(),
                open_ap: false,
                stored: 0,
                vault_pin: false,
                dtr: false,
                out: VecDeque::new(),
                inbuf: Vec::new(),
                seen: Vec::new(),
                ignore_ap_configure: false,
            }
        }
        fn answer(&mut self, v: Value) {
            if !self.dtr {
                return; // the board's CDC only writes to a host that raised DTR
            }
            self.out.extend(format!("{v}\n").bytes());
        }
        fn handle(&mut self, line: &str) {
            let b: Value = serde_json::from_str(line).unwrap();
            let c = b["cmd"].as_str().unwrap_or("").to_string();
            self.seen.push(c.clone());
            let r = match c.as_str() {
                "status" => json!({"status": "ok", "reply": "status", "recovery": self.recovery,
                                   "recovery_left_s": 287, "mac": "DC:B4:D9:09:F7:C8",
                                   "firmware": "hid_fi_v4.3", "auth_set": !self.pin.is_empty()}),
                "ap_password" => json!({"status": "ok", "reply": "ap_password", "ssid": "ESP32-HID-09F7C8",
                                        "password": if self.open_ap { "" } else { &self.ap_pass },
                                        "open": self.open_ap, "generated": true}),
                "vault_list" => json!({"status": "ok", "reply": "vault_list", "vault_pin_set": self.vault_pin,
                                       "entries": (0..self.stored).map(|i| json!({"slot": i})).collect::<Vec<_>>()}),
                "ap_configure" => {
                    if !self.ignore_ap_configure {
                        self.ap_pass = b["password"].as_str().unwrap_or("").into();
                    }
                    return; // restarts the access point: no answer, by design
                }
                "set_auth" => {
                    let wiped = !self.vault_pin && self.stored > 0;
                    self.pin = b["token"].as_str().unwrap_or("").into();
                    if wiped {
                        self.stored = 0;
                    }
                    json!({"status": "ok", "reply": "set_auth", "vault_wiped": wiped})
                }
                "recovery_exit" => {
                    if !self.recovery {
                        json!({"status": "error", "reply": "not_in_recovery", "message": "Recovery mode is not open"})
                    } else {
                        self.recovery = false;
                        json!({"status": "ok", "reply": "recovery_exit", "restart": true})
                    }
                }
                _ => json!({"status": "error", "message": "Unknown command", "cmd": c}),
            };
            self.out.extend(b"{\"event\":\"recovery\",\"state\":\"ready\"}\n".iter()); // events are skipped
            self.answer(r);
        }
    }

    impl Port for &mut FakeBoard {
        fn write_all(&mut self, data: &[u8]) -> io::Result<()> {
            self.inbuf.extend_from_slice(data);
            while let Some(i) = self.inbuf.iter().position(|&b| b == b'\n') {
                let l: Vec<u8> = self.inbuf.drain(..=i).collect();
                let l = String::from_utf8_lossy(&l).trim().to_string();
                self.handle(&l);
            }
            Ok(())
        }
        fn read(&mut self, buf: &mut [u8], _t: u32) -> io::Result<usize> {
            let n = buf.len().min(self.out.len());
            for b in buf.iter_mut().take(n) {
                *b = self.out.pop_front().unwrap();
            }
            Ok(n)
        }
        fn set_dtr(&mut self, on: bool) -> io::Result<()> {
            self.dtr = on;
            Ok(())
        }
        fn set_rts(&mut self, _on: bool) -> io::Result<()> {
            Ok(())
        }
        fn set_baud(&mut self, _b: u32) -> io::Result<()> {
            Ok(())
        }
        fn sleep_ms(&mut self, _ms: u32) {}
    }

    #[test]
    fn dtr_is_raised_so_the_board_answers() {
        let mut b = FakeBoard::new();
        let mut l = Link::new(&mut b).unwrap();
        assert!(info(&mut l).is_ok());
        assert!(b.dtr);
    }

    #[test]
    fn info_proves_recovery_mode_and_holds_no_secret() {
        let mut b = FakeBoard::new();
        b.stored = 2;
        let i = info(&mut Link::new(&mut b).unwrap()).unwrap();
        assert_eq!((i.mac.as_str(), i.left_s, i.pin_set, i.stored), ("DCB4D909F7C8", 287, true, 2));
        assert!(!format!("{i:?}").contains("k7m2"), "the summary must not carry the password");
        assert!(i.vault_fate().contains("WILL BE ERASED"));
    }

    #[test]
    fn a_board_not_in_recovery_is_refused() {
        let mut b = FakeBoard::new();
        b.recovery = false;
        assert_eq!(info(&mut Link::new(&mut b).unwrap()), Err(RecError::NotInRecovery));
    }

    #[test]
    fn reveal_shows_the_password_or_says_open() {
        let mut b = FakeBoard::new();
        assert_eq!(reveal(&mut Link::new(&mut b).unwrap()).unwrap().1.as_deref(), Some("k7m2-x9qp-4tre"));
        b.open_ap = true;
        assert_eq!(reveal(&mut Link::new(&mut b).unwrap()).unwrap().1, None);
    }

    #[test]
    fn a_new_password_is_set_and_read_back() {
        let mut b = FakeBoard::new();
        assert!(set_password(&mut Link::new(&mut b).unwrap(), "a-new-pass-1").is_ok());
        assert_eq!(b.ap_pass, "a-new-pass-1");
    }

    #[test]
    fn a_password_the_radio_cannot_use_is_never_sent() {
        for bad in ["short", &"x".repeat(64)] {
            let mut b = FakeBoard::new();
            assert!(matches!(set_password(&mut Link::new(&mut b).unwrap(), bad), Err(RecError::Invalid(_))));
            assert!(!b.seen.contains(&"ap_configure".to_string()));
        }
    }

    #[test]
    fn a_password_the_board_did_not_take_says_so() {
        let mut b = FakeBoard::new();
        b.ignore_ap_configure = true;
        assert_eq!(set_password(&mut Link::new(&mut b).unwrap(), "a-new-pass-1"), Err(RecError::NotTaken));
    }

    #[test]
    fn the_pin_is_reset_keeping_a_guarded_vault() {
        let mut b = FakeBoard::new();
        b.stored = 1;
        b.vault_pin = true;
        assert_eq!(reset_pin(&mut Link::new(&mut b).unwrap(), "1357", false), Ok(false));
        assert_eq!((b.pin.as_str(), b.stored), ("1357", 1));
    }

    #[test]
    fn an_unguarded_vault_is_never_erased_without_erase() {
        let mut b = FakeBoard::new();
        b.stored = 3;
        assert_eq!(reset_pin(&mut Link::new(&mut b).unwrap(), "1357", false), Err(RecError::WouldErase(3)));
        assert_eq!((b.pin.as_str(), b.stored), ("4826", 3));
        assert!(!b.seen.contains(&"set_auth".to_string()));
        assert_eq!(reset_pin(&mut Link::new(&mut b).unwrap(), "1357", true), Ok(true));
        assert_eq!((b.pin.as_str(), b.stored), ("1357", 0));
    }

    #[test]
    fn the_pin_can_be_turned_off() {
        let mut b = FakeBoard::new();
        assert_eq!(reset_pin(&mut Link::new(&mut b).unwrap(), "", false), Ok(false));
        assert_eq!(b.pin, "");
    }

    #[test]
    fn a_pin_that_is_not_four_digits_is_never_sent() {
        for bad in ["12", "12345", "12a4"] {
            let mut b = FakeBoard::new();
            assert!(matches!(reset_pin(&mut Link::new(&mut b).unwrap(), bad, true), Err(RecError::Invalid(_))));
            assert!(b.seen.is_empty(), "nothing is sent for {bad}");
        }
    }

    #[test]
    fn close_ends_recovery_mode() {
        let mut b = FakeBoard::new();
        assert!(close(&mut Link::new(&mut b).unwrap()).is_ok());
        assert!(!b.recovery);
        assert!(matches!(close(&mut Link::new(&mut b).unwrap()), Err(RecError::Refused(_))));
    }

    // ---- the worker, with a fake board on a fake port ----
    use std::sync::{mpsc, Arc, Mutex};

    struct Shared(Arc<Mutex<FakeBoard>>);
    impl Port for Shared {
        fn write_all(&mut self, d: &[u8]) -> io::Result<()> {
            (&mut *self.0.lock().unwrap()).write_all(d)
        }
        fn read(&mut self, b: &mut [u8], t: u32) -> io::Result<usize> {
            (&mut *self.0.lock().unwrap()).read(b, t)
        }
        fn set_dtr(&mut self, on: bool) -> io::Result<()> {
            self.0.lock().unwrap().dtr = on;
            Ok(())
        }
        fn set_rts(&mut self, _: bool) -> io::Result<()> {
            Ok(())
        }
        fn set_baud(&mut self, _: u32) -> io::Result<()> {
            Ok(())
        }
        fn sleep_ms(&mut self, _: u32) {}
    }

    struct FakePorts {
        board: Arc<Mutex<FakeBoard>>,
        here: Arc<Mutex<bool>>,
    }
    impl Ports for FakePorts {
        fn find(&self, _m: &Mac) -> Option<String> {
            (*self.here.lock().unwrap()).then(|| "/dev/fake".into())
        }
        fn open(&self, _p: &str) -> Result<Link<DynPort>, RecError> {
            self.board.lock().unwrap().dtr = false; // each open starts with DTR down
            Link::new(DynPort(Box::new(Shared(self.board.clone()))))
        }
    }

    fn worker(b: FakeBoard) -> (Worker, Arc<Mutex<Vec<RecView>>>, Arc<Mutex<FakeBoard>>, Arc<Mutex<bool>>, mpsc::Receiver<()>) {
        let board = Arc::new(Mutex::new(b));
        let here = Arc::new(Mutex::new(true));
        let state = Arc::new(Mutex::new(Vec::new()));
        let (tx, rx) = mpsc::channel();
        let tx = Mutex::new(tx);
        let w = Worker::start(Box::new(FakePorts { board: board.clone(), here: here.clone() }), state.clone(), move || {
            let _ = tx.lock().unwrap().send(());
        });
        (w, state, board, here, rx)
    }

    fn mac() -> Mac {
        Mac::parse("DCB4D909F7C8").unwrap()
    }

    fn settle(state: &Arc<Mutex<Vec<RecView>>>, rx: &mpsc::Receiver<()>, ok: impl Fn(&Vec<RecView>) -> bool) {
        let end = Instant::now() + Duration::from_secs(5);
        while Instant::now() < end {
            if ok(&state.lock().unwrap()) {
                return;
            }
            let _ = rx.recv_timeout(Duration::from_millis(100));
        }
        panic!("never settled: {:?}", state.lock().unwrap());
    }

    fn act(w: &Worker, op: Op) -> Reply {
        let (tx, rx) = mpsc::channel();
        w.act(mac(), op, move |r| {
            let _ = tx.send(r);
        });
        rx.recv_timeout(Duration::from_secs(10)).expect("a reply")
    }

    #[test]
    fn a_board_in_recovery_shows_up_with_its_summary_and_no_secret() {
        let (w, state, _b, _h, rx) = worker(FakeBoard::new());
        w.present(vec![mac()]);
        settle(&state, &rx, |v| v.first().is_some_and(|r| r.info.is_some() && !r.busy));
        let v = state.lock().unwrap();
        assert!(v[0].closes.is_some_and(|c| c > Instant::now() + Duration::from_secs(280)));
        assert!(!format!("{v:?}").contains("k7m2"), "the view must never hold the password");
    }

    #[test]
    fn a_link_that_is_not_in_recovery_says_so() {
        let mut b = FakeBoard::new();
        b.recovery = false;
        let (w, state, _b, _h, rx) = worker(b);
        w.present(vec![mac()]);
        settle(&state, &rx, |v| v.first().is_some_and(|r| r.note.is_some()));
        assert_eq!(state.lock().unwrap()[0].note, Some((false, RecError::NotInRecovery.text())));
    }

    #[test]
    fn the_password_goes_only_to_the_caller() {
        let (w, state, _b, _h, rx) = worker(FakeBoard::new());
        w.present(vec![mac()]);
        settle(&state, &rx, |v| v.first().is_some_and(|r| r.info.is_some()));
        assert_eq!(act(&w, Op::Reveal), Reply::Shown { ssid: "ESP32-HID-09F7C8".into(), password: Some("k7m2-x9qp-4tre".into()) });
        assert!(!format!("{:?}", state.lock().unwrap()).contains("k7m2"));
    }

    #[test]
    fn an_unguarded_vault_needs_erase_typed() {
        let mut b = FakeBoard::new();
        b.stored = 2;
        let (w, state, board, _h, rx) = worker(b);
        w.present(vec![mac()]);
        settle(&state, &rx, |v| v.first().is_some_and(|r| r.info.is_some()));
        let r = act(&w, Op::ResetPin { pin: "1357".into(), erase: false });
        assert_eq!(r, Reply::Failed(RecError::WouldErase(2).text()));
        assert_eq!(board.lock().unwrap().pin, "4826");
        assert!(matches!(act(&w, Op::ResetPin { pin: "1357".into(), erase: true }), Reply::Done(t) if t.contains("erased, as warned")));
        assert_eq!(board.lock().unwrap().stored, 0);
    }

    #[test]
    fn a_new_wifi_password_and_close() {
        let (w, state, board, _h, rx) = worker(FakeBoard::new());
        w.present(vec![mac()]);
        settle(&state, &rx, |v| v.first().is_some_and(|r| r.info.is_some()));
        assert!(matches!(act(&w, Op::SetPassword("a-new-pass-1".into())), Reply::Done(_)));
        assert_eq!(board.lock().unwrap().ap_pass, "a-new-pass-1");
        assert!(matches!(act(&w, Op::Close), Reply::Done(t) if t.contains("closed")));
        assert!(!board.lock().unwrap().recovery);
    }

    #[test]
    fn a_link_that_went_away_says_so_and_leaving_drops_the_board() {
        let (w, state, _b, here, rx) = worker(FakeBoard::new());
        w.present(vec![mac()]);
        settle(&state, &rx, |v| v.first().is_some_and(|r| r.info.is_some()));
        *here.lock().unwrap() = false;
        assert_eq!(act(&w, Op::Reveal), Reply::Failed(RecError::NoAnswer.text()));
        w.present(vec![]);
        settle(&state, &rx, |v| v.is_empty());
    }

    #[test]
    fn page_messages_are_parsed_strictly() {
        let m = parse_msg(r#"rec:{"mac":"DCB4D909F7C8","op":"reveal"}"#).unwrap();
        assert_eq!((m.mac.hex(), m.op), ("DCB4D909F7C8".to_string(), Op::Reveal));
        let m = parse_msg(r#"rec:{"mac":"dc:b4:d9:09:f7:c8","op":"reset_pin","value":"1357","erase":true}"#).unwrap();
        assert_eq!(m.op, Op::ResetPin { pin: "1357".into(), erase: true });
        for bad in [
            "act:open:DCB4D909F7C8",
            r#"rec:{"mac":"nope","op":"reveal"}"#,
            r#"rec:{"mac":"DCB4D909F7C8","op":"erase_everything"}"#,
            r#"rec:{"mac":"DCB4D909F7C8","op":"set_password"}"#,
            r#"rec:{"mac":"DCB4D909F7C8","op":"reset_pin","value":"123456"}"#,
            "rec:not json",
        ] {
            assert_eq!(parse_msg(bad), None, "{bad}");
        }
        let long = format!(r#"rec:{{"mac":"DCB4D909F7C8","op":"set_password","value":"{}"}}"#, "x".repeat(64));
        assert_eq!(parse_msg(&long), None, "longer than the radio takes");
    }

    #[test]
    fn only_a_board_in_recovery_that_answered_and_is_idle_is_admitted() {
        let m = parse_msg(r#"rec:{"mac":"DCB4D909F7C8","op":"close"}"#).unwrap();
        let info = info(&mut Link::new(&mut FakeBoard::new()).unwrap()).unwrap();
        let rv = |busy, info: Option<Info>| RecView { mac: "DCB4D909F7C8".into(), info, closes: None, busy, note: None };
        assert!(admit(&[rv(false, Some(info.clone()))], &m));
        assert!(!admit(&[rv(true, Some(info.clone()))], &m), "mid-action");
        assert!(!admit(&[rv(false, None)], &m), "never answered");
        assert!(!admit(&[], &m), "not in recovery");
    }

    #[test]
    fn the_reply_carries_the_password_only_for_reveal() {
        let m = parse_msg(r#"rec:{"mac":"DCB4D909F7C8","op":"reveal"}"#).unwrap();
        let j: Value = serde_json::from_str(&reply_json(&m, &Reply::Shown { ssid: "S".into(), password: Some("pw-123456".into()) })).unwrap();
        assert_eq!((j["ok"].as_bool(), j["password"].as_str()), (Some(true), Some("pw-123456")));
        let c = parse_msg(r#"rec:{"mac":"DCB4D909F7C8","op":"close"}"#).unwrap();
        assert!(!reply_json(&c, &Reply::Done("closed".into())).contains("password"));
    }

    #[test]
    fn errors_say_what_to_do() {
        assert!(RecError::NoAnswer.text().contains("hold BOOT 8 s again"));
        assert!(RecError::WouldErase(2).text().contains("Type ERASE"));
    }
}
