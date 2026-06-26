# Biotact — Telegram Mini App E-commerce

## Stack

| Component | Tech | Path |
|-----------|------|------|
| Frontend (Mini App) | React 19 + Vite 7 + TypeScript 5.9 + Zustand + TanStack Query | `packages/frontend/` |
| Admin Panel | React 19 + Vite 7 + TypeScript 5.9 + Radix UI + Tailwind | `admin-panel-v2/` |
| API | FastAPI 0.124 + SQLAlchemy 2.0 async + Pydantic 2 + Alembic | `packages/api/` |
| Bot | aiogram 3.23 + SQLAlchemy + asyncpg | `packages/bot/` |
| Database | PostgreSQL (asyncpg) | `biotact_db` |

## Commands

```bash
# Frontend
cd packages/frontend && npm ci && npm run build
npm run test          # vitest (129 tests)
npm run lint          # eslint
npx tsc --noEmit      # type check

# Admin
cd admin-panel-v2 && npm ci && npm run build
npm run lint
npx tsc -b

# API
cd packages/api && pip install -r requirements.txt
ruff check src/
pytest tests/ -v

# Bot
cd packages/bot && pip install -r requirements.txt
ruff check bot/
```

## Workflow

```
feature-branch → push → CI (type-check + lint + test + build) → PR → merge to main → CD → prod
```

- Branch protection on `main` — direct push forbidden
- CI must pass before merge
- Lint: `continue-on-error: true` (temporary, fix incrementally)
- Type-check + build: strict (must pass)

## Architecture

- Auth: Telegram X-Init-Data (HMAC-SHA256)
- Payments: Multicard integration
- Two bots: Mini App bot (7748404469) + Notification bot (8175184075)
- Server: biotact-main (95.111.224.251:2222), PM2

## Rules

- Follow standards from `~/Codex/standards/` (react.md, typescript.md, fastapi.md, python.md)
- No `any` in TypeScript — use proper types
- Type hints mandatory in Python
- No hardcoded secrets — use .env
- Named exports in React (not default)

## Biotact Club (программа лояльности — Этап 1 CRM в работе; 3 PR на проде)

Полная программа лояльности/оцифровки поверх этого мини-аппа.

**📌 ГЛАВНЫЙ ДОКУМЕНТ (работаем по нему):** `.claude/specs/2026-06-25-biotact-club-plan.md` — план **ФИНАЛ v4** (вся программа, 8 этапов). Всё остальное подчинено ему.

**Источники правды (по порядку):**
1. **План ФИНАЛ v4** (выше) — что строим в целом.
2. **Текущая инженерная спека:** `.claude/specs/2026-06-25-stage0-1-gate-crm.md` — Этап 0+1 (по ней идёт текущий PR).
3. **Концепт для руководства:** `docs/biotact-club-concept.html`.
4. **Состояние/resume:** memory `handoff-biotact-club` (единственный актуальный хэндофф; старые описания НЕ актуальны — объём менялся).

Суть: уникальный QR на коробке (несёт SKU) → бот (лаунчер + шеринг телефона) → мини-апп под initData пишет CRM/лоялти. Кэшбэк 5%/8% + статус «Друг» (10 активаций lifetime) + welcome ×1.5 + трата баллов в мини-аппе + розыгрыш. **Инкрементальная customer-модель:** `consent`/`customer_profile`/`customer_event` рядом с `users`; `balance = SUM(point_ledger)` (единый источник, не колонка). Лоялти живёт в `customer_profile`, НЕ на `users` (у бота своя `User`-модель).

⚠️ **КРИТИЧНО — урок инцидента 2026-06-19:** в этом репо **merge в `main` = деплой на прод** (CD по `workflow_run`). Schema-PR #30 уронил прод (деплой не накатывал миграции) → откат #31 → `deploy.yml` укреплён (`alembic upgrade head` + откат кода + health-check). ✅ **Happy-path ДОКАЗАН на реальной схеме** (PR-1.1 #38: `CREATE TABLE consent` на проде чисто, health OK). Перед ЛЮБЫМ schema-PR: гейт **`Migration parity`** (CI-паритет модель↔миграция на чистом PG) + миграция руками против чистой parity-БД (НЕ автоген против прода) + enum-DROP в downgrade. Новые колонки `users` — только `nullable`/`server_default`. ⚠️ fail-rollback-путь деплоя ещё не проверен (Issue #34, гейтит Этап 2).
🚫 **НЕ ре-мёржить `origin/feat/loyalty-pr1-schema`** — это та самая схема, что клала прод (откатана #31). Устаревшие лоялти-ветки на origin (`feat/loyalty-pr0/pr1`, `revert/restore-prod-pre-loyalty`, `wip/ai-integration-snapshot-*`) — к удалению.

**Прогресс (план §7):** ✅ **0 Гейт** (PR-0.1 parity-gate+reconcile #32 · PR-0.2 флаг `loyalty_enabled=false` #37) → 🔵 **1 CRM-фундамент** (`consent` ✅ #38 на проде → **next PR-1.2 `customer_profile`**+`users.phone_verified_at` → PR-1.3 `customer_event`) → 2 коды+активация+кэшбэк → 3 статус «Друг» → 4 регистрация-поток → 5 розыгрыш → 6 трата баллов → 7 бонусы/рефералы/сгорание.
