//! Pixel work that platform encoders need and that is the same everywhere, so
//! it is tested here rather than on each OS: BGRA to NV12, halving a picture,
//! and drawing the mouse pointer into a frame (Windows' Desktop Duplication
//! captures the desktop without the pointer, and seeing the pointer is the
//! point of watching a computer you are steering).

/// BGRA (8 bits per channel) to NV12, BT.601 limited range - what browsers
/// assume for H.264 that says nothing else. `w` and `h` must be even.
/// `nv12` is `w*h` luma bytes followed by `w*h/2` interleaved U,V bytes.
pub fn bgra_to_nv12(bgra: &[u8], stride: usize, w: usize, h: usize, nv12: &mut [u8]) {
    assert!(
        w.is_multiple_of(2) && h.is_multiple_of(2),
        "NV12 needs even sizes"
    );
    assert!(nv12.len() >= w * h * 3 / 2 && bgra.len() >= stride * (h - 1) + w * 4);
    let (luma, chroma) = nv12.split_at_mut(w * h);
    for y in 0..h {
        let row = &bgra[y * stride..y * stride + w * 4];
        let out = &mut luma[y * w..y * w + w];
        for x in 0..w {
            let (b, g, r) = (
                row[x * 4] as i32,
                row[x * 4 + 1] as i32,
                row[x * 4 + 2] as i32,
            );
            out[x] = (((66 * r + 129 * g + 25 * b + 128) >> 8) + 16) as u8;
        }
    }
    for y in (0..h).step_by(2) {
        let r0 = &bgra[y * stride..];
        let r1 = &bgra[(y + 1) * stride..];
        let out = &mut chroma[(y / 2) * w..(y / 2) * w + w];
        for x in (0..w).step_by(2) {
            let mut sb = 0;
            let mut sg = 0;
            let mut sr = 0;
            for p in [
                &r0[x * 4..],
                &r0[x * 4 + 4..],
                &r1[x * 4..],
                &r1[x * 4 + 4..],
            ] {
                sb += p[0] as i32;
                sg += p[1] as i32;
                sr += p[2] as i32;
            }
            let (b, g, r) = ((sb + 2) >> 2, (sg + 2) >> 2, (sr + 2) >> 2);
            out[x] = (((-38 * r - 74 * g + 112 * b + 128) >> 8) + 128).clamp(0, 255) as u8;
            out[x + 1] = (((112 * r - 94 * g - 18 * b + 128) >> 8) + 128).clamp(0, 255) as u8;
        }
    }
}

/// BGRA to I420 (planar Y, then U, then V), BT.601 limited range - what
/// OpenH264 takes. Same arithmetic as [`bgra_to_nv12`], planes split.
pub fn bgra_to_i420(
    bgra: &[u8],
    stride: usize,
    w: usize,
    h: usize,
    nv12: &mut Vec<u8>,
    i420: &mut [u8],
) {
    nv12.resize(w * h * 3 / 2, 0);
    bgra_to_nv12(bgra, stride, w, h, nv12);
    assert!(i420.len() >= w * h * 3 / 2);
    let (y, rest) = i420.split_at_mut(w * h);
    let (u, v) = rest.split_at_mut(w * h / 4);
    y.copy_from_slice(&nv12[..w * h]);
    for (i, [cu, cv]) in nv12[w * h..].as_chunks::<2>().0.iter().enumerate() {
        u[i] = *cu;
        v[i] = *cv;
    }
}

/// Undoes premultiplied alpha (X11's cursor image is premultiplied); the
/// result is what [`Pointer::Color`] blends.
pub fn unpremultiply_argb(argb: &[u32], out: &mut Vec<u8>) {
    out.clear();
    for &p in argb {
        let a = (p >> 24) & 0xff;
        let (r, g, b) = ((p >> 16) & 0xff, (p >> 8) & 0xff, p & 0xff);
        let un = |c: u32| {
            (c * 255 + a / 2)
                .checked_div(a)
                .map_or(0, |x| x.min(255) as u8)
        };
        out.extend_from_slice(&[un(b), un(g), un(r), a as u8]);
    }
}

/// Halves a BGRA picture (2x2 box filter). For screens wider than the stream
/// may be (a 4K display becomes 1920x1080). Returns the new width and height.
pub fn halve_bgra(
    src: &[u8],
    stride: usize,
    w: usize,
    h: usize,
    dst: &mut Vec<u8>,
) -> (usize, usize) {
    let (nw, nh) = (w / 2, h / 2);
    dst.resize(nw * nh * 4, 0);
    for y in 0..nh {
        let a = &src[(2 * y) * stride..];
        let b = &src[(2 * y + 1) * stride..];
        for x in 0..nw {
            for c in 0..4 {
                let s = a[8 * x + c] as u32
                    + a[8 * x + 4 + c] as u32
                    + b[8 * x + c] as u32
                    + b[8 * x + 4 + c] as u32;
                dst[(y * nw + x) * 4 + c] = ((s + 2) / 4) as u8;
            }
        }
    }
    (nw, nh)
}

/// A pointer shape, as Windows reports it (DXGI_OUTDUPL_POINTER_SHAPE_INFO).
pub enum Pointer<'a> {
    /// 32-bit BGRA with alpha.
    Color {
        w: usize,
        h: usize,
        pitch: usize,
        px: &'a [u8],
    },
    /// 1-bit AND mask over 1-bit XOR mask: `h` is twice the pointer's height.
    Mono {
        w: usize,
        h: usize,
        pitch: usize,
        px: &'a [u8],
    },
    /// 32-bit: alpha 0 = replace with the colour, alpha 0xFF = XOR with it.
    Masked {
        w: usize,
        h: usize,
        pitch: usize,
        px: &'a [u8],
    },
}

/// Draws `p` with its top-left at (`px`, `py`) into a BGRA frame, clipped to
/// the frame. Coordinates may be negative (the pointer at a screen edge).
pub fn draw_pointer(
    frame: &mut [u8],
    stride: usize,
    fw: usize,
    fh: usize,
    p: &Pointer,
    px: i64,
    py: i64,
) {
    let put = |frame: &mut [u8], x: i64, y: i64, f: &mut dyn FnMut(&mut [u8])| {
        if x >= 0 && y >= 0 && (x as usize) < fw && (y as usize) < fh {
            let i = y as usize * stride + x as usize * 4;
            f(&mut frame[i..i + 4]);
        }
    };
    match *p {
        Pointer::Color {
            w,
            h,
            pitch,
            px: src,
        } => {
            for y in 0..h {
                for x in 0..w {
                    let s = &src[y * pitch + x * 4..y * pitch + x * 4 + 4];
                    let a = s[3] as u32;
                    if a == 0 {
                        continue;
                    }
                    put(frame, px + x as i64, py + y as i64, &mut |d| {
                        for c in 0..3 {
                            d[c] = ((s[c] as u32 * a + d[c] as u32 * (255 - a) + 127) / 255) as u8;
                        }
                    });
                }
            }
        }
        Pointer::Mono {
            w,
            h,
            pitch,
            px: src,
        } => {
            let hh = h / 2;
            for y in 0..hh {
                for x in 0..w {
                    let bit = 0x80 >> (x % 8);
                    let and = src[y * pitch + x / 8] & bit != 0;
                    let xor = src[(y + hh) * pitch + x / 8] & bit != 0;
                    put(frame, px + x as i64, py + y as i64, &mut |d| {
                        for c in d.iter_mut().take(3) {
                            let v = if and { *c } else { 0 };
                            *c = if xor { v ^ 0xFF } else { v };
                        }
                    });
                }
            }
        }
        Pointer::Masked {
            w,
            h,
            pitch,
            px: src,
        } => {
            for y in 0..h {
                for x in 0..w {
                    let s = &src[y * pitch + x * 4..y * pitch + x * 4 + 4];
                    put(frame, px + x as i64, py + y as i64, &mut |d| {
                        if s[3] == 0 {
                            d[..3].copy_from_slice(&s[..3]);
                        } else {
                            for c in 0..3 {
                                d[c] ^= s[c];
                            }
                        }
                    });
                }
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn solid(w: usize, h: usize, b: u8, g: u8, r: u8) -> Vec<u8> {
        (0..w * h).flat_map(|_| [b, g, r, 255]).collect()
    }

    #[test]
    fn nv12_of_known_colours_is_bt601_limited() {
        // (B, G, R) -> (Y, U, V), from the BT.601 limited-range equations.
        for (bgr, yuv) in [
            ((0, 0, 0), (16, 128, 128)),
            ((255, 255, 255), (235, 128, 128)),
            ((0, 0, 255), (82, 90, 240)),    // red
            ((0, 255, 0), (145, 54, 34)),    // green
            ((255, 0, 0), (41, 240, 110)),   // blue
            ((255, 210, 0), (147, 179, 33)), // HID-Fi accent, #00d2ff
        ] {
            let src = solid(4, 2, bgr.0, bgr.1, bgr.2);
            let mut out = vec![0; 4 * 2 * 3 / 2];
            bgra_to_nv12(&src, 16, 4, 2, &mut out);
            assert!(
                out[..8].iter().all(|&v| (v as i32 - yuv.0).abs() <= 1),
                "{bgr:?} Y {:?}",
                &out[..8]
            );
            assert!(
                (out[8] as i32 - yuv.1).abs() <= 1 && (out[9] as i32 - yuv.2).abs() <= 1,
                "{bgr:?} UV {:?}",
                &out[8..]
            );
        }
    }

    #[test]
    fn nv12_respects_the_row_stride() {
        let mut src = vec![0u8; 32 * 2];
        for y in 0..2 {
            for x in 0..2 {
                src[y * 32 + x * 4..y * 32 + x * 4 + 4].copy_from_slice(&[255, 255, 255, 255]);
            }
        }
        let mut out = vec![0; 2 * 2 * 3 / 2];
        bgra_to_nv12(&src, 32, 2, 2, &mut out);
        assert_eq!(&out[..4], &[235, 235, 235, 235]);
    }

    #[test]
    fn i420_is_nv12_with_the_planes_split() {
        let mut src = vec![0u8; 4 * 2 * 4];
        for (i, px) in src.as_chunks_mut::<4>().0.iter_mut().enumerate() {
            px.copy_from_slice(&[(i * 30) as u8, (i * 20) as u8, (255 - i * 25) as u8, 255]);
        }
        let mut nv12 = vec![0; 12];
        bgra_to_nv12(&src, 16, 4, 2, &mut nv12);
        let (mut scratch, mut i420) = (Vec::new(), vec![0; 12]);
        bgra_to_i420(&src, 16, 4, 2, &mut scratch, &mut i420);
        assert_eq!(&i420[..8], &nv12[..8]);
        assert_eq!(&i420[8..10], &[nv12[8], nv12[10]], "U plane");
        assert_eq!(&i420[10..12], &[nv12[9], nv12[11]], "V plane");
    }

    #[test]
    fn premultiplied_cursor_pixels_come_back_straight() {
        let mut out = Vec::new();
        // Half-transparent white, premultiplied: 0x80 808080.
        unpremultiply_argb(&[0x8080_8080, 0xff00_ff00, 0x0000_0000], &mut out);
        assert_eq!(&out[0..4], &[255, 255, 255, 128]);
        assert_eq!(&out[4..8], &[0, 255, 0, 255]);
        assert_eq!(&out[8..12], &[0, 0, 0, 0]);
    }

    #[test]
    fn halving_averages_each_2x2_block() {
        let mut src = vec![0u8; 4 * 2 * 4];
        src[0..4].copy_from_slice(&[100, 100, 100, 255]);
        src[4..8].copy_from_slice(&[200, 200, 200, 255]);
        let mut dst = Vec::new();
        assert_eq!(halve_bgra(&src, 16, 4, 2, &mut dst), (2, 1));
        assert_eq!(&dst[0..4], &[75, 75, 75, 128]);
        assert_eq!(&dst[4..8], &[0, 0, 0, 0]);
    }

    #[test]
    fn a_colour_pointer_blends_and_clips() {
        let mut f = solid(4, 4, 0, 0, 0);
        let p = [
            255u8, 255, 255, 255, 255, 255, 255, 0, 255, 255, 255, 128, 255, 255, 255, 255,
        ];
        let ptr = Pointer::Color {
            w: 2,
            h: 2,
            pitch: 8,
            px: &p,
        };
        draw_pointer(&mut f, 16, 4, 4, &ptr, 3, 3); // only the top-left pixel lands
        assert_eq!(&f[(3 * 4 + 3) * 4..(3 * 4 + 3) * 4 + 3], &[255, 255, 255]);
        let mut g = solid(2, 2, 0, 0, 0);
        draw_pointer(&mut g, 8, 2, 2, &ptr, 0, 0);
        assert_eq!(&g[4..7], &[0, 0, 0], "alpha 0 leaves the screen");
        assert_eq!(&g[8..11], &[128, 128, 128], "alpha 128 blends");
        draw_pointer(&mut g, 8, 2, 2, &ptr, -5, -5); // fully off-screen: no panic
    }

    #[test]
    fn a_monochrome_pointer_follows_and_xor_rules() {
        // 2x1 pointer: AND row then XOR row, one byte each.
        // pixel 0: AND 0, XOR 0 -> black; pixel 1: AND 1, XOR 1 -> inverted screen.
        let masks = [0b0100_0000u8, 0b0100_0000u8];
        let ptr = Pointer::Mono {
            w: 2,
            h: 2,
            pitch: 1,
            px: &masks,
        };
        let mut f = solid(2, 1, 10, 20, 30);
        draw_pointer(&mut f, 8, 2, 1, &ptr, 0, 0);
        assert_eq!(&f[0..3], &[0, 0, 0]);
        assert_eq!(&f[4..7], &[245, 235, 225]);
    }

    #[test]
    fn a_masked_pointer_replaces_or_xors() {
        let p = [1u8, 2, 3, 0, 0xFF, 0, 0, 0xFF];
        let ptr = Pointer::Masked {
            w: 2,
            h: 1,
            pitch: 8,
            px: &p,
        };
        let mut f = solid(2, 1, 10, 20, 30);
        draw_pointer(&mut f, 8, 2, 1, &ptr, 0, 0);
        assert_eq!(&f[0..3], &[1, 2, 3]);
        assert_eq!(&f[4..7], &[10 ^ 0xFF, 20, 30]);
    }
}
