"""Central model registry — imports every model module onto ``Base.metadata``.

Used by ``alembic/env.py`` (autogenerate / ``alembic check``) and the app
(``Base.metadata.create_all`` in tests) so that model-migration parity and schema
creation do not depend on ad-hoc imports scattered across routers.
"""

import importlib

# ВАЖНО: порядок импорта моделей зафиксирован вручную. В пакетах есть пред-существующий
# циклический импорт (src.users.router <-> src.auth.dependencies), который резолвится,
# только если src.auth.* грузится ПОСЛЕ src.users / src.categories. Алфавитный порядок
# (auth первым) ломает его: ImportError cannot import name 'CurrentUser'. Реальный фикс —
# расшить цикл в роутерах (вне скоупа PR-0.1). Новый модуль моделей добавляй сюда,
# после auth/users-ядра.
_MODEL_MODULES = (
    "src.categories.models",
    "src.products.models",
    "src.users.models",
    "src.orders.models",
    "src.auth.models",
    "src.crm.models",
    "src.banners.models",
    "src.broadcasts.models",
    "src.loyalty.models",
    "src.payments.models",
)

for _module in _MODEL_MODULES:
    importlib.import_module(_module)
