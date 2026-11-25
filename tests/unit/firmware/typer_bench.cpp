// Host bench for hid_fi/typer.h (tests/unit/test_typer.py).
//
// Reads one script per line from stdin, runs it on a simulated millisecond clock and
// prints one line of JSON per script: every key event with its time, and when DONE came.
//   <start-ms> <cps> <rhythm 0|1> <seed> <steps...>
// steps (all at a time t in ms, applied before the tick of that millisecond):
//   add:<t>:<hex text>:<enter 0|1>    queue text
//   pause:<t>   resume:<t>   stop:<t>   pace:<t>:<cps>
//   end:<t>                            stop the clock here
// A character's plan stands in for the firmware's typing code: press, wait 10, release,
// wait 10 (typeAscii), and for a byte >= 0x80 a longer one (press, wait 30, release,
// wait 30) - a Unicode entry takes longer and its waits must never be shortened.
#include <cstdio>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <iostream>
#include <sstream>
#include <string>
#include <vector>
#include TYPER_H

static std::string unhex(const std::string& h) {
    std::string s;
    for (size_t i = 0; i + 1 < h.size(); i += 2) s += (char)std::strtol(h.substr(i, 2).c_str(), nullptr, 16);
    return s;
}

static uint16_t planOne(const typer::T& t, typer::Op* ops, uint8_t* n) {
    uint16_t i = t.pos;
    const uint8_t b = (uint8_t)t.text[i];
    uint16_t next = i + 1;
    if (b >= 0xC0) while (next < t.len && ((uint8_t)t.text[next] & 0xC0) == 0x80) next++;
    const uint16_t w = b >= 0x80 ? 30 : 10;
    ops[0] = {typer::PRESS, b}; ops[1] = {typer::WAIT, w};
    ops[2] = {typer::RELEASE, b}; ops[3] = {typer::WAIT, w};
    *n = 4;
    return next;
}

int main() {
    std::string line;
    while (std::getline(std::cin, line)) {
        if (line.empty()) continue;
        std::stringstream ss(line);
        uint32_t start, seed; int cps, rhythm;
        ss >> start >> cps >> rhythm >> seed;
        struct Step { std::string kind; uint32_t t; std::string a; int b; };
        std::vector<Step> steps;
        std::string tok;
        uint32_t end = start + 600000;
        while (ss >> tok) {
            std::vector<std::string> f;
            std::stringstream fs(tok);
            std::string x;
            while (std::getline(fs, x, ':')) f.push_back(x);
            Step s{f[0], (uint32_t)std::strtoul(f[1].c_str(), nullptr, 10), f.size() > 2 ? f[2] : "", f.size() > 3 ? std::atoi(f[3].c_str()) : 0};
            if (s.kind == "end") end = start + s.t;
            steps.push_back(s);
        }
        static typer::T t;
        t = typer::T();
        typer::pace(t, (uint8_t)cps, rhythm != 0, seed);
        std::string out = "{\"ev\":[";
        bool first = true;
        long done = -1;
        for (uint32_t now = start; now != end; now++) {
            const uint32_t rel = now - start;
            for (auto& s : steps) {
                if (s.t != rel) continue;
                if (s.kind == "add" || s.kind == "adddrop") {
                    std::string txt = unhex(s.a);
                    const char drop = s.kind == "adddrop" ? '\x01' : 0;
                    if (!typer::add(t, txt.data(), (uint16_t)txt.size(), s.b != 0, drop)) {
                        out += std::string(first ? "" : ",") + "[" + std::to_string(rel) + ",\"F\",0]";
                        first = false;
                    }
                } else if (s.kind == "pause") t.paused = true;
                else if (s.kind == "resume") t.paused = false;
                else if (s.kind == "stop") typer::stop(t);
                else if (s.kind == "pace") typer::pace(t, (uint8_t)std::atoi(s.a.c_str()), t.rhythm, t.seed);
            }
            for (int guard = 0; guard < 64; guard++) {
                typer::Op o{};
                typer::Act a = typer::tick(t, now, &o);
                if (a == typer::IDLE) break;
                if (a == typer::NEED_PLAN) {
                    typer::Op ops[typer::PLAN]; uint8_t n = 0;
                    uint16_t next = planOne(t, ops, &n);
                    typer::setPlan(t, ops, n, next, now);
                    continue;
                }
                if (a == typer::DONE) { done = rel; break; }
                out += std::string(first ? "" : ",") + "[" + std::to_string(rel) + ",\"" +
                       (a == typer::DO_PRESS ? "P" : "R") + "\"," + std::to_string(o.v) + "]";
                first = false;
            }
        }
        out += "],\"done\":" + std::to_string(done) + ",\"typed\":" + std::to_string(t.typed) +
               ",\"total\":" + std::to_string(t.total) + ",\"busy\":" + (typer::busy(t) ? "true" : "false") + "}";
        std::puts(out.c_str());
    }
    return 0;
}
