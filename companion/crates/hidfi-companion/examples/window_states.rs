//! Writes the window's golden states (tests/golden/companion_window_states.tsv):
//!   cargo run -p hidfi-companion --example window_states > ../tests/golden/companion_window_states.tsv
//! The Rust test `window::tests::the_golden_states_are_current` fails until it is.

fn main() {
    print!("{}", hidfi_companion::window::samples::golden());
}
