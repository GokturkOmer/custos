"""V11-000-D kapsamı: pyproject whitelist audit script'i için unit testler.

`pip list` çağrısı mock'lanır — saf parse + karşılaştırma + main() exit
kodu doğrulaması. Hedef: paket düzeyi denetim mantığı pyproject formatı
ya da extras gibi varyasyonlar değiştiğinde sessiz şekilde kırılmasın.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.whitelist_audit import (
    find_unknown_packages,
    main,
    parse_whitelist_from_pyproject,
)

# --- pyproject.toml fixture'ları ---


_BASE_PYPROJECT = """\
[project]
name = "demo"
dependencies = [
    "asyncpg",
    "pymodbus>=3.6.0,<3.13.0",
    "uvicorn[standard]",
    "lxml>=6.1.0",  # transitive guard
]

[project.optional-dependencies]
dev = [
    "ruff",
    "pytest",
]
"""


def _write_pyproject(tmp_path: Path, content: str = _BASE_PYPROJECT) -> Path:
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(content, encoding="utf-8")
    return pyproject


# --- Whitelist parse ---


def test_parse_whitelist_extracts_canonical_names(tmp_path: Path) -> None:
    """Sürüm + extras kısıtları kırpılır; lowercase kanonik ad döner."""
    pyproject = _write_pyproject(tmp_path)
    whitelist = parse_whitelist_from_pyproject(pyproject)
    assert whitelist == {"asyncpg", "pymodbus", "uvicorn", "lxml"}


def test_parse_whitelist_includes_dev_when_flag_set(tmp_path: Path) -> None:
    """`include_dev=True` dev extras'ı listeye katar."""
    pyproject = _write_pyproject(tmp_path)
    whitelist = parse_whitelist_from_pyproject(pyproject, include_dev=True)
    assert whitelist == {
        "asyncpg",
        "pymodbus",
        "uvicorn",
        "lxml",
        "ruff",
        "pytest",
    }


def test_parse_whitelist_handles_empty_dependencies(tmp_path: Path) -> None:
    """`dependencies` yoksa boş set — patlamadan."""
    empty = "[project]\nname = \"empty\"\n"
    pyproject = _write_pyproject(tmp_path, empty)
    assert parse_whitelist_from_pyproject(pyproject) == set()


# --- Karşılaştırma ---


def test_find_unknown_returns_packages_outside_whitelist() -> None:
    """Whitelist dışı paketler (transitive olabilir) raporlanır."""
    whitelist = {"asyncpg", "pymodbus"}
    installed = [
        {"name": "asyncpg", "version": "0.30.0"},
        {"name": "pymodbus", "version": "3.12.0"},
        {"name": "anyio", "version": "4.13.0"},  # transitive
        {"name": "Sniffio", "version": "1.3.1"},  # casing dirençli
    ]
    unknown = find_unknown_packages(whitelist, installed)
    names = sorted(p["name"].lower() for p in unknown)
    assert names == ["anyio", "sniffio"]


def test_find_unknown_empty_when_all_whitelisted() -> None:
    """Hepsi whitelist'teyse boş liste."""
    whitelist = {"a", "b"}
    installed = [
        {"name": "A", "version": "1"},
        {"name": "B", "version": "2"},
    ]
    assert find_unknown_packages(whitelist, installed) == []


# --- main() exit kodları ---


def test_main_default_mode_returns_zero_even_with_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Varsayılan mod: unknown olsa bile exit 0 (informational)."""
    pyproject = _write_pyproject(tmp_path)

    def _fake_list() -> list[dict[str, str]]:
        return [
            {"name": "asyncpg", "version": "0.30"},
            {"name": "anyio", "version": "4.13"},  # transitive
        ]

    monkeypatch.setattr(
        "scripts.whitelist_audit.list_installed_packages", _fake_list
    )
    exit_code = main(["--pyproject", str(pyproject)])
    assert exit_code == 0
    captured = capsys.readouterr()
    assert "anyio" in captured.out


def test_main_strict_mode_returns_one_when_unknown_found(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--strict` + unknown varsa exit 1 (CI gating)."""
    pyproject = _write_pyproject(tmp_path)

    def _fake_list() -> list[dict[str, str]]:
        return [
            {"name": "asyncpg", "version": "0.30"},
            {"name": "rogue", "version": "1.0"},  # whitelist dışı
        ]

    monkeypatch.setattr(
        "scripts.whitelist_audit.list_installed_packages", _fake_list
    )
    assert main(["--pyproject", str(pyproject), "--strict"]) == 1


def test_main_strict_mode_returns_zero_when_clean(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--strict` + tümü whitelist içinde → exit 0."""
    pyproject = _write_pyproject(tmp_path)

    def _fake_list() -> list[dict[str, str]]:
        return [
            {"name": "asyncpg", "version": "0.30"},
            {"name": "pymodbus", "version": "3.12"},
        ]

    monkeypatch.setattr(
        "scripts.whitelist_audit.list_installed_packages", _fake_list
    )
    assert main(["--pyproject", str(pyproject), "--strict"]) == 0
