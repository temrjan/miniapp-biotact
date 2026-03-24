# Biotact Bot

Telegram-бот для магазина Biotact. Регистрация пользователей, запуск Mini App, AI-ассистент, приём заказов.

## Стек

| Технология | Версия | Назначение |
|------------|--------|------------|
| aiogram | 3.23 | Telegram Bot API |
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
