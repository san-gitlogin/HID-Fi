# HID-Fi Core

A 9 mm printed shell for the YD-ESP32-S3 board HID-Fi runs on: three parts, no
screws, no glue, about 10.6 g of PETG. The build guide (print settings, fit check,
assembly, home tests, design review and every measurement with how sure it is) is
`index.html`, with an interactive 3D model of the shell and board: assembled,
exploded, X-ray and section views.

> [!NOTE]
> **First print in progress.** These files have been sliced and checked by the
> build script, and the first set is being printed now. They have not yet been
> test-fitted on a real board. Print the fit coupon first.

## Print files

Everything is in [`print/`](print), already turned the way each part sits on the
bed. Do not rotate them, and no part needs supports.

| File | What it is |
|---|---|
| [`kit-all.stl`](print/kit-all.stl) | **Everything in one file:** unibody, plate, trim guide, fit coupon and peg gauge, laid out on one 106 × 96 mm bed, about 18 g of black PETG. The cheapest way at a print shop, which charges a minimum per file. |
| [`kit-test.stl`](print/kit-test.stl) | Fit coupon + peg gauge: print first to check the fit |
| [`kit-core.stl`](print/kit-core.stl) | Unibody + plate: the Core itself |
| `unibody.stl`, `plate.stl`, `fit-coupon.stl`, `peg-gauge.stl`, `trim-guide.stl`, `plate-feet.stl` | Each part on its own |
| [`light-pipe.stl`](print/light-pipe.stl) | Optional, **clear** PETG only, so it never shares a file with black parts. A drop of clear hot glue in the 2 mm window works too. |
| [`PRINT-NOTES.txt`](print/PRINT-NOTES.txt) | Settings and print order, for you or the print shop |
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

Open it through a local web server, because it loads `manifest.json` and
`parts-core.json` beside it:

```bash
cd hardware/hid-fi-core && python3 -m http.server 8000   # then http://localhost:8000
```

- `parts-core.json` holds the exact STL geometry of each part (base64), the same
  parts as in `print/`.
- `manifest.json` holds the board geometry, the dimensions and every build check.
- The model's libraries (three.js) load from cdn.jsdelivr.net.

The parametric source (`build.mjs`) is kept with the enclosure project, outside
this repository. If the fit coupon or peg gauge says your printer needs a
different fit, open an issue with what you measured.
