# Отчёт: Модель-1 — резерв → списание при оплате (частичное списание баллов)

**Кресло:** Инженер · **Ветка:** `feat/model1-reserve-commit-partial-redeem` · **Коммит:** `e08e7d1` (локальный, не запушен)
**Спека (Гейт-1 APPROVED):** `.claude/specs/2026-07-16-model1-reserve-commit-partial-redeem.md`
**Диф для ревью:** `.review.diff` (10 файлов, +735/−90)

---

## Сделано — по пунктам спеки

**Резерв вместо списания (create_order).** Частичный заказ РЕЗЕРВИРУЕТ баллы — строки в леджере НЕТ; pending-заказ (`points_applied>0`) сам и есть резерв. `loyalty/service.py::reserve_points` (бывш. `redeem_points`) допускает под `customer_profile FOR UPDATE`, reserve-aware: `available = SUM(ledger) − Σ(points_applied чужих unpaid/pending, exclude=этот заказ)`; нехватка → `InsufficientPointsError` (409). `settle_now=True` только для полного покрытия (списывает атомарно при создании — карты нет, гореть нечему).

**M1 — commit при оплате (`payments/lifecycle.py`).** `commit_redeem` пишет реальный `redeem −N` в success-ветке `process_payment_event`, **физически внутри** `if status == STATUS_SUCCESS:` (после гварда `_ALLOWED_FROM`, стр.177). Никогда не бросает на нехватку (карта уже оплатила — откат = вечный ретрай Multicard): при структурно-невозможной нехватке лог `CRITICAL` + пропуск (не уводит баланс в минус).

**M2 — атомарный cancel очистки.** `orders/service.py::_expire_partial` отменяет зомби условным `UPDATE ... WHERE payment_status IN ('unpaid','pending')` (no-op если реальный success успел settle), паттерн `try_arm_payment_attempt` / claim `activate_code`.

**M3 — порядок delete→cancel + обработка ошибки.** `delete_invoice(uuid)` СНАЧАЛА; cancel только при `success:true`; любой `success:false`/сеть → НЕ отменять, лог + оставить `unpaid` (штатный колбэк доведёт, если деньги пришли). `payments/service.py::MultiCardClient.delete_invoice` возвращает `bool`, тело ответа — источник истины про «уже оплачен» (`ERROR_FIELDS`), не бросает.

**Ленивая очистка (`orders/router.py`).** POST /orders на redeem-пути (`points_to_apply>0`) вызывает `expire_stale_partials` + **собственный `db.commit()`** ДО create_order (внешний DELETE не осиротеет при откате). Best-effort: сбой очистки не блокирует заказ. TIMEOUT = **30 мин от `order.created_at`** (`REDEEM_RESERVE_TIMEOUT`).

**Индекс + миграция.** `orders(user_id, payment_status)` в модели + ручная миграция `b7d2f4a9c1e3`. Гейт Migration parity прогнан локально на чистой БД: `upgrade head` → `alembic check` («No new upgrade operations detected») → round-trip `downgrade base` → `upgrade head` → `check` — зелёно.

**Стейл-докстрины** `redeem_points`/`InsufficientPointsError` (описывали all-or-nothing) — переписаны под резерв-модель. Модуль-докстринг `test_orders_redeem.py` тоже.

## Как тестировал — команды + вывод

4 гейта локально (точь-в-точь CI):
```
ruff check src/ tests/         → All checks passed!
ruff format --check src/       → 77 files already formatted
mypy src/ --strict             → Success: no issues found in 77 source files
pytest tests/ -q               → 267 passed, 11 warnings in ~70s
```
Migration parity: `alembic upgrade head` → `check` → `downgrade base` → `upgrade head` → `check` на чистой `biotact_parity` — оба `check` = "No new upgrade operations detected".

**Мутации RED (правило №0, каждая — своя проба; коммит ДО мутаций, `git checkout` восстановил):**
| # | Мутация | Тест | Итог |
|---|---|---|---|
| M1 | `settle_now=is_full`→`True` (партиал списывает) | `test_partial_redeem_reserves_points_writes_no_ledger_row` | RED ✅ |
| M2 | снять `.with_for_update()` в `reserve_points` | `test_race_two_partial_reservations_never_overspend` | RED ✅ |
| M3 | cleanup не отменяет (`Order.id==order.id`→`-1`) | `test_stale_partial_reservation_is_expired_and_frees_reserve` | RED ✅ |
| M4 | cleanup отменяет несмотря на delete=False | `test_stale_partial_with_unremovable_invoice_is_not_cancelled` | RED ✅ |
| M5 | убрать `commit_redeem` в success-ветке | `test_partial_order_paid_via_callback_commits_redeem_and_notifies_paid` | RED ✅ |
| M6 | `commit_redeem` ВНЕ гварда (перед стр.177) | `test_late_success_on_cancelled_partial_writes_no_redeem` | RED ✅ |
| M7 | `available`→raw `balance` в `reserve_points` | `test_full_coverage_refused_when_reservation_holds_the_balance` | RED ✅ |

## Отклонения от плана и почему

1. **Полное покрытие тоже reserve-aware** (спека: «без изменений»). Трактую «без изменений» как _тайминг дебета_ (атомарно при создании), а не обход available-проверки. Без этого full-order с балансом 100k при pending-резерве 60k списал бы 50k → инвариант `SUM(ledger) ≥ Σ резервов` сломан → при settle партиала M3.3-нехватка. Reserve-aware full закрывает дыру (тест M7). **Флажу: если Ревьюер хочет буквальное «без изменений» — верну, но это money-safety-регресс.**

2. **Ленивая очистка — на уровне РОУТЕРА, не в create_order-сервисе.** Очистке нужен собственный commit (M3.4), а сервис вызывается в тестах внутри `session.begin()` — mid-функция `db.commit()` там сломался бы. Роутер вызывается только по HTTP (get_db с commit-on-success). Прецедент — ранний `db.commit()` в `payments/router` (`_notify_paid`) и в этом же хендлере (полное покрытие). Core-логика резерва осталась в сервисе и тестируется напрямую; Multicard-оркестрация — на роутере (как /payments/create).

3. **Барьер теста гонки перенесён из `_price_lines` в `reserve_points`.** Исходный барьер (до flush заказа) НЕ ловил снятие лока: asyncio сериализует коммиты, проигравший видит резерв победителя даже без лока (M2 оставалась GREEN — «проба не выполнилась»). Барьер внутри `reserve_points` (оба заказа во flush, ни один не закоммичен) делает лок доказуемо необходимым (M2 → RED). Урок в докстринге теста.

## Не сделано / отложено (в скоупе спеки — намеренно)

- **Периодический sweep** брошенных-навсегда резервов — отдельный PR (планировщика в приложении нет, Развилка 3). Money-safety не зависит: резерв per-user, чужих не трогает.
- **Вернуть фронт `REDEEM_ENABLED=true` + §8** (реальный OFD-чек, сплит qty>1) — после выката этого бэкенда (отдельные шаги, спека §DoD).

## Замечено, не трогаю — вне скоупа

- **M3.4 идемпотентность повторного `delete_invoice`** на уже-удалённом инвойсе: политика консервативна — любой `success:false` ⇒ НЕ отменять (money-safe при любом ответе Multicard). Реальный ответ на double-delete не проверял вживую (нет тестового Multicard); отдельной пробой при песочнице.
- **Каскад DELETE user → point_ledger** (RESTRICT не переживает каскад через users) — известная дыра из PR-6.1, отмечена в `models.py`, не трогаю.
- Pydantic `class Config` deprecation warnings в `orders/schemas.py` — предсуществующие, вне скоупа.

## Вопросы Ревьюеру

1. **Отклонение №1** (reserve-aware full coverage) — принять как верную трактовку спеки, или откатить к буквальному «без изменений»?
2. **Отклонение №2** (очистка на роутере) — согласен с местом, или ожидал в сервисе?
3. **TIMEOUT = 30 мин** от `created_at` (дефолт Капитана) — ок для гейта, или отдельная проба?
4. Индекс — schema-change в money-PR: parity зелёный, но если предпочитаешь вынести индекс отдельным PR (меньше blast radius деплоя) — скажи.
