# Claude Agent Project Memory

## MCP Servers Usage Rules

### 1. Sequential Thinking (`sequential-thinking`)
**ОБЯЗАТЕЛЬНО использовать при:**
- Написании нового кода (более 50 строк)
- Рефакторинге существующего кода
- Архитектурных решениях
- Отладке сложных багов
- Планировании многошаговых задач
- Анализе требований

**Как использовать:**
```
1. Вызвать sequential-thinking MCP
2. Разбить задачу на шаги
3. Продумать edge cases
4. Только потом писать код
```

### 2. Memory (`memory`)
**Использовать для:**
- Сохранения важных решений проекта
- Запоминания архитектурных паттернов
- Фиксации найденных багов и их решений
- Хранения контекста между сессиями

**Сохранять:**
- Ключевые архитектурные решения
- Паттерны, которые работают в этом проекте
- Известные проблемы и их решения

### 3. GitHub (`github`)
**Использовать для:**
- Создания issues
- Работы с pull requests
- Просмотра кода в репозиториях
- Поиска по коду на GitHub

### 4. Playwright (`playwright`)
**Использовать для:**
- Тестирования веб-интерфейсов
- Автоматизации браузера
- Скриншотов для документации
- E2E тестирования

### 5. Context7 (`context7`)
**Использовать для:**
- Получения актуальной документации библиотек
- Поиска примеров кода
- Изучения API внешних сервисов

---

## Code Standards

### Python Style Guide
**Файл:** `docs/PYTHON_STYLE_GUIDE_V2.md`

**Ключевые правила:**
- Python 3.11+, 88 символов макс (Black/Ruff)
- Type hints ОБЯЗАТЕЛЬНЫ: `str | None` (не Optional)
- Google docstrings: Args, Returns, Raises
- `dataclass(slots=True)` для данных
- `Pydantic BaseModel` для API/валидации
- `match-case` для паттерн-матчинга
- `asyncio.gather` для параллелизма
- Параметризованные SQL (`:param`)
- `logger` с context, НЕ `print()`

**Запрещено:**
- `import *`
- Хардкод секретов
- Mutable defaults: `def f(x=[])`
- SQL f-strings
- Блокирующий код в async
- `except: pass`

### Telegram Bot Style Guide
**Файл:** `docs/TELEGRAM_BOT_GUIDE.md`

**Ключевые правила:**
- aiogram 3.x
- Routers для модульности
- FSM для многошаговых диалогов
- Magic filters (F.text, F.photo)
- Middleware для cross-cutting concerns
- Pydantic для конфигов
- Graceful shutdown

---

## Project Structure

```
/home/temrjan/claude-agent/
├── docs/                 # Документация и стандарты
├── src/                  # Исходный код
├── tests/                # Тесты (pytest)
├── config/               # Конфигурация
├── data/                 # Данные (SQLite, JSON)
├── logs/                 # Логи
├── screenshots/          # Скриншоты
├── projects/             # Рабочие проекты
├── backups/              # Бэкапы
└── tmp/                  # Временные файлы
```

---

## Development Workflow

### Before Writing Code
1. Use `sequential-thinking` MCP to plan
2. Check `docs/` for relevant style guides
3. Design tests first (TDD)

### While Writing Code
1. Follow PYTHON_STYLE_GUIDE_V2.md
2. Type hints for ALL functions
3. Google docstrings for public API
4. Handle errors explicitly

### After Writing Code
1. Run `ruff format .`
2. Run `ruff check .`
3. Run `mypy .`
4. Run `pytest`
5. Verify coverage >= 80%

---

## Tools Configuration

### Ruff (Linter + Formatter)
```toml
line-length = 88
target-version = "py311"

[lint]
select = ["E", "W", "F", "I", "B", "C4", "UP", "ARG", "SIM", "TCH", "PTH", "RUF"]
```

### pytest
```
asyncio_mode = "auto"
testpaths = ["tests"]
```

---

## Security Rules

- Secrets ONLY from environment variables
- SQL ONLY parameterized queries
- User input ALWAYS sanitized
- Audit log for all actions
- Rate limiting enabled
- Allowlist for shell commands

---

## Available Style Guides in docs/standards/

| Guide | Use For |
|-------|---------|
| PYTHON_STYLE_GUIDE_V2.md | All Python code |
| TELEGRAM_BOT_GUIDE.md | Telegram bot (aiogram) |
| FASTAPI_GUIDE.md | REST API |
| DEVOPS_GUIDE.md | DevOps practices |
| POSTGRESQL_GUIDE.md | Database |
| TYPESCRIPT_STYLE_GUIDE.md | TypeScript |
| REACT_STYLE_GUIDE.md | React |
| RAG_SYSTEM_GUIDE.md | RAG systems |

---

*Last updated: 2026-01-22*
