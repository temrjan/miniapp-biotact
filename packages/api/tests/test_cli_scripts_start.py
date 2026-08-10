"""Every CLI script must survive being STARTED — a fresh interpreter, not an in-process import.

``scripts/expire_stale_orders.py`` was delivered, reviewed and merged without ever being run: the
unit tests call ``expire_stale_orders`` directly, and by then ``conftest`` has already populated
``sys.modules``, so the module-level import order never gets exercised. On production the script
died instantly on the ``src.users.router`` ↔ ``src.auth.dependencies`` cycle, which resolved only
when ``src.auth.*`` loaded AFTER ``src.users``.

That cycle is gone (``src/users/__init__.py`` no longer re-exports the router), and
``test_modules_import_standalone.py`` now guards every module the same way this file guards every
script. Both still earn their place: a script can break on its own — a bad shebang, an argument
parsed at import time, a missing dependency — none of which is an import cycle.

Hence a SUBPROCESS: only a clean interpreter re-walks the import graph. ``--help`` exits before any
database or network work, so this stays a pure start-up check.
"""

import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = sorted((Path(__file__).resolve().parents[1] / "scripts").glob("*.py"))


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.name)
def test_cli_script_starts_in_a_clean_interpreter(script: Path) -> None:
    # S603 подавлен ниже: ввод не внешний — наш же интерпретатор и путь из glob по scripts/
    # этого репо. Подпроцесс тут не деталь реализации, а суть теста: в текущем процессе
    # sys.modules уже заполнен conftest'ом, и порядок импортов — тот самый, что лёг на проде, —
    # не проверяется вовсе.
    result = subprocess.run(  # noqa: S603
        [sys.executable, str(script), "--help"],
        capture_output=True,
        text=True,
        cwd=script.parents[1],
        timeout=120,
    )
    assert result.returncode == 0, f"{script.name} не стартует:\n{result.stderr}"
