// HID-Fi companion: the pairing cryptography, board side.
//
// Pure functions over mbedTLS (3.6, as shipped in the ESP32 core). No Arduino,
// no globals, no randomness except through the caller's rng, so the same file
// compiles on a PC and is checked there against companion/vectors/pairing_v1.json
// - the vectors the Rust app is pinned to. If you change anything here, that
// check must still pass; a mismatch means the board and the app no longer agree.
//
// The protocol is specified in companion/DESIGN.md.
#pragma once

#include <stddef.h>
#include <stdint.h>
#include <string.h>
#include "mbedtls/ecp.h"
#include "mbedtls/hkdf.h"
#include "mbedtls/md.h"
#include "mbedtls/sha256.h"

#define COMP_KEY_LEN   32
#define COMP_NONCE_LEN 16

typedef int (*comp_rng_t)(void* ctx, unsigned char* out, size_t len);

// Wipes memory the compiler is not allowed to optimise away.
static inline void comp_wipe(void* p, size_t n) {
    volatile uint8_t* v = (volatile uint8_t*)p;
    while (n--) *v++ = 0;
}

// Time does not depend on where the first difference is.
static inline bool comp_ct_equal(const uint8_t* a, const uint8_t* b, size_t n) {
    uint8_t d = 0;
    for (size_t i = 0; i < n; i++) d |= (uint8_t)(a[i] ^ b[i]);
    return d == 0;
}

// Exactly 2n lowercase hex digits and nothing else. Uppercase is refused on
// purpose: the proofs cover the text as sent, so there is one spelling only.
static inline bool comp_hex_parse(const char* s, uint8_t* out, size_t n) {
    if (!s || strlen(s) != n * 2) return false;
    for (size_t i = 0; i < n; i++) {
        uint8_t v = 0;
        for (int j = 0; j < 2; j++) {
            char c = s[i * 2 + j];
            uint8_t x;
            if (c >= '0' && c <= '9') x = (uint8_t)(c - '0');
            else if (c >= 'a' && c <= 'f') x = (uint8_t)(c - 'a' + 10);
            else return false;
            v = (uint8_t)((v << 4) | x);
        }
        out[i] = v;
    }
    return true;
}

static inline bool comp_hex_ok(const char* s, size_t n) {
    uint8_t tmp[64];
    if (n > sizeof(tmp)) return false;
    bool ok = comp_hex_parse(s, tmp, n);
    comp_wipe(tmp, sizeof(tmp));
    return ok;
}

// out must hold 2n+1 bytes.
static inline void comp_hex(const uint8_t* in, size_t n, char* out) {
    static const char D[] = "0123456789abcdef";
    for (size_t i = 0; i < n; i++) { out[i * 2] = D[in[i] >> 4]; out[i * 2 + 1] = D[in[i] & 15]; }
    out[n * 2] = 0;
}

static inline int comp_sha256(const uint8_t* const* parts, const size_t* lens, int count, uint8_t out[32]) {
    mbedtls_sha256_context c;
    mbedtls_sha256_init(&c);
    int rc = mbedtls_sha256_starts(&c, 0);
    for (int i = 0; rc == 0 && i < count; i++) rc = mbedtls_sha256_update(&c, parts[i], lens[i]);
    if (rc == 0) rc = mbedtls_sha256_finish(&c, out);
    mbedtls_sha256_free(&c);
    return rc;
}

// H("hidfi-commit" || a_pub || nA)
static inline int comp_commit(const uint8_t a_pub[32], const uint8_t n_a[16], uint8_t out[32]) {
    const uint8_t* p[] = { (const uint8_t*)"hidfi-commit", a_pub, n_a };
    const size_t   l[] = { 12, 32, 16 };
    return comp_sha256(p, l, 3, out);
}

// u32_be(H("hidfi-sas" || a_pub || b_pub || nA || nB)[0..4]) mod 10^6
static inline int comp_sas(const uint8_t a_pub[32], const uint8_t b_pub[32],
                           const uint8_t n_a[16], const uint8_t n_b[16], uint32_t* code) {
    uint8_t d[32];
    const uint8_t* p[] = { (const uint8_t*)"hidfi-sas", a_pub, b_pub, n_a, n_b };
    const size_t   l[] = { 9, 32, 32, 16, 16 };
    int rc = comp_sha256(p, l, 5, d);
    if (rc == 0) *code = (((uint32_t)d[0] << 24) | ((uint32_t)d[1] << 16) | ((uint32_t)d[2] << 8) | d[3]) % 1000000u;
    return rc;
}

// X25519 (RFC 7748) on mbedTLS's Montgomery curve. The scalar is clamped here,
// exactly as RFC 7748 and x25519-dalek do, so a raw 32-byte secret means the
// same key on both sides. Little-endian throughout, as the RFC specifies.
// peer == NULL computes the public key (scalar times the base point).
// Returns 0, or -1 if the result is all zeros (a low-order peer point).
static inline int comp_x25519(const uint8_t secret[32], const uint8_t* peer, uint8_t out[32],
                              comp_rng_t rng, void* rng_ctx) {
    uint8_t k[32];
    memcpy(k, secret, 32);
    k[0] &= 248; k[31] &= 127; k[31] |= 64;

    mbedtls_ecp_group grp; mbedtls_mpi d; mbedtls_ecp_point P, R;
    mbedtls_ecp_group_init(&grp); mbedtls_mpi_init(&d);
    mbedtls_ecp_point_init(&P); mbedtls_ecp_point_init(&R);
    size_t olen = 0;
    int rc = mbedtls_ecp_group_load(&grp, MBEDTLS_ECP_DP_CURVE25519);
    if (rc == 0) rc = mbedtls_mpi_read_binary_le(&d, k, 32);
    // read_binary masks the top bit of a Curve25519 u-coordinate (RFC 7748 5).
    if (rc == 0) rc = peer ? mbedtls_ecp_point_read_binary(&grp, &P, peer, 32)
                           : mbedtls_ecp_copy(&P, &grp.G);
    if (rc == 0) rc = mbedtls_ecp_mul(&grp, &R, &d, &P, rng, rng_ctx);
    if (rc == 0) rc = mbedtls_ecp_point_write_binary(&grp, &R, MBEDTLS_ECP_PF_UNCOMPRESSED, &olen, out, 32);
    if (rc == 0 && olen != 32) rc = -2;
    mbedtls_ecp_point_free(&R); mbedtls_ecp_point_free(&P);
    mbedtls_mpi_free(&d); mbedtls_ecp_group_free(&grp);
    comp_wipe(k, sizeof(k));
    if (rc != 0) { comp_wipe(out, 32); return rc; }
    uint8_t z = 0;
    for (int i = 0; i < 32; i++) z |= out[i];
    if (z == 0) return -1;
    return 0;
}

// K = HKDF-SHA256(ikm = shared, salt = H(a_pub || b_pub || nA || nB), info = "hidfi-companion-v1")
static inline int comp_derive_key(const uint8_t shared[32], const uint8_t a_pub[32], const uint8_t b_pub[32],
                                  const uint8_t n_a[16], const uint8_t n_b[16], uint8_t key[32]) {
    uint8_t salt[32];
    const uint8_t* p[] = { a_pub, b_pub, n_a, n_b };
    const size_t   l[] = { 32, 32, 16, 16 };
    int rc = comp_sha256(p, l, 4, salt);
    if (rc == 0) rc = mbedtls_hkdf(mbedtls_md_info_from_type(MBEDTLS_MD_SHA256), salt, 32, shared, 32,
                                   (const uint8_t*)"hidfi-companion-v1", 18, key, 32);
    return rc;
}

static inline int comp_hmac(const uint8_t key[32], const char* msg, uint8_t out[32]) {
    return mbedtls_md_hmac(mbedtls_md_info_from_type(MBEDTLS_MD_SHA256), key, 32,
                           (const uint8_t*)msg, strlen(msg), out);
}
