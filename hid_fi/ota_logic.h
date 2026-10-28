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

// ---- installing: may this release go on this board? ---------------------------
enum OtaRefusal {
    OTA_INSTALL_OK = 0,
    OTA_REF_BAD_VERSION,      // the manifest's version or min_from is not a version
    OTA_REF_NOT_NEWER,        // same or older than what runs (unless the person asked for that one)
    OTA_REF_TOO_OLD,          // this board runs something older than the release can update from
    OTA_REF_WRONG_CHIP,
    OTA_REF_TOO_BIG,          // larger than the other slot
    OTA_REF_EMPTY,            // a zero-byte image
};

struct OtaOffer {
    const char* version;      // "4.1.0"
    const char* min_from;     // the oldest version it can update from
    const char* chip;         // "esp32s3"
    uint32_t size;            // image bytes
    bool downgrade_ok;        // the person chose this exact older version
};

inline OtaRefusal otaCanInstall(const OtaOffer& o, const char* running, uint32_t slot_size) {
    OtaVersion v = otaParseVersion(o.version), m = otaParseVersion(o.min_from), r = otaParseVersion(running);
    if (!v.ok || !m.ok || !r.ok) return OTA_REF_BAD_VERSION;
    if (!o.chip || strcmp(o.chip, "esp32s3") != 0) return OTA_REF_WRONG_CHIP;
    if (o.size == 0) return OTA_REF_EMPTY;
    if (o.size > slot_size) return OTA_REF_TOO_BIG;
    if (otaCompare(r, m) < 0) return OTA_REF_TOO_OLD;
    int c = otaCompare(v, r);
    if (c < 0 && !o.downgrade_ok) return OTA_REF_NOT_NEWER;
    if (c == 0) return OTA_REF_NOT_NEWER;              // never "update" to what already runs
    return OTA_INSTALL_OK;
}

inline const char* otaRefusalStr(OtaRefusal r) {
    switch (r) {
        case OTA_INSTALL_OK:      return "";
        case OTA_REF_BAD_VERSION: return "bad_version";
        case OTA_REF_NOT_NEWER:   return "not_newer";
        case OTA_REF_TOO_OLD:     return "too_old";
        case OTA_REF_WRONG_CHIP:  return "wrong_chip";
        case OTA_REF_TOO_BIG:     return "too_big";
        default:                  return "empty";
    }
}

// ---- the steps of an install, and what cancel does in each ----------------------
enum OtaStep {
    OTA_STEP_IDLE = 0,
    OTA_STEP_CHECKING,        // fetching the manifest - nothing written anywhere
    OTA_STEP_DOWNLOADING,     // writing the INACTIVE slot only
    OTA_STEP_VERIFYING,       // hashing what was written
    OTA_STEP_SWITCHING,       // the one otadata write (about 0.1 s)
    OTA_STEP_READY,           // switched; the new version starts at the next restart
};

enum OtaCancel {
    OTA_CANCEL_NOTHING = 0,   // nothing to undo
    OTA_CANCEL_STOP,          // stop; the inactive slot is ignored (esp_ota_abort)
    OTA_CANCEL_SWITCH_BACK,   // put the boot choice back on the running slot
    OTA_CANCEL_REFUSED,       // inside the otadata write: wait a moment
};

inline OtaCancel otaCancelAction(OtaStep s) {
    switch (s) {
        case OTA_STEP_CHECKING:
        case OTA_STEP_DOWNLOADING:
        case OTA_STEP_VERIFYING:  return OTA_CANCEL_STOP;
        case OTA_STEP_SWITCHING:  return OTA_CANCEL_REFUSED;
        case OTA_STEP_READY:      return OTA_CANCEL_SWITCH_BACK;
        default:                  return OTA_CANCEL_NOTHING;
    }
}

// ---- a download that breaks -------------------------------------------------------
const int OTA_MAX_ATTEMPTS = 8;

// After a failed attempt: resume from `written` (the write handle is still open,
// and the server honours Range), start over (0), or give up (-1).
inline long otaResumeFrom(uint32_t written, uint32_t total, bool range_ok, int attempts) {
    if (attempts >= OTA_MAX_ATTEMPTS) return -1;
    if (!range_ok || written == 0 || written >= total) return 0;
    return (long)written;
}

// Wait before attempt n (1-based): 2 s, 4 s, 8 s ... capped at 60 s.
inline uint32_t otaRetryDelayMs(int attempt) {
    if (attempt <= 1) return 2000;
    uint32_t d = 2000;
    for (int i = 1; i < attempt && d < 60000; i++) d *= 2;
    return d > 60000 ? 60000 : d;
}

// ---- when to look for an update by itself ---------------------------------------------
const uint32_t OTA_FIRST_CHECK_MS = 30000;              // after coming online
const uint32_t OTA_CHECK_EVERY_MS = 24UL * 3600 * 1000;

inline bool otaShouldCheck(bool enabled, bool online, uint32_t online_for_ms, bool checked_before,
                           uint32_t since_last_ms, bool busy) {
    if (!enabled || !online || busy) return false;
    if (!checked_before) return online_for_ms >= OTA_FIRST_CHECK_MS;
    return since_last_ms >= OTA_CHECK_EVERY_MS;
}

// May a READY board restart into the new version now? Only when asked, or when
// the person allowed automatic security installs and nothing is held down (a
// held key released by a restart would be a key typed by nobody).
inline bool otaMayApply(bool asked, bool auto_security, bool security, bool keys_held, bool pending_verify) {
    if (pending_verify || keys_held) return false;
    return asked || (auto_security && security);
}

// Commands that only read or manage updates - they never press, type or move
// anything, so they do not make the board "in use" (a dashboard polls status
// every second; counting that would keep a ready update from ever applying).
// Anything not listed counts as use: wrongly counting one only delays a restart.
inline bool otaQueryOnly(const char* cmd) {
    static const char* const q[] = {
        "status", "ping", "echo", "wifi_status", "wifi_networks", "storage_info", "clients", "macro_list",
        "pc_list", "vault_list", "ui_get", "companion_list", "actions", "ota_status", "ota_check",
        "ota_install", "ota_cancel", "ota_apply", "ota_settings", "ota_source", "led_status", "auth"};
    if (!cmd) return false;
    for (const char* x : q) if (!strcmp(cmd, x)) return true;
    return false;
}
