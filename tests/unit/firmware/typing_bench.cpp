// Host bench for the firmware's text typing (typeString and the Unicode entry).
//
// Built by tests/unit/test_typing.py from three generated slices:
//   gen_layout.inc - KeyboardLayout_en_US[] from the INSTALLED ESP32 core
//   gen_core.inc   - USBHIDKeyboard press/release/... from the INSTALLED core
//   gen_type.inc   - the TYPE STRING section of hid_fi/hid_fi.ino
// so the real code runs, against the real core, and every HID report and delay
// it produces is recorded. A core upgrade is re-verified automatically.
//
//   typing_bench canary              - assert the core behaves the way HID-Fi relies on
//   typing_bench check <cases.txt>   - every property below; exit 1 on any failure
//   typing_bench dump  <inputs.txt>  - one JSON line of events per input (golden/replay)
#include <cstdio>
#include <cstdint>
#include <cstring>
#include <cstdlib>
#include <string>
#include <vector>
#include <fstream>
#include <sstream>
#include <random>

#define PROGMEM
#define SHIFT           0x80
#define ALT_GR          0x40
#define ISO_KEY         0x64
#define ISO_REPLACEMENT 0x32
#define HID_KEY_SHIFT_LEFT 0xE1
#define KEY_LEFT_CTRL   0x80
#define KEY_LEFT_SHIFT  0x81
#define KEY_LEFT_ALT    0x82
#define KEY_RIGHT_GUI   0x87
#define KEY_RETURN      0xB0
#define KEY_CAPS_LOCK   0xC1
#define CHAR_DELAY      10

#include "gen_layout.inc"

typedef struct { uint8_t modifiers; uint8_t reserved; uint8_t keys[6]; } KeyReport;
struct Ev { char kind; KeyReport r; int ms; };      // 'R' report, 'D' delay, 'W' wake
static std::vector<Ev> trace;

class Kbd {
public:
    KeyReport _keyReport; const uint8_t* _asciimap = KeyboardLayout_en_US; bool shiftKeyReports = false;
    Kbd() { memset(&_keyReport, 0, sizeof _keyReport); }
    void sendReport(KeyReport* k) { Ev e{'R', *k, 0}; trace.push_back(e); }
    size_t pressRaw(uint8_t k); size_t releaseRaw(uint8_t k);
    size_t press(uint8_t k); size_t release(uint8_t k); void releaseAll(void);
};
#include "gen_core.inc"
static Kbd Keyboard;

static void delay(int ms) { Ev e{'D', {}, ms}; trace.push_back(e); }
static void usbWakeHost() { Ev e{'W', {}, 0}; trace.push_back(e); }

enum HostOS : uint8_t { OS_UNKNOWN, OS_WINDOWS, OS_MAC, OS_LINUX };
static HostOS curOs = OS_WINDOWS;
static HostOS hostOs() { return curOs; }
static uint8_t unicodeHosts = 0;
static HostOS hostOsDetected = OS_UNKNOWN;
static volatile uint8_t ledStateBits = 0;

namespace fw {
#include "gen_type.inc"
}

// ---------------------------------------------------------------- host models
static int inv[256][2];   // HID usage x shift -> ASCII, from the same en_US table
static void buildInverse() {
    for (int u = 0; u < 256; u++) inv[u][0] = inv[u][1] = -1;
    for (int c = 1; c < 128; c++) {
        uint8_t k = KeyboardLayout_en_US[c]; if (!k) continue;
        int sh = (k & SHIFT) ? 1 : 0; k &= ~SHIFT;
        if (inv[k][sh] < 0) inv[k][sh] = c;
    }
}
static int hexOf(uint8_t usage) {                     // unshifted hex digit keys only
    if (usage >= 0x04 && usage <= 0x09) return 10 + usage - 0x04;
    if (usage >= 0x1E && usage <= 0x26) return 1 + usage - 0x1E;
    if (usage == 0x27) return 0;
    return -1;
}
static void putUtf8(std::string& o, uint32_t cp) {
    if (cp < 0x80) o += (char)cp;
    else if (cp < 0x800) { o += (char)(0xC0 | cp >> 6); o += (char)(0x80 | (cp & 0x3F)); }
    else if (cp < 0x10000) { o += (char)(0xE0 | cp >> 12); o += (char)(0x80 | ((cp >> 6) & 0x3F)); o += (char)(0x80 | (cp & 0x3F)); }
    else { o += (char)(0xF0 | cp >> 18); o += (char)(0x80 | ((cp >> 12) & 0x3F)); o += (char)(0x80 | ((cp >> 6) & 0x3F)); o += (char)(0x80 | (cp & 0x3F)); }
}
static bool isDown(const KeyReport& r, uint8_t k) { for (int i = 0; i < 6; i++) if (r.keys[i] == k) return true; return false; }

// What a host does with the recorded reports. os 0 = plain US keyboard host,
// 2 = macOS with Unicode Hex Input selected, 3 = Linux IBus/GTK.
static std::string decode(int os, bool capsStart, std::string& e) {
    std::string out; KeyReport prev{}; std::vector<uint16_t> units; std::string hex;
    bool optHeld = false, caps = capsStart, ibus = false; uint32_t ibusCp = 0; int ibusDigits = 0;
    auto flushOpt = [&]() {
        if (hex.empty()) return;
        if (hex.size() % 4) { e = "mac: hex run not a multiple of 4: " + hex; return; }
        for (size_t i = 0; i < hex.size(); i += 4) units.push_back((uint16_t)strtoul(hex.substr(i, 4).c_str(), 0, 16));
        hex.clear();
        for (size_t i = 0; i < units.size(); i++) {
            uint32_t u = units[i];
            if (u >= 0xD800 && u <= 0xDBFF) {
                if (i + 1 >= units.size() || units[i+1] < 0xDC00 || units[i+1] > 0xDFFF) { e = "mac: lone high surrogate"; return; }
                putUtf8(out, 0x10000 + ((u - 0xD800) << 10) + (units[i+1] - 0xDC00)); i++;
            } else if (u >= 0xDC00 && u <= 0xDFFF) { e = "mac: lone low surrogate"; return; }
            else putUtf8(out, u);
        }
        units.clear();
    };
    for (auto& ev : trace) {
        if (ev.kind != 'R') continue;
        const KeyReport& r = ev.r;
        bool opt = r.modifiers & 0x44;
        if (os == 2 && optHeld && !opt) flushOpt();
        optHeld = opt;
        for (int i = 0; i < 6; i++) {
            uint8_t k = r.keys[i];
            if (!k || isDown(prev, k)) continue;
            bool sh = r.modifiers & 0x22, ctl = r.modifiers & 0x11;
            if (k == 0x39) { caps = !caps; continue; }
            if (os == 2 && opt) {
                int h = hexOf(k);
                if (h < 0 || sh || ctl) { e = "mac: non-hex key under Option"; return out; }
                hex += "0123456789abcdef"[h]; continue;
            }
            if (os == 3 && k == 0x18 && ctl && sh) {
                if (caps) { e = "linux: Ctrl+Shift+U with Caps Lock on"; return out; }
                ibus = true; ibusCp = 0; ibusDigits = 0; continue;
            }
            if (os == 3 && ibus) {
                if (k == 0x2C) { if (!ibusDigits) { e = "linux: empty hex"; return out; } putUtf8(out, ibusCp); ibus = false; continue; }
                int h = hexOf(k); if (h < 0 || sh || ctl) { e = "linux: non-hex in entry"; return out; }
                ibusCp = ibusCp * 16 + h; ibusDigits++; continue;
            }
            if (ctl || opt) { e = "unexpected chord outside entry"; return out; }
            int c = inv[k][sh ? 1 : 0];
            if (c < 0) { e = "key with no ASCII meaning: usage " + std::to_string(k); return out; }
            out += (char)c;
        }
        prev = r;
    }
    if (os == 2 && optHeld) e = "mac: Option still held at end";
    if (ibus) e = "linux: entry never committed";
    if (caps != capsStart) e = "caps lock state not restored";
    return out;
}
static bool reportIsZero(const KeyReport& r) { if (r.modifiers) return false; for (int i = 0; i < 6; i++) if (r.keys[i]) return false; return true; }

static int fails = 0; static long checks = 0;
#define CHECK(cond, ...) do { checks++; if (!(cond)) { if (fails++ < 30) { printf("  FAIL: "); printf(__VA_ARGS__); printf("\n"); } } } while (0)

static void reset() { trace.clear(); memset(&Keyboard._keyReport, 0, sizeof(KeyReport)); }
static int run(const std::string& s) { reset(); return fw::typeString(s.c_str()); }
static std::string hexs(const std::string& s) { std::string o; char b[4]; for (unsigned char c : s) { snprintf(b, 4, "%02x", c); o += b; } return o; }
static std::string unhex(const std::string& h) { std::string o; for (size_t i = 0; i + 1 < h.size(); i += 2) o += (char)strtoul(h.substr(i, 2).c_str(), 0, 16); return o; }

struct Mode { const char* name; HostOS os; uint8_t uni; bool caps; HostOS det; };
static const Mode MODES[] = {
    {"windows",          OS_WINDOWS, 0,                            false, OS_UNKNOWN},
    {"windows_unibits",  OS_WINDOWS, (1<<OS_MAC)|(1<<OS_LINUX),    false, OS_UNKNOWN},
    {"unknown_unibits",  OS_UNKNOWN, (1<<OS_MAC)|(1<<OS_LINUX),    false, OS_UNKNOWN},
    {"mac_off",          OS_MAC,     0,                            false, OS_UNKNOWN},
    {"mac_off_linux_on", OS_MAC,     1<<OS_LINUX,                  false, OS_UNKNOWN},
    {"mac_on",           OS_MAC,     1<<OS_MAC,                    false, OS_UNKNOWN},
    {"mac_on_det_win",   OS_MAC,     1<<OS_MAC,                    false, OS_WINDOWS},
    {"linux_off",        OS_LINUX,   0,                            false, OS_UNKNOWN},
    {"linux_on",         OS_LINUX,   1<<OS_LINUX,                  false, OS_UNKNOWN},
    {"linux_caps",       OS_LINUX,   1<<OS_LINUX,                  true,  OS_UNKNOWN},
    {"linux_on_det_mac", OS_LINUX,   1<<OS_LINUX,                  false, OS_MAC},
};
static const Mode* modeNamed(const std::string& n) { for (auto& m : MODES) if (n == m.name) return &m; return nullptr; }
// Which host model reads this mode's output: Unicode entry only where the firmware may use it.
static int hostModel(const Mode& m) {
    bool on = (m.os == OS_MAC || m.os == OS_LINUX) && (m.uni & (1 << m.os));
    bool vetoed = (m.os == OS_MAC && (m.det == OS_WINDOWS || m.det == OS_LINUX)) || (m.os == OS_LINUX && m.det == OS_MAC);
    return (on && !vetoed) ? m.os : 0;
}
static void setMode(const Mode& m) { curOs = m.os; unicodeHosts = m.uni; ledStateBits = m.caps ? 0x02 : 0; hostOsDetected = m.det; }

static void invariants(const char* tag, const std::string& in) {
    for (auto& ev : trace) if (ev.kind == 'R') {
        for (int i = 0; i < 6; i++) { uint8_t k = ev.r.keys[i]; if (!k) continue;
            CHECK(k == 0x39 || inv[k][0] >= 0 || inv[k][1] >= 0, "%s: illegal usage 0x%02x for input %s", tag, k, hexs(in).c_str()); }
        CHECK((ev.r.modifiers & ~0x07) == 0, "%s: unexpected modifier 0x%02x for input %s", tag, ev.r.modifiers, hexs(in).c_str());
    }
    CHECK(reportIsZero(Keyboard._keyReport), "%s: key left held after %s", tag, hexs(in).c_str());
}

// ---------------------------------------------------------------- canary
// Facts about the ESP32 core that HID-Fi's code depends on. If a core upgrade
// changes one, the typing code must be revisited before the pin moves.
static int canary() {
    buildInverse();
    reset(); Keyboard.press(0x88 + 0x28);
    CHECK(Keyboard._keyReport.keys[0] == 0x28, "core: press(0x88+n) no longer sends raw usage n - typeString's '>= 0x80 never reaches press()' rule and KEY_* constants need review");
    reset(); Keyboard.press(KEY_LEFT_ALT);
    CHECK(Keyboard._keyReport.modifiers == 0x04 && Keyboard._keyReport.keys[0] == 0, "core: press(0x82) is no longer Left Alt as a modifier");
    reset(); Keyboard.press('A');
    CHECK(Keyboard._keyReport.modifiers == 0x02 && Keyboard._keyReport.keys[0] == 0x04, "core: 'A' is no longer Shift + usage 0x04 in the en_US table");
    reset(); Keyboard.press('a'); Keyboard.release('a');
    CHECK(reportIsZero(Keyboard._keyReport), "core: press/release of 'a' leaves something held");
    reset(); Keyboard.press(KEY_CAPS_LOCK);
    CHECK(Keyboard._keyReport.keys[0] == 0x39, "core: KEY_CAPS_LOCK no longer maps to usage 0x39");
    int printable = 0; for (int c = 0x20; c < 0x7F; c++) if (KeyboardLayout_en_US[c]) printable++;
    CHECK(printable == 95, "core: en_US table maps %d of 95 printable ASCII characters", printable);
    for (const char* h = "0123456789abcdef"; *h; h++) {
        uint8_t k = KeyboardLayout_en_US[(int)*h];
        CHECK(k && !(k & SHIFT), "core: hex digit '%c' needs Shift in en_US - Unicode entry would break", *h);
    }
    printf("%ld canary checks, %d failed\n", checks, fails);
    return fails ? 1 : 0;
}

// ---------------------------------------------------------------- check
static int check(const char* casesPath) {
    buildInverse();
    std::mt19937 rng(20261017);

    printf("ascii: every character, every mode\n");
    for (const Mode& m : MODES) {
        setMode(m);
        for (int c = 1; c < 128; c++) {
            std::string s(1, (char)c); int sk = run(s);
            invariants(m.name, s);
            CHECK(sk == 0, "[%s] ASCII 0x%02x reported skipped", m.name, c);
        }
    }

    printf("vectors: multilingual text, emoji, ill-formed UTF-8 against independent expectations\n");
    std::ifstream f(casesPath); std::string line; long nv = 0;
    if (!f) { printf("  FAIL: cannot read %s\n", casesPath); return 1; }
    while (std::getline(f, line)) {
        std::istringstream ls(line); std::string label, inH, offH, onH; int offSk, onSk;
        ls >> label >> inH >> offH >> offSk >> onH >> onSk;
        std::string in = unhex(inH), wantOff = unhex(offH == "-" ? "" : offH), wantOn = unhex(onH == "-" ? "" : onH);
        for (const Mode& m : MODES) {
            setMode(m); int sk = run(in);
            invariants(m.name, in);
            int model = hostModel(m);
            std::string e; std::string got = decode(model, m.caps, e);
            const std::string& want = model ? wantOn : wantOff; int wantSk = model ? onSk : offSk;
            CHECK(e.empty() && got == want && sk == wantSk, "[%s] %s: %s | got %s skipped %d, want %s skipped %d %s",
                  m.name, label.c_str(), hexs(in).c_str(), hexs(got).c_str(), sk, hexs(want).c_str(), wantSk, e.c_str());
            nv++;
        }
    }
    printf("  %ld case-mode runs\n", nv);

    printf("exhaustive: every Unicode scalar value U+0080..U+10FFFF, every mode\n");
    for (const Mode& m : MODES) {
        setMode(m); int model = hostModel(m);
        for (uint32_t cp = 0x80; cp <= 0x10FFFF; cp++) {
            if (cp >= 0xD800 && cp <= 0xDFFF) continue;
            std::string s = "a"; putUtf8(s, cp); s += "Z";
            int sk = run(s);
            invariants(m.name, s);
            std::string e; std::string got = decode(model, m.caps, e);
            if (model) CHECK(e.empty() && got == s && sk == 0, "[%s] U+%04X round trip failed: got %s %s", m.name, cp, hexs(got).c_str(), e.c_str());
            else {
                CHECK(e.empty() && got.size() >= 2 && got.front() == 'a' && got.back() == 'Z', "[%s] U+%04X fallback broke the ASCII around it", m.name, cp);
                bool chord = false;
                for (auto& ev : trace) if (ev.kind == 'R' && (ev.r.modifiers & 0x55)) chord = true;
                CHECK(!chord, "[%s] U+%04X pressed Ctrl/Alt although Unicode entry may not run here", m.name, cp);
            }
        }
    }

    printf("fuzz: random bytes, every mode\n");
    for (const Mode& m : MODES) {
        setMode(m); int model = hostModel(m);
        for (int t = 0; t < 60000; t++) {
            std::string s; int n = 1 + rng() % 24; for (int i = 0; i < n; i++) { uint8_t b = rng() % 256; if (!b) b = 0xFF; s += (char)b; }
            run(s); invariants(m.name, s);
            std::string e; decode(model, m.caps, e);
            CHECK(e.empty(), "[%s] fuzz %s: %s", m.name, hexs(s).c_str(), e.c_str());
        }
    }

    printf("held keys: a key the user holds survives typing and Unicode entry\n");
    for (const char* mn : {"windows", "mac_on", "linux_on", "linux_caps"}) {
        setMode(*modeNamed(mn));
        for (const char* s : {"hello", "\xF0\x9F\x98\x80", "caf\xC3\xA9"}) {
            reset(); Keyboard.press(KEY_RIGHT_GUI); trace.clear(); fw::typeString(s);
            CHECK(Keyboard._keyReport.modifiers == 0x80, "[%s] held Right-GUI dropped while typing %s (mods=0x%02x)", mn, hexs(s).c_str(), Keyboard._keyReport.modifiers);
        }
    }

    printf("test button: the sample through each OS's entry, whatever the saved setting\n");
    for (HostOS target : {OS_MAC, OS_LINUX}) for (const Mode& m : MODES) {
        setMode(m); reset(); usbWakeHost();
        fw::typeUnicode(0x00E9, target); fw::typeUnicode(0x1F600, target);
        invariants("test-sample", "x");
        std::string e; std::string got = decode(target, m.caps, e);
        CHECK(e.empty() && got == "\xC3\xA9\xF0\x9F\x98\x80", "test sample for os %d in mode %s: got %s %s", target, m.name, hexs(got).c_str(), e.c_str());
    }

    printf("\n%ld checks, %d failed\n", checks, fails);
    return fails ? 1 : 0;
}

// ---------------------------------------------------------------- dump
// One JSON line per input: the skipped count and every event, compactly:
// "W" wake, "D10" delay 10 ms, "R<mods>:<k0>.<k1>..." a report (hex).
static int dump(const char* path, bool heldGui) {
    buildInverse();
    std::ifstream f(path); std::string line;
    if (!f) { fprintf(stderr, "cannot read %s\n", path); return 1; }
    while (std::getline(f, line)) {
        std::istringstream ls(line); std::string label, inH, mode; ls >> label >> inH >> mode;
        const Mode* m = modeNamed(mode);
        if (!m) { fprintf(stderr, "unknown mode %s\n", mode.c_str()); return 1; }
        setMode(*m); reset();
        if (heldGui) { Keyboard.press(KEY_RIGHT_GUI); trace.clear(); }
        int sk = fw::typeString(unhex(inH).c_str());
        printf("{\"label\":\"%s\",\"mode\":\"%s\",\"skipped\":%d,\"ev\":\"", label.c_str(), mode.c_str(), sk);
        bool first = true;
        for (auto& ev : trace) {
            if (!first) printf(" ");
            first = false;
            if (ev.kind == 'W') printf("W");
            else if (ev.kind == 'D') printf("D%d", ev.ms);
            else { printf("R%02x:", ev.r.modifiers); for (int i = 0; i < 6; i++) printf(i ? ".%02x" : "%02x", ev.r.keys[i]); }
        }
        printf("\"}\n");
    }
    return 0;
}

int main(int argc, char** argv) {
    std::string cmd = argc > 1 ? argv[1] : "";
    if (cmd == "canary") return canary();
    if (cmd == "check" && argc > 2) return check(argv[2]);
    if (cmd == "dump" && argc > 2) return dump(argv[2], false);
    if (cmd == "dump-held" && argc > 2) return dump(argv[2], true);
    fprintf(stderr, "usage: typing_bench canary | check <cases.txt> | dump <inputs.txt> | dump-held <inputs.txt>\n");
    return 2;
}
