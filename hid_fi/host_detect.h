// Mac or PC, from how the host read our string descriptors. Pure, so it is
// tested on the computer against real enumeration sequences
// (tests/unit/test_host_detect.py) - this rule has been wrong before.
//
// macOS reads each text string twice, back to back: two bytes for the length,
// then the whole string. Windows does that for the odd string only (measured on
// Windows 11: 11 requests, 2 repeats). Index 0 is the language table, not a
// string, and hosts fetch it a varying number of times, so it is left out of
// the "which strings came in pairs" count.
//
// Two ways to read as a Mac:
//  - the original ratio: at least 3 repeats, and repeats at least half of all
//    string requests;
//  - by string: at least 3 different strings read as back-to-back pairs, and
//    pairs for at least three quarters of the strings read. Added after macOS
//    15.6 enumerated as 2,2,1,1,3,3,4,4,0 (4 repeats of 9 - just under half,
//    every string paired) and was called Windows on every boot.
#pragma once
#include <stdint.h>

static inline uint32_t hdBits(uint16_t m) {
    uint32_t n = 0;
    while (m) { n += m & 1u; m >>= 1; }
    return n;
}

// seen / paired: bit i set for string index i (1..15) read / read twice in a row.
static inline bool hdLooksMac(uint32_t rereads, uint32_t reqs, uint16_t seen, uint16_t paired) {
    if (rereads >= 3 && rereads * 2 >= reqs) return true;
    uint32_t p = hdBits(paired), n = hdBits(seen);
    return p >= 3 && p * 4 >= n * 3;
}

// What the descriptor callback records for one request.
static inline void hdNote(uint8_t index, uint8_t lastIndex, uint16_t* seen, uint16_t* paired) {
    if (index == 0 || index >= 16) return;
    *seen |= (uint16_t)(1u << index);
    if (index == lastIndex) *paired |= (uint16_t)(1u << index);
}
