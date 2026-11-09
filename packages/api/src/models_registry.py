"""Central model registry — imports every model module onto ``Base.metadata``.

Used by ``alembic/env.py`` (autogenerate / ``alembic check``) and the app
(``Base.metadata.create_all`` in tests) so that model-migration parity and schema
creation do not depend on ad-hoc imports scattered across routers.
"""

import importlib

# Порядок больше НЕ несущий. Он был зафиксирован вручную, пока в пакетах жил цикл
# src.users.router <-> src.auth.dependencies: тот резолвился, только если src.auth.* грузился
# ПОСЛЕ src.users, и алфавитный порядок ломал импорт с ImportError 'CurrentUser'. Цикл снят в
# src/users/__init__.py (роутер больше не реэкспортируется), а
# tests/test_modules_import_standalone.py следит, чтобы он не вернулся.
# Порядок оставлен как есть намеренно: перестановка ничего не чинит и ничего не доказывает, а
# трогать список, от которого зависит паритет миграций, без нужды не стоит.
_MODEL_MODULES = (
    "src.categories.models",
    "src.consultant.models",
    "src.products.models",
    "src.users.models",
    "src.orders.models",
    "src.auth.models",
    "src.buyer_auth.models",
    "src.crm.models",
    "src.banners.models",
    "src.broadcasts.models",
    "src.loyalty.models",
    "src.payments.models",
    "src.employee_referrals.models",
    "src.employee_referrals.paid_models",
    "src.employee_referrals.return_models",
    "src.employee_referrals.delivery_models",
    "src.journal.models",
    "src.event_promotions.models",
)

for _module in _MODEL_MODULES:
    importlib.import_module(_module)
