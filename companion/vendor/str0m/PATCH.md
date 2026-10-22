# str0m 0.24.1, patched

The [str0m](https://github.com/algesten/str0m) WebRTC library (MIT OR
Apache-2.0, `LICENSE-MIT.txt`), version 0.24.1 from crates.io, with three changes
that bring its bandwidth estimation closer to libwebrtc's. `companion/Cargo.toml`
uses this copy through `[patch.crates-io]`. Only `src/`, `Cargo.toml` and the
licence are kept; the test, example and benchmark targets were removed from
`Cargo.toml` because their files are not here.

Every change is marked `HID-Fi patch` in the source.

## Why

HID-Fi Companion streams a screen over home WiFi. It sends little while the
screen is still and a lot when something moves. With the stock library, three
things happened on WiFi that a laptop on the same network handles easily
(measured with `companion/tests/overload.py`, then confirmed in the library's
own trace, built with `--features str0m/_internal_dont_use_log_stats`):

1. **Backing off from an idle screen's throughput.** On congestion, the
   delay-based controller cuts to 85% of the measured throughput. After a still
   period that throughput is what the app chose to send (40 kbit/s, the floor),
   not what the link carries. The first WiFi stall after the screen started
   moving cut the estimate of a 25 Mbit/s link to about 34 kbit/s.
2. **The throughput figure lagging behind a jump.** After the sender leaves
   the application-limited region (ALR), its rate jumps, but the throughput
   estimator weighs new samples against a figure that decayed while idle.
3. **The pacer capping media at 1.1x the estimate.** A keyframe or the first
   frames of motion waited in the pacer for most of a second whenever the
   estimate ran low, so pictures arrived late and stalled.

## The changes

| File | Change | From libwebrtc |
|---|---|---|
| `src/bwe/delay/rate_control.rs`, `src/bwe/delay/control.rs`, `src/bwe/mod.rs` | While the sender is in ALR, or has just left it, a delay-based decrease backs off from the current estimate rather than the observed throughput. | "ALR-limited backoff" |
| `src/bwe/acked_bitrate_estimator.rs`, `src/bwe/mod.rs` | When ALR ends, the first acked packet *sent after it* widens the estimator's variance (+200), so post-ALR samples take over. Applying it when ALR ends, before those packets are acked, let idle-period acks take over instead, and the estimate collapsed (seen, and fixed). | `AcknowledgedBitrateEstimator::SetAlrEndedTime` / `BitrateEstimator::ExpectFastRateChange` |
| `src/pacer/control.rs` | `PACING_FACTOR` 1.1 → 2.5. | `kDefaultPaceMultiplier` |

## Updating str0m

Do not just bump the version: the patch would silently disappear. Instead:
diff the new release's `src/bwe` and `src/pacer` against these, carry each
`HID-Fi patch` across (or drop it if upstream now does the same), and run
`companion/tests/overload.py` (all phases), whose WiFi phases fail without
these changes. The patch is meant to go upstream; once str0m ships it, delete
this directory and the `[patch.crates-io]` entry.
