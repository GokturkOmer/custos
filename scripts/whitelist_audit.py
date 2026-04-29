"""Pyproject whitelist audit — kurulu paketler ile beyan edilenleri karşılaştır.

V11-000-D: A1 architecture_check.py kod düzeyi import yasaklarını
denetler. Bu script paket düzeyinde "yalnızca pyproject.toml'da
beyan edilen paketler kuruludur" görünürlüğünü verir. Transitive
bağımlılıklar genellikle whitelist dışında kaldığı için varsayılan
mod "informational" — exit 0 döner ve liste raporlar. `--strict`
modunda whitelist dışı paket varsa exit 1 (CI gating için).

Kullanım:
    python scripts/whitelist_audit.py
    python scripts/whitelist_audit.py --strict
    python scripts/whitelist_audit.py --include-dev

Test edilebilirlik için iş mantığı saf fonksiyonlara ayrılmıştır;
`pip list` çağrısı bir nokta üzerinden geçer (mock'lanabilir).
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Final

REPO_ROOT: Final[Path] = Path(__file__).resolve().parent.parent
PYPROJECT_PATH: Final[Path] = REPO_ROOT / "pyproject.toml"

# Paket adının version specifier'larından önceki kısmını yakalar:
#   "package>=1.0,<2.0"  →  "package"
#   "package[extra]==2"  →  "package"
#   "package ; python_version >= '3.10'"  →  "package"
_PKG_NAME_RE: Final[re.Pattern[str]] = re.compile(
    r"^\s*([A-Za-z0-9_.\-]+)"
)


def parse_whitelist_from_pyproject(
    pyproject_path: Path, include_dev: bool = False
) -> set[str]:
    """`[project.dependencies]` (ve opsiyonel `dev`) listesinden paket adlarını çıkarır.

    Sürüm kısıtları ve extras temizlenir; sadece kanonik paket adı
    döner (lowercase). `include_dev=True` ile `[project.optional-dependencies.dev]`
    listesi de eklenir.
    """
    data = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    project = data.get("project", {})
    deps: list[str] = list(project.get("dependencies", []))
    if include_dev:
        optional = project.get("optional-dependencies", {})
        deps.extend(optional.get("dev", []))

    whitelist: set[str] = set()
    for dep in deps:
        # `uvicorn[standard]` gibi extras'ı kırpmak için `[` öncesini al.
        bare = dep.split("[", 1)[0]
        match = _PKG_NAME_RE.match(bare)
        if match is None:
            continue
        whitelist.add(match.group(1).lower())
    return whitelist


def list_installed_packages() -> list[dict[str, str]]:
    """`pip list --format=json` çıktısını parse eder; hata olursa boş liste."""
    try:
        completed = subprocess.run(
            ["pip", "list", "--format=json"],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return []
    try:
        parsed = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    result: list[dict[str, str]] = []
    for item in parsed:
        if isinstance(item, dict) and "name" in item:
            result.append(
                {
                    "name": str(item.get("name", "")),
                    "version": str(item.get("version", "")),
                }
            )
    return result


def find_unknown_packages(
    whitelist: set[str], installed: list[dict[str, str]]
) -> list[dict[str, str]]:
    """Whitelist dışında kalan kurulu paketleri döner.

    Kurulu paketler whitelist'e (lowercase) göre eşleştirilir; eşleşmeyenler
    transitive bağımlılık olabilir veya beklenmedik kurulum olabilir.
    Kanonik karar çağırana bırakılır.
    """
    unknown: list[dict[str, str]] = []
    for pkg in installed:
        name = pkg.get("name", "").lower()
        if name and name not in whitelist:
            unknown.append(pkg)
    return unknown


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Pyproject whitelist audit (V11-000-D).",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Whitelist dışı paket varsa exit 1 döndür (CI gating).",
    )
    parser.add_argument(
        "--include-dev",
        action="store_true",
        help="dev optional-dependencies'i de whitelist'e dahil et.",
    )
    parser.add_argument(
        "--pyproject",
        type=Path,
        default=PYPROJECT_PATH,
        help="pyproject.toml yolu (test override).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Whitelist audit raporlar; --strict modunda unknown varsa exit 1."""
    args = _parse_args(argv)
    whitelist = parse_whitelist_from_pyproject(
        args.pyproject, include_dev=args.include_dev
    )
    installed = list_installed_packages()
    unknown = find_unknown_packages(whitelist, installed)

    print(  # noqa: T201
        f"Custos whitelist audit — {len(whitelist)} beyan, "
        f"{len(installed)} kurulu, {len(unknown)} whitelist dışı."
    )
    if unknown:
        print("\nWhitelist dışı paketler (transitive olabilir):")  # noqa: T201
        for pkg in sorted(unknown, key=lambda p: p["name"].lower()):
            print(f"  - {pkg['name']} {pkg['version']}")  # noqa: T201
    else:
        print("\nKurulu tüm paketler whitelist içinde.")  # noqa: T201

    if args.strict and unknown:
        print(  # noqa: T201
            "\n--strict mod: whitelist dışı paket var, exit 1.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
