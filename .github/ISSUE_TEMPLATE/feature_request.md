---
name: Feature request
about: Suggest something the board should be able to do
labels: enhancement
---

## What do you want to do

<!-- Describe the thing you are trying to achieve, not just the feature. -->

## Which part of the dashboard

<!-- Trackpad / Keyboard / Shortcuts / Media / Gamepad / Session / Settings -->

## Can the board already do it?

Two constraints decide most of these, so it is worth checking first:

- **HID is one way.** The board can never read the PC's state back. Anything that
  needs to know what the host is doing — current volume, which window is focused,
  whether something is muted — is not possible without software on the host.
- **Key combos must resolve in `mapKeyName()`.** If the keys you need are not
  there, they can usually be added, but say which ones.

## Anything else

<!-- Screenshots, links, how another tool does it. -->
