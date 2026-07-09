# Отчёт: Ручной ввод кода в боте (Biotact Club)

**Дата:** 2026-07-09 · **Кресло:** Claude=Инженер · **Ветка:** `feat/loyalty-manual-code-entry`
**Спека:** `.claude/specs/2026-07-09-manual-code-entry-bot.md` (Гейт-1 APPROVED WITH CONDITIONS →
условие `StateFilter(None)` внесено). **Тип:** бот-only, дополняет QR-поток вторым входом.

## Сделано — по пунктам спеки
- **`_start_activation(message, session, state, code)`** — извлечён общий «хвост» (телефон-ветка →
  launch) из `cmd_start_qr`; оба входа (deep-link и текст) теперь тонкие обёртки → телефон-логика в
  одном месте (не дублируется). `cmd_start_qr` поведенчески не изменён.
- **`_normalize_code`** — strip пробелов/дефисов → upper → Crockford-конфузаблы (`O→0`, `I/L→1`) →
  требует ровно 12 символов ∈ алфавита `codes.py:20`. Иначе `None`.
- **`is_loyalty_code`** — фильтр: dark-gate (`loyalty_enabled=false` → `False`), затем нормализация;
  на матч возвращает `{"loyalty_code": <norm>}` (инъекция в хендлер), иначе `False` → сообщение
  проваливается в обычные хендлеры.
- **`cmd_manual_code`** — `@router.message(StateFilter(None), is_loyalty_code)` → зовёт
  `_start_activation`. `StateFilter(None)` = условие Гейта-1 (иначе перехват кода во время заказа/AI-чата).
- **TEXTS:** «launch» переформулирован под оба входа («Код принят!» / «Kod qabul qilindi!») — раньше
  «Код отсканирован», теперь ручной ввод тоже уместен.
- **11 новых тестов** в `test_loyalty_handler.py` (нормализация ×5 / фильтр ×3 / хендлер ×2 / StateFilter ×1).

## Не сделано / отложено (вне скоупа v1, §10 спеки)
Инлайн-кнопка «Ввести код»; активация прямо в боте; изменение телефон-политики; правки мини-аппа/бэка/кодов.

## Замечено, не трогаю — вне скоупа
- Пред-существующие ruff-ошибки в `tests/conftest.py` + `tests/test_handlers.py` (2 шт.) — **не мои**,
  CI бота линтит только `bot/` (не `tests/`), поэтому не гейтят. Не чиню (не в скоупе).
- Локальный `.venv` = aiogram **3.29.1**, а `requirements.txt`/CI пинят **3.23.0** (дрейф venv). Мои API
  (`StateFilter`, фильтр-возврат-dict, `CommandStart(magic=)`) стабильны в обеих версиях; локально
  верифицировано на 3.29.1, финальное подтверждение на 3.23.0 — CI.

## Как тестировал — команды + вывод
Полный CI Bot job (ci.yml, `working-directory: packages/bot`, `BOT_TOKEN=0000000000:AAFake…`):
```
ruff check bot/            → All checks passed! (exit 0)
ruff format --check bot/   → 23 files already formatted (exit 0)
pytest tests/              → 28 passed (было 17; +11 новых) (exit 0)
```
(mypy в CI отключён — TODO в ci.yml, не мой.)

**red→green (правило №0) — доказано мутациями** (тесты после кода → фальсифицируемость + откат):
```
StateFilter(None) → StateFilter(waiting_phone)  ⇒ RED: test_manual_code_handler_is_state_gated_to_none
dark-gate убран из is_loyalty_code               ⇒ RED: test_is_loyalty_code_dark_gate_returns_false
конфузаблы str.maketrans({}) (пустой)            ⇒ RED: test_normalize_code_maps_crockford_confusables
```
Все откачены, финальные 28 зелёные подтверждают чистоту.

## Отклонения от плана и почему
- **Dark-gate — в фильтре `is_loyalty_code`, а не в теле хендлера** (спека §5 обновлена до кода): при
  выключенной программе код-текст проваливается в обычные хендлеры, а не глотается молча — точнее
  отражает утверждённый интент «пусть обрабатывают обычные хендлеры».
- **Извлечён `_start_activation`** (ратифицированное микро-решение §11.1) — телефон-ветка в одном месте.

## Вопросы Ревьюеру
1. `is_loyalty_code` возвращает `dict[str,str] | bool` (aiogram-инъекция kwargs). Подтверди, что этот
   контракт фильтра стабилен на пинованной 3.23.0 (я проверил на 3.29.1; CI подтвердит).
2. Over-catching: фильтр ловит только 12-символьные строки ∈ Crockford после нормализации И при
   `StateFilter(None)` (вне диалога). Согласен, что остаточный риск (случайное 12-симв. слово без U вне
   диалога → кнопка мини-аппа) приемлем для v1?
