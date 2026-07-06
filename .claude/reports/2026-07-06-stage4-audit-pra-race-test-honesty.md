# PR-A — Честные тесты гонок + правдивые докстринги (аудит MAJOR-1)

> Отчёт Инженера к Гейту-2. Спека: `.claude/specs/2026-07-03-stage4-audit-pra-race-test-honesty.md`
> (Гейт-1 APPROVED WITH NITS). Кресло: Claude=Инженер. Ветка `test/loyalty-audit-pra-race-honesty`.
> **Прод-поведение НЕ менялось — только тесты, докстринги и комментарии.**

## Что сделано
1. **Два новых детерминированных race-теста** в `test_loyalty_activation.py` на несущий замок
   `activate_code` (`SELECT customer_profile … FOR UPDATE`): welcome-гонка (профиль lifetime=0) и
   boundary-гонка (профиль lifetime=9). Оба сидят identity-match юзера + **закоммиченный** профиль
   (seed-хелпер `_seed_matched_user_with_profile`) → снимают две побочные сериализации
   (`UPDATE users` в `get_or_create_user` и `ON CONFLICT`-вставку профиля).
2. **Правка 3 лживых докстрингов:** `test_loyalty_consent.py` (race-тест), `test_loyalty_survey.py`
   (race-тест), `test_loyalty_handler.py` (модуль, бот) — приведены к доказанной реальности.
3. **Правка комментов `service.py`** (consent-лок, survey-лок, activate-лок) — честно: где реально
   происходит сериализация. `git diff service.py` = **только строки комментариев** (проверено).
4. **Мутационная таблица** (ниже) — доказательство red/green по каждому замку.

## 🔑 ДЕВИАЦИЯ ОТ СПЕКИ (внимание Ревьюера) — механизм детерминизма
Спека предполагала, что identity-match + закоммиченный профиль **сами по себе** сделают снятие
`:390` детерминированно-красным. **Эмпирически это НЕверно** — с одним лишь `asyncio.gather` тесты
оказались ФЛЕЙКИ: под мутацией welcome краснел 4/10, boundary 5/10 (то же наблюдение, что команда
уже фиксировала в PR-4.4b: «race-тест на FOR UPDATE 5/5 ложно-зелёных»). Причина: на localhost-PG
один racer успевает закоммититься раньше, чем второй прочитает `lifetime_before` → окна гонки нет.

**Решение — форсировать окно гонки двумя инъекциями в тест-хелпере** (`_activate_two_forced_concurrent`,
через `monkeypatch`, прод-код не тронут):
- `asyncio.Barrier(2)` вокруг `get_or_create_user` — оба racer'а входят в лок-регион одновременно
  (снимает зависимость от относительной скорости старта). *Здесь identity-match обязателен: иначе
  `UPDATE users` взял бы users-row-лок → первый racer держит его, ожидая второго на барьере →
  дедлок.*
- `asyncio.sleep(0.25)` вокруг `get_unit_price` (вызывается ПОСЛЕ чтения `lifetime_before`, ДО
  любой записи/commit) — держит racer'а в критической секции, пока второй читает.

**Это не подделка исхода:** обе инъекции лишь расширяют окно; ИСХОД (1 vs 2 начисления) по-прежнему
определяется ТОЛЬКО наличием `:390`. Двойное начисление — реальный прод-сценарий (два быстрых
конкурентных скана); инъекции лишь гарантируют, что тест в это окно попадает. Проверено
детерминированно **10/10 в обе стороны** (см. ниже).

## Мутационная таблица (харнесс backup→снять `.with_for_update()`→прогон→restore, md5-verified)
| Замок (service.py) | Что снимаем | Тест | Итог | Вывод |
|---|---|---|---|---|
| `activate_code` `:390` | `.with_for_update()` | 2 новых race-теста | **2 failed (RED)** | несущий, денежно-критичный |
| `record_consent` `:174` | `.with_for_update()` | `test_race_two_consent*` | 2 passed (GREEN) | избыточный (реальный сериализатор — обязательный `UPDATE customer_profile`) |
| `submit_survey` `:284` | `.with_for_update()` | `test_race_two_survey*` | 1 passed (GREEN) | избыточный (то же) |

restore после каждой мутации — **service.py байт-идентичен** (md5 `ab0c564…`).

### Детерминизм нового теста (10 прогонов каждый)
```
С замком (unmutated):   welcome 10/10 PASS   boundary 10/10 PASS
Без замка (mutation):   welcome 10/10 FAIL   boundary 10/10 FAIL
```

## Ground truth (подтверждено кодом + мутацией)
- **`activate :390` — НЕСУЩИЙ.** Единственный per-user сериализатор гонки РАЗНЫМИ кодами: атомарный
  claim (`:401`) дедупит один код, не два; решение о welcome/flip принимается в Python по `lifetime_before`,
  прочитанному под этим локом. Снятие → двойной welcome / двойной +5000.
- **`consent :174` / `survey :284` — ИЗБЫТОЧНЫЕ.** Реальный сериализатор — обязательный
  `UPDATE customer_profile` следом (`:179` COALESCE registration_source / `:290` `**updates`): UPDATE
  держит row-lock до commit, guard'ы дедупа идут после него. Замки оставлены как намеренный бэкстоп
  (см. follow-up), НЕ удалены.

## Доказательства CI (полные команды локально)
**API** (`packages/api`, `DATABASE_URL=…biotact_ci@localhost:5432/biotact_test_db`, podman postgres:17):
```
ruff check src/                         → All checks passed!
ruff format --check src/                → 75 files already formatted
mypy src/ --strict --ignore-missing-imports → Success: no issues found in 75 source files
pytest tests/                           → 154 passed  (было 152 → +2 новых)
```
**Bot** (`packages/bot`, `BOT_TOKEN=<CI-fake>`):
```
ruff check bot/                         → All checks passed!
ruff format --check bot/                → 23 files already formatted
pytest tests/                           → 17 passed
```
(bot mypy в CI закомментирован — не гоняем. CI линтит `src/`/`bot/`, НЕ `tests/`.)

## Скоуп-заметка (форс гейта, вне PR-A по сути)
Post-edit-хук гоняет ruff по всему тест-файлу; всплыли **2 пред-существующих нита** (не мои,
латентные — CI не линтит `tests/`): `×`→`x` в докстринге `test_welcome_rounding` и составной
`assert a and b`→два assert в `test_conflict_http_body_is_generic`. Починены минимально, чтобы
разблокировать гейт. Поведение тестов не изменилось.

## Затронутые файлы
- `packages/api/tests/test_loyalty_activation.py` — +seed-хелпер, +2 race-теста, +barrier/delay-хелпер, +2 нит-фикса.
- `packages/api/src/loyalty/service.py` — **только комментарии** (consent/survey/activate локи).
- `packages/api/tests/test_loyalty_consent.py` / `test_loyalty_survey.py` — докстринг race-теста.
- `packages/bot/tests/test_loyalty_handler.py` — докстринг модуля (bot CI гоняет pytest — `ci.yml:239`).

## Definition of Done
- [x] Новый детерминированный race-тест зелёный на parity-PG + мутационно доказан (`:390` RED 10/10).
- [x] Ни один докстринг/комментарий не утверждает ложь о механизме сериализации.
- [x] `git diff service.py` — только комментарии (проверено программно).
- [x] Все CI-гейты локально зелёные (api ruff/format/mypy/pytest 154; bot ruff/format/pytest 17). Число dark-404 тестов не изменилось.
- [ ] Гейт-2 независимым Ревьюером (**/python-review ОБЯЗАТЕЛЕН**) → merge на зелёном.

## Follow-up Капитану (НЕ в этом PR — своя Гейт-1 спека)
Избыточные локи `consent :174` / `survey :284`: реко — **реордер** (перенести обязательный `UPDATE`
после guard'ов → `FOR UPDATE` станет единственным сериализатором → покрыть детерминир. тестом), а не
удаление. Защита-как-побочный-эффект обязательного UPDATE — тот же класс хрупкости, что снятая здесь
маска. Behavior-adjacent money-код возле go-live → отдельная развилка.
