//! The "Open the dashboard" page for companion/tests/open_dashboard.py:
//! `[OPENER_BROWSER=Arc] opener_page URL [URL ...]` prints the page's address, prints "settings"
//! each time its button is pressed, and stops when stdin closes.
use hidfi_companion::opener::Opener;
use std::io::{BufRead, Write};

fn main() {
    let o = Opener::start(Box::new(|| {
        println!("settings");
        let _ = std::io::stdout().flush();
    }))
    .expect("start");
    // OPENER_BROWSER: the default browser's name, as the app would read it.
    println!(
        "{}",
        o.page_for(
            std::env::args().skip(1).collect(),
            match std::env::var("OPENER_BROWSER").ok().as_deref() {
                // "auto": what this computer's default browser really is.
                Some("auto") => hidfi_companion::browser::default_browser(),
                other => other.map(Into::into),
            }
        )
    );
    let _ = std::io::stdout().flush();
    for _ in std::io::stdin().lock().lines() {}
}
