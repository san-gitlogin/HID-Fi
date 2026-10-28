// Host check: hid_fi/companion_crypto.h against companion/vectors/pairing_v1.json (run check_board.sh).
#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <sstream>
#include <string>
#include "companion_crypto.h"
static std::string J;
static std::string field(const std::string& key, size_t from = 0) {
    size_t k = J.find("\"" + key + "\"", from); if (k == std::string::npos) { printf("missing %s\n", key.c_str()); exit(2); }
    size_t q1 = J.find('"', J.find(':', k) + 1), q2 = J.find('"', q1 + 1);
    return J.substr(q1 + 1, q2 - q1 - 1);
}
static void unhex(const std::string& s, uint8_t* o, size_t n) { if (!comp_hex_parse(s.c_str(), o, n)) { printf("bad hex\n"); exit(2);} }
static int rng(void*, unsigned char* o, size_t n) { for (size_t i = 0; i < n; i++) o[i] = (unsigned char)rand(); return 0; }
static int fails = 0;
static void check(const char* what, bool ok) { printf("%s %s\n", ok ? "ok  " : "FAIL", what); if (!ok) fails++; }
int main(int argc, char** argv) {
    std::ifstream f(argv[1]); std::stringstream ss; ss << f.rdbuf(); J = ss.str();
    uint8_t as[32], bs[32], na[16], nb[16], ap[32], bp[32], c[32], sh1[32], sh2[32], k[32], m[32];
    char hx[65];
    unhex(field("a_secret"), as, 32); unhex(field("b_secret"), bs, 32);
    unhex(field("n_a"), na, 16); unhex(field("n_b"), nb, 16);
    check("a_pub", comp_x25519(as, NULL, ap, rng, NULL) == 0 && (comp_hex(ap, 32, hx), field("a_pub") == hx));
    check("b_pub", comp_x25519(bs, NULL, bp, rng, NULL) == 0 && (comp_hex(bp, 32, hx), field("b_pub") == hx));
    check("commit", comp_commit(ap, na, c) == 0 && (comp_hex(c, 32, hx), field("commit") == hx));
    uint32_t code = 0; char cs[8];
    comp_sas(ap, bp, na, nb, &code); snprintf(cs, sizeof cs, "%06u", (unsigned)code);
    check("code", field("code") == cs);
    check("shared agrees", comp_x25519(bs, ap, sh1, rng, NULL) == 0 && comp_x25519(as, bp, sh2, rng, NULL) == 0 && comp_ct_equal(sh1, sh2, 32));
    check("key", comp_derive_key(sh1, ap, bp, na, nb, k) == 0 && (comp_hex(k, 32, hx), field("key") == hx));
    size_t bt = J.find("\"beat\"");
    std::string msg = field("msg", bt);
    check("beat.mac", comp_hmac(k, msg.c_str(), m) == 0 && (comp_hex(m, 32, hx), field("mac", bt) == hx));
    size_t ot = J.find("\"ota\"");      // the Companion's update request (hid_fi/ota.h compOta)
    std::string om = "ota|" + field("id", ot) + "|" + field("act", ot) + "|" + field("url", ot) + "|" + field("chal", ot);
    check("ota.msg", om == field("msg", ot));
    check("ota.mac", comp_hmac(k, om.c_str(), m) == 0 && (comp_hex(m, 32, hx), field("mac", ot) == hx));
    size_t tk = J.find("\"ticket\"");
    std::string tm = "tkt|" + field("tn", tk) + "|" + field("vn", tk);
    check("ticket", comp_hmac(k, tm.c_str(), m) == 0 && (comp_hex(m, 32, hx), field("mac", tk) == hx));
    uint8_t zero[32] = {0}, one[32] = {1}, out[32];
    check("low-order 0 refused", comp_x25519(as, zero, out, rng, NULL) != 0);
    check("low-order 1 refused", comp_x25519(as, one, out, rng, NULL) != 0);
    uint8_t t[16];
    check("uppercase hex refused", !comp_hex_parse("AB", t, 1));
    check("short hex refused", !comp_hex_parse("abc", t, 2));
    check("non-hex refused", !comp_hex_parse("zz", t, 1));
    return fails ? 1 : 0;
}
