// Host bench for hid_fi/combo.h - a key combination pressed without stopping the board.
// One request per line on stdin, one answer each (tests/unit/test_combo.py).
//
//   push <mods 0|1> <text>   -> 1 queued | 0 refused (full or too long)
//   tick <now_ms>            -> NONE | PRESS_MODS <text> | PRESS_KEYS <text> | RELEASE <text>
//   reset                    -> ok
#include <iostream>
#include <sstream>
#include <string>
#ifndef COMBO_H
#define COMBO_H "../../../hid_fi/combo.h"
#endif
#include COMBO_H

int main() {
    combo::Q q;
    std::string line;
    static const char* NAMES[] = {"NONE", "PRESS_MODS", "PRESS_KEYS", "RELEASE"};
    while (std::getline(std::cin, line)) {
        std::istringstream in(line);
        std::string op;
        in >> op;
        if (op == "push") {
            int m;
            std::string text;
            in >> m >> text;
            std::cout << (combo::push(q, text.c_str(), m != 0) ? 1 : 0) << "\n";
        } else if (op == "tick") {
            unsigned long now;
            in >> now;
            const char* s = nullptr;
            combo::Act a = combo::tick(q, (uint32_t)now, &s);
            std::cout << NAMES[a];
            if (a != combo::NONE) std::cout << " " << s;
            std::cout << "\n";
        } else if (op == "reset") {
            q = combo::Q();
            std::cout << "ok\n";
        } else {
            std::cout << "?\n";
        }
        std::cout.flush();
    }
}
