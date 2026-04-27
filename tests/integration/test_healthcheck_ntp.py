"""healthcheck.py NTP kontrolu testleri (V11-113 / P-06).

``timedatectl show --property=NTPSynchronized --value`` cıktısı parse edilir.
Subprocess.run mock'lanir; gerçek timedatectl gerekmez.
"""

from __future__ import annotations

import subprocess

# scripts/healthcheck.py paket dışı; sys.path manipulation ile import edilir.
import sys
from pathlib import Path
from unittest.mock import patch

_SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(_SCRIPTS_DIR))

import healthcheck  # noqa: E402


def _mock_completed(
    stdout: str,
    returncode: int = 0,
    stderr: str = "",
) -> subprocess.CompletedProcess[str]:
    """subprocess.run dönüş tipi yardımcısı."""
    return subprocess.CompletedProcess(
        args=["timedatectl"],
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
    )


def test_ntp_synced_yes_returns_ok() -> None:
    """timedatectl yes → status ok."""
    with patch.object(healthcheck.subprocess, "run", return_value=_mock_completed("yes\n")):
        result = healthcheck.check_ntp_synced()
    assert result["status"] == "ok"
    assert "NTPSynchronized=yes" in result["detail"]


def test_ntp_synced_no_returns_fail() -> None:
    """timedatectl no → status fail (saat sapmasi alarm timestamp'ini bozar)."""
    with patch.object(healthcheck.subprocess, "run", return_value=_mock_completed("no\n")):
        result = healthcheck.check_ntp_synced()
    assert result["status"] == "fail"
    assert "NTPSynchronized=no" in result["detail"]


def test_ntp_timedatectl_not_found_returns_fail() -> None:
    """timedatectl yoksa (FileNotFoundError) → status fail."""
    with patch.object(healthcheck.subprocess, "run", side_effect=FileNotFoundError):
        result = healthcheck.check_ntp_synced()
    assert result["status"] == "fail"
    assert "timedatectl bulunamadı" in result["detail"]


def test_ntp_timedatectl_nonzero_rc_returns_fail() -> None:
    """timedatectl rc != 0 → status fail (sistem hatasi)."""
    with patch.object(
        healthcheck.subprocess,
        "run",
        return_value=_mock_completed("", returncode=2, stderr="Failed to connect"),
    ):
        result = healthcheck.check_ntp_synced()
    assert result["status"] == "fail"
    assert "rc=2" in result["detail"]


def test_ntp_timedatectl_timeout_returns_fail() -> None:
    """timedatectl timeout → status fail."""
    with patch.object(
        healthcheck.subprocess,
        "run",
        side_effect=subprocess.TimeoutExpired(cmd="timedatectl", timeout=3),
    ):
        result = healthcheck.check_ntp_synced()
    assert result["status"] == "fail"
    assert "yanıt vermedi" in result["detail"]
