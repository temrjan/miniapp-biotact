"""Every ``src.*`` module must survive being imported FIRST, in a clean interpreter.

Sibling of ``test_cli_scripts_start.py``, one level deeper. That test guards the scripts; this one
guards the modules they (and any future script, and any ``python -c`` during an incident) import.
The gap between the two is where a package cycle lives unnoticed: the application resolves it by
loading modules in an order that happens to work, so the suite, the app and CI all stay green while
a third of the tree cannot be imported on its own.

That is not hypothetical here. ``src/users/__init__.py`` re-exported the users ROUTER, which pulled
``src.auth.dependencies`` back into a half-built ``src.auth`` — 27 of 81 modules failed to import
first, and the breakage surfaced twice: once on production (``expire_stale_orders`` died on start,
see the sibling test) and once in review, where a shared predicate had to be given a new home
because importing it from its natural one raised ImportError.

A SUBPROCESS per module is the whole point: inside this process ``conftest`` has already populated
``sys.modules``, so every import after the first is answered from cache and proves nothing. Running
them concurrently keeps that property — each module still gets its own fresh interpreter — while
turning a serial walk of 81 processes into a parallel one.
"""

import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

API_ROOT = Path(__file__).resolve().parents[1]
SRC = API_ROOT / "src"


def _module_names() -> list[str]:
    """Every importable module under ``src/``, packages included, as dotted paths."""
    names = []
    for path in sorted(SRC.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        dotted = path.relative_to(API_ROOT).with_suffix("")
        parts = list(dotted.parts)
        if parts[-1] == "__init__":
            parts.pop()
        names.append(".".join(parts))
    return names


def _import_fails(module: str) -> tuple[str, str]:
    """Import ``module`` first thing in a clean interpreter; return its stderr on failure."""
    result = subprocess.run(  # noqa: S603 — our own interpreter, module name from a glob of src/
        [sys.executable, "-c", f"import {module}"],
        capture_output=True,
        text=True,
        cwd=API_ROOT,
        timeout=120,
    )
    return module, "" if result.returncode == 0 else result.stderr.strip().splitlines()[-1]


def test_every_module_imports_first_in_a_clean_interpreter() -> None:
    """No module may depend on something else having been imported before it.

    One test over all modules rather than a parametrised case each: the failure that matters is a
    CYCLE, and a cycle takes down a whole cluster of modules at once. Reporting the cluster in one
    message says "the users↔auth edge is back"; 27 separate red cases say it 27 times and bury the
    shape of the problem.
    """
    modules = _module_names()
    assert modules, "no modules discovered under src/ — the glob is broken, not the tree"

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(_import_fails, modules))

    broken = {name: err for name, err in results if err}
    assert not broken, "модули не импортируются первыми ({} из {}):\n{}".format(
        len(broken),
        len(modules),
        "\n".join(f"  {name}: {err}" for name, err in sorted(broken.items())),
    )
