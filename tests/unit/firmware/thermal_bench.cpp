// Host bench for hid_fi/thermal.h - the chip temperature log. One request per line on
// stdin, one answer each (tests/unit/test_thermal.py).
//
//   r <celsius|nan> <ok 0|1> <now_s>   -> now_deci peak_deci errors     (NONE prints as "-")
//   log                                -> count last_logged_s v1 v2 ...  (oldest first, "-" for a gap)
//   reset                              -> ok   (a fresh log, as after a restart)
//   band <0|1> <deci>                  -> the band to read in next
#include <cmath>
#include <iostream>
#include <sstream>
#include <string>
#ifndef THERMAL_H
#define THERMAL_H "../../../hid_fi/thermal.h"
#endif
#include THERMAL_H

static std::string d(int16_t v) { return v == thermal::NONE ? "-" : std::to_string(v); }

int main() {
    thermal::Log l;
    std::string line;
    while (std::getline(std::cin, line)) {
        std::istringstream in(line);
        std::string op;
        in >> op;
        if (op == "r") {
            std::string c;
            int ok;
            unsigned long t;
            in >> c >> ok >> t;
            float v = c == "nan" ? NAN : std::stof(c);
            thermal::reading(l, v, ok != 0, (uint32_t)t);
            std::cout << d(l.nowDeci) << " " << d(l.peakDeci) << " " << l.errors << "\n";
        } else if (op == "band") {
            int b, v;
            in >> b >> v;
            std::cout << (int)thermal::nextBand((thermal::Band)b, (int16_t)v) << "\n";
        } else if (op == "reset") {
            l = thermal::Log();
            std::cout << "ok\n";
        } else if (op == "log") {
            int16_t out[thermal::LOG_LEN];
            uint16_t n = thermal::unroll(l, out);
            std::cout << n << " " << thermal::lastLoggedAt(l);
            for (uint16_t i = 0; i < n; i++) std::cout << " " << d(out[i]);
            std::cout << "\n";
        } else {
            std::cout << "?\n";
        }
        std::cout.flush();
    }
}
