# Отчёт: PR-3 · Штамп-гард в `commit_draw` + уборка докстрингов (полировка Этапа 5)

> Спека: `.claude/specs/2026-08-17-stage5-polish.md` (§PR-3; файл едет в ветке PR-1). Ветка
> `fix/loyalty-stamp-count-guard` от `main` = `64a81ad`. Коммит `4183deb`. **Не запушено, PR не
> открыт** — жду «чисто». Схема не тронута, миграций нет.

## Сделано — по пунктам спеки

**3а · сверка числа заштампованных строк.** В `commit_draw` после `executemany`-штампа, в той же
транзакции: `SELECT count(*) FROM lottery_tickets WHERE draw_id = <новый тираж>` против
`len(candidates)`. Расхождение → `rollback()` + `ConflictException` (409) с новым сообщением
`_STAMP_INCOMPLETE`: «Штамп лёг не на все билеты: к заморозке готовилось {expected}, заштамповано
{actual}. Список изменился, пока шла заморозка, — ничего не заморожено, повторите.» Формулировка
намеренно не пересекается с `_SNAPSHOT_TAMPERED` (там — «не сходится с опубликованным хешем»).
Почему `COUNT`, а не `rowcount` — в комментарии у проверки (asyncpg-диалект оставляет `rowcount = -1`
для `executemany`, `supports_sane_multi_rowcount = False`; проверено по исходнику SQLAlchemy 2.0.45).
Докстринг `commit_draw` дополнен одной фразой: этот отказ — единственный, который физически не может
прийти до вставки строки тиража (сгоревший номер за редчайшую гонку — принято, см. спеку).

**3б · докстринг `get_my_tickets`.** Абзац «⚠️ Known gap until step 5 rewrites the card…» удалён —
шаг 5 сделан (#112), строка «Ждут следующего розыгрыша» рендерится.

**3в · два докстринга «не бэкфилили» (включено Ревьюером на Гейте-1).**
- `owed_survey_ticket_sql`: теперь говорит правду — бэкфилл проведён 31.07 (`initial-2026-08`), замер
  04.08 подтвердил ноль; **не ноль = дефект выдачи**; прежняя запись опровергнута замером; список
  `list_ticket_holders(owed)` превращает тревогу в диагноз *кого* пропустили.
- `list_ticket_holders`: рассуждение про `DESC NULLS LAST` **сохранено** (уточнение Ревьюера);
  поправлена только посылка: «никогда не активировавший — тоже должник (остатка не ожидается), но
  сигнал слабее — сбой лежит на пути анкеты, поэтому сортируется после».

Дифф = `lottery.py` + `test_lottery_freeze.py` (`+85/−11`).

## Не сделано / отложено

Ничего.

## Замечено, не трогаю

- `ruff format --check tests/test_lottery_freeze.py` — «would reformat», но **все** хунки в
  предсуществующих строках (≤ 350: `_member_with_tickets`, `_draw_count`, …), мои строки (418+)
  чистые. CI формат-чек гоняет только `src/` — чужие строки не трогаю (хирургический дифф).
- `mypy`: `pyproject.toml: note: unused section(s): module = ['passlib.*']` — предсуществующая
  заметка конфигурации, не ошибка.

## Как тестировал — команды + вывод

### 🔴 Red — новый тест на текущем `lottery.py`

```
$ .venv/bin/python -m pytest tests/test_lottery_freeze.py -q -k fewer_rows --no-header
>           with pytest.raises(ConflictException) as excinfo:
E           Failed: DID NOT RAISE <class 'src.common.exceptions.ConflictException'>
FAILED tests/test_lottery_freeze.py::test_a_stamp_that_lands_on_fewer_rows_than_counted_freezes_nothing
1 failed, 17 deselected in 0.56s
```

**Доказательство, что проба исполнилась** (одноразовый `tests/_scratch_probe_understamp.py` на
текущем коде — тот же сценарий, но смотрим, что записалось; файл удалён до коммита):
```
PROBE: outcome.total_tickets=2 draw.total_tickets=2 stamped_rows=1 status=committed
1 passed in 0.46s
```
То есть на текущем коде тираж **закоммичен** с `total_tickets=2` и хешем по двум билетам, а в базе
заштампован один — ровно дефект из ТЗ, воспроизведён числами.

### 🟢 Green — после правки

```
$ .venv/bin/python -m pytest tests/test_lottery_freeze.py -q --no-header
..................                                                       [100%]
18 passed in 7.67s
```

### 🧬 Мутация — защита несущая

`if stamped != len(candidates):` → `if False and …` → прогон → восстановление:
```
377:    if False and stamped != len(candidates):
E           Failed: DID NOT RAISE <class 'src.common.exceptions.ConflictException'>
1 failed, 17 deselected in 0.60s
--- восстановлено (grep -c "if False" → 0) ---
1 passed, 17 deselected in 0.41s
```

### Гейты CI API — все четыре шага `ci.yml` (job API), локально, точные команды

```
$ ruff check src/
All checks passed!                        exit 0

$ ruff format --check src/
81 files already formatted                exit 0

$ mypy src/ --strict --ignore-missing-imports
Success: no issues found in 81 source files

$ pytest tests/ -v --no-header
======================= 539 passed in 191.87s (0:03:11) ========================
```
(плюс `ruff check tests/test_lottery_freeze.py` — All checks passed; хук post-edit поймал два
замечания в процессе — недостающий импорт `delete` и PT018 — исправлены до прогона.)

### Само-ревью

`/python-review` — 0 проблем; `/review` (inline, 98 строк) — 0 blocking/important, 2 praise.
Дифф прочитан от и до; секретов/дебага нет; проба-файл удалён (`git status` чист, кроме двух файлов PR).

## Отклонения от плана и почему

- Докстринг `commit_draw` получил одну дополнительную фразу (не было в спеке): без неё абзац «оба
  отказа — до вставки строки» стал бы неполным после появления третьего отказа. Правка функции,
  которую я и так меняю.

## Вопросы Ревьюеру

Нет. Твой чек-лист: red-прогон + проба числами (выше), мутация, дифф = 2 файла, схема не тронута,
все команды CI целиком с выводом.
