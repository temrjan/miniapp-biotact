# Employee referrals — K1 receiver and order capture

The shop receiver stores the portal's complete campaign roster atomically.
Optional order capture uses that local registry to save employee attribution.
Both features are **OFF by default** and have independent switches.
The portal owns roster issuance; K2 and customer entry flows use separate contracts.

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

K1 classifies the configured ASGI path before CORS can return early, without reading the
body or trusting Host. Request-scope tagging survives late send failures and SDK capture.
The matched route retains the same privacy boundary; only K1 events/transactions are filtered.
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
cat > /tmp/biotact-k1-coverage.ini <<'CONFIG'
[run]
branch = True
concurrency = thread, greenlet
[report]
show_missing = True

CONFIG
pytest tests/ -v --no-header --cov=src --cov-config=/tmp/biotact-k1-coverage.ini --cov-branch --cov-report=json:coverage.json
```

The local coverage configuration is required for SQLAlchemy async greenlet switches;
without it, executed continuation lines are missed. See the pinned
[Coverage 7.11 concurrency documentation](https://coverage.readthedocs.io/en/7.11.0/config.html#run-concurrency).
It adds no exclusions or shared CI changes. Inspect actual branches in the critical
referral and new return modules: covered_branches / num_branches must reach80%.
This is separate from the deferred overall API coverage debt.

`pytest.ini` is the active test configuration. The shared API coverage debt is deferred;
this feature's local critical-domain coverage gate does not change CI.
The `test_employee_referral_k1_*` modules cover wire, auth, real database races,
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
only in the owned scratch database. Optional order capture is described below;
portal issuance and the later K2 lifecycle have separate contracts.


## Optional order capture

`EMPLOYEE_REFERRAL_CAPTURE_ENABLED=false` independently gates capture at
`POST ${API_V1_PREFIX}/orders`. Default OFF leaves existing callers and orders ordinary.
It needs no transport key: it uses the already received local K1 registry and makes
no portal/network request. SQL echo must be disabled; an enabled canonical-token
capture request with `DATABASE_ECHO=true` returns fixed 503 before pricing/capture
queries. Ordinary requests without a canonical token remain available.

`referral_token` is optional, null or a strict string. Missing/null, noncanonical,
unknown, omitted-from-roster, stale, ended, outside-window or mismatched-product
attribution yields an ordinary order. Other request validation and shopper access
continue to apply. The token does not change prices, point rules or quote fingerprint;
`POST /orders/quote` ignores attribution and writes no snapshots. No public response
contains employee identity, campaign identity or the referral token.

Capture holds a campaign share lock conflicting with K1 receiver writes until the
order transaction ends. It refreshes the replica/current roster after acquiring the
lock and checks the PostgreSQL wall clock afterward. The local 60-second lease is
never extended by checkout. A successful capture copies the campaign/employee
identity, currency and all separate server-priced lines for that campaign product,
including duplicate requested lines; other basket products stay ordinary.

`employee_referral_orders` has one header per order; `employee_referral_order_items`
has one snapshot per participating order item. These tables store no token, phone or
Telegram ID. Their product identity has no catalogue foreign key, so catalogue/K1
changes and product deletion leave the snapshots unchanged. Order/item deletion
retains the existing cascade policy. Snapshots record order terms, not paid totals or
employee rewards; later payment/refund projection uses separate lifecycle logic.

The caller owns commit/rollback. Capture flushes after pending order/items have IDs,
before full-points debit and the paid transition. Partial points remain a reservation.
The full-points router commits before Telegram; card/partial commit on dependency
completion. A failure before commit rolls back the new related facts, whereas an
error/cancellation after durable commit leaves them saved together. A lost commit
confirmation cannot prove absence of the order and introduces no automatic retry or
new request idempotency. The pre-existing stale-reservation cleanup has its own commit.

The POST order/quote privacy boundary drops only its own Sentry events/transactions,
classifies the decoded ASGI path independently of Host, hides validation input and
uses fixed diagnostics for affected errors. K1 filtering and unrelated telemetry
remain active. No launcher, K2 sender, customer UI or production activation is added.
Deployment is additive and defaults OFF. Recovery disables capture while retaining
these tables/history; production destructive downgrade is not a recovery operation.


## Customer entry and resolve

Bot `EMPLOYEE_REFERRAL_ENTRY_ENABLED=false`, frontend
`VITE_EMPLOYEE_REFERRAL_ENTRY` (only literal `true`) and API
`EMPLOYEE_REFERRAL_ENTRY_ENABLED=false` are independent default-OFF switches.
The capture switch remains the authority for new attribution. An incident OFF
bundle disables capture + API entry + bot entry and rebuilds/serves frontend OFF;
K1 sender/receiver switches are separate. No new credentials or migration are needed.

Private `/start ref_<positive decimal product hint>_<canonical 22-char token>`
opens an inline WebApp product button after normal language/optional-phone
registration. The bot preserves only an unconfirmed per-user Redis FSM intent,
serialized by process-local event isolation in the single fork bot, and never resolves it over the network.
Malformed ref starts carry an empty `ref` marker so a previous context is retired.

Authenticated POST `${API_V1_PREFIX}/employee-referrals/resolve` accepts only
`{"token":"..."}` (strict max256 string; streamed JSON limit1024 bytes). Entry or
capture OFF returns404 before authentication/body/SQL. ON reads local current K1
under the same campaign share lock/time predicates as capture, without renewal.
Valid200 contains only `state`, string `product_id`, `observed_at`, `expires_at`
(UTC microseconds); all negative registry states return `{"state":"invalid"}`.
Auth401/403 and other failures have fixed bodies; all responses are no-store.
The expiry is a campaign-end storage deadline, not a K1 freshness guarantee.
Existing shared Telegram verifier limitations require owner assessment before ON.

Frontend owns one current context above routes, removes URL ref before resources
and keeps only `{version:1,token,expires_at}` under `biotact_employee_referral`.
Stored/reopened/resumed context must resolve again. New entries retire old tokens
synchronously; pending/error/expired/OFF flows permit ordinary checkout. Storage
failure selects document-wide memory-only mode. Quote remains independent; current
active token is sampled immediately before POST orders, never payment/return URLs.

### Serving privacy preflight before ON

The HTML's early no-referrer meta and early query cleanup do not hide the initial
GET from a proxy. Verify the actual serving layer before activation:

1. Inspect the effective vhost/access/error/analytics configuration and every CDN
   hop for product/root entry URLs. Use pathname-only logging (nginx `$uri`, never
   `$request`, `$request_uri`, `$args`, full Referer or Location). Disable or redact
   error/analytics fields that would record a query. Do not log initData headers.
2. Serve `Referrer-Policy: no-referrer` on the actual HTML response before resources.
3. Send a distinct synthetic canonical token canary through root/product entry,
   duplicated/empty ref and an error route on an owned staging proxy. Inspect all
   access/error/analytics outputs; canary must be absent while an unrelated
   pathname control proves those channels are active. Observe browser requests to
   first assets/external Telegram, images/iframe and checkout navigation: no full
   entry Referer. Confirm URL cleanup preserves Club code and Telegram hash.
4. Record config hashes, served assets, header and observations in release evidence.
   A local harness demonstrates its own configuration only; production readiness
   remains unverified until that evidence exists. Flags remain OFF on release.

Focused entry suites use real mounted auth/PostgreSQL, real bot Dispatcher/Redis
and deferred frontend replies/checkout payloads. The critical branch guard above includes resolve; coverage exclusions and shared CI
are unchanged.

Bot entry integration tests additionally require an explicit `BOT_TEST_REDIS_URL`
pointing to an owned disposable Redis 7 server. The bot CI job provides its own
Redis service. Tests use unique per-fixture FSM key prefixes and delete only their
own keys; a separate sentinel verifies that unrelated Redis data survives. They
never use the application `REDIS_URL` as a fallback or flush the database.

## Retained paid count

Applied signed callback success and fully-points checkout append one immutable
`employee_referral_paid_outbox` row in the existing paid transaction. Card, mixed
and fully-points count every retained campaign-product item quantity alike, including
separate repeated product rows. Current catalogue/K1/capture OFF/End/approval cannot
change saved attribution. Ordinary orders and unsuccessful, unsigned, rejected or
manually edited payments append nothing. Existing customer Club behavior is preserved.

The outbox has unique order/event IDs, exact frozen count-only K2 bytes, SHA256 and
creation time. Its sequence ID is source_position, not a commit watermark. The
history has no cascading source FKs and an app-role UPDATE/DELETE rejection trigger.
Retries reuse stored bytes; no sender, bookkeeping table or new activation flag is
introduced. Serialization/storage/commit errors return fixed503
`{"detail":{"code":"referral_paid_unavailable"}}` and roll back the paid unit;
post-commit Telegram failure preserves it. Private paid logging and Sentry channels
are scoped; unrelated telemetry remains active. SQL echo blocks this private channel.

Additive migration b5a1d6e8f230 follows9b47c6e3d821. Existing history is not backfilled.
Confirmed returned units are described below; delivery and employee UI remain separate.
The current minimum is80% actual critical branches; overall API debt is deferred. Scoped paid tests use
real PG17, mounted get_db/commits and external Telegram substitutes only. Existing
API lint/format/strict-mypy/full-suite and migration parity gates remain required;
shared coverage debt is reported independently without changing exclusions or CI.


## Confirmed returned campaign units

GET/POST `${API_V1_PREFIX}/admin/orders/{order_id}/referral-returns` require an
existing active admin authenticated by the ordinary admin JWT. SQL echo refuses503
before admin SQL. GET returns only `{order_id, order_version, lines}`. Each line
has decimal-string `order_item_id`, `product_id`, `paid_quantity`,
`returned_quantity`; remaining is paid minus returned. Frozen paid lines survive
original order/item/product deletion and roster/capture changes. Missing retained
paid source gives404; payment status edits and provider revert never infer returns.

POST confirms an already performed quantity return. It performs no provider, Club,
stock or status operation. JSON has exactly canonical UUIDv4 `return_id` and
nonempty `lines` of positive BIGINT decimal-string `order_item_id` and delta
`quantity`. Items must be unique retained campaign rows. The service sorts them
numerically and bounds cumulative returns by original paid quantities. Body size,
media, duplicate-key and strict validation use the existing bounded JSON contract.

200 follows durable commit and contains only `{receipt, replayed}`. Receipt has
return_id/order_id/order_version as strings, UTC microsecond confirmed_at, and
original normalized delta lines. Exact retry returns the original receipt, including
after remaining reaches zero. Reused UUID with another order/delta or paid event
UUID gives409. All errors are fixed no-store `detail.code`:
`referral_return_invalid_auth`401, forbidden403, not_found404, conflict/bounds409,
payload_too_large413, unsupported_media415, invalid_request422, unavailable503.
These names share the `referral_return_` prefix. Queries, noncanonical/trailing
paths are invalid; unsupported methods give fixed405 invalid_request.

The OrderDetails control shows paid/returned/remaining per item, partial quantities
and all remaining, with explicit confirmation before submission. One UUID and its
deltas are persisted in sessionStorage before POST. Double click is disabled and
an unknown network/503 outcome, including reload, retries that same intent. Storage
failure blocks sending.200 clears intent and refreshes;409/422 refresh for a new
explicit action;401/403 hide protected returns. Ordinary source404 hides only this
control. No history/dashboard or employee result UI is introduced.

`employee_referral_return_outbox` is one immutable confirmation plus frozen K2
record, with server admin ID/time and sorted deltas. Writers lock the immutable
paid root FOR UPDATE, then validate every saved hash/body/metadata/identity and
consecutive version/cumulative delta chain under fresh READ COMMITTED. Corruption
fails503, including replay. Global UUID races roll back before a fresh read.
Cancellation propagates; lost commit confirmation requires the same-ID retry, never
a compensating deletion. GET validates history within one repeatable-read snapshot.
Both outboxes share the paid sequence; gaps are permitted and are not completeness.

The unchanged paid v1 bytes have three line fields. Refund v2+ freezes all original
paid lines with cumulative integer returned_quantity, the same attribution and
exact compact sorted-key UTF-8/hash. It has event_type sale_refunded, event_id=return
UUID, shared source_position, DB instant for both wire times, and no admin/money/PII.
The receiving Portal must support returned snapshots before delivery.

Additive b5b2e7f9a341 follows b5a1d6e8f230 without rewriting paid history or resetting
its sequence. App role needs paid-root row lock, return INSERT and shared sequence
USAGE; UPDATE/DELETE triggers refuse history changes. Scratch downgrade requires
empty returns and drops only new objects; production downgrade is not recovery.
After saved confirmations, retain return-compatible code/schema. Capture OFF stops
new attribution without dropping retained facts; no new producer flag exists.
Release order: extended Portal/schema with K2 OFF, then Shop schema/confirmation,
then separately verified delivery. No transport worker is part of this module.

`test_employee_referral_returns.py` uses the real mounted get_db/sessionmaker and
owned PG17 for3→2→0, root/UUID races, durable/unknown commit, corrupt history, auth
and scoped privacy with live local SDK/log positive controls. Only external provider
and notification boundaries are substituted. Migration tests use unique owned
scratch databases/restricted roles for populated compatibility, immutable triggers,
shared sequence and whole-chain roundtrip/parity. Admin tests exercise the actual
control and runtime decoder against the axios boundary.
