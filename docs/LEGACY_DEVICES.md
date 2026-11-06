# Old phones, tablets and small screens

HID-Fi is meant to be driven from whatever touchscreen is to hand: an iPad that
stopped getting updates, an old Android tablet, a Raspberry Pi with a 7" screen.
The dashboard is one page for all of them. Current browsers run it as written. An
older browser gets a small layer, built into the page, that fills in what it
lacks. You don't install or switch anything.

## What works where

| Browser | Status |
|---|---|
| **iOS 12 Safari**: iPad Air, iPad mini 2 and 3, iPhone 5s, iPhone 6 (all stop at iOS 12.5.7) | **Tested on a real iPad Air, iOS 12.5.7.** Layout, trackpad, keyboard and buttons. |
| Safari 15.4 and later, Chrome and Edge 108 and later, Firefox 121 and later | Run the page as written. The layer never starts. |
| Safari 12 to 15.3, Chrome 79 to 107, Firefox 75 to 120 (Android tablets and phones, Raspberry Pi OS Chromium, older desktops) | **Should work: the layer covers what these lack, but only iOS 12 has been tested on a device.** If you try another, please report it. |
| Older than that: iOS 11 and earlier, Chrome before 79, the Android 4.x stock "Internet" browser, Internet Explorer | **Not supported.** The layer relies on `min()`, `max()` and grid `gap`, which arrived in Safari 12, Chrome 79 and Firefox 75. On Android, install a current Chrome or Firefox from the Play Store. |

**Chrome on an iPhone or iPad is Safari underneath.** Apple makes every iOS
browser use Safari's engine, so Chrome on iOS 12 behaves like Safari 12.

**Screen sharing on iOS 12** has not been tested yet. Safari 12 has the video
calling (WebRTC) and H.264 support the Screen tab needs.

## What the layer does

It starts only if the browser lacks one of these: `dvh` units, `aspect-ratio`,
`color-mix()`, the `:has()` selector, or pointer events. A current browser checks
those five and does nothing else.

On an older browser, once at load:

- **The stylesheet is rewritten** into what it can read:
  - `clamp(a,b,c)` becomes `max(a,min(b,c))`, which means exactly the same.
  - `inset` becomes `top`/`right`/`bottom`/`left`.
  - `:is()` and `:where()` become the plain selector lists they stand for.
  - `color-mix()` is worked out to the colour it gives.
  - `dvh` follows the screen's real visible height.
  - A selector list that holds something the browser can't read is split, so the
    browser drops only that part and keeps the rest.
- **Flex `gap` is emulated** where the browser has none (Safari before 14.1,
  Chrome before 84). The layer reads the browser's own layout to find which items
  are laid out, which are hidden, and where wrapping rows break, then adds the gap
  as margin exactly there.
- **`aspect-ratio` is emulated** for the knobs, sticks, d-pad and screen frame.
- **Touches become pointer events.** The trackpad, keys, knobs and gamepad listen
  for pointer events, which iOS 12 doesn't have. Without this, a finger did
  nothing.
- `touch-action: none` (not supported on iOS 12) is honoured by cancelling the
  scroll on surfaces that handle their own touches.

The result is checked to the pixel. With every iOS 12 gap simulated at once,
every element of every tab sits exactly where the modern page puts it: on an
iPad, an iPhone 5s and a 7" Raspberry Pi screen.

### What it costs

Measured on the iPad Air (2013, A7 chip):

- **The rewrite:** about 110 ms, once, at page load.
- **Each change on screen** (a key lighting up, a tab shown): a few milliseconds
  to keep the emulated spacing right.
- **The first visit to a tab:** up to about 100 ms, while its elements are read
  for the first time.
- **When nothing changes:** nothing runs.

Every pass is counted in `window.__hidfiLegacy` (`runs`, `runMs`).

What it does **not** fix: an A7 still takes 40–190 ms to lay out a big tab the
first time it is shown. That is the browser's own work, and the same with or
without the layer.

## Input latency, on every device

The board's message leaves the page on the input path itself. It never waits for
the next frame or for a redraw, so a page that is slow to redraw (an old phone
changing what it shows) can't hold the pointer or a key back.

- **Trackpad:** movement is sent from the finger's own event, at most every 8 ms
  (the USB polling interval). Nothing runs while nobody is touching.
- **Keys:** each key's two messages (press and release) are built the first time
  it is used and reused after that, so a key press allocates no memory.

Measured with `tests/perf/latency.py`: Chromium with the CPU slowed 6×, and every
frame spending 20 ms redrawing first, as a slow device does.

| Touch to message | Before | Now |
|---|---|---|
| Trackpad movement | 20.8 ms | **0.1 ms** |
| Key press | 0.0 ms | **0.0 ms** |

`tests/ui/legacy/test_latency_budget.py` fails if either goes over 3 ms.

## Testing a real device

`tests/support/live_board.py` serves the dashboard and the test suite's board
model on real sockets. Any device on the same WiFi can open it, and it never
talks to a real board:

```bash
python3 tests/support/live_board.py            # then open http://<this computer>/ on the device
python3 tests/support/live_board.py --pin 4826 --crowded
```

To see inside an iPhone or iPad's Safari from a Mac:

1. Install the debugging bridge: `brew install libimobiledevice ios-webkit-debug-proxy`.
2. On the device, turn on **Settings → Safari → Advanced → Web Inspector**.
3. Connect the device with a **data** cable and tap **Trust**.
4. Start the bridge with `ios_webkit_debug_proxy`. Its tabs are then listed at
   `http://127.0.0.1:9222/json`.

## The automated checks

`tests/ui/legacy/` (in the `ui` tier) runs a current Chromium told to behave like
iOS 12: no flex gap, no `aspect-ratio`, no `:has`, and no pointer events, with the
browser's own pointer events blocked. It checks that:

- a current browser never runs the layer;
- every tab lays out exactly as the modern page, on three screen sizes, and no box
  is quietly kept right by the browser's own gap;
- nothing is left in the rewritten stylesheet that iOS 12 can't read;
- touches alone drive the trackpad and the keys;
- the layer settles on an idle page, with and without Reduce Motion (it once ran
  142 times in 3 s from its own writes);
- touch to message stays within budget on a slowed CPU.

Each check was confirmed to fail when the part it guards is switched off.

## Adding to the dashboard

New CSS can use anything current. If it uses a feature the layer doesn't handle,
add it to the layer in `hid_fi/web_ui.h` (the script right after `</style>`). Then
run `python tests/run.py --tier ui`. The layout check names every element that
moved.
