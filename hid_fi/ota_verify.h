// ==================== OTA VERIFY ====================
// Is a release manifest ours? ECDSA P-256 over SHA-256 of the manifest's exact
// bytes, signature r||s as 128 hex characters, the key chosen by its id (first 8
// bytes of SHA-256 of the public key, as hex - computed here, never trusted from
// a table). mbedTLS only: the same code runs on the board and, compiled against
// the same mbedTLS release, on the host (tests/unit/test_ota_verify.py), where it
// must give the answers of tests/vectors/ota_v1.json - as tools/ota_release.py
// and the Companion (hidfi-core/src/ota.rs) do.
#pragma once
#include <stdint.h>
#include <stddef.h>
#include <string.h>
#include "mbedtls/sha256.h"
#include "mbedtls/ecdsa.h"
#include "mbedtls/ecp.h"
#include "mbedtls/bignum.h"

struct OtaKey { uint8_t pub[65]; };   // 04 || x || y

inline int otaHexNibble(char c) {
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'a' && c <= 'f') return c - 'a' + 10;
    if (c >= 'A' && c <= 'F') return c - 'A' + 10;
    return -1;
}

// Exactly 2*n hex characters, nothing more or less.
inline bool otaHexDecode(const char* hex, uint8_t* out, size_t n) {
    if (!hex || strlen(hex) != 2 * n) return false;
    for (size_t i = 0; i < n; i++) {
        int hi = otaHexNibble(hex[2 * i]), lo = otaHexNibble(hex[2 * i + 1]);
        if (hi < 0 || lo < 0) return false;
        out[i] = (uint8_t)(hi << 4 | lo);
    }
    return true;
}

inline void otaKeyId(const uint8_t pub[65], char out[17]) {
    uint8_t h[32];
    mbedtls_sha256(pub, 65, h, 0);
    static const char* x = "0123456789abcdef";
    for (int i = 0; i < 8; i++) { out[2 * i] = x[h[i] >> 4]; out[2 * i + 1] = x[h[i] & 15]; }
    out[16] = 0;
}

// True only when sigHex was made over exactly m[0..n) by the trusted key keyId names.
inline bool otaVerifyManifest(const uint8_t* m, size_t n, const char* keyId, const char* sigHex,
                              const OtaKey* keys, size_t nkeys) {
    if (!m || n == 0 || !keyId) return false;
    uint8_t sig[64];
    if (!otaHexDecode(sigHex, sig, 64)) return false;
    const OtaKey* k = nullptr;
    for (size_t i = 0; i < nkeys && !k; i++) {
        char id[17];
        otaKeyId(keys[i].pub, id);
        if (!strcmp(id, keyId)) k = &keys[i];
    }
    if (!k) return false;

    uint8_t hash[32];
    if (mbedtls_sha256(m, n, hash, 0) != 0) return false;

    mbedtls_ecp_group grp; mbedtls_ecp_point q; mbedtls_mpi r, s;
    mbedtls_ecp_group_init(&grp); mbedtls_ecp_point_init(&q); mbedtls_mpi_init(&r); mbedtls_mpi_init(&s);
    bool ok = mbedtls_ecp_group_load(&grp, MBEDTLS_ECP_DP_SECP256R1) == 0
           && mbedtls_ecp_point_read_binary(&grp, &q, k->pub, 65) == 0
           && mbedtls_ecp_check_pubkey(&grp, &q) == 0
           && mbedtls_mpi_read_binary(&r, sig, 32) == 0
           && mbedtls_mpi_read_binary(&s, sig + 32, 32) == 0
           && mbedtls_ecdsa_verify(&grp, hash, sizeof(hash), &q, &r, &s) == 0;   // checks 1 <= r,s < n
    mbedtls_mpi_free(&s); mbedtls_mpi_free(&r); mbedtls_ecp_point_free(&q); mbedtls_ecp_group_free(&grp);
    return ok;
}
