// Host bench for hid_fi/ota_verify.h, linked against the mbedTLS release the
// pinned core ships (tests/unit/test_ota_verify.py builds it).
//   argv[1..]: trusted public keys, hex (65 bytes each)
//   stdin, one per line: <key_id> <sig_hex or -> <manifest_hex or ->   -> 1 | 0
#include <cstdio>
#include <iostream>
#include <sstream>
#include <string>
#include <vector>
#include "ota_verify.h"

static std::vector<uint8_t> unhex(const std::string& s) {
    std::vector<uint8_t> out(s.size() / 2);
    if (s == "-" || !otaHexDecode(s.c_str(), out.data(), out.size())) out.clear();
    return out;
}

int main(int argc, char** argv) {
    std::vector<OtaKey> keys;
    for (int i = 1; i < argc; i++) {
        OtaKey k;
        if (!otaHexDecode(argv[i], k.pub, 65)) { std::printf("bad key\n"); return 2; }
        keys.push_back(k);
    }
    std::string line;
    while (std::getline(std::cin, line)) {
        std::istringstream in(line);
        std::string id, sig, man;
        in >> id >> sig >> man;
        std::vector<uint8_t> m = unhex(man);
        bool ok = otaVerifyManifest(m.empty() ? nullptr : m.data(), m.size(), id.c_str(),
                                    sig == "-" ? "" : sig.c_str(), keys.data(), keys.size());
        std::printf("%d\n", ok ? 1 : 0);
    }
    return 0;
}
