# Biotact Club — Go-Live + initData `signature` incident (2026-07-06)

## Go-live
Программа лояльности включена на проде: `LOYALTY_ENABLED=true` в `packages/api/.env` **и**
`packages/bot/.env`, `pm2 restart api bot` (оба процесса разом — MUST-FIX #1 аудита). Пред-флип
чеклист был зелёный (оба процесса тёмные, PM2 online, прод-код == аудированный `service.py`, БД
чистая 0/0/0). Smoke после флипа: loyalty-эндпоинты 404→**401** (живые), `/health` 200.

Сквозной тест (2 аккаунта): скан QR (BIFOLAK NEO) → бот запросил телефон → мини-апп → согласие →
активация. Начислено верно: **cashback 3325 (5%) + welcome 1663 (2.5%, ROUND_HALF_UP) = 4988 баллов**;
события `joined`/`phone_shared`/`qr_activated`; профиль Клуб/Новичок/баланс; повторный скан →
`already_activated` («код уже активирован»); анкета (пол/возраст/город) → `survey_done`.

## Инцидент: 401 на активации (корневой баг)
Первый живой скан падал «Что-то пошло не так» (retry). В логах `POST /loyalty/consent` → **401**.

**Диагностика (метод пробы на проде):** временный лог в `loyalty/router.py:get_init_data_user`
показал `present=True len=593`, поля `['auth_date','hash','query_id','signature','user']`, и пересчёт
hash двумя способами: исключая ТОЛЬКО `hash` → **совпал** с присланным; исключая `hash`+`signature`
(как было в коде) → не совпал. HMAC-совпадение = криптодоказательство.

**Причина:** Telegram считает initData `hash` над ВСЕМИ полями кроме `hash`, **включая** новое поле
`signature`. Валидатор (`src/auth/telegram.py`) исключал `signature` из data-check-string → неверный
HMAC → **401 для КАЖДОГО реального клиента** (весь initData-auth; лоялти — первый обязательный вход).

**Ловушка теста:** тест-хелпер `_sign_init_data` тоже считал hash без `signature` (той же неверной
гипотезой) → signature-тест был **ложно-зелёным** и маскировал баг.

**Фикс (PR #66 `96b12a7`, merged+deployed+verified):** исключать из data-check-string только `hash`.
Хелпер приведён к реальному Telegram (signature в хэше); добавлен негативный регресс-тест (вектор с
signature НЕ в хэше отвергается). red→green доказан (откат фикса → 2 теста падают). Прод после
деплоя: with-signature initData → **200**.

Хронология фикса: сначала хотфикс прода (обратимый, ручная правка + рестарт) для немедленного
разблока презентации → затем PR с регресс-тестом → мёрж на зелёном CI → деплой (`git reset --hard`)
свёл прод с git (хотфикс-дивергенция устранена).

Урок вынесен в память ассистента (`reference-telegram-initdata-signature`), в комментарий
`telegram.py`, в регресс-тест и в этот отчёт. ⚠️ Перепроверить bozoom (может быть тот же баг).

## Прочее
- **PR-A** (аудит MAJOR-1, честные race-тесты) merged #65 `b553a60`: детерминизм гонки достигнут
  барьером+sleep вокруг `get_or_create_user`/`get_unit_price` (identity-match+committed-profile из
  спеки оказалось НЕдостаточно — флейки 4-5/10; с барьером мутация :390 RED 10/10).
- **Очистка после теста:** ledger/events/profiles/consents обнулены, активированные коды сброшены в
  `generated`, `phone_verified_at` снят — чистый старт для реального запуска. 73 тест-кода остаются.
- QR для печати: `~/Dev/biotact-golive-qr/biotact-club-bad-qr-A4.png` (300 DPI, 13 БАД).
- Концепт `docs/biotact-club-concept.html` обновлён под запуск.

## NEXT (follow-ups, не срочно; каждый — своя Гейт-1 спека)
- **PR-B (404 UX):** на живой программе 404 от `/qr/activate` = «код недействителен», но фронт
  `ActivationPage.tsx:23` мапит 404→«скоро». Микро-развилка: отличить тёмное от ненайденного.
- **PR-C (MAJOR-2):** миграции round-trip (`DROP TYPE orderstatus` в down + round-trip в CI).
- Реордер избыточных consent/survey-локов; бэклог аудита (money-1 price≤0→503, security-1 is_active
  на money-пути, security-4 consent server-enforce).
- Реальный запуск: печать коробок с реальными кодами (CLI `generate_qr_batch.py` → CSV для типографии).
