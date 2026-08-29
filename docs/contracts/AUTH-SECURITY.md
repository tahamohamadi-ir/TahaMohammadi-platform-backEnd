# Authentication and Security Contract

The migrated backend contains staff authentication, MFA endpoints, admin API middleware, permission decorators, preview secrets, rebuild protection, content sanitization, media inspection, and security tests. These are security boundaries and must not be bypassed during frontend integration.

Before the new admin scaffold, freeze the exact session/cookie, CSRF, MFA challenge, timeout, permission, forbidden, and OpenAPI-access behavior. The selected pre-scaffold baseline is same-origin reverse-proxy delivery; a cross-origin design requires a separately accepted CORS/cookie/CSRF decision. Before production, validate secure-cookie/proxy settings, host policy, secrets, Argon2, TLS, log redaction, rate/abuse controls, contact delivery, preview expiry, upload constraints, and rebuild execution.
