# Sound with the screen

The computer's sound - and, if the person wants, its microphone - comes with its
screen to the dashboard: in the Screen tab, the floating window, watch-only
full screen and the wall. Owner's brief (2026-10-29): volume and mute on the
screen viewer in every mode, a Companion switch for sharing sound and one for
the microphone, every edge case thought through, no added latency, tested on
both boards with real sound, with several audio devices connected.

This file is the plan and the progress log, like docs/OTA.md.

## Verified at the source (2026-10-29)

| Question | Answer | Where |
|---|---|---|
| Codec | Opus: the one every browser's WebRTC decodes at music quality (G.711/G.722 are telephone band). | WebRTC (RFC 7874) |
| Encoder, without C | `opus-pure` 0.2.2 (BSD-3, no dependencies). Measured here against the reference libopus 1.6.1 (Homebrew): every packet of speech (`say`), music-like, sweep and click signals decodes with libopus; band energies and SNR within about 1 dB of libopus's own encoder; the asked bitrate held; ~35 us per 10 ms stereo frame on this Mac (libopus: ~60 us). `rusty-opus` 1.0.2 measured the same in low-delay mode but ignores the music mode. Neither is byte-exact with libopus (float encoders differ in rounding) - quality, not bytes, is the test. | scratch evaluation, `opuseval` |
| Lowest latency | `RestrictedLowDelay` (CELT only): 2.5 ms look-ahead; 10 ms frames. Total algorithmic delay 12.5 ms. | opus-pure `Application`, RFC 6716 |
| Transport | str0m 0.24.1 `enable_opus(enabled, use_red)`: Opus, optionally with RED (RFC 2198) - each packet carries the previous frame too, so one lost WiFi packet costs no gap and no added delay. | `str0m/src/config.rs` |
| Resampling | `rubato` 5.0.1 (MIT/Apache, pure Rust), only where a device will not give 48 kHz (Windows loopback runs at the mix format). | crates.io |
| macOS system sound | ScreenCaptureKit: `capturesAudio`, `sampleRate` 48000, `channelCount` 2, `excludesCurrentProcessAudio`; audio of the apps in the filter, before routing to any device - so the output device (speakers, headphones, a virtual device) does not matter. | Apple, SCStreamConfiguration |
| macOS microphone | ScreenCaptureKit `captureMicrophone` + `microphoneCaptureDeviceID`, macOS 15+; needs the Microphone permission (and `NSMicrophoneUsageDescription`). | Apple |
| Windows system sound | WASAPI process loopback, excluding the Companion's own process tree (`AUDIOCLIENT_ACTIVATION_TYPE_PROCESS_LOOPBACK`, Windows 10 build 20348+ / Windows 11): every app's sound, whichever device plays it. Older Windows: loopback of the default output device, reopened when the default changes. | Microsoft Learn |
| Linux system sound | PulseAudio / PipeWire-Pulse: record `@DEFAULT_MONITOR@` with libpulse-simple, loaded at run time (like OpenH264) - no C build; reopened when the default output changes. | freedesktop |

## The person's view

- **Dashboard, every mode** (docked Screen tab, floating window, watch-only full
  screen, wall, browser full screen): a speaker button (mute / unmute) and a volume
  slider next to the other picture controls. Remembered per computer.
- **The first tap.** Browsers refuse to start sound by themselves. The picture
  starts muted; the button says *Tap for sound* until the person taps it once
  (the tap that opened the viewer counts, when the browser allows it).
- **iPhone/iPad Safari** lets a page mute but not set the volume (the phone's
  buttons do): the slider is hidden there, mute stays, and a hint says so.
- **The wall** shows one computer's displays: its sound comes once, with the
  wall's own control - never once per tile.
- **What the Companion shares is the Companion's choice**, shown on the viewer:
  *Sound off on this computer* when its owner switched it off; *No sound
  device* when there is nothing to capture; *Microphone on* when the mic is shared.

## The Companion

- Tray and window: **Share this computer's sound** (on by default - the screen is
  already shared with this person; sound is part of what they see) and
  **Share the microphone** (off by default: it carries the room, and it can echo
  if the phone plays near the computer). Both apply at once, to every viewer.
- Nothing is captured unless someone watches with sound: capture starts with the
  first such viewer and stops with the last (A3: the app costs nothing in the tray).
- One capture and one encode for everyone; each viewer only adds its sending.
- System sound and microphone are mixed into one track, like a call app; a
  microphone running on another clock is kept within 40 ms of the system sound
  (drop or pad, never a growing delay).

## Latency budget (target: under 60 ms from the computer's sound to the phone)

| Stage | Time |
|---|---|
| capture buffer (OS) | 5-20 ms |
| Opus frame + look-ahead | 12.5 ms |
| send (LAN) | 1-5 ms |
| browser jitter buffer, asked to stay minimal (`jitterBufferTarget` 0) | 10-30 ms |

Audio is never held back for video: it has its own path into each viewer's
session, ahead of frames, and is never paced or dropped to make room for them.
Picture and sound share one clock (each written with the time it was
captured), so the browser keeps them in step.

## Edge cases

- The output device changes while watching (headphones plugged, Bluetooth
  connects): macOS captures before routing; Windows process loopback likewise;
  endpoint loopback and Linux follow the new default within 2 s.
- No output device at all, or capture refused (permission, a VM with no sound
  card): the picture carries on, the viewer says why there is no sound.
- 44.1 kHz, mono, 5.1/7.1 devices: resampled to 48 kHz, down/up-mixed to stereo.
- Silence: frames keep flowing (a few bytes each), so the first sound after a
  pause is never late or clipped.
- A viewer that cannot keep up loses audio packets, never queues them.
- Several viewers: one capture, every viewer gets the same packets.
- The microphone's clock drifts from the system sound's: bounded, never growing.
- Echo: the microphone off by default; the viewer warns when both the mic and
  the phone's speaker are on.
- The Companion's own sounds are never captured.
- A dashboard from before sound, or a Companion from before sound: no audio is
  offered or answered; everything else works.

## Tests

- **Unit (Rust):** conform (rates, channels), mixer clock drift, framing and RTP
  time, the hub starting and stopping with viewers and settings, Opus packets
  decodable (the crate's own decoder; the reference libopus in the evaluation).
- **Dashboard (BDD):** controls in every mode, remembered, the first-tap rule, the
  iOS rule, the wall's single sound, states from the Companion.
- **System:** the real Companion with a test sound (clicks in step with picture
  flashes) to Chrome: sound arrives, no gaps for a minute, picture/sound offset,
  latency, CPU.
- **Real capture on this Mac:** a tone played to the speakers and to the virtual
  "Microsoft Teams Audio" device - both captured; the microphone with each of the
  three connected microphones.
- **Real boards:** DA1A2C and 09F7C8 serving the dashboard and the tickets, real
  Chrome watching with sound.
- **Performance:** CPU with sound on and off, video frame rate unchanged.

## Build order

1. [ ] Rust core: conform, mixer, encoder, hub, test sound; unit tests.
2. [ ] WebRTC: Opus (+RED) in the session, own path, shared clock; video written
       with its capture time.
3. [ ] macOS capture: system sound and microphone via ScreenCaptureKit.
4. [ ] Companion settings and tray, signalling states.
5. [ ] Dashboard: controls in every mode; BDD.
6. [ ] System, real-capture, real-board and performance tests.
7. [ ] Windows (WASAPI) and Linux (Pulse) capture; their checks.
