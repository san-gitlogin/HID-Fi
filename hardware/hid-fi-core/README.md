# HID-Fi Core

A 9 mm printed shell for the YD-ESP32-S3 board HID-Fi runs on: two parts, no
screws, no glue, about 10.4 g of PETG. The build guide (print settings, fit check,
assembly, home tests, design review and every measurement with how sure it is) is
`index.html`, with an interactive 3D model of the shell and board: assembled,
exploded, X-ray and section views.

> [!NOTE]
> **Rev C, after the first print.** The first printed set (November 2026) fitted
> the board perfectly, but its base did not close: the front tongue had 0.1 mm of
> room in its slot, less than one layer line of a service print, and a press-fit
> peg snapped when the base came off. Rev C closes like a TV remote's battery
> cover: two thick front tabs and a click latch at the back, with 0.3 mm round every
> fit. Rev C is sliced and checked but not printed yet: print `kit-test.stl` first.

## Print files

Everything is in [`print/`](print), already turned the way each part sits on the
bed. Do not rotate them, and no part needs supports.

| File | What it is |
|---|---|
| [`kit-all.stl`](print/kit-all.stl) | **Everything in one file:** unibody, plate, trim guide, fit coupon and latch coupon, laid out on one 106 × 89 mm bed, about 17 g of PETG. The cheapest way at a print shop, which charges a minimum per file. |
| [`kit-test.stl`](print/kit-test.stl) | Fit coupon + latch coupon + a plate: print first, check the sockets, your cable, the tabs and the click |
| [`kit-core.stl`](print/kit-core.stl) | Unibody + plate: the Core itself |
| `unibody.stl`, `plate.stl`, `fit-coupon.stl`, `latch-coupon.stl`, `trim-guide.stl`, `plate-feet.stl` | Each part on its own |
| [`PRINT-NOTES.txt`](print/PRINT-NOTES.txt) | Settings, print order and how to open and close it, for you or the print shop |
| [`HID-Fi-Core-kit-all.zip`](print/HID-Fi-Core-kit-all.zip) | `kit-all.stl` with the print notes and licence notice |
| [`HID-Fi-Core-print.zip`](print/HID-Fi-Core-print.zip) | All of the above in one download |

## License: personal use only

Licensed under [PolyForm Noncommercial 1.0.0](../../LICENSE.md): personal,
noncommercial printing and use only. Selling printed units, kits or these files,
or any commercial use, is not licensed and infringes the author's copyright.
Commercial licences: contact the author. Unauthorised commercial use will be
pursued legally.

Each STL carries this notice in its header, and `print/LICENSE-NOTICE.txt` goes
with every copy.

## The 3D build guide

**Open it at <https://san-gitlogin.github.io/HID-Fi/hardware/hid-fi-core/>**:
this folder's `index.html`, served by GitHub Pages from the repository.

To open it offline, use a local web server, because it loads `manifest.json` and
`parts-core.json` beside it:

```bash
cd hardware/hid-fi-core && python3 -m http.server 8000   # then http://localhost:8000
```

- `parts-core.json` holds the exact STL geometry of each part (base64), the same
  parts as in `print/`.
- `manifest.json` holds the board geometry, the dimensions, every build check and
  the closure's numbers (fits, tabs, latch).
- `tests/static/test_shell_print.py` measures every mating gap on these meshes by
  itself (ray casts) and checks each print file is a closed, licensed solid.
- The model's libraries (three.js) load from cdn.jsdelivr.net.

The parametric source (`build.mjs`) is kept with the enclosure project, outside
this repository. If the fit coupon or peg gauge says your printer needs a
different fit, open an issue with what you measured.
