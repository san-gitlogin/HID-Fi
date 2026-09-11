---
name: Bug report
about: Something does not work
labels: bug
---

## What happened

<!-- What you did, what you expected, what actually happened. -->

## Which port was plugged in where

<!-- This is the cause of most reports. Please be specific. -->

- [ ] **COM** port connected to: 
- [ ] **USB** port connected to: 
- [ ] Both cables are data cables, not charge-only

## Hardware

- Board:  <!-- e.g. YD-ESP32-S3 N16R8 -->
- Host OS being controlled:  <!-- Windows 11 / macOS 15 / Ubuntu 24.04 -->
- Phone / browser used for the dashboard: 

## Firmware

Output of `{"cmd":"status"}` over serial, or the version shown under the logo in
the dashboard:

```
paste here
```

## Test suite

```
python tests\test_v34_features.py
```

```
paste the last few lines here
```

## Anything else

<!-- Serial log, screenshot at phone width, whatever you have. -->
