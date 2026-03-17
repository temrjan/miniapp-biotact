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
