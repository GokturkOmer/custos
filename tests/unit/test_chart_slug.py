"""Overview chart slug üretim fonksiyonu unit testleri."""

from __future__ import annotations

from custos.analytics.dashboard.app import slugify_chart_title


def test_slug_lowercases_and_dashes() -> None:
    """Boşluk ve büyük harf → küçük harf + tire."""
    assert slugify_chart_title("HVAC System") == "hvac-system"


def test_slug_replaces_turkish_chars() -> None:
    """Türkçe karakterler ASCII karşılığına çevrilmeli."""
    assert slugify_chart_title("Sirkülasyon Pompası #1") == "sirkulasyon-pompasi-1"


def test_slug_collapses_multiple_separators() -> None:
    """Ardışık özel karakterler tek tire olmalı."""
    assert slugify_chart_title("HVAC / AHU   v2") == "hvac-ahu-v2"


def test_slug_strips_leading_trailing_dashes() -> None:
    """Başta ve sonda tire olmamalı."""
    assert slugify_chart_title("  --foo--  ") == "foo"


def test_slug_empty_input_returns_fallback() -> None:
    """Boş ya da tamamen özel karakter içeren girdi için fallback döner."""
    assert slugify_chart_title("") == "chart"
    assert slugify_chart_title("###") == "chart"


def test_slug_numbers_preserved() -> None:
    """Rakamlar korunmalı."""
    assert slugify_chart_title("Chiller 101") == "chiller-101"
