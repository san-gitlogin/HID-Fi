# esp32s3.json - Espressif's flasher stub

From espressif/esp-flasher-stub **v1.4.1** (2026-10-14), file `esp32s3.json`,
Apache-2.0 (LICENSE-APACHE here). SHA-256
`767c0ef61d864bb9524c52a2fa7ff5b144598c8df729b2cd31e84d8785e2d65b` - the digest
GitHub publishes for that release asset; `src/stub.rs` refuses any other bytes.

Why it is here: the ESP32-S3's ROM loader cannot read flash (esptool implements
the slow ROM read only for the original ESP32), and the Companion must read the
partition table, `otadata` and the settings backup before it writes anything.
The stub runs in the chip's RAM for the session only; nothing is written to
flash by loading it. Moving to a newer stub: check its release notes, replace
the file, update the digest in `src/stub.rs` and here, run the fault sweep.
