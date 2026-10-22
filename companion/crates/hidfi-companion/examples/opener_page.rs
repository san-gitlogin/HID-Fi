//! The "Open the dashboard" page for companion/tests/open_dashboard.py:
//! `opener_page URL [URL ...]` prints the page's address, prints "settings"
//! each time its button is pressed, and stops when stdin closes.
use hidfi_companion::opener::Opener;
use std::io::{BufRead, Write};

fn main() {
    let o = Opener::start(Box::new(|| {
        println!("settings");
        let _ = std::io::stdout().flush();
    }))
    .expect("start");
    println!("{}", o.page_for(std::env::args().skip(1).collect()));
    let _ = std::io::stdout().flush();
    for _ in std::io::stdin().lock().lines() {}
}
