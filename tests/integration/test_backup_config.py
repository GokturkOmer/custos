"""backup_config_json + restore_config_json helper testleri (V11-109 / P-06).

Tam roundtrip (gerçek PG bağlantısı) integration testi pilot dry-run
sırasında staging DB'de yapılır. Bu dosya: serialization + retention helper'ı.
"""

from __future__ import annotations

import sys
import time
from datetime import UTC, date, datetime
from datetime import time as dtime
from pathlib import Path

import pytest

# scripts/ paket disinda, sys.path manipulation ile import.
_SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(_SCRIPTS_DIR))

import backup_config_json  # noqa: E402
import restore_config_json  # noqa: E402

# --- backup_config_json._json_default ---


def test_json_default_serializes_datetime_utc() -> None:
    """timezone-aware datetime → ISO 8601 UTC string."""
    dt = datetime(2026, 6, 5, 10, 30, 0, tzinfo=UTC)
    out = backup_config_json._json_default(dt)
    assert out.startswith("2026-06-05T10:30:00")
    assert out.endswith("+00:00")


def test_json_default_serializes_naive_datetime_as_utc() -> None:
    """tzinfo=None datetime UTC kabul edilir."""
    dt = datetime(2026, 6, 5, 10, 30, 0)
    out = backup_config_json._json_default(dt)
    assert out.startswith("2026-06-05T10:30:00")
    assert "+00:00" in out


def test_json_default_serializes_date_and_time() -> None:
    """date / time → ISO 8601 string."""
    assert backup_config_json._json_default(date(2026, 6, 5)) == "2026-06-05"
    assert backup_config_json._json_default(dtime(8, 0, 0)) == "08:00:00"


def test_json_default_unsupported_type_raises() -> None:
    """Bilinmeyen tip TypeError firlatmali."""
    with pytest.raises(TypeError):
        backup_config_json._json_default(object())


# --- backup_config_json._purge_old_backups ---


def test_purge_old_backups_removes_old_files(tmp_path: Path) -> None:
    """mtime > retention_days → dosya silinir."""
    old_file = tmp_path / "config-20240101.json"
    old_file.write_text("{}")
    new_file = tmp_path / "config-20260601.json"
    new_file.write_text("{}")
    # old_file mtime'ini 100 gün öncesine çek
    old_mtime = time.time() - (100 * 86400)
    import os

    os.utime(old_file, (old_mtime, old_mtime))

    removed = backup_config_json._purge_old_backups(tmp_path, retention_days=30)
    assert removed == 1
    assert not old_file.exists()
    assert new_file.exists()


def test_purge_old_backups_keeps_within_retention(tmp_path: Path) -> None:
    """Retention dâhilindeki dosyalara dokunulmaz."""
    recent = tmp_path / "config-20260601.json"
    recent.write_text("{}")
    removed = backup_config_json._purge_old_backups(tmp_path, retention_days=30)
    assert removed == 0
    assert recent.exists()


# --- restore_config_json._parse_value ---


def test_parse_value_datetime_string() -> None:
    """ISO 8601 string → datetime object."""
    out = restore_config_json._parse_value(
        "2026-06-05T10:30:00+00:00",
        "timestamp with time zone",
    )
    assert isinstance(out, datetime)
    assert out.year == 2026 and out.tzinfo is not None


def test_parse_value_date_string() -> None:
    """ISO 8601 date string → date object."""
    out = restore_config_json._parse_value("2026-06-05", "date")
    assert isinstance(out, date)
    assert out == date(2026, 6, 5)


def test_parse_value_time_string() -> None:
    """ISO 8601 time string → time object."""
    out = restore_config_json._parse_value("08:00:00", "time without time zone")
    assert isinstance(out, dtime)
    assert out == dtime(8, 0, 0)


def test_parse_value_passthrough_for_native_types() -> None:
    """int / bool / dict / None aynen geçer (asyncpg native handler)."""
    assert restore_config_json._parse_value(42, "integer") == 42
    assert restore_config_json._parse_value(True, "boolean") is True
    assert restore_config_json._parse_value(None, "any") is None
    assert restore_config_json._parse_value({"k": "v"}, "jsonb") == {"k": "v"}
