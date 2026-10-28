//! Read-only check against a real board: cargo run -p hidfi-flash --features serial
//! --example preflight -- <port> <MAC like B8:1F:3F:DA:1A:2C>. Enters the ROM loader,
//! reads, restarts the board. Writes nothing; prints identifiers and hashes only.
use hidfi_flash::flasher::Flasher;
use hidfi_flash::serial::Serial;
use hidfi_flash::update::{self, Connect};
use md5::{Digest, Md5};

fn main() {
    let a: Vec<String> = std::env::args().collect();
    let mac: Vec<u8> = a[2].split(':').map(|h| u8::from_str_radix(h, 16).unwrap()).collect();
    let mac: [u8; 6] = mac.try_into().unwrap();
    let mut f = Flasher::new(Serial::open(&a[1], 115_200).expect("open"));
    let t = std::time::Instant::now();
    match update::preflight(&mut f, &mac, Connect::Reset) {
        Ok(found) => {
            println!("ok in {:.1}s: layout {:?}, running slot {}, version {:?}", t.elapsed().as_secs_f32(),
                     found.layout, found.running_slot, found.running_version);
            println!("table: {:?}", found.table.as_ref().map(|t| t.iter().map(|p| (p.name.clone(), p.offset)).collect::<Vec<_>>()));
            println!("backup 0x0-0xFFFF md5 {}", Md5::digest(&found.backup).iter().map(|b| format!("{b:02x}")).collect::<String>());
        }
        Err(e) => println!("preflight failed: {e}"),
    }
    f.hard_reset().expect("reset");
    println!("restarted");
}
