// typer.h - text typed at a chosen pace, without stopping the board.
//
// The text tab's `type` used to type the whole text inside one call: every character
// held loop() for its key presses, so a long text froze the pointer and the dashboard
// for as long as it typed, and the pace was fixed (about 25-50 characters a second -
// "the typing speed is much fast, can't we control it", a viewer of the demo video).
// Now the text waits here and loop() plays it one character at a time:
//
//   - a character's own key actions are planned by the firmware's typing code (the very
//     same code that types directly - the same keys, the same waits between them, so
//     Unicode entry and dead keys behave exactly as before) and played back here;
//   - after each character the pace adds a gap so a character takes 1000/cps ms; 0 cps
//     adds none - the board's own pace, the default - and the rhythm option varies each
//     gap by up to 30% (a seeded sequence: the same every time for a test);
//   - pause and stop act between characters, never between a key's press and release;
//     stop forgets what is left (the caller then releases every key).
//
// Pure logic, no Arduino: tests/unit/test_typer.py compiles it on the computer, and its
// planted bugs (tests/unit/firmware/typer_mutations.json) must fail it.
#pragma once
#include <stdint.h>
#include <string.h>

namespace typer {

enum OpKind : uint8_t { PRESS = 1, RELEASE = 2, WAIT = 3 };
struct Op { uint8_t k; uint16_t v; };     // v: the key for PRESS/RELEASE, ms for WAIT

constexpr uint8_t PLAN = 48;               // one character's actions (Unicode entry is the longest)
constexpr uint16_t TEXT = 4096;            // bytes waiting: a whole dashboard frame (4096), as before
constexpr uint8_t CPS_MAX = 60;            // the slowest pace is 1 cps; 0 = the board's own pace
constexpr uint8_t RHYTHM_PCT = 30;         // the rhythm option: each gap 70%..130%

enum Act : uint8_t { IDLE = 0, NEED_PLAN, DO_PRESS, DO_RELEASE, DONE };

struct T {
    char text[TEXT + 1];
    uint16_t len = 0, pos = 0;             // UTF-8 bytes; the next character starts at pos
    Op plan[PLAN];
    uint8_t n = 0, at = 0;
    bool planned = false;
    bool done = true;                      // DONE reported for what was typed
    uint32_t nextAt = 0;                   // no action before this, while hold
    bool hold = false;                     // a wait is pending (time 0 is a time, not "none":
                                           // after ~24.8 days of uptime it once read as the future)
    uint32_t charStart = 0;
    uint8_t cps = 0;
    bool rhythm = false;
    uint32_t seed = 1;
    bool paused = false;
    uint16_t typed = 0, total = 0;         // characters, for progress
};

inline bool busy(const T& t) { return t.pos < t.len || t.planned; }

inline uint16_t chars(const char* s, uint16_t n) {        // UTF-8 lead bytes
    uint16_t c = 0;
    for (uint16_t i = 0; i < n; i++) if (((uint8_t)s[i] & 0xC0) != 0x80) c++;
    return c;
}

// Queue text (and an Enter after it as a newline); false when it does not fit - the
// caller says so, nothing is cut. Pace and rhythm apply to everything still waiting.
// drop: a byte never taken from the text (the firmware's in-stream shortcut marker).
inline bool add(T& t, const char* s, uint16_t n, bool enter, char drop = 0) {
    if (!busy(t)) { t.len = t.pos = 0; t.typed = t.total = 0; t.paused = false; }   // a new text starts unpaused
    const uint16_t need = n + (enter ? 1 : 0);
    if (need == 0) return true;
    if (t.pos > 0 && (uint32_t)t.len + need > TEXT) {       // what is typed makes room
        memmove(t.text, t.text + t.pos, t.len - t.pos);
        t.len -= t.pos;
        t.pos = 0;
    }
    if ((uint32_t)t.len + need > TEXT) return false;
    const uint16_t from = t.len;
    for (uint16_t i = 0; i < n; i++) if (!drop || s[i] != drop) t.text[t.len++] = s[i];
    t.total += chars(t.text + from, t.len - from) + (enter ? 1 : 0);
    if (enter) t.text[t.len++] = '\n';
    t.text[t.len] = 0;
    t.done = false;
    return true;
}

inline void pace(T& t, uint8_t cps, bool rhythm, uint32_t seed) {
    t.cps = cps > CPS_MAX ? CPS_MAX : cps;
    t.rhythm = rhythm;
    t.seed = seed ? seed : 1;
}

// Forget everything still waiting (the caller releases every key).
inline void stop(T& t) {
    t.len = t.pos = 0;
    t.planned = false;
    t.n = t.at = 0;
    t.paused = false;
    t.hold = false;
    t.done = true;
}

// One character's actions, planned by the firmware; next is where the character after
// it starts.
inline void setPlan(T& t, const Op* ops, uint8_t n, uint16_t next, uint32_t now) {
    if (n > PLAN) n = PLAN;
    memcpy(t.plan, ops, n * sizeof(Op));
    t.n = n;
    t.at = 0;
    t.pos = next > t.len ? t.len : next;
    t.planned = true;
    t.charStart = now;
}

inline uint32_t gapAfter(T& t, uint32_t spent) {
    if (!t.cps) return 0;
    uint32_t period = 1000u / t.cps;
    if (t.rhythm) {
        t.seed = t.seed * 1103515245u + 12345u;                  // the same sequence every time
        const uint32_t r = (t.seed >> 16) % (2u * RHYTHM_PCT + 1u);  // 0..60
        period = period * (100u - RHYTHM_PCT + r) / 100u;        // 70%..130%
    }
    return period > spent ? period - spent : 0;
}

// What the board must do now, if anything. Called from loop() as often as it likes; it
// never waits. NEED_PLAN: plan the character at t.pos and call setPlan. DONE: once, when
// everything queued has been typed.
inline Act tick(T& t, uint32_t now, Op* out) {
    if (t.hold) {
        if ((int32_t)(now - t.nextAt) < 0) return IDLE;
        t.hold = false;
    }
    if (t.planned) {
        while (t.at < t.n) {
            const Op o = t.plan[t.at++];
            if (o.k == WAIT) { t.nextAt = now + o.v; t.hold = true; return IDLE; }
            *out = o;
            return o.k == PRESS ? DO_PRESS : DO_RELEASE;
        }
        t.planned = false;
        t.typed++;
        const uint32_t gap = gapAfter(t, now - t.charStart);
        if (gap) { t.nextAt = now + gap; t.hold = true; return IDLE; }
        // no gap due: the next character starts now, as the old loop did
    }
    if (t.pos < t.len) return t.paused ? IDLE : NEED_PLAN;
    if (!t.done) { t.done = true; return DONE; }
    return IDLE;
}

}  // namespace typer
