# Biotact Bot

Telegram-бот для магазина Biotact. Регистрация пользователей, запуск Mini App, AI-ассистент, приём заказов.

## Стек

| Технология | Версия | Назначение |
|------------|--------|------------|
| aiogram | 3.31 | Telegram Bot API |
| SQLAlchemy | 2.0 | ORM (async, asyncpg) |
| Redis | 5.0 | FSM-хранилище |
| httpx | 0.25 | Async HTTP-клиент |
| Pydantic | 2.12 | Конфигурация |

## Функции

| Функция | Описание |
|---------|----------|
| Регистрация | Выбор языка (рус/узб) → запрос телефона → сохранение |
| Mini App | Кнопка «Открыть магазин» → WebApp в Telegram |
| AI-ассистент | Чат с AskBiotact API, история (10 сообщений), детекция покупки |
| Заказ через бота | Имя → телефон → описание → отправка в группу продаж |
| Авторизация админа | Deep link `/start auth_ABCDEF` → верификация кода через API |
| CRM-синхронизация | Данные клиента отправляются в CRM API при каждом `/start` |

## Структура

```
bot/
├── __main__.py      # Точка входа, startup/shutdown
├── core/
│   ├── config.py    # Переменные окружения
│   └── loader.py    # Bot + Dispatcher (Redis FSM)
├── handlers/
│   ├── common.py    # /start, /help, AI-чат, заказ (FSM)
│   └── auth.py      # Верификация кодов админки
├── keyboards/
│   └── inline.py    # Inline + Reply клавиатуры
├── middlewares/
│   ├── database.py  # Инъекция AsyncSession
│   └── logging.py   # Логирование всех обновлений
├── models/
│   └── user.py      # SQLAlchemy модель User
└── services/
    ├── user.py      # CRUD пользователей
    ├── askbiotact.py    # Интеграция с AI API
    ├── notification.py  # Отправка заказов в группу
    └── crm.py           # Синхронизация с CRM
```

## Команды

```bash
pip install -r requirements.txt   # Установка
python -m bot                     # Запуск
ruff check bot/                   # Линтер
mypy bot/                         # Проверка типов
pytest tests/ -v                  # Тесты
```

## Переменные окружения

| Переменная | Описание |
|------------|----------|
| `BOT_TOKEN` | Токен бота (@BotFather) |
| `DATABASE_URL` | PostgreSQL (asyncpg) |
| `REDIS_URL` | Redis для FSM-состояний |
| `WEBAPP_URL` | URL Mini App (по умолчанию `https://app.biotact.uz`) |
| `ASKBIOTACT_API_URL` | URL AI-ассистента |
| `ASKBIOTACT_API_KEY` | Ключ API AI-ассистента |
| `CRM_API_URL` | URL CRM API |
| `CRM_API_TOKEN` | Токен для CRM |
| `NOTIFICATION_BOT_TOKEN` | Токен бота уведомлений |
| `SALES_GROUP_CHAT_ID` | Chat ID группы продаж |

## Интеграции

```
Пользователь → Telegram Bot → Mini App (WebApp)
                    ↓
              AskBiotact AI → заказ → Notification Bot → группа продаж
                    ↓
                CRM API → синхронизация клиентов
```

Бот и Mini App используют общую БД (`biotact_db`, таблица `users`). Авторизация в Mini App через X-Init-Data того же бота.


## Website sign-in namespace

`BUYER_TELEGRAM_AUTH_ENABLED=false` keeps website entry disabled. When separately
authorized, enable it with the same main bot configured by the API and the trusted
HTTPS `BUYER_TELEGRAM_API_URL` (default
`https://app.biotact.uz/api/v1/bot/buyer/telegram`). No browser field chooses that
transport destination. `/start web_<request_id>` presents a website-login prompt;
only its explicit Confirm/Decline callback submits a decision. Return to the
originating website browser to finish. Opening Telegram never approves a login.

Telegram Retry-After is reported to the same trusted API through POST
`/retry-after` with `{telegram_id,retry_after}`. The API retains the longer of the
current prompt restriction and its receipt time plus that interval. The bot does
not immediately send a fallback after Telegram throttling or rate-limited
presentation, and does not automatically resend. In-flight prompt ownership is
retained through cancellation until delivery and its bounded throttle report finish.

The web namespace preserves existing registration/referral/QR FSM state, including
on malformed inputs and transport failures. Authentication state remains in the
API's SQL storage. The bot does not create customers, schedule CRM sync or enrol
Club members for these updates. Other start namespaces retain their existing behavior.

The normal bot test suite runs with the bot requirements. The separate
`telegram-bot-api` CI job installs the API plus explicitly pinned integration
adapters and runs `tests/test_web_login.py` with `TG1_API_INTEGRATION=1`, PostgreSQL16
and isolated Redis. This crosses the real dispatcher/API/SQL boundary and stubs
only Telegram transport; it does not poll Telegram or send a real message.
