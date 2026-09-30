# Campaign administration

Protected routes use the existing admin authentication and layout:
`/campaigns`, `/campaigns/new`, `/campaigns/:id`. The list starts unfiltered at
page 1, size 20. Stored state determines Draft/Published/Ended; passing the end
date does not manufacture an Ended state. Refresh is manual.

The editor keeps saved server data separate from input. A Draft may have only a
name. PATCH sends the exact saved `updated_at` and changed fields only; explicit
clearing uses null, reverting an edit removes it, and a no-op emits no PATCH.
Untouched timestamp strings retain their original offset and fractional seconds.
Dates entered at second precision are aware RFC3339 strings with `+05:00`.

Publish and End each require their own confirmation and send only the saved
version. Dirty Drafts must be saved first. Published and Ended content is readable
and selectable; only Published has End. No delete, clone, product mutation or
optimistic publication exists. Product eligibility, image origin, complete
publication fields and overlapping periods remain server decisions.

Boundary decoders require the exact record/page shape and expected HTTP status,
identity, state, requested pagination and advancing version. Messages use a local
Russian allowlist for exact error status/code pairs. Diagnostic bodies are never
used as UI content. 403 hides actor-owned campaign data until a fresh successful
authorization read. Token/actor changes also remove previous campaign cache and
invalidate late page callbacks.

The tab controller acquires its guard synchronously and saves a minimal validated
actor-owned sessionStorage marker before dispatch. Writes have retries disabled,
always network mode and browser/Query offline checks at dispatch. Reconnection,
navigation, mount and refresh never resume a write. A sent request is not aborted
on page unmount. The marker contains attempt metadata, not a JWT or replayable
form payload, and is limited to 2 KiB.

A stale/state/immutable refusal preserves input. Fresh server data is shown
separately; accepting it requires a loss-of-input confirmation. A timeout,
network/cancellation error, malformed response, stale success or unrecognized
server error means **Результат действия не подтверждён**. The same actor's
marker survives reload and logout/re-login in that tab. Another actor's marker
is not displayed or cleared. Other tabs/devices are outside this guard.

Record recovery performs a new GET, even when presentation cache is fresh. A
successful read does not establish attribution or certify failure of the old
operation. Only explicit acceptance closes the check. Create recovery requires
an unfiltered fresh list; an administrator may choose an ID and read it fresh,
or acknowledge possible duplication and open a blank Draft. There is no unique
name/idempotency certificate. Failed, malformed or forbidden inspection keeps
writes blocked. A damaged marker must be reconciled using a fresh list and its
separate confirmation.

From the admin directory run `npx tsc -b`, `npm run lint`, `npm run test`, and
`npm run build`. Tests exercise actual feature code with transport/storage/time
boundaries. The existing uploader's optional activity callback is used only by
campaigns; default multi-file callers and their upload endpoints remain intact.
Private coverage and real-app browser acceptance evidence is maintained with the
engineering handoff, outside the product repository.
