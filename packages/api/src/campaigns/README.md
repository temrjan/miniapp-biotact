# Campaign administration proxy

The shop authenticates an admin Bearer JWT and reads the current shop account on
every operation. Both `is_admin` and `is_active` must be true. The authorization
session closes before the server calls the portal with its separate service
credential. The portal owns campaign records, publication eligibility, versions
and history; the shop stores no campaign copy.

Set the optional server environment variables `CAMPAIGN_PORTAL_ORIGIN` and
`CAMPAIGN_SERVICE_TOKEN`. The origin must be an absolute HTTPS origin with an
optional port, without credentials, path, query, fragment or whitespace. The
nonblank token is used exactly as configured. Keep it out of browser builds,
requests and logs. Missing or invalid campaign configuration disables campaign
operations while import, startup, health, catalogue and checkout remain usable.
Settings retain the shop's case-insensitive environment-name matching.

## HTTP operations

Paths below are relative to `/api/v1/admin/campaigns`. The upstream prefix is
`/api/v1/internal/campaigns`. Only these fixed operations are forwarded; there
is no arbitrary destination, catch-all proxy or DELETE operation.

| Method | Suffix | Input | Success |
| --- | --- | --- | --- |
| GET | empty | Optional `state=draft\|published\|ended`, `page>=1` (default 1), `size=1..100` (default 20) | 200 CampaignPage |
| GET | `/{campaign_id}` | Positive integer ID | 200 CampaignRecord |
| POST | empty | DraftInput | 201 CampaignRecord |
| PATCH | `/{campaign_id}` | EditInput | 200 CampaignRecord |
| POST | `/{campaign_id}/publish` | TransitionInput | 200 CampaignRecord |
| POST | `/{campaign_id}/end` | TransitionInput | 200 CampaignRecord |

Unknown query keys are not forwarded. Bodies reject extra fields and type
coercion. DraftInput requires a string `name` (max 200) and permits nullable
`product_id` (positive JSON integer), `image_url` (string, max 500), `description`,
`terms`, `start_at` and `end_at`. An incomplete Draft is valid. Text, image
origin, date ordering, product eligibility and lifecycle semantics are checked
by the portal, preserving its error precedence.

EditInput requires `expected_updated_at` and at least one editable Draft field.
Omission leaves a field unchanged; explicit null clears a nullable field. `name`
cannot be null. TransitionInput contains only `expected_updated_at`. Dates and
versions are aware RFC 3339 strings; the proxy preserves the supplied precision
and instant. It never generates or fetches a precondition on behalf of the caller.

CampaignRecord contains exactly `id`, `name`, `product_id`, `image_url`,
`description`, `terms`, `state`, `start_at`, `end_at`, `published_at`, `ended_at`,
`created_at` and `updated_at`, including required null-valued keys. CampaignPage
contains exactly `items`, `total`, `page`, `size` and `pages`. IDs/counts are
strict JSON integers; states are `draft`, `published`, `ended`. Returned IDs
must match addressed campaigns. The proxy rejects malformed replies, unknown
fields and unexpected success statuses rather than returning upstream data.

## Errors and recovery

All registered operation responses carry `Cache-Control: no-store`. Errors have
the fixed shape `{"detail":{"code":"...","message":"..."}}`. Only local
`401 invalid_admin_auth` includes `WWW-Authenticate: Bearer`. Upstream cookies,
authentication headers, exception text and arbitrary diagnostics are not returned.

| Status | Code | Fixed message |
| --- | --- | --- |
| 401 | `invalid_admin_auth` | Войдите в административную панель заново. |
| 403 | `admin_forbidden` | Доступ к кампаниям разрешён только действующему администратору. |
| 422 | `invalid_request` | Проверьте формат запроса и обязательные поля. |
| 404 | `campaign_not_found` | Кампания не найдена. Обновите список. |
| 409 | `campaign_changed` | Кампания изменена другим администратором. Обновите данные. |
| 409 | `invalid_campaign_state` | Действие недоступно в текущем состоянии кампании. Обновите данные. |
| 409 | `immutable_campaign` | После публикации кампания не редактируется. Завершите её и создайте новую. |
| 409 | `campaign_overlap` | Период пересекается с опубликованной кампанией этого товара. |
| 422 | `invalid_campaign_dates` | Проверьте начало и окончание периода кампании. |
| 422 | `incomplete_campaign` | Перед публикацией заполните товар, изображение, описание, условия и даты. |
| 422 | `invalid_image_url` | Используйте изображение из загрузок магазина. |
| 422 | `product_not_found` | Товар не найден. Выберите другой товар. |
| 422 | `product_inactive` | Товар отключён в магазине. |
| 422 | `product_not_purchasable` | Этот товар недоступен для покупки. |
| 422 | `product_out_of_stock` | Товар отмечен как отсутствующий в наличии. |
| 503 | `shop_unavailable` | Не удалось проверить товар в магазине. Повторите позже. |
| 503 | `service_unavailable` | Сервис кампаний временно недоступен. Повторите позже. |
| 503 | `campaign_service_unavailable` | Сервис кампаний недоступен. Обратитесь к ответственному за систему. |
| 503 | `campaign_result_unknown` | Не удалось подтвердить результат операции. Обновите данные кампаний перед повтором. |

Portal errors are recognized only for an exact status/code pair and exact nested
`{"detail":{"code":"..."}}` shape. A portal service-auth refusal is a service
failure, not a browser login failure. Domain/product refusals retain their code.

Each outbound operation has a 10-second whole-operation deadline (the approved
V3 operational default), including response receipt and validation, verified TLS,
no redirects, no environment proxy and no automatic retry. A write that entered
the client but cannot be confirmed returns `campaign_result_unknown`. This
includes an exact portal `503 service_unavailable`, which does not prove rollback.
Reads receiving that exact error return `service_unavailable`; other unconfirmed
reads return `campaign_service_unavailable`. Local auth/input/config/database
failures before sending remain definite failures.

After an unconfirmed PATCH, Publish or End, read the campaign. After an unconfirmed
create, inspect the list before deciding whether to create again. A failed or
inconclusive read is not permission to repeat the write. No rollback success is
inferred from an HTTP error. Published and Ended content, including images, is
immutable. End retains history; correcting a published campaign means End and a
new campaign ID. Existing shop product APIs support selection; this proxy does
not perform another product check or write product prices, stock or sale flags.

## Release boundary and tests

V3 adds the server API only. Admin UI, token installation, portal deployment and
live campaign publication are separate work. The inert V3 code release requires
independent code/security approval, green checks on the concrete head, missing
configuration/startup/checkout proof, read-only absence proof for the token in
both deployments' active processes and selected runtime sources, and the current
unauthenticated portal internal-route 404. Accepted case variants, blank or
shadowed token declarations fail absence. Captain's go on that verified head
authorizes its merge and automatic shop code release. Changed conditions/head
require relevant verification and approval again. No rollback duration is promised.

Tests are `tests/test_campaign_proxy.py` and `tests/test_campaign_portal_client.py`.
They use committed real PostgreSQL authorization and a fake only at the external
HTTP boundary, including retained-Draft/read-back recovery after a valid generic
503. Use a dedicated disposable PostgreSQL instance/port with synthetic settings:
existing fixtures create/drop `biotact_test_db`. Never point them at a shared or
production database. Run the complete API lint, format, type and coverage gates,
including standalone imports and existing quote/order tests, before review.
