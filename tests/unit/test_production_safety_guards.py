"""H-3 (29 Nis 2026 denetim) — production safety guard birim testleri.

`_enforce_production_safety_guards` ``CUSTOS_HOST_IP`` set edilmişse
"production mode" kabul eder; bu modda ``CUSTOS_DEV_INSECURE_COOKIE=1``
RuntimeError fırlatır (TLS bypass'a yol açan dev escape hatch'i).

DB veya servis gerektirmez — settings + os.environ üzerinden çalışır.
"""

from __future__ import annotations

import pytest

from custos.__main__ import _enforce_production_safety_guards
from custos.shared.config import settings


def test_production_guard_blocks_insecure_cookie_in_production(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CUSTOS_HOST_IP + CUSTOS_DEV_INSECURE_COOKIE=1 RuntimeError fırlatır."""
    monkeypatch.setattr(settings, "custos_host_ip", "192.168.1.10")
    monkeypatch.setenv("CUSTOS_DEV_INSECURE_COOKIE", "1")

    with pytest.raises(RuntimeError, match="production'da reddedildi"):
        _enforce_production_safety_guards()


def test_production_guard_allows_dev_with_insecure_cookie(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Dev mode (CUSTOS_HOST_IP boş): insecure cookie flag'i izinli."""
    monkeypatch.setattr(settings, "custos_host_ip", "")
    monkeypatch.setenv("CUSTOS_DEV_INSECURE_COOKIE", "1")

    # Exception fırlatmamalı
    _enforce_production_safety_guards()


def test_production_guard_allows_production_without_insecure_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Production mode + insecure flag yoksa: sorun yok (mevcut deploy akışı)."""
    monkeypatch.setattr(settings, "custos_host_ip", "192.168.1.10")
    monkeypatch.delenv("CUSTOS_DEV_INSECURE_COOKIE", raising=False)

    _enforce_production_safety_guards()


def test_production_guard_treats_other_insecure_values_as_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CUSTOS_DEV_INSECURE_COOKIE=0 veya boş = unset davranışı."""
    monkeypatch.setattr(settings, "custos_host_ip", "192.168.1.10")
    monkeypatch.setenv("CUSTOS_DEV_INSECURE_COOKIE", "0")
    _enforce_production_safety_guards()  # Hata yok

    monkeypatch.setenv("CUSTOS_DEV_INSECURE_COOKIE", "")
    _enforce_production_safety_guards()  # Hata yok


def test_allowed_hosts_list_parses_csv(monkeypatch: pytest.MonkeyPatch) -> None:
    """Settings.allowed_hosts_list CSV'i listeye çevirir, boş elemanları atar."""
    monkeypatch.setattr(
        settings,
        "custos_allowed_hosts",
        "192.168.1.10, custos.local ,127.0.0.1",
    )
    assert settings.allowed_hosts_list == [
        "192.168.1.10",
        "custos.local",
        "127.0.0.1",
    ]


def test_allowed_hosts_list_empty_when_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Boş string boş liste döndürür (TrustedHostMiddleware eklenmez)."""
    monkeypatch.setattr(settings, "custos_allowed_hosts", "")
    assert settings.allowed_hosts_list == []

    monkeypatch.setattr(settings, "custos_allowed_hosts", "  ,  ")
    assert settings.allowed_hosts_list == []
