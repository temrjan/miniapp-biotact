# Administrator authentication and operator accounts

Administrator login reuses Shop `users`, the existing form and signed admin JWT.
`admin_password_accounts` contains only normalized unique login and a bcrypt cost12
hash, keyed to one real User. Login never creates a User or grants/restores rights.
Every JWT admin request requires a current active administrator in PostgreSQL.
Customer Telegram initData authentication is unchanged. Legacy `admin_users`
remains schema-only; its public registration/login routes stay removed.

## Operator commands

Run from `packages/api` in the configured service environment, using an interactive
terminal and the deployed interpreter. Confirm the person's existing positive
Telegram ID privately before provisioning. They first open the Shop bot if no
Shop User exists. The following is syntax, not a real identity or password:

```sh
python scripts/admin_accounts.py --help
python scripts/admin_accounts.py create --login LOGIN --telegram-id TELEGRAM_ID
python scripts/admin_accounts.py set-password --login LOGIN
python scripts/admin_accounts.py revoke --login LOGIN
```

Create atomically inserts the credential and grants admin to an existing active
User. Any occupied login or User binding refuses without an overwrite. Reset
changes only the hash, including for revoked/inactive users. Revoke removes admin
rights and retains credentials and active status; repeated revoke succeeds.
No list/export/delete or implicit regrant command is provided.

Logins trim ASCII outer whitespace and lowercase ASCII; require 3–64 characters
matching `[a-z][a-z0-9_.-]*`. `biotact` is reserved. Password creation/reset requires
at least eight Unicode characters and at most 72 UTF-8 bytes, without trimming.
Passwords are entered twice with hidden input. Piped/nonterminal input, fallback
that could echo input, mismatch and cancellation refuse without writing. Never
pass passwords in arguments, environment variables or files. The CLI owns an
engine with SQL echo disabled and parameter hiding; errors contain no values.
Exit0 is success, exit2 is validation/identity/conflict, exit1 is service failure.

## Login and access

`POST /api/v1/admin/auth/login` accepts only strict string `username` and `password`
fields, up to 4096 body bytes. Success retains the existing token/User DTO.
Wrong, unknown, inactive or revoked credentials share a generic401. Malformed
wire is422; oversized body413. Storage/hash/SQL-echo/capacity failure is503.
Existing per-IP five-failures/60seconds and subsequent 900second lockout remain.
The IP is the buyer `peer()` address: `X-Real-IP` counts only from a peer in
`BUYER_TRUSTED_PROXY_PEERS`; the application does not read `X-Forwarded-For`.
Under the `ecosystem.config.js` launch, uvicorn's proxy-header handling has already
set the peer from the nginx-appended `X-Forwarded-For` entry; it must trust only the
nginx address (`FORWARDED_ALLOW_IPS` unset or `127.0.0.1`), because `*` makes the
address client-chosen. With that handling off (`--no-proxy-headers`),
`BUYER_TRUSTED_PROXY_PEERS` must be `["127.0.0.1/32"]`.
Bcrypt runs off the event loop, with four in-flight admissions and no waiting
queue; cancellation holds admission until its worker finishes. Unknown valid
named login performs a fixed cost12 dummy comparison.

`biotact` remains the sole plaintext-config compatibility credential, using private
`ADMIN_PASSWORD` and `ADMIN_TELEGRAM_ID`. Empty password disables it. It requires
an existing active admin and never creates/promotes/renames that identity.
Telegram-code verification retains its whitelist and bot header; final issuance
rechecks both whitelist and current active/admin rights. Rights refusal/success
consumes a verified code; storage failure retains an unexpired code for retry.

Admin JWT requires integer expiration and canonical positive decimal User ID.
Revocation/deactivation/deletion blocks the next request even with an old JWT.
Already admitted requests may finish. Password reset and client logout do not
invalidate JWT. Regrant before expiry revives it. For suspected exposure: revoke,
reset privately, wait at least the **actual configured** lifetime since revoke,
then explicitly regrant through the existing authorized User role operation
(`PATCH /api/v1/admin/users/{user_id}`). Read the live lifetime; do not infer it
from source defaults. Named login does not require the Telegram whitelist.

## Private boundary and delivery

The entire application auth prefix, including errors/preflight/aliases, is
`no-store`. Auth exceptions and logs are value-free, private Sentry events are
suppressed, and private breadcrumbs/spans are removed from unrelated telemetry.
The real login hook does not log credential-bearing error objects. This does
not change external nginx logging configuration.

Delivery uses the existing GitHub PR → green CI → merge → green exact-main CI →
automatic deployment route. Before merging: verify backup/recovery, SQL echo OFF,
actual configured JWT lifetime and existing active/admin identities for compat
and whitelisted code access. An unresolved identity blocks release. Deployment
migrates before restarting; the old code ignores the additive empty table.
Verify deployed source/schema, compatibility login and a protected section using
private credentials. Provision named accounts only with operator authority; then
confirm real login/protected access. Do not test revocation on a colleague's live
rights. A separately assigned disposable identity can exercise refusal/revoke.
Retire compatibility only after confirmed named access and separate operator
approval, by emptying its private password and restarting the configured service.

Migration `b6d4e8f2a301` is additive over `b6a3c8d1e240`. Populated downgrade is
refused under a table lock and retains rows/revision. Empty roundtrip is supported.
Code rollback retains table/data and does not downgrade production. Old code
restores automatic promotion and weak guards, so it is **not** a safe revocation
fallback: stop admin use and apply a forward fix, or obtain explicit authorization
for the older behavior. Backup restoration is a separate operator decision.
