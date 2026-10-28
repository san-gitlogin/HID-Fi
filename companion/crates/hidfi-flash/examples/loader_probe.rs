//! Diagnostic, for a board already in the ROM loader on <port>: the one register
//! write before and after the stub, timed, then out of the loader. Writes no flash.
use hidfi_flash::flasher::Flasher;
use hidfi_flash::proto::SafeWrite;
use hidfi_flash::serial::Serial;
use hidfi_flash::stub::Stub;
use std::time::Instant;

fn main() {
    let a: Vec<String> = std::env::args().collect();
    let stub_first = a.get(2).map(String::as_str) == Some("stub");
    let mut f = Flasher::new(Serial::open(&a[1], 115_200).expect("open"));
    f.attach_to_loader().unwrap();
    let t = Instant::now();
    println!("sync: {:?} ({} ms)", f.sync(), t.elapsed().as_millis());
    if stub_first {
        let t = Instant::now();
        println!(
            "stub: {:?} ({} ms)",
            f.load_stub(&Stub::esp32s3().unwrap()),
            t.elapsed().as_millis()
        );
    }
    let t = Instant::now();
    println!(
        "clear flag: {:?} ({} ms)",
        f.safe_write(SafeWrite::ClearForceDownload),
        t.elapsed().as_millis()
    );
    println!("reset: {:?}", f.hard_reset());
}
