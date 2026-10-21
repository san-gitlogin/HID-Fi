// HID-Fi companion: the board's side of screen sharing.
//
// The board is a directory and a trust anchor, never a video path: it pairs a
// computer running the companion app, remembers where that app is listening,
// and hands a signed-in dashboard a short-lived ticket to watch it. The video
// goes from the computer straight to the phone. Protocol: companion/DESIGN.md.
//
// Rules this file keeps, and why:
// - The app never holds a WebSocket. Three features count WebSocket clients
//   (release-all on the last disconnect, radio sleep, AP auto-off), so it speaks
//   short HTTP requests to /api/companion instead.
// - Nothing here is reachable without an access PIN set (F4): no PIN, no
//   pairing and no tickets.
// - The pairing key never leaves the board in any reply, on any transport.
// - A blind access-PIN reset removes every pairing (compWipeAll from set_auth),
//   the same rule as the vault.
// - Every input is bounded before it is copied, and nothing waits: the two
//   X25519 operations per pairing take tens of milliseconds, once.
//
// Included once from hid_fi.ino, after the globals it uses.
#pragma once

#include "companion_crypto.h"
#include "esp_random.h"

#define COMP_PAIRED_MAX   4
#define COMP_PENDING_MAX  4
#define COMP_NAME_MAX     32
#define COMP_BODY_MAX     768
#define COMP_PENDING_MS   120000UL   // an unanswered pairing request is forgotten after 2 min
#define COMP_ONLINE_MS    60000UL    // a computer counts as online for 60 s after a heartbeat
#define COMP_CHAL_MS      30000UL
#define COMP_CODE_TRIES   3
#define COMP_RATE_SLOTS   8
#define COMP_RATE_WINDOW  5000UL
#define COMP_RATE_MAX     25         // requests per address per window; the app needs ~2

enum CompPhase : uint8_t { CP_FREE = 0, CP_PENDING, CP_CONFIRM, CP_CODE_SHOWN, CP_ABORTED };

struct CompPending {
    CompPhase phase;
    uint8_t  id[16];
    char     name[COMP_NAME_MAX + 1];
    uint8_t  aPub[32];
    uint8_t  commit[32];
    uint8_t  bSec[32];       // held only between Pair and the reveal
    uint8_t  bPub[32];
    uint8_t  nB[16];
    uint8_t  key[32];        // derived at the reveal; stored only when the code is confirmed
    uint32_t code;
    uint8_t  tries;
    uint32_t ip;
    uint32_t at;             // created or last moved on
};

struct CompPaired {
    bool     used;
    uint8_t  id[16];
    char     name[COMP_NAME_MAX + 1];
    uint8_t  key[32];
    // Runtime only, from the last good heartbeat.
    uint32_t ip;
    uint16_t port;
    char     fp[65];
    char     tn[33];
    uint32_t seenAt;
    uint8_t  chal[16];
    uint32_t chalAt;
    bool     chalLive;
};

struct CompRate { uint32_t ip; uint32_t windowAt; uint16_t count; };

CompPending compPending[COMP_PENDING_MAX];
CompPaired  compPaired[COMP_PAIRED_MAX];
CompRate    compRate[COMP_RATE_SLOTS];

static int compRng(void*, unsigned char* out, size_t len) { esp_fill_random(out, len); return 0; }

static void compPendingFree(CompPending& p) { comp_wipe(&p, sizeof(p)); p.phase = CP_FREE; }

static bool compOnline(const CompPaired& c) {
    return c.used && c.seenAt && (millis() - c.seenAt) < COMP_ONLINE_MS;
}

// ---- NVS: namespace "compan", keys i0..i3 (id), n0..n3 (name), k0..k3 (key) ----

static void compNvsKey(char kind, int i, char* out) { out[0] = kind; out[1] = (char)('0' + i); out[2] = 0; }

void compLoad() {
    for (int i = 0; i < COMP_PAIRED_MAX; i++) comp_wipe(&compPaired[i], sizeof(CompPaired));
    for (int i = 0; i < COMP_PENDING_MAX; i++) compPendingFree(compPending[i]);
    preferences.begin("compan", true);
    for (int i = 0; i < COMP_PAIRED_MAX; i++) {
        char k[3];
        CompPaired& c = compPaired[i];
        compNvsKey('i', i, k);
        if (preferences.getBytesLength(k) != 16) continue;
        preferences.getBytes(k, c.id, 16);
        compNvsKey('k', i, k);
        if (preferences.getBytesLength(k) != 32) { comp_wipe(&c, sizeof(c)); continue; }
        preferences.getBytes(k, c.key, 32);
        compNvsKey('n', i, k);
        String n = preferences.getString(k, "Computer");
        strncpy(c.name, n.c_str(), COMP_NAME_MAX);
        c.name[COMP_NAME_MAX] = 0;
        c.used = true;
    }
    preferences.end();
}

static bool compStore(int slot) {
    CompPaired& c = compPaired[slot];
    char k[3];
    preferences.begin("compan", false);
    compNvsKey('i', slot, k); bool ok = preferences.putBytes(k, c.id, 16) == 16;
    compNvsKey('k', slot, k); ok = ok && preferences.putBytes(k, c.key, 32) == 32;
    compNvsKey('n', slot, k); ok = ok && preferences.putString(k, c.name) > 0;
    preferences.end();
    return ok;
}

static void compForget(int slot) {
    char k[3];
    preferences.begin("compan", false);
    compNvsKey('i', slot, k); preferences.remove(k);
    compNvsKey('k', slot, k); preferences.remove(k);
    compNvsKey('n', slot, k); preferences.remove(k);
    preferences.end();
    comp_wipe(&compPaired[slot], sizeof(CompPaired));
}

// From set_auth: a PIN replaced without proving the old one removes every
// pairing, or the cable would be a way to watch a paired computer's screen.
void compWipeAll() {
    preferences.begin("compan", false);
    preferences.clear();
    preferences.end();
    for (int i = 0; i < COMP_PAIRED_MAX; i++) comp_wipe(&compPaired[i], sizeof(CompPaired));
    for (int i = 0; i < COMP_PENDING_MAX; i++) compPendingFree(compPending[i]);
}

int compPairedCount() { int n = 0; for (auto& c : compPaired) n += c.used; return n; }
int compPendingCount() {
    int n = 0;
    for (auto& p : compPending) n += (p.phase != CP_FREE && p.phase != CP_ABORTED);
    return n;
}
int compOnlineCount() { int n = 0; for (auto& c : compPaired) n += compOnline(c); return n; }

static int compFindPaired(const uint8_t id[16]) {
    for (int i = 0; i < COMP_PAIRED_MAX; i++)
        if (compPaired[i].used && memcmp(compPaired[i].id, id, 16) == 0) return i;
    return -1;
}

static int compFindPending(const uint8_t id[16]) {
    for (int i = 0; i < COMP_PENDING_MAX; i++)
        if (compPending[i].phase != CP_FREE && memcmp(compPending[i].id, id, 16) == 0) return i;
    return -1;
}

// Called from loop() through compTick(): expiry only, never blocks.
void compTick() {
    static uint32_t last = 0;
    if (millis() - last < 1000) return;
    last = millis();
    for (auto& p : compPending)
        if (p.phase != CP_FREE && millis() - p.at > COMP_PENDING_MS) compPendingFree(p);
    for (auto& c : compPaired)
        if (c.chalLive && millis() - c.chalAt > COMP_CHAL_MS) { c.chalLive = false; comp_wipe(c.chal, 16); }
}

// ---- the app's endpoint: POST /api/companion ----

static bool compRateOk(uint32_t ip) {
    int slot = -1, oldest = 0;
    for (int i = 0; i < COMP_RATE_SLOTS; i++) {
        if (compRate[i].ip == ip) { slot = i; break; }
        if (compRate[i].windowAt < compRate[oldest].windowAt) oldest = i;
    }
    if (slot < 0) { slot = oldest; compRate[slot] = CompRate{ ip, millis(), 0 }; }
    CompRate& r = compRate[slot];
    if (millis() - r.windowAt > COMP_RATE_WINDOW) { r.windowAt = millis(); r.count = 0; }
    return ++r.count <= COMP_RATE_MAX;
}

static void compReply(int code, const JsonDocument& d) {
    String out;
    serializeJson(d, out);
    webServer.send(code, "application/json", out);
}

static void compRefuse(int code, const char* reply, const char* message) {
    JsonDocument d;
    d["status"] = "error"; d["reply"] = reply; d["message"] = message;
    if (!strcmp(reply, "rate_limited")) d["retry_ms"] = COMP_RATE_WINDOW;
    compReply(code, d);
}

static const char* compPhaseStr(CompPhase p) {
    switch (p) {
        case CP_PENDING:    return "pending";
        case CP_CONFIRM:    return "confirm";
        case CP_CODE_SHOWN: return "code_shown";
        case CP_ABORTED:    return "aborted";
        default:            return "none";
    }
}

// The state an app sees for its id. b_pub and nB are not secret: they are the
// board's half of the exchange, and only useful with the app's own private key.
static void compReplyState(const uint8_t id[16]) {
    JsonDocument d;
    d["status"] = "ok";
    int pi = compFindPending(id);
    if (pi >= 0) {
        CompPending& p = compPending[pi];
        d["state"] = compPhaseStr(p.phase);
        if (p.phase == CP_CONFIRM) {
            char hx[65];
            comp_hex(p.bPub, 32, hx); d["b_pub"] = hx;
            comp_hex(p.nB, 16, hx);   d["n_b"] = hx;
        }
    } else {
        d["state"] = compFindPaired(id) >= 0 ? "paired" : "none";
    }
    compReply(200, d);
}

static void compHello(const JsonDocument& in, uint32_t ip) {
    uint8_t id[16], aPub[32], commit[32];
    const char* name = in["name"] | "";
    if ((in["v"] | 0) != 1) { compRefuse(400, "unsupported", "Protocol version not supported"); return; }
    if (!comp_hex_parse(in["id"] | "", id, 16) || !comp_hex_parse(in["a_pub"] | "", aPub, 32) ||
        !comp_hex_parse(in["commit"] | "", commit, 32) || strlen(name) > COMP_NAME_MAX) {
        compRefuse(400, "bad_request", "Malformed hello"); return;
    }
    int pi = compFindPending(id);
    if (pi >= 0) {
        CompPending& p = compPending[pi];
        // The same attempt polling: answer where it stands.
        if (memcmp(p.aPub, aPub, 32) == 0 && memcmp(p.commit, commit, 32) == 0) { compReplyState(id); return; }
        // A new attempt from the same app (it restarted): start over. The old
        // half-finished one cannot be resumed with new keys.
        compPendingFree(p);
    }
    // One pending request per address, so one machine cannot fill the list.
    for (auto& p : compPending)
        if (p.phase != CP_FREE && p.ip == ip) compPendingFree(p);
    int slot = -1;
    for (int i = 0; i < COMP_PENDING_MAX; i++) if (compPending[i].phase == CP_FREE) { slot = i; break; }
    if (slot < 0) { compRefuse(503, "full", "Too many computers are asking to pair right now"); return; }
    CompPending& p = compPending[slot];
    compPendingFree(p);
    p.phase = CP_PENDING;
    memcpy(p.id, id, 16); memcpy(p.aPub, aPub, 32); memcpy(p.commit, commit, 32);
    strncpy(p.name, name[0] ? name : "Computer", COMP_NAME_MAX); p.name[COMP_NAME_MAX] = 0;
    // The app cleans the name too, but the board does not trust it to: no
    // control characters reach a dashboard or the serial log.
    for (char* q = p.name; *q; q++) if ((uint8_t)*q < 0x20 || *q == 0x7f) *q = ' ';
    p.ip = ip; p.at = millis();

    // Tell open dashboards now, so the request shows without waiting for a poll.
    JsonDocument ev;
    ev["event"] = "companion_pending"; ev["name"] = p.name;
    String out; serializeJson(ev, out);
    webSocket.broadcastTXT(out);
    compReplyState(id);
}

static void compReveal(const JsonDocument& in) {
    uint8_t id[16], nA[16], c[32], shared[32];
    if (!comp_hex_parse(in["id"] | "", id, 16) || !comp_hex_parse(in["n_a"] | "", nA, 16)) {
        compRefuse(400, "bad_request", "Malformed reveal"); return;
    }
    int pi = compFindPending(id);
    if (pi < 0 || compPending[pi].phase != CP_CONFIRM) { compRefuse(409, "not_ready", "Nothing to reveal yet"); return; }
    CompPending& p = compPending[pi];
    if (comp_commit(p.aPub, nA, c) != 0 || !comp_ct_equal(c, p.commit, 32)) {
        // Someone answered with a nonce that does not match what was promised.
        // That attempt is dead; the user starts again.
        compPendingFree(p);
        compRefuse(400, "bad_mac", "The reveal does not match the commitment"); return;
    }
    int rc = comp_x25519(p.bSec, p.aPub, shared, compRng, nullptr);
    comp_wipe(p.bSec, 32);
    if (rc != 0 || comp_sas(p.aPub, p.bPub, nA, p.nB, &p.code) != 0 ||
        comp_derive_key(shared, p.aPub, p.bPub, nA, p.nB, p.key) != 0) {
        comp_wipe(shared, 32);
        compPendingFree(p);
        compRefuse(400, "bad_request", "Unusable key"); return;
    }
    comp_wipe(shared, 32);
    p.phase = CP_CODE_SHOWN;
    p.at = millis();
    compReplyState(id);
}

static void compChal(const JsonDocument& in) {
    uint8_t id[16];
    if (!comp_hex_parse(in["id"] | "", id, 16)) { compRefuse(400, "bad_request", "Malformed id"); return; }
    int i = compFindPaired(id);
    if (i < 0) { compRefuse(404, "unknown_pc", "This computer is not paired"); return; }
    CompPaired& c = compPaired[i];
    esp_fill_random(c.chal, 16);
    c.chalAt = millis(); c.chalLive = true;
    char hx[33]; comp_hex(c.chal, 16, hx);
    JsonDocument d; d["status"] = "ok"; d["chal"] = hx;
    compReply(200, d);
}

static void compBeat(const JsonDocument& in, uint32_t ip) {
    uint8_t id[16], chal[16], mac[32], want[32];
    const char* fp = in["fp"] | "";
    const char* tn = in["tn"] | "";
    const char* ch = in["chal"] | "";
    long port = in["port"] | 0L;
    if (!comp_hex_parse(in["id"] | "", id, 16) || !comp_hex_parse(ch, chal, 16) ||
        !comp_hex_parse(in["mac"] | "", mac, 32) || !comp_hex_ok(fp, 32) || !comp_hex_ok(tn, 16) ||
        port < 1024 || port > 65535) {
        compRefuse(400, "bad_request", "Malformed heartbeat"); return;
    }
    int i = compFindPaired(id);
    if (i < 0) { compRefuse(404, "unknown_pc", "This computer is not paired"); return; }
    CompPaired& c = compPaired[i];
    // A challenge is good for one heartbeat, right or wrong, so a recorded
    // heartbeat can never be replayed.
    bool fresh = c.chalLive && millis() - c.chalAt <= COMP_CHAL_MS && comp_ct_equal(c.chal, chal, 16);
    c.chalLive = false; comp_wipe(c.chal, 16);
    if (!fresh) { compRefuse(401, "stale_chal", "Ask for a new challenge"); return; }
    char msg[200];
    char idh[33]; comp_hex(id, 16, idh);
    snprintf(msg, sizeof(msg), "beat|%s|%ld|%s|%s|%s", idh, port, fp, tn, ch);
    if (comp_hmac(c.key, msg, want) != 0 || !comp_ct_equal(want, mac, 32)) {
        compRefuse(401, "bad_mac", "The heartbeat proof is wrong"); return;
    }
    bool wasOnline = compOnline(c);
    // The address is where the request came from, never one the app claims.
    c.ip = ip; c.port = (uint16_t)port;
    strncpy(c.fp, fp, 64); c.fp[64] = 0;
    strncpy(c.tn, tn, 32); c.tn[32] = 0;
    c.seenAt = millis();
    if (!wasOnline) {
        JsonDocument ev;
        ev["event"] = "companion_online"; ev["name"] = c.name;
        String out; serializeJson(ev, out);
        webSocket.broadcastTXT(out);
    }
    JsonDocument d; d["status"] = "ok";
    compReply(200, d);
}

// webServer has already buffered the body; this refuses an oversized one before
// any further copy or parse.
void handleCompanionHttp() {
    uint32_t ip = (uint32_t)webServer.client().remoteIP();
    if (!compRateOk(ip)) { compRefuse(429, "rate_limited", "Too many requests"); return; }
    if (authToken.length() == 0) {
        compRefuse(403, "pin_required", "Set an access PIN on the board before sharing a screen"); return;
    }
    if (!webServer.hasArg("plain")) { compRefuse(400, "bad_request", "No body"); return; }
    const String& body = webServer.arg("plain");
    if (body.length() > COMP_BODY_MAX) { compRefuse(413, "bad_request", "Body too large"); return; }
    JsonDocument in;
    if (deserializeJson(in, body)) { compRefuse(400, "bad_request", "Invalid JSON"); return; }
    const char* op = in["op"] | "";
    if      (!strcmp(op, "hello"))  compHello(in, ip);
    else if (!strcmp(op, "state")) {
        uint8_t id[16];
        if (!comp_hex_parse(in["id"] | "", id, 16)) compRefuse(400, "bad_request", "Malformed id");
        else compReplyState(id);
    }
    else if (!strcmp(op, "reveal")) compReveal(in);
    else if (!strcmp(op, "chal"))   compChal(in);
    else if (!strcmp(op, "beat"))   compBeat(in, ip);
    else compRefuse(400, "bad_request", "Unknown op");
}

// ---- dashboard commands, through processCommand (PIN-gated like all others) ----

static bool compIdArg(const JsonDocument& in, uint8_t id[16]) {
    if (comp_hex_parse(in["id"] | "", id, 16)) return true;
    JsonDocument d;
    d["status"] = "error"; d["reply"] = "bad_request"; d["message"] = "id must be 32 lowercase hex digits";
    sendJson(d);
    return false;
}

void handleCompanionList() {
    JsonDocument d;
    d["status"] = "ok"; d["reply"] = "companion_list";
    d["pin_set"] = authToken.length() > 0;
    char hx[33];
    JsonArray pc = d["paired"].to<JsonArray>();
    for (auto& c : compPaired) {
        if (!c.used) continue;
        JsonObject o = pc.add<JsonObject>();
        comp_hex(c.id, 16, hx); o["id"] = hx;
        o["name"] = c.name;
        o["online"] = compOnline(c);
        if (c.seenAt) {
            o["ip"] = IPAddress(c.ip).toString();
            o["port"] = c.port;
            o["seen_s"] = (millis() - c.seenAt) / 1000;
        }
    }
    JsonArray pe = d["pending"].to<JsonArray>();
    for (auto& p : compPending) {
        if (p.phase == CP_FREE) continue;
        JsonObject o = pe.add<JsonObject>();
        comp_hex(p.id, 16, hx); o["id"] = hx;
        o["name"] = p.name;
        o["state"] = compPhaseStr(p.phase);
        o["ip"] = IPAddress(p.ip).toString();
    }
    sendJson(d);
}

// Two steps, one command. Without "code": approve the request, which makes the
// board's key and lets the app reveal. With "code": the six digits the computer
// shows. Three wrong codes end the attempt.
void handleCompanionPair(const JsonDocument& in) {
    uint8_t id[16];
    if (!compIdArg(in, id)) return;
    JsonDocument d;
    if (authToken.length() == 0) {
        d["status"] = "error"; d["reply"] = "pin_required";
        d["message"] = "Set an access PIN before pairing a computer";
        sendJson(d); return;
    }
    int pi = compFindPending(id);
    if (pi < 0) {
        d["status"] = "error"; d["reply"] = "not_found"; d["message"] = "That computer is no longer asking to pair";
        sendJson(d); return;
    }
    CompPending& p = compPending[pi];
    const char* code = in["code"] | "";
    if (!code[0]) {
        if (p.phase != CP_PENDING) {
            d["status"] = "error"; d["reply"] = "not_ready"; d["message"] = "Already approved";
            sendJson(d); return;
        }
        esp_fill_random(p.bSec, 32);
        esp_fill_random(p.nB, 16);
        if (comp_x25519(p.bSec, nullptr, p.bPub, compRng, nullptr) != 0) {
            compPendingFree(p);
            d["status"] = "error"; d["reply"] = "failed"; d["message"] = "Could not make a key";
            sendJson(d); return;
        }
        p.phase = CP_CONFIRM; p.at = millis();
        d["status"] = "ok"; d["reply"] = "companion_pair"; d["state"] = "confirm";
        sendJson(d); return;
    }
    if (p.phase != CP_CODE_SHOWN) {
        d["status"] = "error"; d["reply"] = "not_ready";
        d["message"] = "The computer has not shown its code yet";
        sendJson(d); return;
    }
    // Exactly six digits.
    bool shape = strlen(code) == 6;
    uint32_t got = 0;
    for (int i = 0; shape && i < 6; i++) {
        if (code[i] < '0' || code[i] > '9') shape = false;
        else got = got * 10 + (uint32_t)(code[i] - '0');
    }
    if (!shape || got != p.code) {
        if (++p.tries >= COMP_CODE_TRIES) {
            uint8_t keepId[16]; memcpy(keepId, p.id, 16);
            char keepName[COMP_NAME_MAX + 1]; memcpy(keepName, p.name, sizeof(keepName));
            uint32_t keepIp = p.ip;
            compPendingFree(p);
            // Kept as "aborted" so the app can tell the user, instead of polling forever.
            p.phase = CP_ABORTED; memcpy(p.id, keepId, 16); memcpy(p.name, keepName, sizeof(keepName));
            p.ip = keepIp; p.at = millis();
            d["status"] = "error"; d["reply"] = "aborted";
            d["message"] = "Wrong code three times. Start pairing again on the computer.";
        } else {
            d["status"] = "error"; d["reply"] = "wrong_code";
            d["message"] = "That is not the code on the computer";
            d["tries_left"] = COMP_CODE_TRIES - p.tries;
        }
        sendJson(d); return;
    }
    int slot = compFindPaired(id);
    if (slot < 0) for (int i = 0; i < COMP_PAIRED_MAX; i++) if (!compPaired[i].used) { slot = i; break; }
    if (slot < 0) {
        d["status"] = "error"; d["reply"] = "full";
        d["message"] = "Four computers are paired already. Remove one first.";
        sendJson(d); return;
    }
    CompPaired& c = compPaired[slot];
    comp_wipe(&c, sizeof(c));
    c.used = true;
    memcpy(c.id, p.id, 16); memcpy(c.key, p.key, 32);
    memcpy(c.name, p.name, sizeof(c.name));
    bool stored = compStore(slot);
    compPendingFree(p);
    if (!stored) {
        compForget(slot);
        d["status"] = "error"; d["reply"] = "failed"; d["message"] = "Could not save the pairing";
        sendJson(d); return;
    }
    d["status"] = "ok"; d["reply"] = "companion_pair"; d["state"] = "paired"; d["name"] = c.name;
    sendJson(d);
}

void handleCompanionUnpair(const JsonDocument& in) {
    uint8_t id[16];
    if (!compIdArg(in, id)) return;
    JsonDocument d;
    int i = compFindPaired(id), pi = compFindPending(id);
    if (pi >= 0) compPendingFree(compPending[pi]);
    if (i >= 0) compForget(i);
    d["status"] = "ok"; d["reply"] = "companion_unpair"; d["removed"] = (i >= 0 || pi >= 0);
    sendJson(d);
}

// A ticket lets one viewer watch for as long as the app keeps its ticket nonce
// (90 s). Refused over serial: serial has no PIN, and the screen is not
// something the cable should be able to open.
void handleScreenTicket(const JsonDocument& in) {
    uint8_t id[16];
    if (!compIdArg(in, id)) return;
    JsonDocument d;
    if (cmdSource == SRC_SERIAL) {
        d["status"] = "error"; d["reply"] = "wifi_only";
        d["message"] = "Screens are only shown on a signed-in dashboard";
        sendJson(d); return;
    }
    if (authToken.length() == 0) {
        d["status"] = "error"; d["reply"] = "pin_required";
        d["message"] = "Set an access PIN before sharing a screen";
        sendJson(d); return;
    }
    int i = compFindPaired(id);
    if (i < 0) { d["status"] = "error"; d["reply"] = "not_found"; d["message"] = "That computer is not paired"; sendJson(d); return; }
    CompPaired& c = compPaired[i];
    if (!compOnline(c)) {
        d["status"] = "error"; d["reply"] = "pc_offline";
        d["message"] = "The companion app on that computer is not running";
        sendJson(d); return;
    }
    uint8_t vn[16], t[32];
    char vnh[33], th[65], msg[80];
    esp_fill_random(vn, 16);
    comp_hex(vn, 16, vnh);
    snprintf(msg, sizeof(msg), "tkt|%s|%s", c.tn, vnh);
    if (comp_hmac(c.key, msg, t) != 0) {
        d["status"] = "error"; d["reply"] = "failed"; d["message"] = "Could not sign a ticket";
        sendJson(d); return;
    }
    comp_hex(t, 32, th);
    d["status"] = "ok"; d["reply"] = "screen_ticket";
    d["id"] = in["id"] | "";
    d["name"] = c.name;
    d["ip"] = IPAddress(c.ip).toString();
    d["port"] = c.port;
    d["fp"] = c.fp;
    d["tn"] = c.tn;
    d["vn"] = vnh;
    d["ticket"] = th;
    sendJson(d);
}
