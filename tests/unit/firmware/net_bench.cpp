// Host bench for hid_fi/net_logic.h - which saved network the board joins. One request
// per line on stdin, one answer each (tests/unit/test_net_logic.py). Names are hex, so
// spaces and any byte travel as they are; "-" is an empty list.
//
//   pick <saved: hexname:usable,...> <seen: hexname:rssi,...>  -> index rssi
#include <cstdio>
#include <cstdlib>
#include <iostream>
#include <sstream>
#include <string>
#include <vector>
#ifndef NET_LOGIC
#define NET_LOGIC "../../../hid_fi/net_logic.h"
#endif
#include NET_LOGIC

static std::string unhex(const std::string& h) {
    std::string out;
    for (size_t i = 0; i + 1 < h.size(); i += 2) out += (char)std::strtol(h.substr(i, 2).c_str(), nullptr, 16);
    return out;
}

static std::vector<std::pair<std::string, int>> parse(const std::string& spec) {
    std::vector<std::pair<std::string, int>> v;
    if (spec == "-") return v;
    std::stringstream ss(spec);
    std::string item;
    while (std::getline(ss, item, ',')) {
        size_t c = item.rfind(':');
        v.push_back({unhex(item.substr(0, c)), std::atoi(item.substr(c + 1).c_str())});
    }
    return v;
}

int main() {
    std::string line;
    while (std::getline(std::cin, line)) {
        std::stringstream in(line);
        std::string op, a, b;
        in >> op >> a >> b;
        if (op != "pick") { std::cout << "?\n"; continue; }
        auto sv = parse(a), sn = parse(b);
        std::vector<NetSaved> saved;
        std::vector<NetSeen> seen;
        for (auto& p : sv) saved.push_back({p.first.c_str(), p.second != 0});
        for (auto& p : sn) seen.push_back({p.first.c_str(), p.second});
        int rssi = 0;
        int i = netPickBest(saved.data(), (int)saved.size(), seen.data(), (int)seen.size(), &rssi);
        std::cout << i << " " << (i < 0 ? 0 : rssi) << "\n";
    }
}
