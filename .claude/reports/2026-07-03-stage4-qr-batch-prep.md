# Отчёт: QR-батч prep (ветка 1) — dry-run + генератор-фикс + go-live спека

Кресло: Claude=Инженер. Ревьюер=Капитан (Гейт-2). Ветка `fix/qr-batch-cli-registry` (`da968df`),
НЕ запушено — жду «чисто».

## Сделано — по пунктам задачи Капитана

### 1. Сухой прогон генератора на parity, E2E — ✅ + НАШЁЛ БАГ
Прогнал полную цепочку на **migrations-built parity-БД** (`alembic upgrade head`, `alembic check`
= нет дрейфа): генерация → формат → deeplink → активация → кэшбэк → replay.
- **🐛 БАГ НАЙДЕН (генератор ни разу не работал — 0 батчей в истории не случайность):** CLI
  `scripts/generate_qr_batch.py` падал standalone —
  `InvalidRequestError: mapper Consent ... 'User' failed to locate a name`. Причина: скрипт
  импортировал только `loyalty.codes`, но не реестр моделей → relationship `Consent.User` не
  резолвился. В приложении/тестах маскировалось (conftest грузит `src.main` = все модели).
- **Фикс:** `import src.models_registry` в CLI (зеркалит `alembic/env.py`). 1 строка.
- **red→green:** снял импорт → CLI-subprocess-тест краснеет тем же mapper-error; вернул → зелёный.
- **Формат подтверждён на боевом выводе:** 12-символьный Crockford base32 (без I/L/O/U),
  `quote(code)` = no-op (transport-safe), `qr_<code>`→strip→bare code == сохранённый.
- **Петля на parity:** slug→product → батч 5 кодов (уникальны) → активация 1 кода = **9000 баллов**
  (5% кэшбэк 6000 + welcome 2.5% 3000) → replay `already_activated` (без нового начисления) →
  used=1, generated=4. `[OK] parity dry-run passed`.

### 2. Тест-батч — parity ✅ / **прод ⏸ (пост-мёрж)**
- **Parity:** механизм доказан (dry-run выше + фактический прогон CLI после фикса: `batch 2: 3 codes`).
- **Прод ⏸ ОТЛОЖЕН до мёржа:** прод-CLI сломан тем же багом; **сознательно НЕ хочу хендпатчить прод**
  (самый правильный способ = фикс через git → CD → задеплоенный CLI). После «чисто»+мёрж+Deploy
  генерю тёмный батч одной командой на проде:
  `venv/bin/python scripts/generate_qr_batch.py --slug immunocomplex --quantity 10 --label test-batch-2026-07-03 --out /tmp/test-batch-2026-07-03.csv`
  (idempotent: один прогон = один батч+10 кодов status`generated`; метка уникальна → откат = удалить
  батч; активацию на проде НЕ гоняю — коды лежат тёмными, флаг off). Пред-прогон прод-счётчики: `qr_codes=0`.

### 3. Go-live спека дошита (Гейт-1 находки, НЕ флипаем) — ✅
`.claude/specs/2026-07-03-stage4-pr4.5-go-live.md`:
- **(#1) Точка невозврата:** флип обратим (env=false + restart) РОВНО до первой реальной активации;
  после первого начисления (баллы=1 сум) откат не отменяет обязательство.
- **(#2)** записано: первый flag-on = сразу прод (нет staging-репетиции flag-on); механика доказана
  тестами+parity, но живой flag-on первый раз на проде → сразу smoke под наблюдением.
- **(#5)** `webapp_url` бота — в пре-флип чеклист (deep-link активации открывает `{webapp_url}?code=`).

### 4. Доказательства / гейты
- Полный api-гон локально (как CI): `ruff check src/` ✓ · `ruff format --check` ✓ ·
  `mypy src/ --strict` ✓ (76 файлов, включая фикс-скрипт) · `pytest tests/` **152 passed** (+4).
- `test_qr_batch_e2e.py` ruff+format+mypy чисто.
- Прод `src/` НЕ менялся (фикс в `scripts/`, не в рантайме приложения).

## Не сделано / отложено
- Прод-батч (пост-мёрж, см. п.2).
- Сам флип `loyalty_enabled` (PR-4.5, отдельный явный «go» Капитана).
- Физическая печать коробок (логистика).

## Замечено, не трогаю — вне скоупа
- `scripts/` не покрыт CI (`ruff check src/`/`mypy src/` — только src/). Фикс-скрипт я прогнал
  вручную; расширение CI на scripts/ — housekeeping Капитана, не в скоупе.
- Docs-хвост (specs/reports 4.2–4.4b + эта спека) untracked → отдельный docs-PR.

## Как тестировал — команды + вывод
- parity: `DROP/CREATE biotact_parity` → `alembic upgrade head` (4 ревизии) → `alembic check` =
  «No new upgrade operations detected».
- dry-run скрипт (scratchpad): `[act] status=activated points=9000 ... [OK] parity dry-run passed`.
- CLI после фикса: `batch 2: 3 codes for parity-0f1271 -> cli_verify.csv` (CSV `code,product_id`).
- red→green ×2: transport-тест (генератор с «/» → red), CLI-регресс (без registry-импорта → mapper-error red).
- full api suite: 152 passed.

## Отклонения от плана и почему
- Прод-батч перенесён на пост-мёрж (баг сделал прод-CLI неработоспособным; хендпатч прода отвергнут
  в пользу git→CD). Это единственное отклонение от порядка Капитана — по находке, флагнуто.

## Вопросы Ревьюеру (Капитану)
1. Ок ли порядок: «чисто» → мёрж (фикс+тесты) → CD → тогда генерю прод-тёмный батч → верификация?
   (Альтернатива — сгенерить прод-батч сейчас через уже-корректный `generate_batch` inline, минуя
   сломанный CLI-враппер; НЕ рекомендую — грязнее, обходит задеплоенный путь.)
2. slug для тест-батча: взял `immunocomplex` (76000, реальный БАД). Подтверди/замени. Кол-во 10 — ок?
