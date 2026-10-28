// Host bench for hid_fi/ota_logic.h - the update decisions the board makes.
// Reads one request per line on stdin, answers one line each (see
// tests/unit/test_ota_logic.py). "-" stands for an empty or missing string.
//
//   version <text>                                 -> ok major minor patch
//   compare <a> <b>                                -> -1 | 0 | 1 | x (not comparable)
//   selftest <uptime_ms> <hid> <need_net> <net_up> <forced>  -> verdict why
//   outcome <step> <to> <slot> <running> <running_slot> <pending> <aborted_other> -> outcome
//   restart <pending>                               -> 0 | 1
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <iostream>
#include <sstream>
#include <string>

#ifndef OTA_LOGIC
#define OTA_LOGIC "../../../hid_fi/ota_logic.h"
#endif
#include OTA_LOGIC   // the firmware's file, or a mutant of it (tests/unit/test_ota_logic.py)

static const char* arg(const std::string& s) { return s == "-" ? "" : s.c_str(); }

int main() {
    std::string line;
    while (std::getline(std::cin, line)) {
        std::istringstream in(line);
        std::string op;
        in >> op;
        if (op == "version") {
            std::string s; in >> s;
            OtaVersion v = otaParseVersion(arg(s));
            std::printf("%d %d %d %d\n", v.ok, v.major, v.minor, v.patch);
        } else if (op == "compare") {
            std::string a, b; in >> a >> b;
            OtaVersion x = otaParseVersion(arg(a)), y = otaParseVersion(arg(b));
            if (!x.ok || !y.ok) std::printf("x\n");
            else std::printf("%d\n", otaCompare(x, y));
        } else if (op == "selftest") {
            unsigned long up; int hid, need, net, forced;
            in >> up >> hid >> need >> net >> forced;
            OtaSelfTestIn t = {(uint32_t)up, hid != 0, need != 0, net != 0, forced != 0};
            OtaSelfTestOut o = otaSelfTest(t);
            std::printf("%s %s\n", o.verdict == OTA_PASS ? "pass" : o.verdict == OTA_FAIL ? "fail" : "wait",
                        o.why && *o.why ? o.why : "-");
        } else if (op == "outcome") {
            std::string step, to, slot, running, rslot; int pending, aborted;
            in >> step >> to >> slot >> running >> rslot >> pending >> aborted;
            OtaRecord r = {arg(step), arg(to), arg(slot)};
            OtaOutcome o = otaOutcome(r, arg(running), arg(rslot), pending != 0, aborted != 0);
            const char* n[] = {"none", "verifying", "updated", "rolled_back"};
            std::printf("%s\n", n[o]);
        } else if (op == "restart") {
            int p; in >> p;
            std::printf("%d\n", otaRestartAllowed(p != 0) ? 1 : 0);
        } else {
            std::printf("? %s\n", op.c_str());
        }
    }
    return 0;
}
