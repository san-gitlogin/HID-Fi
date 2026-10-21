/* md.c references PSA error mapping only on its PSA path, which this build never takes. */
#include <stddef.h>
typedef int psa_status_t;
const int psa_to_md_errors[1] = {0};
int psa_generic_status_to_mbedtls(psa_status_t s) { return s ? -1 : 0; }
int psa_status_to_mbedtls(psa_status_t s, const int *t, size_t n, int (*f)(psa_status_t)) { (void)t; (void)n; (void)f; return s ? -1 : 0; }
