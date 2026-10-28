// ==================== OTA LOGIC ====================
// The decisions behind updates, with no Arduino or ESP-IDF in sight, so the
// very same code is compiled on the host and tested there
// (tests/unit/firmware/ota_bench.cpp, tests/unit/test_ota_logic.py, planted
// bugs in tests/unit/firmware/ota_mutations.json). The firmware's glue - flash,
// NVS, the radio - is in ota.h. Plan: docs/OTA.md.
#pragma once
#include <stdint.h>
#include <string.h>

// ---- versions --------------------------------------------------------------
// "hid_fi_v4.0.4", "v4.1.0", "4.1" -> major.minor.patch. Anything else is not a
// version and compares as nothing: an update is never decided on a guess.
struct OtaVersion { int major, minor, patch; bool ok; };

inline OtaVersion otaParseVersion(const char* s) {
    OtaVersion v = {0, 0, 0, false};
    if (!s) return v;
    const char* p = strstr(s, "_v");
    p = p ? p + 2 : s;
    if (*p == 'v' || *p == 'V') p++;
    int part[3] = {0, 0, 0}, n = 0;
    while (n < 3) {
        if (*p < '0' || *p > '9') break;
        long x = 0;
        int digits = 0;
        while (*p >= '0' && *p <= '9') {
            x = x * 10 + (*p - '0');
            if (++digits > 6) return v;          // absurd: refuse
            p++;
        }
        part[n++] = (int)x;
        if (*p != '.') break;
        p++;
    }
    if (n < 2) return v;                          // at least major.minor
    if (*p != '\0' && *p != '-' && *p != '+') return v;   // trailing junk: not a version
    v.major = part[0]; v.minor = part[1]; v.patch = part[2]; v.ok = true;
    return v;
}

// <0 a older, 0 same, >0 a newer. Callers check .ok first.
inline int otaCompare(const OtaVersion& a, const OtaVersion& b) {
    if (a.major != b.major) return a.major < b.major ? -1 : 1;
    if (a.minor != b.minor) return a.minor < b.minor ? -1 : 1;
    if (a.patch != b.patch) return a.patch < b.patch ? -1 : 1;
    return 0;
}

// ---- the self-test after an update ------------------------------------------
// A new firmware starts "pending verify". It is marked valid only when it has
// proved itself; any reset before that makes the bootloader go back to the old
// slot. Never mark valid early: the core does that at start unless
// verifyRollbackLater() returns true (esp32-hal-misc.c), which is why HID-Fi
// overrides it.
const uint32_t OTA_SELFTEST_MIN_MS   = 60000;    // run this long without a reset
const uint32_t OTA_HID_DEADLINE_MS   = 30000;    // the USB HID stack must be up by then
const uint32_t OTA_NET_DEADLINE_MS   = 180000;   // and the home network back, if it was there before

enum OtaVerdict { OTA_WAIT = 0, OTA_PASS = 1, OTA_FAIL = 2 };

struct OtaSelfTestIn {
    uint32_t uptime_ms;     // since this boot
    bool hid_ready;         // the USB HID interfaces started (not: a host mounted them -
                            // a sleeping computer must not roll an update back)
    bool need_network;      // the board was on its home network when the update began
    bool network_up;        // and is on it now
    bool forced_fail;       // a test build that must fail its self-test
};

struct OtaSelfTestOut { OtaVerdict verdict; const char* why; };

inline OtaSelfTestOut otaSelfTest(const OtaSelfTestIn& in) {
    if (in.forced_fail) return {OTA_FAIL, "test_build"};
    if (!in.hid_ready) {
        if (in.uptime_ms >= OTA_HID_DEADLINE_MS) return {OTA_FAIL, "usb_hid"};
        return {OTA_WAIT, "usb_hid"};
    }
    if (in.need_network && !in.network_up) {
        if (in.uptime_ms >= OTA_NET_DEADLINE_MS) return {OTA_FAIL, "network"};
        return {OTA_WAIT, "network"};
    }
    if (in.uptime_ms < OTA_SELFTEST_MIN_MS) return {OTA_WAIT, "running"};
    return {OTA_PASS, ""};
}

// ---- what happened, told after a restart ------------------------------------
// The board keeps a small record in NVS (namespace "ota") of the update it was
// doing. After a restart it compares the record with what is running:
enum OtaOutcome {
    OTA_OUT_NONE = 0,        // no update in progress or just finished
    OTA_OUT_VERIFYING = 1,   // running the new version, self-test under way
    OTA_OUT_UPDATED = 2,     // running the new version, proven
    OTA_OUT_ROLLED_BACK = 3, // the new version failed; running the old one again
};

struct OtaRecord {
    const char* step;        // "", "switched", "pending", "valid", "rolled_back"
    const char* to;          // the version being installed
    const char* slot;        // the slot it was installed into ("app0"/"app1"; "" if unknown)
};

inline bool otaStepOpen(const char* step) {
    return step && (!strcmp(step, "switched") || !strcmp(step, "pending"));
}

// running/running_slot: this firmware's version and slot; pending: the running
// slot is in pending verify; aborted_other: the bootloader marked the other slot
// aborted/invalid (esp_ota_get_last_invalid_partition).
//
// The slot decides, not the version: reinstalling the same version, or a test
// image ("4.0.4-test"), compares equal to what runs after a rollback - only the
// slot tells that the board is no longer where the update put it.
inline OtaOutcome otaOutcome(const OtaRecord& rec, const char* running, const char* running_slot,
                             bool pending, bool aborted_other) {
    if (pending) return OTA_OUT_VERIFYING;
    if (otaStepOpen(rec.step)) {
        if (rec.slot && *rec.slot && running_slot && *running_slot)
            return strcmp(rec.slot, running_slot) == 0 ? OTA_OUT_UPDATED : OTA_OUT_ROLLED_BACK;
        OtaVersion r = otaParseVersion(running), t = otaParseVersion(rec.to);
        if (r.ok && t.ok && otaCompare(r, t) == 0) return OTA_OUT_UPDATED;
        return OTA_OUT_ROLLED_BACK;   // the record says an update was on its way, and it is not running
    }
    if (rec.step && !strcmp(rec.step, "rolled_back")) return OTA_OUT_ROLLED_BACK;
    if (rec.step && !strcmp(rec.step, "valid")) return OTA_OUT_UPDATED;
    if (aborted_other) return OTA_OUT_ROLLED_BACK;
    return OTA_OUT_NONE;
}

// May the firmware restart itself now? Not while proving a new version: that
// reset would roll the update back. The restart waits for the verdict.
inline bool otaRestartAllowed(bool pending_verify) { return !pending_verify; }
