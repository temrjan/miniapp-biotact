# Отчёт: legal-docs PR-2 — согласия (бэк + фронт)

Ветка `feat/legal-docs-pr2-consents` (от main 928a84f), коммиты `d44634c` (API) + `5db0a80` (фронт).
Спека запечатанная: `.claude/specs/2026-09-10-legal-docs.md` (печать сверена на старте сессии,
sha256 совпал). Критерии приёмки PR-2: п. 3, 4, 5, 6, 9.

## Сделано — по пунктам спеки

**API (спека «Контракты/API»):**
- `ConsentRequest` += `doc_version`: `max_length=50`, паттерн-union
  `^(offer:…{1,18}\+policy:…{1,18}|1\.0)$`, невалид → 422; без поля и с явным `"1.0"` → в БД
  `"1.0"` (М2, асимметрии нет — Finding 4). Константа `DATA_PROCESSING_CONSENT_VERSION` удалена,
  бэк пишет присланное как есть.
- `POST /loyalty/consent/marketing` `{granted, doc_version?}` → `{marketing_active}`.
  Append-style: grant — вставка `granted=True` если активного нет; revoke — `granted=False`
  если грант активен; активность = последняя строка по (user, MARKETING). Под тем же
  `SELECT … FOR UPDATE` по `customer_profile`, что сериализует `record_consent` (Finding 3).
- `GET /loyalty/me` += `consents: {data_processing, marketing}` (последняя строка по purpose;
  не-member → false/false; endpoint остался read-only).
- Событие `CustomerEventType.CONSENT_RECORDED` при каждой РЕАЛЬНОЙ записи (no-op → события
  нет); payload `{purpose, granted, doc_version, source, user_agent}`, отсутствие UA → null.
  Миграций нет (колонка String(40)).

**Фронт (спека «Где это живёт» / «Флаг»):**
- `ActivationPage` + `JoinCard`: два не предзаполненных чекбокса за флагом (обязательный со
  ссылками на `/legal/offer`+`/legal/policy`, опциональный маркетинг); кнопка активна только с
  обязательным. OFF — прежний одиночный чекбокс и запрос без версии, без изменений.
- Гейт по `me.consents.dataProcessing === true` → кнопка «Активировать» сразу, без чекбоксов.
- `stores/consentStore.ts` (in-memory, без persist) — галочки переживают переход на документ
  и возврат (Finding 2); reset после успешной записи.
- consent-запрос шлёт `doc_version: buildConsentVersion()` (только ON; OFF — ключ отсутствует,
  запрос байт-в-байт прежний).
- `ProfilePage`: блок «Согласия» (только `is_member`, за флагом): маркетинг-тоггл
  (состояние = ответ ручки/инвалидация /me) + строка отзыва через поддержку с `LEGAL_CONTACTS`.
- Слой API: `api/loyalty.ts` (consent/setMarketingConsent/getMe consents), `useLoyalty(enabled)`,
  `useActivation`, `useJoin`, новый `useMarketingConsent`; типы `ConsentsState`,
  `MarketingConsentResult`. i18n ru+uz (parity-тест зелёный).

## Критерии приёмки — покрытие

- **п.3** (два чекбокса на обоих путях, маркетинг не блокирует, состояние переживает навигацию):
  `ActivationPage.test.tsx`, `JoinCard.test.tsx` (ON), remount-тест стора.
- **п.4** (член с грантом без чекбоксов): тест ON «member with an active grant…».
- **п.5** (версия в БД как прислана; 422; дефолт "1.0"): pytest — 9 новых тестов
  `test_loyalty_consent.py` (валидный/без поля/явный "1.0"/невалид/>50/>18 + UA).
- **п.6** (/me consents; grant/revoke идемпотентны и race-safe; тоггл из ответа): pytest
  `test_loyalty_marketing.py` (11 тестов, incl. barrier-race) + `test_loyalty_summary.py`
  (5 тестов); фронт — `api/loyalty.test.ts`, `JoinCard`/`ActivationPage` ON-тесты.
- **п.9** (событие с payload; UA отсутствует → null): pytest — 4 теста события.

## Не сделано / отложено

- PR-3 (баннер членам) — следующий PR по разбиению.
- Живая приёмка маршрутом пользователя — невозможна до релиза: флаг на проде OFF решением
  Капитана; ON проверен только гейтами (vitest/build в обеих модах).
- Отдельного тест-файла для блока «Согласия» в ProfilePage нет — спека его в тест-плане
  не требует; логика ручки покрыта на уровне api-хука и pytest.

## Замечено, не трогаю — вне скоупа

- `ruff format --check tests/` (вне гейта, гейт = `src/`): свежий ruff переформатировал бы
  13 СТАРЫХ тестовых файлов (стиль переносов). Не трогаю — чужой форматный дифф не мой скоуп.
- `PydanticDeprecatedSince20` в `orders/schemas.py` (class-based config) — пре-exist.
- Для локального прогона создан `packages/api/.env` (креды CI `biotact_ci`, gitignored) и
  `.venv` (uv, py3.12); контейнер `biotact-test-pg` поднят, пароль роли выровнен под CI
  (`ALTER ROLE … PASSWORD 'biotact_ci'` — он же в POSTGRES_PASSWORD контейнера).
- `LoyaltySection.test.tsx` не менялся: JoinCard там заглушен, покрытие двух чекбоксов живёт
  в `JoinCard.test.tsx` (спека: «JoinCard/LoyaltySection под два чекбокса (ON)» — закрыто
  через JoinCard).

## Как тестировал — команды + вывод

**Красная фаза (правило №0):**
- API: 14 новых тестов FAILED против старого кода (doc_version игнорировался pydantic,
  маркетинг-ручки не существовало — ImportError), 43 существующих passed.
- Мутация Finding 3: удалил `.with_for_update()` из `set_marketing_consent` →
  `test_race_two_grants_leave_exactly_one_active_row` FAILED (две активные строки) →
  замок возвращён, тест зелёный. Замок доказан единственным сериализатором.
- Фронт: 3 теста FAILED после смены сигнатур (calledWith без doc_version, getMe без consents)
  → обновлены по спеке → зелёные.

**Зелёные гейты (после последней правки, Л5):**
- API: `ruff check src/` ✓ · `ruff format --check src/` (82 files) ✓ ·
  `mypy src/ --strict --ignore-missing-imports` (81 файл, no issues) ✓ ·
  `pytest tests/` — **568 passed** ✓
- Фронт OFF: `npm run test` — **309 passed (30 файлов)** ✓ · `npm run lint` ✓ · `npx tsc -b` ✓ ·
  `npm run build` ✓
- Фронт ON: `VITE_LEGAL_DOCS=true npm run test` — **309 passed** ✓ ·
  `VITE_LEGAL_DOCS=true npm run build` ✓
- Л4 (тесты по тронутым файлам): новые тесты лежат в test_loyalty_consent/summary/marketing
  (pytest) и ActivationPage/JoinCard/api-loyalty/consentStore (vitest) — все в обоих прогонах.

## Отклонения от плана и почему

- Место правки сверялось с планом ПОСЛЕ кода (`git status`): состав — loyalty
  router/service/schemas/models + тесты; фронт api/hooks/stores/pages/i18n/types. Всё — строки
  таблицы «Где это живёт»; новые файлы (consentStore, useMarketingConsent, test_loyalty_marketing)
  предусмотрены спекой. Ничего лишнего.

## Решения, которые принял сам (спека молчала)

1. `source` маркетинг-записи и события = `"profile"` (константа `MARKETING_CONSENT_SOURCE`) —
   в теле запроса source нет; тоггл живёт в профиле.
2. Маркетинг-ручка для не-member → 404 (зеркало survey-ручки; «тоггл только членам» из гриля;
   замку нужна строка профиля).
3. Строка revoke хранит ту же doc_version/дефолт, что и grant («та же валидация/дефолт»).
4. `marketing_active` в ответе = обеспеченное состояние (== запрошенному `granted`).
5. Отмеченный маркетинг-чекбокс при вступлении → отдельный вызов `setMarketingConsent(true)`
   ПОСЛЕ consent (грант member-only, membership создаёт consent); ошибка маркетинга прерывает
   флоу — весь он идемпотентен и безопасен для retry.
6. `consentStore` общий для ActivationPage и JoinCard (один акт согласия); reset onSuccess
   обоих хуков.
7. `useLoyalty(enabled)` — чтобы OFF не стрелял лишний `/me` (OFF = поведение без изменений,
   включая сеть).
8. Дефолт `"1.0"` живёт как `DOC_VERSION_DEFAULT` в service.py (это НЕ «текущая версия» —
   семантика М2-фолбэка, комментарий в коде).
9. Ошибка маркетинг-тоггла в профиле показывается общей строкой `activation.error.generic`
   (новый i18n-ключ не заводил).

## Опись коммита — кладу 24 файла (2 коммита)

| Файл | Что | Почему в этом PR |
|---|---|---|
| api/src/loyalty/models.py | +CONSENT_RECORDED | событие п. 9 |
| api/src/loyalty/schemas.py | DocVersion, doc_version, маркетинг-модели, ConsentsStateResponse | контракты М1/М2 |
| api/src/loyalty/service.py | record_consent +версия/событие, set_marketing_consent, _is_consent_active, consents в summary | ядро PR |
| api/src/loyalty/router.py | doc_version+UA в consent, ручка marketing, consents в /me | контракты |
| api/tests/test_loyalty_consent.py | +9 тестов (версия, событие, UA) | тест-план |
| api/tests/test_loyalty_summary.py | +5 тестов consents в /me | тест-план |
| api/tests/test_loyalty_marketing.py (новый) | 11 тестов grant/revoke/race/endpoint | тест-план, Finding 3 |
| frontend/api/loyalty.ts | doc_version, setMarketingConsent, consents | слой API |
| frontend/api/loyalty.test.ts | +тесты версии/marketing/consents | тест-план (ломался, обновлён) |
| frontend/hooks/useLoyalty.ts | +enabled | OFF без лишнего /me |
| frontend/hooks/useActivation.ts | doc_version, marketing-грант, reset | флоу QR |
| frontend/hooks/useJoin.ts | то же для direct | флоу direct |
| frontend/hooks/useMarketingConsent.ts (новый) | мутация тоггла | блок «Согласия» |
| frontend/stores/consentStore.ts (новый) | in-memory стор | Finding 2 |
| frontend/stores/consentStore.test.ts (новый) | set/reset | тест-план |
| frontend/stores/index.ts | реэкспорт стора | — |
| frontend/types/index.ts | ConsentsState, MarketingConsentResult, consents в LoyaltySummary | контракт /me |
| frontend/pages/ActivationPage.tsx | два чекбокса, гейт по /me | критерии 3/4 |
| frontend/pages/ActivationPage.test.tsx | ON/OFF-ветви, гейт, маркетинг, remount | тест-план (ломался) |
| frontend/components/features/JoinCard.tsx | два чекбокса | критерий 3 |
| frontend/components/features/JoinCard.test.tsx | ON/OFF-ветви | тест-план |
| frontend/pages/ProfilePage.tsx | блок «Согласия» | скоуп спеки |
| frontend/i18n/ru.json, uz.json | ключи чекбоксов и блока | parity |

**НЕ кладу:** `.claude/` артефакты круга (untracked, docs-PR на закрытии круга по хэндоффу);
`packages/api/.env`, `.venv` (gitignored); `dist/` (билд-артефакт).

## Вопросы Ревьюеру

1. Решение 5 (ошибка маркетинг-гранта прерывает activation/join флоу) — спека молчит; альтернатива
   «тихо проглотить» хуже (выбор пользователя потерян). ОК?
2. Достаточно ли покрытия блока «Согласия» без отдельного ProfilePage-теста (решение: спека не
   требует), или добавить?
