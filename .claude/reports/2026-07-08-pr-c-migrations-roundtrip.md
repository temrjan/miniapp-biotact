# Отчёт Инженера — PR-C: round-trip миграций (аудит MAJOR-2)

**Дата:** 2026-07-08 · **Кресло:** Claude=Инженер · **Спека:** `.claude/specs/2026-07-08-pr-c-migrations-roundtrip.md` (Гейт-1 ✅ APPROVED-for-start, Ревьюер, CoVe).
**Состояние:** код готов, механические гейты зелёные, мутация доказана → ожидает Гейт-2 (Ревьюер свежим контекстом, `/python-review` обязателен по heads-up спеки).

## Сделано — ровно по одобренному скоупу (2 файла)

1. **`packages/api/alembic/versions/ce14a3d28633_initial_migration.py`** — в конец `downgrade()`, последней строкой (после `op.drop_table('categories')`, т.е. после `drop_table('orders')`):
   ```python
   sa.Enum(name='orderstatus').drop(op.get_bind())
   ```
   + 3 строки комментария о констрейнте (enum создан неявно колонкой `orders.status`, не авто-дропается; дропаем последним, чтобы не осталось зависимостей). `upgrade()` НЕ тронут — провенанс прод-схемы цел. Идиома идентична loyalty-миграциям (`consent_purpose`/`gender`/`loyalty_status`). Без `IF EXISTS` — осознанно (на down-пути тип обязан существовать; честный фейл > маскировки).

2. **`.github/workflows/ci.yml`** — в джоб `migration-parity` после `alembic check` добавлен шаг:
   ```yaml
   - name: Migration round-trip (downgrade base → re-upgrade)
     run: |
       alembic downgrade base
       alembic upgrade head
       alembic check
   ```
   Ловит весь класс «объект не дропнут в downgrade» для ВСЕХ будущих миграций. `bash -e` (дефолт GitHub) — падение любой команды валит шаг. Те же env/postgres-service/DATABASE_URL, что и весь джоб.

0 строк прод-кода, 0 новых миграций.

## Доказательства (parity postgres:17, чистая БД, `biotact:biotact_ci`)

- **RED (до фикса)** — воспроизведён на Гейте-1: `up head → down base → up head` = `DuplicateObjectError: type "orderstatus" already exists`.
- **GREEN (с фиксом)** — полный цикл `up → down → up → down → up` + `alembic check`:
  все шаги зелёные; `SELECT typname FROM pg_type WHERE typtype='e'` после `down base` = **пусто** (orderstatus дропнут вместе с loyalty-enum'ами); `alembic check` = "No new upgrade operations detected"; `current` = head.
- **Мутация (честность round-trip-шага)** — backup → удалил строку enum-drop → `down base` (утёк `orderstatus`, подтверждено `SELECT`) → `up head` = **RED, exit 1** `DuplicateObjectError` → restore, файл идентичен backup. ⇒ CI-шаг ловит именно этот класс: без фикса красный, с фиксом зелёный.

## Локальные CI-гейты (api-джоб, все команды точь-в-точь как в ci.yml)

- `ruff check src/` → **All checks passed**
- `ruff format --check src/` → **75 files already formatted**
- `mypy src/ --strict --ignore-missing-imports` → **Success, no issues (75 files)**
- `pytest tests/` → **155 passed** (3 warnings — пред-существующие Pydantic class-based-config deprecation в `jwt_router`/`orders`, не связаны с дифом)
- `migration-parity` (up head + check) → зелёный; новый round-trip-шаг → зелёный (эквивалент прогнан выше)
- YAML `ci.yml` — валиден (`yaml.safe_load` OK)

Джобы `bot` / `frontend` / `admin` дифом не затронуты (ни одной строки в `packages/bot`/`packages/frontend`/`admin-panel-v2`; новый шаг живёт в отдельном джобе `migration-parity` и на них не влияет).

## ⚠️ Отклонение от строгого скоупа (раскрываю Ревьюеру)

Локальный codex-хук (`post-edit-python-check.js`) при правке миграции показал **два ПРЕД-СУЩЕСТВУЮЩИХ** ruff-замечания в АВТОГЕН-шапке файла: `W291` (trailing whitespace в докстринге `Revises: `) и `I001` (несортированный импорт-блок). Оба **не из моей правки**, созданы самим Alembic, файл **применён на проде**, и **CI их никогда не проверял** (`ruff check` гоняется только по `src/`, а `alembic/versions/` вне охвата).

**Решение: НЕ чинил их** — это раздуло бы денежно-смежную миграцию косметикой в применённом файле сверх одобренного Ревьюером узкого скоупа. Это не подавление сигнала (нет `noqa`, нет ослабления конфига) — это решение о границах PR. Автофикс ruff я применил и **откатил** (он ещё и переупорядочивал импорты применённой миграции). Итоговый диф файла = **строго 4 строки enum-drop**, ничего больше. Если Капитан захочет причесать lint автоген-миграций — отдельная housekeeping-задача на все `versions/` (можно вместе с заведением `alembic/` под `ruff check` в CI), не примазывать к PR-C.

## Security-relevant surface (для Ревьюера)

- Прод-путь деплоя (`deploy.yml` = только `alembic upgrade head`) НЕ тронут; downgrade прод никогда не исполняет. Деплой этого PR = no-op по схеме (байты `upgrade()` не менялись, новой ревизии нет).
- Без `IF EXISTS` в drop — намеренно; на down-пути тип обязан существовать.
- Новый CI-шаг только читает/пересоздаёт схему на эфемерной parity-БД; секретов/сети не добавляет.

## Не сделано / вне скоупа
- money-1 (`price<=0`) и его DB CHECK — следующий пункт очереди, отдельно.
- Пред-существующий lint автоген-миграций (см. отклонение выше).

## Как воспроизвести (для Ревьюера)
```bash
podman run -d --name pg -e POSTGRES_USER=biotact -e POSTGRES_PASSWORD=biotact_ci \
  -e POSTGRES_DB=biotact_parity -p 5432:5432 docker.io/library/postgres:17
cd packages/api
export DATABASE_URL='postgresql+asyncpg://biotact:biotact_ci@localhost:5432/biotact_parity'
.venv/bin/alembic upgrade head && .venv/bin/alembic downgrade base && .venv/bin/alembic upgrade head && .venv/bin/alembic check
# enum leak check after a downgrade base:
```
