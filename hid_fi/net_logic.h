// Which saved network to join - the board's own choice, with no WiFi calls in it, so
// tests/unit/test_net_logic.py compiles this same header on the host and asks it.
#pragma once
#include <string.h>

struct NetSaved {          // one saved network, as the supervisor sees it now
    const char* ssid;
    bool usable;           // not refused, not resting after a failure, not staged absent
};

struct NetSeen {           // one scan result: a network on two access points is seen twice
    const char* ssid;
    int rssi;
};

// The saved network to join: the strongest in range among the usable ones (a network
// seen on several access points counts its strongest). A tie goes to the one saved
// first - the most recently used. -1: none of them in range. Names match exactly.
inline int netPickBest(const NetSaved* saved, int nSaved, const NetSeen* seen, int nSeen,
                       int* rssiOut = nullptr) {
    int best = -1, bestRssi = -1000;
    for (int i = 0; i < nSaved; i++) {
        if (!saved[i].usable || !saved[i].ssid) continue;
        for (int k = 0; k < nSeen; k++) {
            if (seen[k].ssid && strcmp(seen[k].ssid, saved[i].ssid) == 0 && seen[k].rssi > bestRssi) {
                best = i;
                bestRssi = seen[k].rssi;
            }
        }
    }
    if (rssiOut) *rssiOut = bestRssi;
    return best;
}
