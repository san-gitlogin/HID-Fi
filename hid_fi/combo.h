// combo.h - a key combination pressed without stopping the board.
//
// `press` (shortcut buttons, quick actions, knobs, the text tab's Enter) used to hold
// loop() for 150 ms: modifiers down, delay(30) so the host sees them in a report of
// their own, keys down, delay(120), release. Every pointer frame and ping waited
// behind it - measured 2026-11-23 on 09F7C8: the dashboard's ping went from 8 ms to
// a 96 ms median with a shortcut five times a second, and a fast knob queued many.
// Now each combination waits its turn in this queue and loop() advances it: the same
// reports at the same moments for the host, and nothing else waits.
//
// Pure logic, no Arduino: tests/unit/test_combo.py compiles it on the computer, and
// its planted bugs (tests/unit/firmware/combo_mutations.json) must fail it.
#pragma once
#include <stdint.h>
#include <string.h>

namespace combo {

constexpr uint8_t QLEN = 8;            // combinations waiting; a knob spun hard fills it
constexpr uint8_t MAXLEN = 48;         // one combination as text ("CTRL+SHIFT+ESC")
constexpr uint32_t MOD_MS = 30;        // modifiers alone in their own report first
constexpr uint32_t HOLD_MS = 120;      // then everything held, then released

enum Act : uint8_t { NONE = 0, PRESS_MODS, PRESS_KEYS, RELEASE };

struct Q {
    char items[QLEN][MAXLEN];
    bool mods[QLEN];
    uint8_t head = 0, count = 0;
    uint8_t phase = 0;                 // 0 waiting, 1 modifiers down, 2 everything down
    uint32_t at = 0;                   // when the current phase began
};

// Queue a combination; false when the queue is full or the text too long (the caller
// then finishes the oldest first, so nothing is ever dropped).
inline bool push(Q& q, const char* s, bool hasMods) {
    if (q.count >= QLEN || strlen(s) >= MAXLEN) return false;
    const uint8_t i = (uint8_t)((q.head + q.count) % QLEN);
    strcpy(q.items[i], s);
    q.mods[i] = hasMods;
    q.count++;
    return true;
}

inline bool busy(const Q& q) { return q.count > 0; }

// What the board must do now, if anything, for the combination at the head: `*s` is
// its text. Called from loop() as often as it likes; it never waits.
inline Act tick(Q& q, uint32_t now, const char** s) {
    if (!q.count) return NONE;
    *s = q.items[q.head];
    switch (q.phase) {
        case 0:
            q.phase = 1;
            q.at = now;
            return PRESS_MODS;
        case 1:
            if (q.mods[q.head] && now - q.at < MOD_MS) return NONE;
            q.phase = 2;
            q.at = now;
            return PRESS_KEYS;
        default:
            if (now - q.at < HOLD_MS) return NONE;
            q.phase = 0;
            q.head = (uint8_t)((q.head + 1) % QLEN);
            q.count--;
            return RELEASE;
    }
}

}  // namespace combo
