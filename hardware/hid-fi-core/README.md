# HID-Fi Core

A 9 mm printed shell for the YD-ESP32-S3 board HID-Fi runs on: three parts, no
screws, no glue, about 10.6 g of PETG. The build guide (print settings, fit check,
assembly, home tests, design review and every measurement with how sure it is) is
`index.html`, with an interactive 3D model of the shell and board: assembled,
exploded, X-ray and section views.

Open it through a local web server, because it loads `manifest.json` and
`parts-core.json` beside it:

```bash
cd hardware/hid-fi-core && python3 -m http.server 8000   # then http://localhost:8000
```

- `parts-core.json` holds the exact STL geometry of each part (base64).
- `manifest.json` holds the board geometry, the dimensions and every build check.
- The model's libraries (three.js) load from cdn.jsdelivr.net.

The parametric source (`build.mjs`) and the print-ready STL files are kept with
the enclosure project, outside this repository.
