// ==================== OTA ====================
// Updates, the board's side: which slot runs, whether a new firmware is still
// proving itself, the self-test that keeps it or sends the board back, and the
// record that lets the dashboard and the Companion say what happened after a
// restart. The decisions themselves are in ota_logic.h (tested on the host);
// this file is the glue to flash, NVS and the radio. Plan: docs/OTA.md.
//
// Never mark a new firmware valid before its self-test. The core marks it valid
// at start unless verifyRollbackLater() returns true - the override below is
// what makes rollback protect anything at all.
#pragma once
#include "ota_logic.h"
#include "esp_ota_ops.h"

// The Companion protocol this firmware speaks (docs/OTA.md, "compatibility").
// Boards that report none speak 1.
#define COMP_API 1

extern "C" bool verifyRollbackLater() { return true; }

#ifdef HIDFI_TEST_SELFTEST_FAIL
static const bool OTA_FORCED_FAIL = true;    // a test build: must fail its self-test
#else
static const bool OTA_FORCED_FAIL = false;
#endif

struct OtaRuntime {
    bool twoSlot = false;          // this table has a second app slot
    String slot = "?";             // app0 / app1
    bool pending = false;          // running a new firmware that has not proved itself
    bool abortedOther = false;     // the bootloader rejected the other slot at some point
    OtaOutcome outcome = OTA_OUT_NONE;
    String from, to, why;          // the record, as read at start
    String recSlot;                // the slot the recorded update went into
    bool needNetwork = false;      // the board was online when the update began
    OtaSelfTestOut last = {OTA_WAIT, ""};
    bool restartDeferred = false;
} ota;

static const char* otaOutcomeStr(OtaOutcome o) {
    switch (o) {
        case OTA_OUT_VERIFYING:   return "verifying";
        case OTA_OUT_UPDATED:     return "updated";
        case OTA_OUT_ROLLED_BACK: return "rolled_back";
        default:                  return "none";
    }
}

static const char* otaStateStr() {
    if (!ota.twoSlot) return "single_slot";
    return ota.pending ? "pending_verify" : "valid";
}

// Writes the record. Each call is one small NVS write - per step, never per
// chunk of a download (flash wear, docs/OTA.md "Checkpoints").
static void otaRecord(const char* step, const String& to, const String& why, const String& slot) {
    preferences.begin("ota", false);
    preferences.putString("step", step);
    preferences.putString("to", to);
    preferences.putString("why", why);
    preferences.putString("slot", slot);
    preferences.end();
}

// Early in setup(): what is running, and what happened since the last start.
void otaBoot() {
    const esp_partition_t* run = esp_ota_get_running_partition();
    if (run) ota.slot = run->label;
    ota.twoSlot = esp_ota_get_next_update_partition(NULL) != NULL;
    esp_ota_img_states_t st;
    ota.pending = run && esp_ota_get_state_partition(run, &st) == ESP_OK && st == ESP_OTA_IMG_PENDING_VERIFY;
    ota.abortedOther = esp_ota_get_last_invalid_partition() != NULL;

    preferences.begin("ota", true);
    String step = preferences.getString("step", "");
    ota.from = preferences.getString("from", "");
    ota.to   = preferences.getString("to", "");
    ota.why  = preferences.getString("why", "");
    ota.recSlot = preferences.getString("slot", "");
    ota.needNetwork = preferences.getBool("net", false);
    preferences.end();

    OtaRecord rec = {step.c_str(), ota.to.c_str(), ota.recSlot.c_str()};
    ota.outcome = otaOutcome(rec, FW_VERSION, ota.slot.c_str(), ota.pending, ota.abortedOther);

    if (ota.pending && !otaStepOpen(step.c_str())) {
        // Installed without a record (over the cable): note it, so a crash before
        // the self-test is reported as this version not starting.
        ota.to = FW_VERSION;
        ota.needNetwork = false;           // nothing says the network was there before
        otaRecord("pending", ota.to, "", ota.slot);
    } else if (ota.pending) {
        preferences.begin("ota", false);
        preferences.putString("step", "pending");
        preferences.end();
    } else if (ota.outcome == OTA_OUT_UPDATED && otaStepOpen(step.c_str())) {
        // Marked valid, then the power went before the record said so: close it.
        otaRecord("valid", ota.to, "", ota.recSlot);
    } else if (ota.outcome == OTA_OUT_ROLLED_BACK && step != "rolled_back") {
        if (!ota.why.length()) ota.why = "did_not_start";   // a reset before the self-test ended
        otaRecord("rolled_back", ota.to, ota.why, ota.recSlot);
    }
    COM_SERIAL.printf("[OTA] %s, slot %s, %s%s%s\n", ota.twoSlot ? "two slots" : "single slot",
                      ota.slot.c_str(), otaOutcomeStr(ota.outcome),
                      ota.why.length() ? " - " : "", ota.why.c_str());
#ifdef HIDFI_TEST_CRASH_AT_BOOT
    if (ota.pending) {
        COM_SERIAL.println("[OTA] test build: crashing before the self-test");
        delay(100);
        abort();
    }
#endif
}

bool otaPendingVerify() { return ota.pending; }

// From loop(), cheap and never blocking: once a second while a new firmware is
// proving itself.
void otaTick() {
    if (!ota.pending) return;
    static uint32_t lastAt = 0;
    if (millis() - lastAt < 1000) return;
    lastAt = millis();
    OtaSelfTestIn in = {millis(), hidReady, ota.needNetwork, wifiConnected, OTA_FORCED_FAIL};
    ota.last = otaSelfTest(in);
    if (ota.last.verdict == OTA_WAIT) return;

    StaticJsonDocument<192> ev;
    ev["event"] = "ota";
    if (ota.last.verdict == OTA_PASS) {
        esp_err_t e = esp_ota_mark_app_valid_cancel_rollback();
        if (e != ESP_OK) {
            COM_SERIAL.printf("[OTA] could not mark this firmware valid: %s\n", esp_err_to_name(e));
            return;                        // try again next second; a reset still rolls back safely
        }
        ota.pending = false;
        ota.outcome = OTA_OUT_UPDATED;
        ota.why = "";
        otaRecord("valid", FW_VERSION, "", ota.slot);
        COM_SERIAL.printf("[OTA] self-test passed - %s is kept\n", FW_VERSION + 7);
        ev["result"] = "updated";
        ev["to"] = FW_VERSION;
        broadcastJson(ev);
        return;
    }
    // Failed: write down why, then let the bootloader take the board back.
    ota.why = ota.last.why;
    otaRecord("rolled_back", FW_VERSION, ota.why, ota.slot);
    COM_SERIAL.printf("[OTA] self-test failed (%s) - going back to the previous version\n", ota.why.c_str());
    ev["result"] = "rolling_back";
    ev["why"] = ota.why;
    broadcastJson(ev);
    delay(100);                            // let the line and the event leave
    esp_err_t e = esp_ota_mark_app_invalid_rollback_and_reboot();
    // Only returns when there is nothing to go back to. Keep running rather than
    // stop a board that has no other firmware; a reset would retry the same.
    COM_SERIAL.printf("[OTA] no previous version to go back to (%s) - staying\n", esp_err_to_name(e));
    ota.pending = false;
}

// Status fields every reader shares (status, /api/status, the dashboard).
void otaStatusFields(JsonDocument& doc) {
    doc["ota_layout"] = ota.twoSlot ? "two_slot" : "single";
    doc["ota_slot"]   = ota.slot;
    doc["ota_state"]  = otaStateStr();
    doc["ota_result"] = otaOutcomeStr(ota.outcome);
    doc["comp_api"]   = COMP_API;
}
void otaJobFields(JsonDocument& doc);


// ==================== OTA: CHECK, DOWNLOAD, INSTALL ====================
// A background task on core 0 (the radio's core) does the network and flash
// work; loop() on core 1 only reads its state and never waits for it, so typing
// and the mouse keep working (AGENTS rule 7). The task never touches the
// WebSocket or the web server - loop() reports for it.
//
// Order of trust: the manifest's signature is checked before anything in it is
// believed or shown; the image's SHA-256 (from the signed manifest) is checked
// while it streams in, again after esp_ota_end(), and once more by reading the
// slot back from flash - only then is the boot choice switched. The running
// slot is never written.
#include <HTTPClient.h>
#include <NetworkClientSecure.h>
#include "mbedtls/sha256.h"
#include "ota_verify.h"

// The keys a release may be signed with: primary and an offline backup, so a
// lost or leaked key can be replaced (docs/OTA.md). Added with the first signed
// release (step 9). Exactly tools/release_keys.json (tests/static/test_release_keys.py).
static const OtaKey OTA_RELEASE_KEYS[] = {
    // primary, key id fa48e032c618628f
    {{
        0x04, 0x49, 0x4d, 0xff, 0xb5, 0x37, 0x85, 0x0e, 0xc4, 0x15, 0x07, 0x0e, 0x75,
        0x8b, 0x1a, 0x62, 0x95, 0x97, 0x84, 0xdd, 0x22, 0xea, 0xb1, 0x75, 0x6c, 0xc6,
        0x35, 0xd7, 0xed, 0xc6, 0xe3, 0xa0, 0xaf, 0x76, 0xbb, 0x12, 0x4b, 0x6b, 0xe9,
        0xe8, 0xff, 0x66, 0x4e, 0x82, 0xa2, 0x15, 0x09, 0xbc, 0xe0, 0x35, 0xf7, 0x6b,
        0x01, 0x24, 0x1c, 0xa5, 0x47, 0xd4, 0x3e, 0xcb, 0x8b, 0x89, 0x5d, 0x7d, 0xa3,
    }},
    // backup, key id d06253869d956bec
    {{
        0x04, 0xee, 0x56, 0xa3, 0x2e, 0xd7, 0x96, 0x65, 0xf6, 0xf4, 0x58, 0x0e, 0xc7,
        0xcd, 0xf8, 0x1b, 0x84, 0xbb, 0x5d, 0x28, 0xd3, 0x4b, 0x22, 0x94, 0xd2, 0x4a,
        0xbd, 0x16, 0x93, 0x4d, 0xb8, 0x1f, 0x2f, 0xed, 0xea, 0xa7, 0x7b, 0xfa, 0x9c,
        0x89, 0x0f, 0xc5, 0x0c, 0xe4, 0xc4, 0x67, 0xce, 0x14, 0x3d, 0xab, 0x88, 0xc4,
        0x99, 0xec, 0x05, 0x39, 0x56, 0x97, 0xec, 0xcc, 0x9f, 0x50, 0x57, 0xe2, 0x9a,
    }},
};
static const size_t OTA_RELEASE_KEY_COUNT = sizeof(OTA_RELEASE_KEYS) / sizeof(OTA_RELEASE_KEYS[0]);
#ifdef HIDFI_TEST_OTA_KEY
// TEST key from tests/vectors/ota_v1.json - only in -DHIDFI_TEST_OTA_KEY builds;
// tools/make_release.sh refuses an image that contains it.
static const char OTA_TEST_KEY_HEX[] =
    "0446f73c2faa078cc0ed394029be39afcda7fd03afaa8506813679dc7a659057fba6a6832b1a0179f1c1bf76edc1cb91395367fee91bb2eb6a9f46224d362ce626";
#endif

#define OTA_DEFAULT_SOURCE "https://github.com/san-gitlogin/HID-Fi/releases/latest/download/"
static const size_t OTA_MANIFEST_MAX = 24 * 1024;
static const uint32_t OTA_IDLE_BEFORE_RESTART_MS = 5000;
static const uint32_t OTA_IN_USE_MS = 3000;     // input this recent means someone is using it
static const uint32_t OTA_ERASE_GAP_MS = 20;     // between two sector erases

struct OtaJob {
    volatile OtaStep step = OTA_STEP_IDLE;
    volatile bool reqCheck = false, reqInstall = false, cancel = false, applyAsked = false;
    volatile uint32_t bytes = 0, total = 0;
    volatile bool checkedOnce = false, available = false, upToDate = false;
    volatile bool paused = false;          // waiting for the keyboard and pointer to rest before erasing
    volatile uint32_t lastCheckAt = 0;
    // Written by the task, read by loop() under otaMux.
    String error;                          // "" or why the last check/install stopped
    String version, minFrom, imageName, sha256, risk, changelog, source;
    uint32_t size = 0;
    bool security = false;
    String installedSlot;
} job;
static SemaphoreHandle_t otaMux = nullptr;
static bool otaAuto = true, otaAutoSecurity = false;

static void otaSetError(const char* why) {
    xSemaphoreTake(otaMux, portMAX_DELAY);
    job.error = why;
    xSemaphoreGive(otaMux);
    COM_SERIAL.printf("[OTA] stopped: %s\n", why);
}

static size_t otaTrustedKeys(OtaKey* out, size_t max) {
    size_t n = 0;
    for (size_t i = 0; i < OTA_RELEASE_KEY_COUNT && n < max; i++) out[n++] = OTA_RELEASE_KEYS[i];
#ifdef HIDFI_TEST_OTA_KEY
    if (n < max && otaHexDecode(OTA_TEST_KEY_HEX, out[n].pub, 65)) n++;
#endif
    return n;
}

// The Companion's relay, for one check (RAM only, set by compOta "from"). Set on
// the loop's core, taken on the worker's: always under otaMux.
static String otaRelaySource;

static String otaStoredSource() {
    preferences.begin("ota", true);
    String s = preferences.getString("src", "");
    preferences.end();
    return s.length() ? s : String(OTA_DEFAULT_SOURCE);
}

// Where this check reads from - the relay once if one is set, else the setting.
// Only the check calls it: the install reads job.source, captured here.
static String otaSource() {
    xSemaphoreTake(otaMux, portMAX_DELAY);
    String s = otaRelaySource;
    otaRelaySource = "";
    xSemaphoreGive(otaMux);
    return s.length() ? s : otaStoredSource();
}

static void otaSetRelay(const String& s) {
    xSemaphoreTake(otaMux, portMAX_DELAY);
    otaRelaySource = s;
    xSemaphoreGive(otaMux);
}

// One GET into a PSRAM buffer, at most `max` bytes. Follows redirects (GitHub
// sends two, the last with a ~1.3 KB Location). Returns the length, -1 on error.
static int otaFetch(const String& url, uint8_t* buf, size_t max) {
    HTTPClient http;
    NetworkClientSecure tls;
    NetworkClient plain;
    bool https = url.startsWith("https://");
    if (https) { tls.useBuiltinCACertBundle(); tls.setHandshakeTimeout(15); }
    if (!(https ? http.begin(tls, url) : http.begin(plain, url))) return -1;
    http.setFollowRedirects(HTTPC_FORCE_FOLLOW_REDIRECTS);
    http.setRedirectLimit(5);
    http.setTimeout(10000);
    int code = http.GET();
    if (code != 200) { http.end(); return -1; }
    int len = http.getSize();
    if (len > (int)max) { http.end(); return -1; }
    NetworkClient* s = http.getStreamPtr();
    size_t got = 0;
    uint32_t until = millis() + 15000;
    while ((len < 0 || got < (size_t)len) && got < max && (long)(until - millis()) > 0) {
        if (!http.connected() && !s->available()) break;
        int n = s->read(buf + got, max - got);
        if (n > 0) got += n; else vTaskDelay(pdMS_TO_TICKS(5));
        if (job.cancel) { http.end(); return -1; }
    }
    http.end();
    if (len >= 0 && got != (size_t)len) return -1;
    return (int)got;
}

static void otaDoCheck() {
    job.step = OTA_STEP_CHECKING;
    // A new check forgets the last one: nothing stays "available" unless THIS
    // manifest's signature holds.
    job.available = false;
    job.upToDate = false;
    xSemaphoreTake(otaMux, portMAX_DELAY);
    job.version = ""; job.minFrom = ""; job.imageName = ""; job.sha256 = ""; job.risk = ""; job.changelog = "";
    job.size = 0; job.security = false;
    xSemaphoreGive(otaMux);
    String src = otaSource();
    uint8_t* man = (uint8_t*)ps_malloc(OTA_MANIFEST_MAX);
    char sigbuf[512];
    if (!man) { otaSetError("memory"); job.step = OTA_STEP_IDLE; return; }
    int mlen = otaFetch(src + "hidfi-ota.json", man, OTA_MANIFEST_MAX);
    int slen = mlen > 0 ? otaFetch(src + "hidfi-ota.sig", (uint8_t*)sigbuf, sizeof(sigbuf) - 1) : -1;
    job.checkedOnce = true;
    job.lastCheckAt = millis();
    if (mlen <= 0 || slen <= 0) {
        free(man);
        otaSetError(job.cancel ? "cancelled" : "no_connection");
        job.step = OTA_STEP_IDLE;
        return;
    }
    sigbuf[slen] = 0;
    // Believe nothing before the signature holds.
    JsonDocument sd;
    OtaKey keys[4];
    size_t nk = otaTrustedKeys(keys, 4);
    if (deserializeJson(sd, sigbuf) || !otaVerifyManifest(man, mlen, sd["key_id"] | "", sd["sig"] | "", keys, nk)) {
        free(man);
        otaSetError("bad_signature");
        job.step = OTA_STEP_IDLE;
        return;
    }
    JsonDocument m;
    DeserializationError e = deserializeJson(m, (const char*)man, mlen);
    free(man);
    if (e || (m["format"] | 0) != 1 || strcmp(m["product"] | "", "hid-fi") != 0) {
        otaSetError("bad_manifest");
        job.step = OTA_STEP_IDLE;
        return;
    }
    const esp_partition_t* next = esp_ota_get_next_update_partition(NULL);
    OtaOffer offer = {m["version"] | "", m["min_from"] | "", m["chip"] | "", m["image"]["size"] | 0u, false};
    OtaRefusal r = otaCanInstall(offer, FW_VERSION, next ? next->size : 0);
    // Only the sections newer than what runs (the manifest carries all since min_from).
    String cl;
    {
        JsonDocument keep;
        JsonArray out = keep.to<JsonArray>();
        OtaVersion running = otaParseVersion(FW_VERSION);
        for (JsonObject s : m["changelog"].as<JsonArray>()) {
            OtaVersion v = otaParseVersion(s["version"] | "");
            if (v.ok && running.ok && otaCompare(v, running) > 0) out.add(s);
        }
        serializeJson(keep, cl);
    }
    xSemaphoreTake(otaMux, portMAX_DELAY);
    job.version = m["version"] | "";
    job.minFrom = m["min_from"] | "";
    job.imageName = m["image"]["name"] | "";
    job.sha256 = m["image"]["sha256"] | "";
    job.size = m["image"]["size"] | 0u;
    job.security = m["security"] | false;
    job.risk = m["risk"] | "";
    job.changelog = cl;
    job.source = src;
    job.error = r == OTA_INSTALL_OK || r == OTA_REF_NOT_NEWER ? "" : otaRefusalStr(r);
    xSemaphoreGive(otaMux);
    job.available = r == OTA_INSTALL_OK && next;
    job.upToDate = r == OTA_REF_NOT_NEWER;
    job.step = OTA_STEP_IDLE;
    COM_SERIAL.printf("[OTA] checked: %s %s\n", job.available ? "update" : job.upToDate ? "up to date" : "not installable",
                      job.version.c_str());
}

static bool otaHexEq(const uint8_t h[32], const String& hex) {
    uint8_t want[32];
    return hex.length() == 64 && otaHexDecode(hex.c_str(), want, 32) && memcmp(h, want, 32) == 0;
}

#ifdef HIDFI_TEST_LOOP_STATS
volatile uint32_t otaEraseMaxMs = 0, otaWriteMaxMs = 0;   // test builds: how long the flash stalls were
#endif

// Erasing flash is what stalls both cores (measured on the board up to ~450 ms for
// one 4 KB sector on this flash; core 3.3.12 has no auto-suspend). Writing into
// flash that is already erased costs only short page programs. So the slot is
// written directly (not through esp_ota_write, which erases as it goes), and a
// sector is erased only while nobody is typing or moving the pointer; while
// someone is, the download waits ("paused") rather than make them feel it.
static bool otaEnsureErased(const esp_partition_t* part, uint32_t& erasedTo, uint32_t upTo) {
    while (erasedTo < upTo) {
        if (job.cancel) return false;
        if (millis() - lastHidAt < OTA_IN_USE_MS) { job.paused = true; vTaskDelay(pdMS_TO_TICKS(50)); continue; }
        job.paused = false;
#ifdef HIDFI_TEST_LOOP_STATS
        uint32_t t0 = millis();
#endif
        if (esp_partition_erase_range(part, erasedTo, 4096) != ESP_OK) return false;
#ifdef HIDFI_TEST_LOOP_STATS
        uint32_t d = millis() - t0; if (d > otaEraseMaxMs) otaEraseMaxMs = d;
#endif
        erasedTo += 4096;
        vTaskDelay(pdMS_TO_TICKS(OTA_ERASE_GAP_MS));   // never two stalls back to back
    }
    job.paused = false;
    return true;
}

static void otaDoInstall() {
    if (!job.available) { otaSetError("nothing_to_install"); return; }
    const esp_partition_t* part = esp_ota_get_next_update_partition(NULL);
    if (!part) { otaSetError("single_slot"); return; }
    String url = job.source + job.imageName;
    uint32_t total = job.size;
    if (total > part->size) { otaSetError("too_big"); return; }
    job.total = total; job.bytes = 0; job.cancel = false; job.paused = false;
    job.step = OTA_STEP_DOWNLOADING;
    mbedtls_sha256_context sha;
    mbedtls_sha256_init(&sha); mbedtls_sha256_starts(&sha, 0);
    // Only while installing: 4 KB of internal RAM that nobody pays for otherwise.
    const size_t BUF = 4096;
    uint8_t* buf = (uint8_t*)heap_caps_malloc(BUF, MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
    if (!buf) { otaSetError("memory"); job.step = OTA_STEP_IDLE; return; }
    uint32_t written = 0, erasedTo = 0;
    int attempts = 0;
    bool ok = false, writeFailed = false;
    while (!job.cancel) {
        HTTPClient http;
        NetworkClientSecure tls;
        NetworkClient plain;
        bool https = url.startsWith("https://");
        if (https) { tls.useBuiltinCACertBundle(); tls.setHandshakeTimeout(15); }
        bool began = https ? http.begin(tls, url) : http.begin(plain, url);
        http.setFollowRedirects(HTTPC_FORCE_FOLLOW_REDIRECTS);
        http.setRedirectLimit(5);
        http.setTimeout(10000);
        if (written) http.addHeader("Range", "bytes=" + String(written) + "-");
        int code = began ? http.GET() : -1;
        if (code == 200 && written) {
            // The server ignored Range: start again from byte 0 (the sectors already
            // erased and written are erased again as they are reached).
            mbedtls_sha256_free(&sha); mbedtls_sha256_init(&sha); mbedtls_sha256_starts(&sha, 0);
            written = 0; erasedTo = 0; job.bytes = 0;
        }
        if (code == 200 || code == 206) {
            NetworkClient* s = http.getStreamPtr();
            uint32_t quiet = millis();
            while (written < total && !job.cancel) {
                // Someone is typing or moving the pointer: do not even read. The radio
                // and the network stack stay theirs; TCP's own window holds the sender,
                // and if it gives up the download resumes with Range.
                if (millis() - lastHidAt < OTA_IN_USE_MS) {
                    job.paused = true;
                    vTaskDelay(pdMS_TO_TICKS(50));
                    quiet = millis();
                    continue;
                }
                job.paused = false;
                int avail = s->available();
                if (avail <= 0) {
                    if (!http.connected() || millis() - quiet > 15000) break;
                    vTaskDelay(pdMS_TO_TICKS(5));
                    continue;
                }
                int n = s->read(buf, min((uint32_t)BUF, total - written));
                if (n <= 0) continue;
                if (!otaEnsureErased(part, erasedTo, written + n)) { if (!job.cancel) writeFailed = true; break; }
                quiet = millis();
                mbedtls_sha256_update(&sha, buf, n);
#ifdef HIDFI_TEST_LOOP_STATS
                uint32_t t0 = millis();
#endif
#ifndef HIDFI_TEST_NO_FLASH_WRITE
                if (esp_partition_write(part, written, buf, n) != ESP_OK) { writeFailed = true; break; }
#endif
#ifdef HIDFI_TEST_LOOP_STATS
                uint32_t d = millis() - t0; if (d > otaWriteMaxMs) otaWriteMaxMs = d;
#endif
                written += n;
                job.bytes = written;
                vTaskDelay(1);
            }
        }
        http.end();
        if (writeFailed) { otaSetError("flash_write"); break; }
        if (written >= total) { ok = true; break; }
        if (job.cancel) break;
        attempts++;
        long from = otaResumeFrom(written, total, code == 206 || code == 200, attempts);
        if (from < 0) { otaSetError("no_connection"); break; }
        if (from == 0 && written) {
            mbedtls_sha256_free(&sha); mbedtls_sha256_init(&sha); mbedtls_sha256_starts(&sha, 0);
            written = 0; erasedTo = 0; job.bytes = 0;
        }
        uint32_t wait = otaRetryDelayMs(attempts), t0 = millis();
        while (millis() - t0 < wait && !job.cancel) vTaskDelay(pdMS_TO_TICKS(100));
    }
    uint8_t digest[32];
    mbedtls_sha256_finish(&sha, digest);
    mbedtls_sha256_free(&sha);
    job.paused = false;
    if (!ok) {
        free(buf);
        if (job.cancel) otaSetError("cancelled");
        job.cancel = false;
        job.step = OTA_STEP_IDLE;
        return;
    }
    job.step = OTA_STEP_VERIFYING;
    if (!otaHexEq(digest, job.sha256)) { free(buf); otaSetError("hash"); job.step = OTA_STEP_IDLE; return; }
    // Read the slot back: what is in flash is what was signed.
    mbedtls_sha256_init(&sha); mbedtls_sha256_starts(&sha, 0);
    for (uint32_t off = 0; off < total; off += BUF) {
        uint32_t n = min((uint32_t)BUF, total - off);
        if (esp_partition_read(part, off, buf, n) != ESP_OK) { free(buf); otaSetError("flash_read"); job.step = OTA_STEP_IDLE; return; }
        mbedtls_sha256_update(&sha, buf, n);
        if (job.cancel) { free(buf); job.cancel = false; otaSetError("cancelled"); job.step = OTA_STEP_IDLE; return; }
    }
    free(buf);
    mbedtls_sha256_finish(&sha, digest);
    mbedtls_sha256_free(&sha);
    if (!otaHexEq(digest, job.sha256)) { otaSetError("flash_verify"); job.step = OTA_STEP_IDLE; return; }
    job.step = OTA_STEP_SWITCHING;
    // esp_ota_set_boot_partition() checks the image itself (format, segments, its
    // own appended SHA-256) and refuses an invalid one - a fourth check.
    esp_err_t se = esp_ota_set_boot_partition(part);
    if (se != ESP_OK) { otaSetError(se == ESP_ERR_OTA_VALIDATE_FAILED ? "image" : "switch"); job.step = OTA_STEP_IDLE; return; }
    preferences.begin("ota", false);
    preferences.putString("step", "switched");
    preferences.putString("to", "hid_fi_v" + job.version);
    preferences.putString("from", FW_VERSION);
    preferences.putString("why", "");
    preferences.putString("slot", part->label);
    preferences.putBool("net", wifiConnected);
    preferences.end();
    job.installedSlot = part->label;
    job.step = OTA_STEP_READY;
    COM_SERIAL.printf("[OTA] %s is in %s - it starts at the next restart\n", job.version.c_str(), part->label);
}

static void otaWorker(void*) {
    for (;;) {
        if (job.reqCheck) { job.reqCheck = false; job.cancel = false; otaDoCheck(); }
        if (job.reqInstall) { job.reqInstall = false; job.cancel = false; otaDoInstall(); }
        vTaskDelay(pdMS_TO_TICKS(200));
    }
}

void otaStartWorker() {
    preferences.begin("ota", true);
    otaAuto = preferences.getBool("auto", true);
    otaAutoSecurity = preferences.getBool("autosec", false);
    preferences.end();
    otaMux = xSemaphoreCreateMutex();
    if (ota.twoSlot) xTaskCreatePinnedToCore(otaWorker, "ota", 12288, nullptr, 1, nullptr, 0);
}

static const char* otaStepStr() {
    switch (job.step) {
        case OTA_STEP_CHECKING:    return "checking";
        case OTA_STEP_DOWNLOADING: return "downloading";
        case OTA_STEP_VERIFYING:   return "verifying";
        case OTA_STEP_SWITCHING:   return "switching";
        case OTA_STEP_READY:       return "ready";
        default:                   return "idle";
    }
}

static bool otaBusy() {
    OtaStep s = job.step;
    return s != OTA_STEP_IDLE && s != OTA_STEP_READY;
}

// From loop(): automatic checks, automatic security installs the person
// allowed, applying a ready update at a safe moment, and progress events.
void otaService() {
    if (!otaMux) return;
    static uint32_t onlineSince = 0;
    if (wifiConnected) { if (!onlineSince) onlineSince = millis(); } else onlineSince = 0;
    if (ota.twoSlot && !ota.pending &&
        otaShouldCheck(otaAuto, wifiConnected, onlineSince ? millis() - onlineSince : 0, job.checkedOnce,
                       millis() - job.lastCheckAt, otaBusy() || job.step == OTA_STEP_READY)) {
        job.reqCheck = true;
        job.checkedOnce = true;            // one request; the task sets the time when it ran
        job.lastCheckAt = millis();
    }
    if (job.available && job.step == OTA_STEP_IDLE && otaAutoSecurity && job.security && !job.reqInstall
        && job.error.length() == 0) {
        job.reqInstall = true;
    }
    if (job.step == OTA_STEP_READY &&
        otaMayApply(job.applyAsked, otaAutoSecurity, job.security,
                    millis() - lastHidAt < OTA_IDLE_BEFORE_RESTART_MS, ota.pending)) {
        COM_SERIAL.println("[OTA] restarting into the new version");
        Keyboard.releaseAll();
        hidMouseButtons(0x07, 0, 1);
        delay(100);
        ESP.restart();
    }
    static OtaStep lastStep = OTA_STEP_IDLE;
    static uint32_t lastAt = 0, lastBytes = 0;
    if (job.step != lastStep || (job.bytes != lastBytes && millis() - lastAt > 500)) {
        StaticJsonDocument<256> ev;
        ev["event"] = "ota_progress";
        ev["step"] = otaStepStr();
        ev["bytes"] = job.bytes;
        ev["total"] = job.total;
        xSemaphoreTake(otaMux, portMAX_DELAY);
        if (job.error.length() && job.step == OTA_STEP_IDLE) ev["error"] = job.error;
        xSemaphoreGive(otaMux);
        broadcastJson(ev);
        lastStep = job.step; lastBytes = job.bytes; lastAt = millis();
    }
}

// ---- commands -------------------------------------------------------------------
// All go through processCommand(): PIN-gated and refused from the home network
// without a PIN, like every other command. Never on the LAN-safe list.
static void otaReply(const char* reply, const char* refused = nullptr) {
    StaticJsonDocument<192> d;
    if (refused) { d["status"] = "error"; d["reply"] = reply; d["message"] = refused; }
    else { d["status"] = "ok"; d["reply"] = reply; d["step"] = otaStepStr(); }
    sendJson(d);
}

// ---- the actions: shared by the dashboard's commands and the paired Companion ----
// Each returns nullptr when done, otherwise why it was refused.

static const char* otaActCheck() {
    if (!ota.twoSlot) return "single_slot";
    if (ota.pending) return "verifying";
    if (otaBusy()) return "busy";
    xSemaphoreTake(otaMux, portMAX_DELAY); job.error = ""; xSemaphoreGive(otaMux);
    // Seen at once, not when the task picks it up: status never shows a stale step.
    job.step = OTA_STEP_CHECKING;
    job.reqCheck = true;
    return nullptr;
}

// Installs only what a check this boot found and verified - the person's
// consent is this command (from the dashboard or the Companion), or the
// automatic security setting they switched on.
static const char* otaActInstall() {
    if (ota.pending) return "verifying";
    if (otaBusy() || job.step == OTA_STEP_READY) return "busy";
    if (!job.available) return "nothing_to_install";
    xSemaphoreTake(otaMux, portMAX_DELAY); job.error = ""; xSemaphoreGive(otaMux);
    // Progress from an earlier attempt must not show as this one's (a test cancelled
    // "too late" because of it), and the step changes the moment it is accepted.
    job.bytes = 0;
    job.total = job.size;
    job.step = OTA_STEP_DOWNLOADING;
    job.reqInstall = true;
    return nullptr;
}

// done: what cancel did - "stopping", "switched_back" or "nothing".
static const char* otaActCancel(const char** done) {
    OtaCancel a = otaCancelAction(job.step);
    *done = "nothing";
    if (a == OTA_CANCEL_REFUSED) return "switching";           // the 0.1 s otadata write: ask again
    if (a == OTA_CANCEL_STOP) {
        if (job.reqInstall || job.reqCheck) {
            // Accepted but not started yet: withdraw it, nothing ran.
            job.reqInstall = job.reqCheck = false;
            job.step = OTA_STEP_IDLE;
            xSemaphoreTake(otaMux, portMAX_DELAY); job.error = "cancelled"; xSemaphoreGive(otaMux);
        } else {
            job.cancel = true;
        }
        *done = "stopping";
        return nullptr;
    }
    if (a == OTA_CANCEL_SWITCH_BACK) {
        // Cancelling a ready update must leave no way for it to start. Choosing the
        // running slot again is not enough: IDF writes that choice as NEW, so after
        // the next restart the running version would be on probation, and a reset
        // in that minute would roll "back" into the cancelled image (found on the
        // board, 2026-10-28). So: erase the cancelled image's first sector - it can
        // never start, not even by rollback - then choose the running slot and
        // confirm it at once; it is the version that was already proven.
        const esp_partition_t* run = esp_ota_get_running_partition();
        const esp_partition_t* other = esp_ota_get_next_update_partition(NULL);
        bool erased = other && esp_partition_erase_range(other, 0, 4096) == ESP_OK;
        if (!(run && erased && esp_ota_set_boot_partition(run) == ESP_OK
              && esp_ota_mark_app_valid_cancel_rollback() == ESP_OK)) return "switch_back_failed";
        otaRecord("", "", "", "");
        job.step = OTA_STEP_IDLE;
        job.applyAsked = false;
        *done = "switched_back";
    }
    return nullptr;
}

static const char* otaActApply() {
    if (job.step != OTA_STEP_READY) return "not_ready";
    job.applyAsked = true;
    return nullptr;
}

void handleOtaCheck() { otaReply("ota_check", otaActCheck()); }
void handleOtaInstall() { otaReply("ota_install", otaActInstall()); }

void handleOtaCancel() {
    const char* done;
    const char* why = otaActCancel(&done);
    StaticJsonDocument<192> d;
    d["reply"] = "ota_cancel";
    if (why) { d["status"] = "error"; d["message"] = why; }
    else { d["status"] = "ok"; d["done"] = done; }
    sendJson(d);
}

void handleOtaApply() {
    const char* why = otaActApply();
    if (why) return otaReply("ota_apply", why);
    StaticJsonDocument<192> d;
    d["status"] = "ok"; d["reply"] = "ota_apply";
    d["waiting_for_idle"] = millis() - lastHidAt < OTA_IDLE_BEFORE_RESTART_MS;
    sendJson(d);
}

void handleOtaSettings(const JsonDocument& in) {
    preferences.begin("ota", false);
    if (in["auto"].is<bool>()) { otaAuto = in["auto"]; preferences.putBool("auto", otaAuto); }
    if (in["auto_security"].is<bool>()) { otaAutoSecurity = in["auto_security"]; preferences.putBool("autosec", otaAutoSecurity); }
    preferences.end();
    StaticJsonDocument<192> d;
    d["status"] = "ok"; d["reply"] = "ota_settings";
    d["auto"] = otaAuto; d["auto_security"] = otaAutoSecurity;
    sendJson(d);
}

// USB only: where to look for releases (tests serve them from this computer).
// The signature rules do not change - only keys the firmware trusts count.
void handleOtaSource(const JsonDocument& in, bool fromSerial) {
    if (!fromSerial) return otaReply("ota_source", "usb_only");
    if (in["url"].isNull()) {                 // no url: only read it
        StaticJsonDocument<256> d;
        d["status"] = "ok"; d["reply"] = "ota_source"; d["url"] = otaStoredSource();
        return sendJson(d);
    }
    String url = String(in["url"] | "");
    if (url.length() && !url.endsWith("/")) url += "/";
    if (url.length() && !url.startsWith("http://") && !url.startsWith("https://")) return otaReply("ota_source", "bad_url");
    preferences.begin("ota", false);
    if (url.length()) preferences.putString("src", url); else preferences.remove("src");
    preferences.end();
    StaticJsonDocument<256> d;
    d["status"] = "ok"; d["reply"] = "ota_source"; d["url"] = otaStoredSource();
    sendJson(d);
}

void otaJobFields(JsonDocument& doc) {
    doc["ota_step"] = otaStepStr();
    doc["ota_bytes"] = job.bytes;
    doc["ota_total"] = job.total;
    doc["ota_auto"] = otaAuto;
    doc["ota_auto_security"] = otaAutoSecurity;
    doc["ota_paused"] = (bool)job.paused;
#ifdef HIDFI_TEST_LOOP_STATS
    doc["ota_erase_max_ms"] = otaEraseMaxMs;
    doc["ota_write_max_ms"] = otaWriteMaxMs;
#endif
    if (!otaMux) return;
    xSemaphoreTake(otaMux, portMAX_DELAY);
    doc["ota_available"] = job.available ? job.version : String("");
    doc["ota_error"] = job.error;
    xSemaphoreGive(otaMux);
}

void handleOtaStatus() {
    StaticJsonDocument<512> doc;
    doc["status"] = "ok";
    doc["reply"]  = "ota_status";
    otaStatusFields(doc);
    doc["firmware"] = FW_VERSION;
    doc["to"]   = ota.to;        // always present, empty when nothing is recorded:
    doc["from"] = ota.from;      // one shape for every reply (tests/board/test_sim_contract.py)
    doc["why"]  = ota.why;
    if (ota.pending) {
        JsonObject s = doc.createNestedObject("selftest");
        s["waiting_for"] = ota.last.why && *ota.last.why ? ota.last.why : "running";
        uint32_t up = millis();
        s["remaining_s"] = up >= OTA_SELFTEST_MIN_MS ? 0 : (OTA_SELFTEST_MIN_MS - up + 999) / 1000;
        s["needs_network"] = ota.needNetwork;
    }
    doc["restart_deferred"] = ota.restartDeferred;
    otaJobFields(doc);
    if (otaMux) {
        xSemaphoreTake(otaMux, portMAX_DELAY);
        if (job.version.length()) {
            JsonObject a = doc["release"].to<JsonObject>();
            a["version"] = job.version;
            a["size"] = job.size;
            a["security"] = job.security;
            a["risk"] = job.risk;
            a["installable"] = (bool)job.available;
            JsonDocument cl;
            if (!deserializeJson(cl, job.changelog)) a["changelog"] = cl.as<JsonArray>();
        }
        xSemaphoreGive(otaMux);
    }
    sendJson(doc);
}

// ==================== OTA: THE PAIRED COMPANION ====================
// POST /api/companion {op:"ota", id, act, url, chal, mac}: the Companion on the
// computer the board is plugged into checks, installs, cancels or applies an
// update for the person sitting at it, relays a release when the board has no
// internet ("from"), or restarts it into the ROM loader for a cable update over
// the native USB port ("download_mode"). Proven like a heartbeat: a one-use
// challenge and HMAC-SHA256 with this computer's pairing key over
//     "ota|" id "|" act "|" url "|" chal
// The signature rules for releases do not change: a relayed manifest must still
// be signed by a key the firmware trusts.
#include "soc/rtc_cntl_reg.h"

static uint32_t otaDownloadModeAt = 0;  // a restart into the ROM loader, after the reply has left

static void compOtaReply(const char* act, const char* done) {
    JsonDocument d;
    d["status"] = "ok"; d["act"] = act; d["step"] = otaStepStr();
    if (done) d["done"] = done;
    compReply(200, d);
}

static void compOta(const JsonDocument& in, uint32_t ip) {
    uint8_t id[16], chal[16], mac[32], want[32];
    const char* ch = in["chal"] | "";
    const char* act = in["act"] | "";
    const char* url = in["url"] | "";
    if (!comp_hex_parse(in["id"] | "", id, 16) || !comp_hex_parse(ch, chal, 16) ||
        !comp_hex_parse(in["mac"] | "", mac, 32) || strlen(url) > 160) {
        compRefuse(400, "bad_request", "Malformed update request"); return;
    }
    int i = compFindPaired(id);
    if (i < 0) { compRefuse(404, "unknown_pc", "This computer is not paired"); return; }
    CompPaired& c = compPaired[i];
    if (!compChalTake(c, chal)) { compRefuse(401, "stale_chal", "Ask for a new challenge"); return; }
    char msg[260];
    char idh[33]; comp_hex(id, 16, idh);
    snprintf(msg, sizeof(msg), "ota|%s|%s|%s|%s", idh, act, url, ch);
    if (comp_hmac(c.key, msg, want) != 0 || !comp_ct_equal(want, mac, 32)) {
        compRefuse(401, "bad_mac", "The update request's proof is wrong"); return;
    }
    const char* why = nullptr;
    const char* done = nullptr;
    if (!strcmp(act, "check")) {
        why = otaActCheck();
    } else if (!strcmp(act, "from")) {
        // Only the requesting computer itself may serve the release - never a third host.
        String prefix = "http://" + IPAddress(ip).toString() + ":";
        if (!String(url).startsWith(prefix)) { compRefuse(400, "bad_request", "A relay must be this computer"); return; }
        String relay = url;
        if (!relay.endsWith("/")) relay += "/";
        otaSetRelay(relay);
        why = otaActCheck();
        if (why) otaSetRelay("");
    } else if (!strcmp(act, "install")) {
        why = otaActInstall();
    } else if (!strcmp(act, "cancel")) {
        why = otaActCancel(&done);
    } else if (!strcmp(act, "apply")) {
        why = otaActApply();
    } else if (!strcmp(act, "download_mode")) {
        if (ota.pending) why = "verifying";        // a reset now would roll the update back
        else if (otaBusy() || job.step == OTA_STEP_READY) why = "busy";
        else otaDownloadModeAt = millis() + 300;
    } else {
        compRefuse(400, "bad_request", "Unknown act"); return;
    }
    if (why) { compRefuse(409, why, "Not now"); return; }
    compOtaReply(act, done);
}

// From loop(): the restart into the ROM loader, once the reply is out. Everything
// held is released first; the loader then appears as USB-Serial-JTAG and the
// Companion writes, and clears the flag, over that port.
void otaDownloadModeTick() {
    if (!otaDownloadModeAt || (long)(millis() - otaDownloadModeAt) < 0) return;
    COM_SERIAL.println("[OTA] restarting into the ROM loader for a cable update");
    Keyboard.releaseAll();
    hidMouseButtons(0x07, 0, 1);
    delay(100);
    REG_WRITE(RTC_CNTL_OPTION1_REG, RTC_CNTL_FORCE_DOWNLOAD_BOOT);
    esp_restart();
}
