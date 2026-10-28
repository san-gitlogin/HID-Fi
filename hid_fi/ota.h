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
    sendJson(doc);
}
