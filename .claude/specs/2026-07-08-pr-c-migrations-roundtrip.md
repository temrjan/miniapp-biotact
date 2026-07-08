# PR-C — Round-trip миграций: DROP TYPE orderstatus + CI-гейт (аудит MAJOR-2)

**Дата:** 2026-07-08 · **Статус:** Гейт-1 ✅ APPROVED-for-start (Ревьюер, 0 БЛОКЕР / 0 МИНОР / 2 НИТ + 1 heads-up; CoVe VERIFIED все 5 утверждений). НИТ-1 (цифра миграций) вписан ниже; НИТ-2 (стиль drop enum без `IF EXISTS`) — осознанный выбор, принят; heads-up (drop типа последней строкой, после `drop_table('orders')`) — учтён на реализации. Ждёт явного «go» Капитана → coding.
**Источник:** предзапусковый аудит 2026-07-03, MAJOR-2 (`.claude/reports/2026-07-03-prelaunch-audit.md`,
план долгов `.claude/reports/2026-07-03-audit-followups-plan-and-handoff.md` §PR-C).
**Дедлайн-привязка:** ДО следующей schema-миграции (money-1 из бэклога требует миграцию → PR-C идёт первым).

## 1. Проблема

`alembic upgrade head → downgrade base → upgrade head` падает на re-upgrade:

```
sqlalchemy.exc.ProgrammingError: <asyncpg> DuplicateObjectError: type "orderstatus" already exists
[SQL: CREATE TYPE orderstatus AS ENUM ('PENDING', 'CONFIRMED', ...)]
```

Причина: initial-миграция `ce14a3d28633` создаёт PG-тип `orderstatus` неявно (через `sa.Enum(...)` внутри
`create_table('orders')`), а её `downgrade()` дропает таблицы, но **не дропает тип** — тип утекает и валит
повторный `CREATE TYPE`.

Бьёт **DR / dev-reset / любой будущий down-путь** (включая fail-rollback-сценарии с downgrade). Прод
happy-path (`alembic upgrade head` на деплое) НЕ задет.

Вторая половина проблемы: CI-джоб `migration-parity` гоняет только `upgrade head` + `alembic check` —
down-путь **никогда не проверяется**, класс бага невидим для CI.

## 2. Доказательства (воспроизведено 2026-07-08, parity postgres:17, чистая БД)

1. `upgrade head` ✅ → `downgrade base` ✅ → `upgrade head` ❌ `DuplicateObjectError: type "orderstatus" already exists`.
2. Инвентарь утечек после `downgrade base`:
   - enum `orderstatus` — **единственный** утёкший тип (баг, чиним);
   - таблицы `banners`, `settings` — остаются от reconcile-миграции `f1e2d3c4b5a6` с **намеренным no-op
     down** (by design, идемпотентный upgrade через `inspect`); re-upgrade их корректно пропускает —
     **безвредно, НЕ чиним** (менять осознанный no-op down = вне скоупа).
3. Проба «дропнуть тип руками → re-upgrade»: дошёл до head чисто, `alembic check` = "No new upgrade
   operations detected" ⇒ **`orderstatus` — единственный блокер round-trip во всей цепочке из 20 миграций.**
4. Ревизия всего класса «enum создан, в down не дропнут» по цепочке: `consent_purpose` (332db6094b1d) ✅
   дропается, `gender`+`loyalty_status` (c7cb4807f4ba) ✅ дропаются, `lead_status` (08f2ab35492b) ✅
   дропается (`DROP TYPE IF EXISTS`), `orderstatus` (ce14a3d28633) ❌ — единственный нарушитель.

## 3. Развилка и решение (предрешено)

**(A) РЕКОМЕНДУЮ — фикс down-пути + CI-гейт:**
- В `downgrade()` миграции `ce14a3d28633` добавить в конец:
  `sa.Enum(name='orderstatus').drop(op.get_bind())` — та же идиома, что в loyalty-миграциях
  (332db6094b1d, c7cb4807f4ba); без `IF EXISTS` — тип в этой точке обязан существовать, честный фейл лучше маскировки.
- В `ci.yml` джоб `migration-parity` после `alembic check` добавить шаг:
  ```yaml
  - name: Migration round-trip (downgrade base -> re-upgrade)
    run: |
      alembic downgrade base
      alembic upgrade head
      alembic check
  ```
  Финальный `alembic check` пере-подтверждает parity после round-trip. Гейт ловит весь класс для всех
  БУДУЩИХ миграций, не только этот инстанс.

**(B) ОТВЕРГНУТО** — guard на CREATE (`checkfirst=True` / `IF NOT EXISTS`) в upgrade: маскирует утечку
вместо починки — тип продолжает жить после downgrade, down-рецепт остаётся лживым.

**Само-атака на (A):** «нельзя править применённые миграции» — это правило про **upgrade()-путь**
(провенанс прод-схемы). Здесь трогается ТОЛЬКО `downgrade()`: прод его никогда не исполнял и не исполняет
(deploy = `upgrade head`), alembic файлы не чексаммит, а текущий down-рецепт заведомо сломан — хуже сделать
нельзя. **Цена ошибки:** ~0 — новый CI-шаг докажет фикс до мержа; деплой этого PR = no-op по схеме
(upgrade()-байты не тронуты, новой ревизии нет). **Факт-переключатель:** если бы прод когда-либо гонял
downgrade — решение пересмотреть (не наш случай).

## 4. Скоуп

Ровно 2 файла, **0 строк прод-кода, 0 новых миграций**:
1. `packages/api/alembic/versions/ce14a3d28633_initial_migration.py` — только тело `downgrade()` (+1 строка).
2. `.github/workflows/ci.yml` — один шаг в `migration-parity`.

Вне скоупа: no-op down у reconcile `f1e2d3c4b5a6` (намеренный, безвредный — задокументировано выше);
money-1 CHECK-констрейнт (следующий PR, после этого).

## 5. План тестов / DoD

- [x] **RED доказан до фикса** (2026-07-08, этот документ §2.1) — DuplicateObjectError воспроизведён.
- [ ] GREEN: на чистой parity-БД полный цикл `up → down → up → down → up` + `alembic check` — зелёный, дважды.
- [ ] **Мутация:** откатить строку фикса → round-trip красный (шаг CI честный, ловит именно этот класс).
- [ ] После `downgrade base` с фиксом: `SELECT typname FROM pg_type WHERE typtype='e'` → пусто.
- [ ] Полный локальный CI-гон api (ruff / ruff format --check / mypy --strict / pytest / parity) — по правилу
  «все CI-джобы локально», хотя прод-код не тронут.
- [ ] Отчёт Инженера → Гейт-2 отдельным Ревьюером свежим контекстом (`/python-review` обязателен по стандарту).
- [ ] Мерж на зелёном CI → прод-verify: деплой no-op по схеме (лог CD: alembic ничего не катит), health 200.

## 6. Роли

Инженер — Claude (это кресло, по директиве Капитана 2026-07-08 «работаем по хвостам»); Гейт-1 аппрувит
Капитан; Гейт-2 — отдельный Ревьюер свежим контекстом через Капитана.
