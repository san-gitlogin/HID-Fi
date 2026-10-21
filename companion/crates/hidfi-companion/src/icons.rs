//! The tray icons, drawn from the HID-Fi mark (assets/, made from
//! docs/assets/logo.svg): monochrome templates on macOS, cyan elsewhere.

use hidfi_companion::menu::Icon;

/// RGBA pixels, width, height.
pub fn rgba(kind: Icon) -> (Vec<u8>, u32, u32) {
    let name = match kind {
        Icon::Idle => "idle",
        Icon::Ready => "ready",
        Icon::Live => "live",
        Icon::Attention => "attention",
    };
    #[cfg(target_os = "macos")]
    let bytes: &[u8] = match name {
        "idle" => include_bytes!("../assets/mac-idle-44.png"),
        "ready" => include_bytes!("../assets/mac-ready-44.png"),
        "live" => include_bytes!("../assets/mac-live-44.png"),
        _ => include_bytes!("../assets/mac-attention-44.png"),
    };
    #[cfg(not(target_os = "macos"))]
    let bytes: &[u8] = match name {
        "idle" => include_bytes!("../assets/color-idle-64.png"),
        "ready" => include_bytes!("../assets/color-ready-64.png"),
        "live" => include_bytes!("../assets/color-live-64.png"),
        _ => include_bytes!("../assets/color-attention-64.png"),
    };
    let dec = png::Decoder::new(std::io::Cursor::new(bytes));
    let mut r = dec.read_info().expect("bundled icon");
    let mut buf = vec![0; r.output_buffer_size().expect("icon size")];
    let info = r.next_frame(&mut buf).expect("bundled icon");
    buf.truncate(info.buffer_size());
    (buf, info.width, info.height)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn every_icon_decodes_to_rgba() {
        for k in [Icon::Idle, Icon::Ready, Icon::Live, Icon::Attention] {
            let (px, w, h) = rgba(k);
            assert_eq!(px.len(), (w * h * 4) as usize);
            assert!(w >= 22 && w == h);
        }
    }
}
