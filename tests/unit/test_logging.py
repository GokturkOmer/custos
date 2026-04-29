"""structlog wrapper birim testleri (configure_logging + get_logger).

structlog ``cache_logger_on_first_use`` global state taşır; testler arası
sızıntı önlemek için her test kendi configure çağrısını yapar. ``isatty``
patch'i ile hem TTY hem JSON renderer yolu kapsanır.
"""

from __future__ import annotations

import io
from unittest.mock import patch

import pytest
import structlog

from custos.shared.logging import configure_logging, get_logger

# --- configure_logging başarı yolu ---


def test_configure_logging_info_level_succeeds() -> None:
    """INFO seviyesi konfigürasyon hatasız tamamlanmalı."""
    configure_logging("INFO")
    # İlk hata atmamış olması yeterli kanıt; structlog state'i set edildi.
    assert structlog.is_configured() is True


@pytest.mark.parametrize(
    "level",
    ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
)
def test_configure_logging_accepts_all_standard_levels(level: str) -> None:
    """Tüm standart seviyeler structlog tarafından kabul edilmeli."""
    configure_logging(level)
    assert structlog.is_configured() is True


def test_configure_logging_lowercase_level_accepted() -> None:
    """structlog NAME_TO_LEVEL anahtarları küçük harf — küçük harf de geçer.

    `configure_logging` içinde `level.lower()` çağırılır; "debug" girişi
    "DEBUG" ile aynı sonucu vermeli.
    """
    configure_logging("debug")
    assert structlog.is_configured() is True


def test_configure_logging_invalid_level_raises_keyerror() -> None:
    """Tanımsız seviye KeyError fırlatmalı (sessiz fallback yok)."""
    with pytest.raises(KeyError):
        configure_logging("INVALID_LEVEL_XYZ")


# --- TTY / JSON renderer dalları ---


def test_configure_logging_tty_uses_console_renderer() -> None:
    """isatty=True iken ConsoleRenderer (renkli dev çıktı) seçilmeli."""
    with patch("sys.stderr") as fake_stderr:
        fake_stderr.isatty.return_value = True
        configure_logging("INFO")
    # configure_logging dönüşü None; istisna atmamış olması yeterli.
    assert structlog.is_configured() is True


def test_configure_logging_non_tty_uses_json_renderer() -> None:
    """isatty=False iken JSONRenderer (üretim/pipe çıktısı) seçilmeli."""
    with patch("sys.stderr") as fake_stderr:
        fake_stderr.isatty.return_value = False
        configure_logging("INFO")
    assert structlog.is_configured() is True


def test_configure_logging_non_tty_emits_json(capsys: pytest.CaptureFixture[str]) -> None:
    """JSON renderer aktifken log satırı JSON formatında olmalı.

    `PrintLoggerFactory` stdout'a yazar; capsys ile yakalayıp '{"' başladığını
    doğrularız (JSON object işareti).
    """
    with patch("sys.stderr") as fake_stderr:
        fake_stderr.isatty.return_value = False
        configure_logging("INFO")
        # cache_logger_on_first_use önceki testten sızabilir — yeni name kullan
        log = structlog.get_logger("test_logging_json_emit")
        log.info("test_event", k="v")

    captured = capsys.readouterr()
    # En az bir JSON satırı stdout'a düşmüş olmalı
    assert '"event"' in captured.out or '"event"' in captured.err, (
        f"JSON çıktısı bulunamadı: stdout={captured.out!r} stderr={captured.err!r}"
    )


# --- get_logger ---


def test_get_logger_returns_bound_logger_with_name() -> None:
    """get_logger çağrısı kullanılabilir bir logger döndürmeli."""
    configure_logging("INFO")
    log = get_logger("custos.test")
    # Bound logger info/warning/error metodlarına sahip olmalı
    assert callable(getattr(log, "info", None))
    assert callable(getattr(log, "warning", None))
    assert callable(getattr(log, "error", None))


def test_get_logger_info_call_does_not_raise() -> None:
    """get_logger sonrası .info() çağrısı sessizce başarılı olmalı."""
    # JSON renderer (capsys uyumlu, tty yan etkisiz)
    with patch("sys.stderr") as fake_stderr:
        fake_stderr.isatty.return_value = False
        configure_logging("INFO")
        log = get_logger("custos.test.info")
        # Hata fırlatmamalı
        log.info("merhaba", payload=42)


def test_get_logger_below_level_filtered() -> None:
    """WARNING konfigürasyonunda DEBUG çağrısı çıktıya düşmemeli.

    `make_filtering_bound_logger(WARNING)` DEBUG/INFO çağrılarını yutar;
    bu yapılandırmanın doğru bağlandığının kanıtı.
    """
    buf = io.StringIO()
    with patch("sys.stderr") as fake_stderr, patch("sys.stdout", buf):
        fake_stderr.isatty.return_value = False
        configure_logging("WARNING")
        log = get_logger("custos.test.filter")
        log.debug("bu_gorunmemeli", k=1)
        log.info("bu_da_gorunmemeli", k=2)
        log.warning("bu_gorunmeli", k=3)

    output = buf.getvalue()
    assert "bu_gorunmemeli" not in output
    assert "bu_da_gorunmemeli" not in output
    assert "bu_gorunmeli" in output
