# Дашборд-мониторинг Biotact Club в админке — Гейт-1 спека (v1)

**Дата:** 2026-07-08 · **Статус:** DISCOVER → ожидает Гейт-1 (Ревьюер свежим контекстом через Капитана).
**Кресло:** Claude=Инженер. **Режим DISCOVER:** A (Инженер предложил топ-метрики → Капитан ратифицировал: 5 блоков, герой=Активации QR, live-SQL, прод-замер сделан).
**Природа:** новая read-only админ-фича (аналитика лоялти). НЕ трогает пользовательский лоялти-путь, НЕ пишет в БД, 0 миграций.

## 1. Цель (что просил Капитан)

Изнутри админки видеть жизнь Biotact Club: сколько QR активировано, сколько участников, воронка,
экономика баллов, демография. Готовность **до** физического запуска коробок — чтобы ловить цифры
в момент старта тиража.

## 2. Факты прод-замера (ратифицируют live-SQL) — 2026-07-08, read-only `COUNT(*)`

| qr_codes | qr used | code_batches | customer_profile | customer_event | point_ledger | consent | users | phone_verified |
|---|---|---|---|---|---|---|---|---|
| 73 | **0** | 24 | 1 | 2 | 0 | 1 | 507 | 0 |

Лоялти-таблицы практически пусты (физического запуска не было; 73 QR — подготовка). **Вывод:**
простые `COUNT/SUM/GROUP BY` по проиндексированным колонкам мгновенны → **live-SQL, без materialized.**
Materialized-слой = отдельный PR позже, если замер объёмов когда-нибудь покажет тормоза (факт-переключатель).

## 3. Решение — эталон `GET /analytics/dashboard` (зеркалим 1:1)

Существующий `DashboardStats` (`analytics/router.py:65` → `service.get_dashboard_stats` → `schemas.DashboardStats`,
фронт `features/dashboard`) — эталон паттерна. Делаем **параллельный** `GET /analytics/loyalty` тем же
устройством, за тем же `require_admin` (admin-JWT `type=="admin"` + `is_admin`).

**Слайсим на 2 маленьких обратимых PR** (риск-first, как весь проект — сначала read-API dark, потом UI):

- **PR-D1 (бэкенд):** `GET /analytics/loyalty` → `LoyaltyStats`; `service.get_loyalty_stats`; схемы; тесты.
  Ноль фронта, ноль миграций. Ships **dark** (эндпоинт живой, никто не зовёт). Прод-деплой = no-op по схеме.
- **PR-D2 (фронт):** раздел «Biotact Club» в админ-дашборде, потребляет эндпоинт. Свой лёгкий Гейт-1
  дизайн-мини (тот же паттерн, что у UI-PR этого проекта).

## 4. PR-D1 — контракт `LoyaltyStats` (каждое поле ↦ реальная колонка)

Параметр `days: int = Query(30, ge=1, le=365)` — как в dashboard (окно для тренда активаций).
Фиксированные окна 7д/30д — независимы от `days` (как `buyers_7d/30d`).

```
LoyaltyStats:
  # Блок 1 — Активации QR (ГЕРОЙ)
  qr_generated:      int      # count(qr_codes)
  qr_activated:      int      # count(qr_codes WHERE status='used')
  qr_activated_7d:   int      # count(customer_event qr_activated, occurred_at >= now-7d)
  qr_activated_30d:  int      # count(customer_event qr_activated, occurred_at >= now-30d)
  activation_rate:   float    # qr_activated/qr_generated*100, 0.0 если generated=0 (guard /0)
  batches_count:     int      # count(code_batches)
  activations_by_day: list[ActivationDataPoint]   # тренд за `days`, дырки = 0 (как revenue_data)

  # Блок 2 — Участники
  profiles_total:    int      # count(customer_profile)  = вошли в лоялти-поток
  phone_verified:    int      # count(users WHERE phone_verified_at IS NOT NULL)
  friends:           int      # count(customer_profile WHERE status == LoyaltyStatus.BIOTACT_FRIEND)
                              # ⚠ enum-колонка → фильтр ЧЛЕНОМ enum (PG хранит NAME 'BIOTACT_FRIEND'),
                              #   НЕ строкой 'biotact_friend' — иначе молчаливый 0 навсегда (MINOR Ревьюера)

  # Блок 3 — Воронка регистрации (distinct users на ступень)
  funnel:            list[FunnelStep]   # joined → phone_shared → qr_activated → survey_done
  consents_ever_granted: int  # count(distinct user_id) в consent WHERE granted=true — «дали согласие
                              #   хоть раз» (append-style: включает позже отозвавших; подпись в UI честная)

  # Блок 4 — Экономика баллов
  points_earned:       int    # sum(point_ledger.amount WHERE amount>0), coalesce 0
  points_spent:        int    # -sum(amount WHERE amount<0), coalesce 0 (показываем положительным)
  points_outstanding:  int    # sum(amount), coalesce 0  = остаток на руках у всех
  points_by_reason:  list[PointsByReason]   # group by reason: {reason, total}

  # Блок 5 — Демография (честно с покрытием)
  demographics:      Demographics

ActivationDataPoint: { date: str(YYYY-MM-DD), count: int }
FunnelStep:          { step: str, users: int }           # step ∈ {joined,phone_shared,qr_activated,survey_done}
PointsByReason:      { reason: str, total: int }
Demographics:
  gender:            list[{ gender: str|"unknown", count: int }]
  age_buckets:       list[{ bucket: str, count: int }]    # 18-24/25-34/35-44/45-54/55+/unknown (CASE)
  top_cities:        list[{ city: str, count: int }]      # group by city, order desc, limit 5, city NOT NULL
  survey_coverage:   float   # profiles с ≥1 survey-полем / profiles_total *100, 0.0 если profiles=0
```

**Все источники подтверждены чтением `loyalty/models.py`:** `customer_event.event_type`/`occurred_at`
проиндексированы; `point_ledger.reason`/`user_id`/`amount` есть, индексы на reason+user_id; `qr_codes.status`;
`customer_profile.status`/`gender`/`age`/`city`/`lifetime_activations`; `consent.granted`/`user_id`;
`users.phone_verified_at`. `CustomerEventType`/`LedgerReason`/`LoyaltyStatus`/`Gender` = StrEnum, PG хранит
StrEnum как VALUE в `customer_event`/`point_ledger` (String-колонки) и как NAME в enum-колонках
(`customer_profile.status`, `gender`) — в SQL сравниваем через `.value`/enum как в существующем сервисе.

## 5. Открытые развилки для Гейта-1 (не голые — с реко)

**Ф1 — метрика «отписавшиеся» (`users.is_active=false`).** Реко: **исключить из v1.** `is_active` —
аккаунт-уровневый флаг (вся e-com база 507), НЕ лоялти-специфичный; security-1 трактует его как «отписка,
не бан» в accrual-пути, но как дашборд-число он двусмысслен (деактивация аккаунта ≠ отписка от лоялти) и
на пустых лоялти-таблицах = шум. Само-атака: «владелец хочет видеть отток» — да, но честный отток лоялти
не смоделирован отдельным полем; давать `is_active` под ярлыком «отписки» = врать числом. Цена решения мала:
добавить отдельным полем тривиально, если Капитан/Ревьюер захотят. **Вопрос Капитану:** включаем или нет.

**Ф2 — интеграция фронта (решается в PR-D2, не сейчас).** Вкладка внутри `DashboardPage` (Radix Tabs
«Магазин» | «Biotact Club») vs отдельный пункт сайдбара/страница. Реко: **вкладка** (в ментальной модели
Капитана — «в дашборде»; минимум нового навигационного провода; не трогает рабочий orders-дашборд).
Деталь оформляем в мини-дизайне PR-D2.

**Ф3 — `consents_ever_granted` семантика (переименовано по MINOR Ревьюера).** consent — append-style
(строка на grant/revoke, актуальное = последняя на (user,purpose)). Реко v1: `count(distinct user_id
WHERE granted=true)` — «сколько людей дали хоть одно согласие»; **включает позже отозвавших** (у них
осталась старая granted=true строка), поэтому поле названо `consents_ever_granted`, а не `consents_granted`,
чтобы не читалось как «сейчас согласны». Точную «активных согласий по purpose» (latest-строка на
(user,purpose)) — отдельным полем в v2, если попросит комплаенс. Помечено честно в докстринге.

## 6. План тестов / DoD — PR-D1 (правило №0: red→green)

- [ ] **red→green:** тест сидит лоялти-строки с **ненулевым ожиданием на КАЖДОЕ поле** — несколько qr_codes
      used/generated, customer_event разных типов, point_ledger +/−, customer_profile с/без survey, consent granted.
      **Обязательно ≥1 профиль `status=BIOTACT_FRIEND` + ассерт `friends == 1`** (MINOR Ревьюера: без этого сида
      `friends=0` «правильно» и на сломанном enum-фильтре — баг всплыл бы только на первом реальном друге в проде).
      Ассертить **точные** числа в `LoyaltyStats`. Против кода без сервиса — падает (эндпоинта нет); с сервисом —
      зелёный. Показать оба прогона. Мутация `friends`: подменить статус в фильтре (`BIOTACT_FRIEND`→`NEWBIE`)
      → тест краснеет (`3 != 1`). ⚠ Эмпирика: `status == "biotact_friend"` (сырой VALUE) НЕ даёт «молчаливый 0» —
      SQLAlchemy Enum-биндер резолвит VALUE→член→NAME; член enum выбран за ЯСНОСТЬ и устойчивость, а не потому,
      что сырая VALUE-строка тихо ломается (первичная формулировка MINOR Ревьюера тут неточна — задокументировано).
- [ ] **Граничные (guard /0):** пустая БД → `activation_rate=0.0`, `survey_coverage=0.0`, все счётчики 0,
      списки пустые, эндпоинт 200 (НЕ 500). Это текущее прод-состояние — критично.
- [ ] **Авторизация:** без Bearer → 401; не-admin токен → 403; admin → 200 (зеркало теста dashboard, если есть).
- [ ] **Тренд:** `activations_by_day` заполняет дырки нулями на всём окне `days` (как revenue_data).
- [ ] Полный локальный CI api на parity-PG (`ruff`/`ruff format --check`/`mypy --strict`/`pytest`),
      `biotact:biotact_ci`, БД `biotact_test_db`. 0 миграций → `Migration parity` не затронут, деплой no-op.
- [ ] Отчёт → Гейт-2 отдельным Ревьюером свежим контекстом (`/python-review` обязателен; read-only + admin-JWT —
      `/security-review` уместен: проверить, что за `require_admin` и не течёт PII сверх агрегатов).

## 7. Не в скоупе (v1)
Materialized/кэш-слой; CSV-экспорт лоялти (dashboard имеет свой — добавим в PR-D2 если попросят); графики
per-product разбивки; realtime; `is_active`-отписки (Ф1, ждёт решения); мелкие долги (common.py:300, doc-PR
security-1/4, NIT-триаж) — они НЕ блокируют дашборд.

## 8. Почему это безопасно
Read-only (ни одного INSERT/UPDATE); за `require_admin`; 0 миграций → деплой no-op по схеме (нулевой риск
инцидента-типа #30); PR-D1 dark (фронт не зовёт) → мержим и деплоим бэкенд без единого видимого изменения,
UI приходит отдельным PR-D2. Полностью обратимо (revert одного PR).
