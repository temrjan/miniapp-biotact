# Рассылки (Broadcasts)

Массовая рассылка сообщений всем активным пользователям Mini App бота.
Поддерживает текст, фото, видео, inline-кнопку, `**bold**` shortcut и
превью-отправку только на админа.

---

## Архитектура

```
Admin UI (admin-panel-v2)
  └─> POST /admin/broadcasts              create draft
  └─> POST /admin/broadcasts/{id}/test-send   preview → только админу
  └─> POST /admin/broadcasts/{id}/send         массовая отправка (BackgroundTask)
  └─> POST /admin/broadcasts/{id}/cancel       отмена

API (packages/api/src/broadcasts/)
  ├─ models.py       — SQLAlchemy model Broadcast
  ├─ schemas.py      — Pydantic schemas (validation)
  ├─ admin_router.py — FastAPI endpoints + JWT admin auth
  └─ service.py      — business logic + Telegram API calls

DB (PostgreSQL)
  └─ broadcasts — title, text, photo/video (url + file_id cache),
                   button, status, counters, timestamps
```

Mass send идёт через `BackgroundTasks` (FastAPI) — один фоновый воркер
с `RATE_LIMIT_DELAY = 0.05s` (20 msg/sec, под лимитом Telegram в 30/sec).

---

## Как создать рассылку с видео

### 1. Получить file_id видео

В Telegram нет способа просто скопировать ссылку на видео из канала.
Работает только `file_id` — идентификатор, привязанный к конкретному боту.

1. Перешли видео в ЛС `@BiotactBot`
2. Бот автоматически ответит: `<code>BAACAgI...</code>` (только для админа `5224375023`, handler в `packages/bot/bot/handlers/common.py`)
3. Скопируй строку

### 2. Создать черновик

Админка → **Рассылки** → **Новая рассылка**:

- **Название** — только для списка в админке, в Telegram не уходит
- **Текст** — до 4096 символов (1024 если есть медиа). Поддерживает:
  - `**жирный**` — автоматически в `<b>жирный</b>`
  - HTML: `<b>`, `<i>`, `<a href="...">`
- **URL фото** — HTTPS-ссылка на картинку ИЛИ file_id
- **URL видео** — HTTPS-ссылка на .mp4 (до 5 MB) ИЛИ file_id. Нельзя одновременно с фото.
- **Кнопка** — текст + URL (оба или ничего)

Жми **"Создать черновик"**.

### 3. Протестировать (обязательно!)

На черновике → кнопка **"Тест (мне)"** → видео/фото/текст придёт **только админу** в ЛС.
Не меняет статус, счётчики не трогает — чистое превью.

### 4. Отправить всем

Только после проверки превью — **"Отправить"** → подтвердить.
Статус переходит `draft → sending`, в админке показывается прогресс-бар.
После завершения — `completed` со счётчиками sent/blocked/failed.

---

## Где что хранится

**В нашей БД:** только текст, заголовок, URL/file_id, кнопка, счётчики.
Физические медиа-файлы **не храним** — `file_id` это указатель на серверы Telegram.

**Кэш file_id:** после первой успешной отправки поля `photo_file_id` /
`video_file_id` заполняются ответом Telegram. Следующие отправки идут
по file_id (мгновенно) вместо повторного скачивания URL.

При повторной отправке (`send` на cancelled/draft) кэш сбрасывается —
на случай если URL сменился.

---

## Ограничения Telegram API

| Что | Лимит |
|-----|-------|
| Rate limit (массовая рассылка) | 30 msg/sec — используем 20/sec с запасом |
| Caption при фото/видео | 1024 символа |
| Plain message | 4096 символов |
| `sendVideo` через URL | ≤5 MB (через file_id — без лимита) |
| file_id | бессрочно, **привязан к токену бота** (другой бот не примет) |
| Блокировки (403) | увеличивается `blocked_count`; пользователь не деактивируется (`is_active` остаётся `True`) |
| Rate limit (429) | ретрай один раз с `retry_after` из ответа Telegram |

---

## HTTP endpoints

Все требуют JWT admin-токена в `Authorization: Bearer <token>`.

| Method | Path | Описание |
|--------|------|----------|
| `POST` | `/admin/broadcasts` | создать черновик |
| `GET` | `/admin/broadcasts` | список (пагинация) |
| `GET` | `/admin/broadcasts/{id}` | детали |
| `POST` | `/admin/broadcasts/{id}/send` | массовая отправка (BackgroundTask) |
| `POST` | `/admin/broadcasts/{id}/test-send` | превью только админу |
| `POST` | `/admin/broadcasts/{id}/cancel` | отмена (draft или sending) |

---

## Файлы

```
packages/api/src/broadcasts/
  models.py          Broadcast SQLAlchemy model + BroadcastStatus enum
  schemas.py         BroadcastCreate / BroadcastResponse / BroadcastListResponse
  admin_router.py    FastAPI endpoints
  service.py         create/send/test_send/cancel + _send_to_user helper
  README.md          (вот этот файл)

packages/bot/bot/handlers/common.py
  handle_video_file_id — admin-only, reply file_id при пересылке видео боту

admin-panel-v2/src/features/broadcasts/
  pages/BroadcastsPage.tsx   form + список + кнопки
  hooks/useBroadcasts.ts     React Query hooks
  api/broadcastsApi.ts       HTTP client
  types/broadcast.types.ts   TS типы
```

---

## Миграции

- `224c920d41d0_add_broadcasts_table.py` — создание таблицы
- `e1f2a3b4c5d6_add_video_to_broadcasts.py` — добавление `video_url` + `video_file_id`

**Важно:** CD-скрипт (`.github/workflows/deploy.yml`) **не запускает** `alembic upgrade head`.
Миграции запускаются вручную на сервере после деплоя:

```bash
ssh biotact-miniapp
cd /var/www/biotact/packages/api && source venv/bin/activate
alembic upgrade head
```

---

## Troubleshooting

**"Рассылка ушла без видео (только текст)"**
→ Проверь в БД: `SELECT video_url FROM broadcasts WHERE id=<id>`. Если `NULL` — file_id не был вставлен в форму или не сохранился (см. PR #25).

**"file_id не работает"**
→ file_id привязан к боту. Тот же file_id из @RawDataBot **не подойдёт** — нужно получать через наш `@BiotactBot`.

**"Пользователь не получил рассылку"**
→ Смотри поля `blocked_count` / `failed_count` у рассылки. Блок (403) увеличивает `blocked_count`, не меняя `is_active` пользователя.

**"Видео не отправилось — ошибка 400"**
→ HTTPS-URL должен отдавать прямой `.mp4`, не HTML-страницу (YouTube/Google Drive не подойдут). Используй file_id.
