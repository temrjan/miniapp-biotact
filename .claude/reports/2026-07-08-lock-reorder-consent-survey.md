# Отчёт Инженера — реордер consent/survey-локов (аудит M1 follow-up)

**Дата:** 2026-07-08 · **Кресло:** Claude=Инженер · **Спека:** `.claude/specs/2026-07-08-lock-reorder-consent-survey.md` (Гейт-1 ✅ APPROVED-for-start; МИНОР-1 + НИТ внесены).
**Состояние:** код готов, гейты зелёные, red→green доказан мутацией обеих функций → ожидает Гейт-2 (Ревьюер свежим контекстом; `/python-review` обязателен; concurrency money-adjacent → `/security-review` уместен).

## Сделано

### Прод (`src/loyalty/service.py`) — behavior-preserving реордер
- `record_consent`: обязательный `UPDATE customer_profile SET registration_source` перенесён из позиции ДО guard'ов в позицию ПОСЛЕ guard'ов (перед `db.flush()`). `SELECT … FOR UPDATE` остался перед guard'ами.
- `submit_survey`: `UPDATE customer_profile SET **updates` перенесён ПОСЛЕ `survey_done`-guard. `SELECT … FOR UPDATE` (+ membership-check `locked is None → 404`) остался на месте, первым.
- Комментарии обеих функций переписаны: `FOR UPDATE` теперь SOLE (первичный) сериализатор, а не «redundant defense-in-depth».
- **НЕ тронуты:** `activate_code`, роутер, схемы, модели, миграции. Порядок в одной транзакции (`db.begin()`) → результат атомарен, итоговое состояние идентично (доказано: вся сюита зелёная без правок логики).

### Тесты — честные детерминированные race-тесты (МИНОР-1 + находка)
🔑 **Находка при реализации:** существующие race-тесты (`test_race_two_consent_on_existing_profile_dedup`, `test_race_two_survey_calls_write_survey_done_once`) были **ложно-зелёными по ДВУМ причинам** — ровно класс, что правит M1:
1. голый `asyncio.gather` без барьера не пересекает транзакции на localhost PG (прямо задокументировано в PR-A `_activate_two_forced_concurrent`);
2. seed identity не совпадала с race-вызовом (`"Seed"`/`"Member"` vs `"RaceExisting"`/`"Race"`) → побочный `UPDATE users` из `get_or_create_user` брал users-row-lock и **маскировал** `customer_profile`-lock.
Проверено экспериментально: снятие `FOR UPDATE` в этих gather-тестах давало green (баг) или flaky, а не детерминированный red.

**Сделано:**
- Старые gather-тесты честно **переописаны** (докстринги) как «happy-path dedup-contract, НЕ mutation-honest lock test» + ссылка на новый детерминированный (МИНОР-1: не оставлять лживый docstring про конкурентность в тест-слое).
- Добавлены **детерминированные барьерные тесты** (образец PR-A) `test_race_consent_barriered_committed_profile_dedup` + `test_race_survey_barriered_committed_profile_dedup`:
  - matched-identity seed (`get_or_create_user` не пишет `UPDATE users`) + committed profile (`ON CONFLICT` no-op) — оба гарда из спеки §4;
  - `asyncio.Barrier(2)` вокруг `get_or_create_user` (consent) / `get_user_by_telegram_id` (survey) — впускает обоих в lock-регион вместе.
- 🔑 **Новый тестовый приём — `_sleep_after_read_from`** (обёртка `session.execute`, вставляет `asyncio.sleep` сразу ПОСЛЕ SELECT против guard-таблицы). Зачем: `record_consent`/`submit_survey` — в отличие от `activate_code` → `get_unit_price` — **не вызывают mid-critical-section service-функцию**, которую PR-A оборачивал sleep'ом. Без расширения окна ПОСЛЕ guard-read гонка flaky (barrier один недостаточен — проверено: 1/6 passed). Обёртка execute расширяет окно ровно у guard-read, у обоих racer'ов → детерминизм. Это НЕ маскировка сигнала (§3): sleep не меняет исход, а делает существующую гонку наблюдаемой; с локом второй racer до этого read не доходит (блокируется на `FOR UPDATE`), без лока — оба доходят.

## red → green (правило №0) — мутация каждой функции

Мутация `.with_for_update()` (с уже применённым реордером), parity postgres:17:
| Функция | GREEN (с локом) | RED (мутация, лок снят) |
|---|---|---|
| consent | 3/3 passed | **6/6 failed** (2 consent + 2 phone_shared) |
| survey  | 3/3 passed | **6/6 failed** (2 survey_done) |
Детерминизм подтверждён многократным прогоном. Файл восстановлен идентично после каждой мутации.
Для контраста — **до** реордера та же мутация оставляла старые тесты зелёными (баг M1 воспроизведён в начале).

## Локальные CI-гейты (api-джоб)
- `ruff check src/ tests/` → **All checks passed**
- `ruff format --check src/ + оба тест-файла` → **77 files already formatted**
- `mypy src/ --strict` → **Success (75 files)**
- `pytest tests/` → **160 passed** (было 158; +2 барьерных теста; 3 пред-существующих Pydantic-warning не из дифа)

Джобы `bot`/`frontend`/`admin`/`migration-parity` дифом не затронуты. Миграций нет → деплой = no-op по схеме.

## Гейт-2 delta — НИТ Ревьюера закрыт (после APPROVED)
Ревьюер APPROVED / чисто (0/0, 1 НИТ на усмотрение). НИТ **внедрён** перед мержем: `_sleep_after_read_from`
раньше выбирал точку sleep подстрокой по отрендеренному SQL (`f" {marker} " in str(stmt)`) — хрупко к
будущей правке guard-SELECT. Заменено на **интроспекцию стейтмента**: `isinstance(stmt, Select) and
target_table in stmt.get_final_froms()`, цель передаётся как `Consent.__table__` / `CustomerEvent.__table__`.
Устойчиво к переписыванию SQL. Пере-проверено: GREEN 2/2 ×3, мутация RED 2/2 ×4 (детерминизм сохранён),
full pytest 160 passed, ruff/format/mypy чисто. (Иронию — оставить хрупкий детект в тесте, чинящем класс
хрупкости — закрыл.)

## Security-relevant surface (для Ревьюера)
- Concurrency money-adjacent: реордер не ослабляет дедуп — наоборот, делает `FOR UPDATE` единственным ЯВНЫМ сериализатором, тестируемым напрямую (раньше защита держалась на побочном `UPDATE`, снятие которого будущим рефактором сломало бы дедуп молча).
- Behavior-preserving: одна транзакция, атомарный коммит; membership-404 в survey по-прежнему до любой записи.
- `_sleep_after_read_from` — только тестовый инструмент (обёртка сессии в тесте), прод не трогает.

## Как воспроизвести (для Ревьюера)
```bash
podman run -d --name pg -e POSTGRES_USER=biotact -e POSTGRES_PASSWORD=biotact_ci \
  -e POSTGRES_DB=biotact_parity -p 5432:5432 docker.io/library/postgres:17
# psql -c "CREATE DATABASE biotact_test_db;"
cd packages/api; export DATABASE_URL=postgresql+asyncpg://biotact:biotact_ci@localhost:5432/biotact_parity
.venv/bin/pytest tests/test_loyalty_consent.py::test_race_consent_barriered_committed_profile_dedup \
                 tests/test_loyalty_survey.py::test_race_survey_barriered_committed_profile_dedup
# mutation: delete `.with_for_update()` in record_consent/submit_survey → both go red (deterministic).
```
