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

// ---- the network your devices use (docs/DECISIONS.md D-02) ----------------------------
// Strength alone chose a network nobody could reach (a stronger router on another
// subnet). What the board remembers about each saved network decides first; strength
// decides between the access points of one network, and on a board nobody has used.
#include <stdint.h>

// dBm: a preferred network this strong or better wins. Where a link really stops
// being usable, not where it is merely weaker: at -75, an extender measured at -80
// (working normally) counted as too weak and the board fell back to a stronger
// router nobody could reach (09F7C8, 2026-11-07).
#define NET_WORKABLE   (-85)
#define NET_ROAM_BELOW (-70)        // dBm: roam only off an access point weaker than this (D-10)
#define NET_ROAM_GAIN  10           // dB: to one of the same network this much stronger
#define NET_LOOK_MS    300000UL     // on a network nobody uses: wait this long for an owner device

struct NetKnown {          // one saved network and what the board remembers about it
    const char* ssid;
    bool usable;           // as NetSaved
    uint32_t net;          // the subnet it was on when joined (address & mask); 0 never joined
    uint32_t used;         // when an owner device last reached the board over it - a counter,
                           // higher is more recent; 0 never
    bool anchor;           // the paired computer last reached it over this one
    bool tried;            // joined since start, and no owner device came within NET_LOOK_MS
    bool refused;          // has worked, but its router refused the saved password since start
                           // (it changed?) - never a reason to move, tried only when nothing else is
};

enum NetWhy { NET_NONE = 0, NET_PREFERRED = 1, NET_STRONGEST = 2, NET_COMING_HOME = 3, NET_LOOKING = 4, NET_ROAMING = 5 };

// The network that defines "where the board is used": the paired computer's, else the
// one used most recently. -1: nobody has used it over any saved network yet.
inline int netPreferred(const NetKnown* k, int n) {
    int p = -1;
    for (int i = 0; i < n; i++) {
        if (!k[i].used && !k[i].anchor) continue;
        if (p < 0 || (k[i].anchor && !k[p].anchor) || (k[i].anchor == k[p].anchor && k[i].used > k[p].used)) p = i;
    }
    return p;
}

// One network: the same saved network, or two on the same subnet (a router and its
// extender). A network never joined has no subnet yet and is only itself.
inline bool netSameNet(const NetKnown* k, int a, int b) {
    return a == b || (k[a].net && k[a].net == k[b].net);
}

inline int netStrongestSeen(const NetKnown& k, const NetSeen* seen, int nSeen) {
    int best = -1000;
    for (int s = 0; s < nSeen; s++)
        if (k.ssid && seen[s].ssid && strcmp(seen[s].ssid, k.ssid) == 0 && seen[s].rssi > best) best = seen[s].rssi;
    return best;
}

// The strongest usable member of p's network that is in range: -1 if none.
inline int netBestOfNet(const NetKnown* k, int n, int p, const NetSeen* seen, int nSeen, int* rssiOut) {
    int best = -1, bestRssi = -1000;
    for (int i = 0; i < n; i++) {
        if (!k[i].usable || k[i].refused || !netSameNet(k, i, p)) continue;
        int r = netStrongestSeen(k[i], seen, nSeen);
        if (r > bestRssi) { best = i; bestRssi = r; }
    }
    if (best >= 0 && bestRssi <= -1000) best = -1;
    if (rssiOut) *rssiOut = bestRssi;
    return best;
}

// Off a network: which saved network to join. The preferred network while it is
// workable; otherwise D-01's strongest, leaving out the ones already tried in vain
// since start (and taking them only if nothing else is in range).
inline int netChoose(const NetKnown* k, int n, const NetSeen* seen, int nSeen, int* why = nullptr) {
    if (why) *why = NET_NONE;
    int p = netPreferred(k, n);
    if (p >= 0) {
        int r = -1000, i = netBestOfNet(k, n, p, seen, nSeen, &r);
        if (i >= 0 && r >= NET_WORKABLE) { if (why) *why = NET_PREFERRED; return i; }
    }
    // A network refusing its saved password, or tried in vain, comes after the rest.
    NetSaved s[16];
    int m = n < 16 ? n : 16;
    for (int pass = 0; pass < 2; pass++) {
        for (int i = 0; i < m; i++) s[i] = {k[i].ssid, k[i].usable && (pass == 1 || (!k[i].tried && !k[i].refused))};
        int i = netPickBest(s, m, seen, nSeen);
        if (i >= 0) { if (why) *why = NET_STRONGEST; return i; }
    }
    return -1;
}

// On network cur: move, and where? -1 stays. Never away from an owner device using it
// now. Coming home: the preferred network is workable and cur is not part of it.
// Looking: nobody has ever used cur, nobody came for NET_LOOK_MS, and another saved
// network not yet tried is in range (the caller marks cur tried).
inline int netMoveFrom(const NetKnown* k, int n, int cur, const NetSeen* seen, int nSeen,
                       uint32_t msHere, bool ownerHereNow, int* why = nullptr) {
    if (why) *why = NET_NONE;
    if (cur < 0 || cur >= n || ownerHereNow) return -1;
    int p = netPreferred(k, n);
    if (p >= 0 && !netSameNet(k, cur, p)) {
        int r = -1000, i = netBestOfNet(k, n, p, seen, nSeen, &r);
        if (i >= 0 && r >= NET_WORKABLE) { if (why) *why = NET_COMING_HOME; return i; }
    }
    bool curUsed = false;
    for (int i = 0; i < n; i++) if ((k[i].used || k[i].anchor) && netSameNet(k, i, cur)) curUsed = true;
    if (curUsed || msHere < NET_LOOK_MS) return -1;
    int best = -1, bestRssi = -1000;
    for (int i = 0; i < n; i++) {
        if (!k[i].usable || k[i].tried || k[i].refused || netSameNet(k, i, cur)) continue;
        int r = netStrongestSeen(k[i], seen, nSeen);
        if (r > bestRssi) { best = i; bestRssi = r; }
    }
    if (best >= 0 && bestRssi > -1000) { if (why) *why = NET_LOOKING; return best; }
    return -1;
}

// While online on cur: is there anything to decide, worth a scan? Off the preferred
// network (it may be back), or on one nobody uses after NET_LOOK_MS with another
// saved network left to try. Nothing to decide, no scan: each one briefly disturbs
// the board's own WiFi, so a board nobody uses that has tried everything stops.
inline bool netNeedsLook(const NetKnown* k, int n, int cur, uint32_t msHere, int curRssi = 0) {
    if (cur < 0 || cur >= n) return false;
    if (curRssi && curRssi < NET_ROAM_BELOW) return true;      // a stronger access point may be near
    int p = netPreferred(k, n);
    if (p >= 0 && !netSameNet(k, cur, p))
        for (int i = 0; i < n; i++) if (k[i].usable && !k[i].refused && netSameNet(k, i, p)) return true;
    for (int i = 0; i < n; i++) if ((k[i].used || k[i].anchor) && netSameNet(k, i, cur)) return false;
    if (msHere < NET_LOOK_MS) return false;
    for (int i = 0; i < n; i++) if (k[i].usable && !k[i].tried && !k[i].refused && !netSameNet(k, i, cur)) return true;
    return false;
}

// Coming from a version before D-02 (no network has been marked used yet): the
// network the board was last on is where it is used - the old firmware kept that
// one first. Without this, a board updated over the air in a home with several
// routers joined the strongest after the restart, and was out of reach until it
// went looking 5 minutes later (DA1A2C, 2026-11-07). -1: nothing to seed.
inline int netSeedFrom(const NetKnown* k, int n) {
    if (n <= 0) return -1;
    for (int i = 0; i < n; i++) if (k[i].used || k[i].anchor) return -1;
    return k[0].usable ? 0 : -1;
}

// ---- roaming within one network (docs/DECISIONS.md D-10) -----------------------------
// Strength decides between the access points of one network only - never between the
// owner's network and another. While connected the board moves to a stronger access
// point of the same network when the one it is on has turned weak, by a margin that
// cannot ping-pong, and never while an owner device is using it.

// The network cur belongs to (same name, or same subnet) has a much stronger access point
// in range: the index to rejoin (cur itself when the stronger one has the same name; the
// join then takes the strongest, WIFI_CONNECT_AP_BY_SIGNAL). -1: stay.
inline int netShouldRoam(const NetKnown* k, int n, int cur, const NetSeen* seen, int nSeen,
                         int curRssi, bool ownerHereNow) {
    if (cur < 0 || cur >= n || ownerHereNow || curRssi >= NET_ROAM_BELOW) return -1;
    int best = -1, bestRssi = -1000;
    for (int i = 0; i < n; i++) {
        if (!k[i].usable || k[i].refused || !netSameNet(k, i, cur)) continue;
        int r = netStrongestSeen(k[i], seen, nSeen);
        if (r > bestRssi) { best = i; bestRssi = r; }
    }
    return (best >= 0 && bestRssi >= curRssi + NET_ROAM_GAIN) ? best : -1;
}

