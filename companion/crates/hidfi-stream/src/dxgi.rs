//! Screen capture on Windows with DXGI Desktop Duplication (Windows 8+): the
//! same API Windows' own remote desktop tools use.
//!
//! - New pixels arrive only when something changed (AcquireNextFrame times out
//!   otherwise), so a still desktop costs a timeout and nothing else.
//! - The pointer is not in the captured image. It is drawn back in from the
//!   shape and position Windows reports, on a copy, so the stored desktop never
//!   keeps an old pointer.
//! - "Access lost" (the desktop switched: UAC, lock screen, a resolution change,
//!   a full-screen game) drops the duplication, which is made again on the next
//!   grab - at the new size if it changed.
//! - Wider than 2560 pixels, the picture is halved (a 4K screen becomes 1920).
//!
//! NOT YET RUN ON WINDOWS: compiled and checked from macOS only. The pixel work
//! it relies on (convert.rs) is tested; the API calls follow Microsoft's
//! DesktopDuplication sample. Run `cargo run --release --example check_capture`
//! on a Windows PC to verify.

use crate::convert::{draw_pointer, halve_bgra, Pointer};
use crate::frame::Frame;
use crate::pipeline::Source;
use std::ffi::c_void;
use windows::core::Interface;
use windows::Win32::Foundation::HMODULE;
use windows::Win32::Graphics::Direct3D::{
    D3D_DRIVER_TYPE_UNKNOWN, D3D_FEATURE_LEVEL_10_1, D3D_FEATURE_LEVEL_11_0,
};
use windows::Win32::Graphics::Direct3D11::{
    D3D11CreateDevice, ID3D11Device, ID3D11DeviceContext, ID3D11Texture2D, D3D11_CPU_ACCESS_READ,
    D3D11_CREATE_DEVICE_BGRA_SUPPORT, D3D11_MAPPED_SUBRESOURCE, D3D11_MAP_READ, D3D11_SDK_VERSION,
    D3D11_TEXTURE2D_DESC, D3D11_USAGE_STAGING,
};
use windows::Win32::Graphics::Dxgi::Common::{DXGI_FORMAT_B8G8R8A8_UNORM, DXGI_SAMPLE_DESC};
use windows::Win32::Graphics::Dxgi::{
    CreateDXGIFactory1, IDXGIFactory1, IDXGIOutput1, IDXGIOutputDuplication, IDXGIResource, DXGI_ERROR_ACCESS_LOST,
    DXGI_ERROR_WAIT_TIMEOUT, DXGI_OUTDUPL_FRAME_INFO, DXGI_OUTDUPL_POINTER_SHAPE_INFO,
    DXGI_OUTDUPL_POINTER_SHAPE_TYPE_COLOR, DXGI_OUTDUPL_POINTER_SHAPE_TYPE_MASKED_COLOR,
    DXGI_OUTDUPL_POINTER_SHAPE_TYPE_MONOCHROME,
};

/// Wider than this, the picture is halved.
pub const HALVE_ABOVE: usize = 2560;

pub struct DxgiSource {
    device: ID3D11Device,
    ctx: ID3D11DeviceContext,
    output: IDXGIOutput1,
    dup: Option<IDXGIOutputDuplication>,
    staging: Option<(ID3D11Texture2D, u32, u32)>,
    /// The desktop as last captured, without the pointer.
    desk: Vec<u8>,
    w: usize,
    h: usize,
    /// The picture handed out: the desktop plus the pointer (and halved).
    comp: Vec<u8>,
    half: Vec<u8>,
    shape: Vec<u8>,
    shape_info: DXGI_OUTDUPL_POINTER_SHAPE_INFO,
    pointer: Option<(i64, i64)>,
    have: bool,
}

// SAFETY: the D3D objects are used only from the pipeline thread that owns
// this source; it is moved there once and never shared.
unsafe impl Send for DxgiSource {}

fn err(what: &str, e: windows::core::Error) -> String {
    format!("{what}: {e}")
}

impl DxgiSource {
    /// Capture display `id` from [`crate::display::list`] (`card << 8 |
    /// output`), or the main display if `None`. The Direct3D device is made on
    /// the card that owns the output: Desktop Duplication refuses any other,
    /// which is how a laptop's external monitor on its second GPU was out of reach.
    pub fn start(id: Option<u32>) -> Result<Self, String> {
        let id = match id {
            Some(i) => i,
            None => crate::display::main_id(&crate::display::list()).unwrap_or(0),
        };
        let (card, out) = (id >> 8, id & 0xff);
        // SAFETY: COM calls on live objects.
        let (adapter, output) = unsafe {
            let f: IDXGIFactory1 = CreateDXGIFactory1().map_err(|e| err("DXGI", e))?;
            let adapter = f.EnumAdapters1(card).map_err(|e| err("no such display", e))?;
            let output = adapter.EnumOutputs(out).map_err(|e| err("no such display", e))?;
            (adapter, output)
        };
        let mut device: Option<ID3D11Device> = None;
        let mut ctx: Option<ID3D11DeviceContext> = None;
        // SAFETY: documented call with valid out pointers. With an adapter the
        // driver type must be UNKNOWN.
        unsafe {
            D3D11CreateDevice(
                &adapter,
                D3D_DRIVER_TYPE_UNKNOWN,
                HMODULE::default(),
                D3D11_CREATE_DEVICE_BGRA_SUPPORT,
                Some(&[D3D_FEATURE_LEVEL_11_0, D3D_FEATURE_LEVEL_10_1]),
                D3D11_SDK_VERSION,
                Some(&mut device),
                None,
                Some(&mut ctx),
            )
            .map_err(|e| err("no Direct3D device", e))?;
        }
        let device = device.ok_or("no Direct3D device")?;
        let ctx = ctx.ok_or("no Direct3D context")?;
        let output: IDXGIOutput1 = output
            .cast()
            .map_err(|e| err("IDXGIOutput1 (Windows 8+)", e))?;
        let mut me = DxgiSource {
            device,
            ctx,
            output,
            dup: None,
            staging: None,
            desk: Vec::new(),
            w: 0,
            h: 0,
            comp: Vec::new(),
            half: Vec::new(),
            shape: Vec::new(),
            shape_info: DXGI_OUTDUPL_POINTER_SHAPE_INFO::default(),
            pointer: None,
            have: false,
        };
        me.duplicate()?;
        Ok(me)
    }

    fn duplicate(&mut self) -> Result<(), String> {
        // SAFETY: COM calls on live objects.
        unsafe {
            let dup = self
                .output
                .DuplicateOutput(&self.device)
                .map_err(|e| err("cannot duplicate the display", e))?;
            let desc = dup.GetDesc();
            self.w = desc.ModeDesc.Width as usize;
            self.h = desc.ModeDesc.Height as usize;
            self.dup = Some(dup);
        }
        self.desk.resize(self.w * self.h * 4, 0);
        Ok(())
    }

    fn staging(&mut self, w: u32, h: u32) -> Result<ID3D11Texture2D, String> {
        if let Some((t, sw, sh)) = &self.staging {
            if *sw == w && *sh == h {
                return Ok(t.clone());
            }
        }
        let desc = D3D11_TEXTURE2D_DESC {
            Width: w,
            Height: h,
            MipLevels: 1,
            ArraySize: 1,
            Format: DXGI_FORMAT_B8G8R8A8_UNORM,
            SampleDesc: DXGI_SAMPLE_DESC {
                Count: 1,
                Quality: 0,
            },
            Usage: D3D11_USAGE_STAGING,
            BindFlags: 0,
            CPUAccessFlags: D3D11_CPU_ACCESS_READ.0 as u32,
            MiscFlags: 0,
        };
        let mut tex: Option<ID3D11Texture2D> = None;
        // SAFETY: valid description and out pointer.
        unsafe { self.device.CreateTexture2D(&desc, None, Some(&mut tex)) }
            .map_err(|e| err("staging texture", e))?;
        let tex = tex.ok_or("staging texture")?;
        self.staging = Some((tex.clone(), w, h));
        Ok(tex)
    }

    /// Takes one frame from the duplication. Ok(true) if the picture changed
    /// (pixels or pointer).
    fn acquire(&mut self) -> Result<bool, String> {
        if self.dup.is_none() {
            self.duplicate()?;
        }
        let dup = self.dup.clone().ok_or("no duplication")?;
        let mut info = DXGI_OUTDUPL_FRAME_INFO::default();
        let mut res: Option<IDXGIResource> = None;
        // SAFETY: valid out pointers; the frame is released below on every path.
        let r = unsafe { dup.AcquireNextFrame(0, &mut info, &mut res) };
        if let Err(e) = r {
            if e.code() == DXGI_ERROR_WAIT_TIMEOUT {
                return Ok(false);
            }
            if e.code() == DXGI_ERROR_ACCESS_LOST {
                self.dup = None; // made again on the next grab
                return Ok(false);
            }
            return Err(err("AcquireNextFrame", e));
        }
        let result = self.take(&dup, &info, res);
        // SAFETY: a frame is held; release it whatever happened.
        unsafe {
            let _ = dup.ReleaseFrame();
        }
        result
    }

    fn take(
        &mut self,
        dup: &IDXGIOutputDuplication,
        info: &DXGI_OUTDUPL_FRAME_INFO,
        res: Option<IDXGIResource>,
    ) -> Result<bool, String> {
        let mut changed = false;
        if info.LastMouseUpdateTime != 0 {
            let p = info.PointerPosition;
            self.pointer = p
                .Visible
                .as_bool()
                .then_some((p.Position.x as i64, p.Position.y as i64));
            changed = true;
            if info.PointerShapeBufferSize > 0 {
                self.shape.resize(info.PointerShapeBufferSize as usize, 0);
                let mut need = 0u32;
                // SAFETY: the buffer is the size Windows asked for.
                unsafe {
                    dup.GetFramePointerShape(
                        self.shape.len() as u32,
                        self.shape.as_mut_ptr() as *mut c_void,
                        &mut need,
                        &mut self.shape_info,
                    )
                }
                .map_err(|e| err("pointer shape", e))?;
            }
        }
        if info.LastPresentTime != 0 {
            let res = res.ok_or("no desktop image")?;
            let tex: ID3D11Texture2D = res.cast().map_err(|e| err("desktop texture", e))?;
            let mut d = D3D11_TEXTURE2D_DESC::default();
            // SAFETY: plain query on a live texture.
            unsafe { tex.GetDesc(&mut d) };
            let staging = self.staging(d.Width, d.Height)?;
            let mut map = D3D11_MAPPED_SUBRESOURCE::default();
            // SAFETY: copy into our CPU-readable texture, then read it while mapped.
            unsafe {
                self.ctx.CopyResource(&staging, &tex);
                self.ctx
                    .Map(&staging, 0, D3D11_MAP_READ, 0, Some(&mut map))
                    .map_err(|e| err("map", e))?;
                let (w, h) = (d.Width as usize, d.Height as usize);
                if w != self.w || h != self.h {
                    self.w = w;
                    self.h = h;
                }
                self.desk.resize(w * h * 4, 0);
                let src = map.pData as *const u8;
                for y in 0..h {
                    std::ptr::copy_nonoverlapping(
                        src.add(y * map.RowPitch as usize),
                        self.desk.as_mut_ptr().add(y * w * 4),
                        w * 4,
                    );
                }
                self.ctx.Unmap(&staging, 0);
            }
            self.have = true;
            changed = true;
        }
        Ok(changed)
    }

    fn compose(&mut self) {
        self.comp.clear();
        self.comp.extend_from_slice(&self.desk);
        if let Some((x, y)) = self.pointer {
            let i = &self.shape_info;
            let (w, h, pitch) = (i.Width as usize, i.Height as usize, i.Pitch as usize);
            if !self.shape.is_empty() && self.shape.len() >= pitch * h {
                let px = &self.shape[..];
                let p = match i.Type {
                    t if t == DXGI_OUTDUPL_POINTER_SHAPE_TYPE_COLOR.0 as u32 => {
                        Some(Pointer::Color { w, h, pitch, px })
                    }
                    t if t == DXGI_OUTDUPL_POINTER_SHAPE_TYPE_MONOCHROME.0 as u32 => {
                        Some(Pointer::Mono { w, h, pitch, px })
                    }
                    t if t == DXGI_OUTDUPL_POINTER_SHAPE_TYPE_MASKED_COLOR.0 as u32 => {
                        Some(Pointer::Masked { w, h, pitch, px })
                    }
                    _ => None,
                };
                if let Some(p) = p {
                    draw_pointer(&mut self.comp, self.w * 4, self.w, self.h, &p, x, y);
                }
            }
        }
    }
}

impl Source for DxgiSource {
    fn size(&self) -> (u32, u32) {
        if self.w > HALVE_ABOVE {
            ((self.w / 2) as u32, (self.h / 2) as u32)
        } else {
            (self.w as u32, self.h as u32)
        }
    }

    fn grab(&mut self, force: bool, f: &mut dyn FnMut(&Frame)) -> Result<bool, String> {
        let changed = self.acquire()?;
        if !self.have || !(changed || force) {
            return Ok(false);
        }
        self.compose();
        let (w, h) = (self.w, self.h);
        if w > HALVE_ABOVE {
            let comp = std::mem::take(&mut self.comp);
            let (nw, nh) = halve_bgra(&comp, w * 4, w, h, &mut self.half);
            self.comp = comp;
            f(&Frame {
                width: nw as u32,
                height: nh as u32,
                stride: nw * 4,
                bgra: &self.half,
            });
        } else {
            f(&Frame {
                width: w as u32,
                height: h as u32,
                stride: w * 4,
                bgra: &self.comp,
            });
        }
        Ok(true)
    }
}
