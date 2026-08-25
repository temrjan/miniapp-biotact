#!/usr/bin/env python3
"""Замер связей между модулями backend miniapp-biotact (§2-bis исследования).

Запуск из корня клона miniapp-biotact:
    python3 <путь>/module-deps.py

Считает импорты `from src.<модуль>` между папками модулей, отдельно —
импорты только для типов (`if TYPE_CHECKING`), которые во время работы
не выполняются.

ГРАНИЦА МЕТОДА: агрегирует по ПАПКАМ, а не по файлам. Взаимная связь между
папками не доказывает настоящую петлю импорта — она может идти через разные
файлы. Настоящие петли ловит tests/test_modules_import_standalone.py.
"""

import ast
import collections
import pathlib
import sys

SKIP = {"common", "config", "database", "models_registry"}
SRC = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "packages/api/src")


def main() -> int:
    if not SRC.is_dir():
        print(f"не найдено: {SRC}", file=sys.stderr)
        return 1

    real: dict[str, set[str]] = collections.defaultdict(set)
    typed: dict[str, set[str]] = collections.defaultdict(set)

    for path in SRC.rglob("*.py"):
        parts = path.relative_to(SRC).parts
        if len(parts) < 2:
            continue
        module = parts[0]
        try:
            tree = ast.parse(path.read_text())
        except SyntaxError:
            continue

        type_only_nodes = {
            id(sub)
            for node in ast.walk(tree)
            if isinstance(node, ast.If) and "TYPE_CHECKING" in ast.dump(node.test)
            for sub in ast.walk(node)
        }

        for node in ast.walk(tree):
            if not (isinstance(node, ast.ImportFrom) and node.module):
                continue
            if not node.module.startswith("src."):
                continue
            other = node.module.split(".")[1]
            if other == module or other in SKIP:
                continue
            bucket = typed if id(node) in type_only_nodes else real
            bucket[module].add(other)

    print(f"{'модуль':<14} {'настоящие связи':<42} только для типов")
    print("-" * 90)
    for module in sorted(set(real) | set(typed)):
        r = " ".join(sorted(real.get(module, set()))) or "—"
        t = " ".join(sorted(typed.get(module, set()))) or "—"
        print(f"{module:<14} {r:<42} {t}")

    edges = sum(len(v) for v in real.values())
    mutual = {tuple(sorted((a, b))) for a in real for b in real[a] if a in real.get(b, set())}
    print(f"\nнастоящих связей: {edges}")
    print(f"взаимных пар (верхняя граница подозрения): {len(mutual)}")
    for a, b in sorted(mutual):
        print(f"  {a} ⇄ {b}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
