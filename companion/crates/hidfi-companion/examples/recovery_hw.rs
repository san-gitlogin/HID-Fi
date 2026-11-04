//! Recovery mode against a real board (docs/RECOVERY_MODE.md): the real USB listing
//! sees the recovery link, the real worker asks it, and closes it. Run with the board
//! already in recovery mode (BOOT held 8 s). Prints facts, never the WiFi password.
//!
//!     cargo run -p hidfi-companion --example recovery_hw -- [keep]
use hidfi_agent::env::UsbList;
use hidfi_agent::real::NusbList;
use hidfi_companion::recovery::{Op, RealPorts, RecView, Reply, Worker};
use std::sync::{mpsc, Arc, Mutex};
use std::time::{Duration, Instant};

fn main() {
    let keep = std::env::args().any(|a| a == "keep");
    let usb = NusbList;
    let t0 = Instant::now();
    let _ = usb.list();
    let macs = usb.recovery_macs();
    println!("listing took {:?}; boards in recovery mode: {:?}", t0.elapsed(), macs.iter().map(|m| m.hex()).collect::<Vec<_>>());
    let Some(mac) = macs.first().copied() else {
        println!("none - hold BOOT 8 s first");
        std::process::exit(2);
    };
    let state: Arc<Mutex<Vec<RecView>>> = Arc::default();
    let (tx, rx) = mpsc::channel();
    let tx = Mutex::new(tx);
    let w = Worker::start(Box::new(RealPorts), state.clone(), move || {
        let _ = tx.lock().unwrap().send(());
    });
    let t1 = Instant::now();
    w.present(macs.clone());
    let end = Instant::now() + Duration::from_secs(10);
    while Instant::now() < end && !state.lock().unwrap().first().is_some_and(|r| !r.busy) {
        let _ = rx.recv_timeout(Duration::from_millis(100));
    }
    let v = state.lock().unwrap().clone();
    let r = &v[0];
    println!("handshake took {:?}", t1.elapsed());
    match &r.info {
        Some(i) => println!(
            "in recovery: mac {} firmware {} left {}s ssid set {} pin set {} stored {} vault pin {} | fate: {}",
            i.mac, i.firmware, i.left_s, !i.ssid.is_empty(), i.pin_set, i.stored, i.vault_pin, i.vault_fate()
        ),
        None => println!("no summary: {:?}", r.note),
    }
    let (rt, rr) = mpsc::channel();
    let t2 = Instant::now();
    w.act(mac, Op::Reveal, move |x| {
        let _ = rt.send(x);
    });
    match rr.recv_timeout(Duration::from_secs(10)) {
        Ok(Reply::Shown { password, .. }) => println!("reveal: a password came back: {} ({:?})", password.is_some(), t2.elapsed()),
        other => println!("reveal: {other:?}"),
    }
    println!("view holds no password: {}", !format!("{:?}", state.lock().unwrap()).contains("password"));
    if !keep {
        let (ct, cr) = mpsc::channel();
        w.act(mac, Op::Close, move |x| {
            let _ = ct.send(x);
        });
        println!("close: {:?}", cr.recv_timeout(Duration::from_secs(10)));
    }
}
