# Employee campaign registry — K1 receiver

This shop-only receiver stores the portal's complete campaign roster atomically.
It is **OFF by default**. It does not create employee links, attribute orders,
run a sender, process K2, expose a dashboard, or start a campaign.

## Configuration and trust

`EMPLOYEE_REFERRAL_K1_ENABLED=false` returns a fixed 404 without reading the body
or accessing PostgreSQL. The default empty `EMPLOYEE_REFERRAL_K1_KEY` is valid for
normal shop startup. Enabling requires a separate service key: exactly 64 lowercase
hex characters representing 32 random bytes. The key is not an admin JWT, bot token,
campaign-proxy credential or K2 key. Provisioning/activation is a separate ops decision.
Enabled K1 with an invalid key or SQL echo returns a fixed 503; other routes continue.

POST `${API_V1_PREFIX}/internal/employee-referrals/k1` (default `/api/v1`) accepts
UTF-8 JSON, optionally `charset=utf-8`; content encoding is absent or `identity`.
The streamed body limit is 1,048,576 bytes, independent of Content-Length.
No JSON parsing or database access occurs before HMAC authentication.

Exactly one `X-Biotact-K1-Timestamp` (canonical nonnegative decimal Unix seconds,
at most 12 digits) and `X-Biotact-K1-Signature` (64 lowercase hex characters) are
required. HMAC-SHA256 uses the decoded key and these exact bytes:

```text
biotact-k1-v1\nportal-to-shop\nPOST\n<actual-path>\n<timestamp>\n<raw-body>
```

Each `\n` is one LF; no LF is appended to the body. The header clock window is
inclusive ±30 seconds against the integer API clock. Retry preserves event ID and
body bytes and supplies a fresh timestamp/signature. TLS is required between services.

## Snapshot and persistence

Required top-level fields: `schema_version` (integer 1), `event_id` (canonical UUIDv4),
`campaign`, `participants` and `issued_at`. Extra/duplicate JSON keys, nonfinite numbers,
implicit type conversions, NUL and surrogate names are rejected. IDs and revisions are
positive signed BIGINT integers. Names retain whitespace; blank names are invalid.
Campaign names have at most 200 Unicode code points, display names at most 161.

Campaign fields: `id`, `content_revision`, `name`, `product_id`, `state`,
`published_at`, `start_at`, `end_at`, `ended_at` (required, nullable).
States are `published` and terminal `ended`. `end_at > start_at`;
`published_at <= issued_at`; published requires a null ended_at, ended requires
`published_at <= ended_at <= issued_at`. Times are UTC strings ending in Z, with
optional 1–6 fractional digits; ACK uses exactly six fractional digits.

Participants have `employee_code` (canonical UUIDv4), `token` (canonical 22-character
unpadded base64url encoding of 16 bytes) and `employee_display_name`. An empty roster
is valid. Employee codes and tokens cannot repeat in a snapshot. A token permanently
belongs to one campaign/employee pair across the new registry; omission sets membership
false without deleting identity. A later campaign requires its own token.

Three additive tables use the existing database engine: `employee_referral_campaigns`,
`employee_referral_tokens`, `employee_referral_k1_receipts`. Migration
`72a6cf91b3d0` follows `d5a83e71c4f2`. Old referral objects and financial data are
untouched. Receipts store hashes and metadata, never the raw roster.

The campaign row is locked, including first-message races. A single transaction checks
receipt identity, known revision content, immutable campaign metadata, token ownership,
and body time against PostgreSQL `clock_timestamp()` after the lock. New future-issued
bodies return 422; expired bodies may be recorded, without making the roster fresh.
`valid_until = issued_at + 60 seconds`, never arrival time. Exact committed replay does
not recheck body time against the changed clock and never extends the lease.

- First/higher revision: replace current roster and deadline (`applied`).
- Equal revision/content, later issuance: update deadline only (`renewed`).
- Equal revision/content, older/equal issuance: receipt only (`unchanged`).
- Lower nonconflicting revision: receipt only (`superseded`); unknown identities are not stored.
- Same event/body: return original receipt and current registry, `replayed=true`.
- Changed event bytes, known revision content, stable identity or immutable metadata: 409.

`content_sha256` excludes event ID and issuance, normalizes timestamps, sorts participants
by employee code, and hashes UTF-8 compact sorted-key JSON with literal Unicode and no LF.
`body_sha256` hashes exact transport bytes. Current registry and original receipt can
name different revisions after replay. A 200 is returned only after successful commit;
it is not a claim that the registry is fresh or the entire referral chain is ready.
On unknown commit/connection outcome, retry exact bytes to discover the durable result.

## Errors and privacy

All responses have `Cache-Control: no-store`. Fixed errors use
`{"detail":{"code":"...","message":"..."}}`: 404 disabled, 401 invalid auth,
413 payload limit, 415 media/encoding, 422 invalid request, 409 event/revision/identity/
campaign conflict, 503 unavailable. Auth errors include
`WWW-Authenticate: Biotact-K1-HMAC`. Cancellation propagates; unfinished work rolls back.

K1 catches errors before shared log handlers and filters only K1 Sentry events/transactions.
Names, raw body, tokens, headers, keys and SQL exceptions are never logged. Other routes'
telemetry is preserved. Test positive controls prove capture is active.

## Verification

Use Python 3.11 and an **owned disposable PostgreSQL 17 cluster/role/port**.
The existing `tests/conftest.py` uses `biotact_test_db` on the configured server and
creates/drops model metadata. Never point these tests at a shared or production server.
Mounted tests explicitly replace `app.state.employee_referral_sessions`; overriding
`get_db` alone does not isolate K1. Migration tests create/drop their own uniquely named
scratch database on that same test server (the test role needs CREATEDB).

From `packages/api`, with an owned venv containing requirements.txt and dev-only
pytest-cov 7.0.0 / coverage 7.11.0, set synthetic SECRET_KEY and DATABASE_URL, then run:

```bash
ruff check src/
ruff format --check src/
mypy src/ --strict --ignore-missing-imports
pytest tests/ -v --no-header
cat > /tmp/biotact-k1-coverage.ini <<'CONFIG'
[run]
branch = True
concurrency = thread, greenlet
[report]
show_missing = True
fail_under = 90
CONFIG
pytest tests/ -v --no-header --cov=src.employee_referrals --cov-config=/tmp/biotact-k1-coverage.ini --cov-branch --cov-fail-under=90 --cov-report=term-missing
```

The local coverage configuration is required for SQLAlchemy async greenlet switches;
without it, executed continuation lines are missed. See the pinned
[Coverage 7.11 concurrency documentation](https://coverage.readthedocs.io/en/7.11.0/config.html#run-concurrency).
It adds no exclusions and does not change the 90% requirement or shared CI.

`pytest.ini` is the active test configuration. The shared API coverage debt is deferred;
this feature's local critical-domain coverage gate does not change CI.
The three `test_employee_referral_k1_*` modules cover wire, auth, real database races,
commit/replay/failure, privacy and baseline→new→baseline→new migration parity.
The frozen synthetic body in the contract test has 524 bytes and SHA-256
`b63ef17e24e4fc592a53cbe0bc84f4a33b34ed17ac64a28a0321f068814ef7de`.
Its public all-zero 32-byte test key and timestamp `1790856000` yield signature
`0901e5b830e982bf1084e6eb104c8a580e33e0642f531c9607fa0e362c2bd38c`.
These are test fixtures, never deployment credentials.

## Release and recovery

Independent code/security approval precedes product push/PR. Merge triggers deployment
and requires a separate release decision with backup/restore and actual CI evidence.
Activation and the first campaign are separate decisions. For a receiver incident,
disable K1, retain identities/receipts and use a separately authorized compatible code
rollback. Do not downgrade/drop new tables in production. Local downgrade is tested
only in the owned scratch database. Sender, order attribution and K2 follow later specs.
