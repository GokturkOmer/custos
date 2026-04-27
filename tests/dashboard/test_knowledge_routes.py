"""Knowledge Base dashboard route testleri (V11-110 / P-07).

KB hibrit yapı: git docs (read-only) + local docs (CRUD). Tüm route'lar
developer-only — operator override edilirse 403 döner. Lokal dizin
`tmp_path` üstüne `monkeypatch` ile yönlendirilir; production yolu
(`/var/custos/knowledge/local`) test makinesinde olmasa bile çalışır.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException, status
from fastapi.testclient import TestClient

from custos.__main__ import app
from custos.analytics.dashboard.auth_dependencies import (
    require_developer,
    require_operator,
)
from custos.shared.database import Session

client = TestClient(app)

_FAR_FUTURE = datetime(2099, 1, 1, tzinfo=UTC)
_OPERATOR_SESSION = Session(
    id=2,
    user_id=42,
    username="test_operator",
    role="operator",
    enabled=True,
    must_change_password=False,
    expires_at=_FAR_FUTURE,
)


def _fake_op_session() -> Session:
    """`require_operator` override için — iki rolü de kabul ediyor."""
    return _OPERATOR_SESSION


def _operator_blocked_for_developer() -> Session:
    """`require_developer` override için — gerçek prod davranışını taklit
    eder: operator session geldiğinde 403 fırlatır.

    FastAPI ``dependency_overrides`` mekanizması orijinal fonksiyonu
    tamamen yerinden eder; bu yüzden role check'i de override fonksiyonu
    yapmak zorunda. Aksi halde "operator developer-only sayfaya
    erişebiliyor mu?" testi anlamsız hale gelir.
    """
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Bu işlem için Geliştirici yetkisi gereklidir",
    )


@pytest.fixture
def _kb_local_tmp(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[Path]:
    """Lokal KB dizinini geçici tmp_path'e yönlendirir.

    Production default `/var/custos/knowledge/local` test makinesinde
    yoksa veya yetki yoksa fail eder. Settings'in field'ını override
    ederek dev/CI'da dosya I/O'nun tmp_path'te kalmasını sağlıyoruz.
    """
    from custos.shared.config import settings as runtime_settings

    local_dir = tmp_path / "kb_local"
    monkeypatch.setattr(
        runtime_settings,
        "custos_assistant_knowledge_local_dir",
        str(local_dir),
        raising=False,
    )
    yield local_dir


@pytest.fixture
def _audit_db(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """app.state.db için audit_log destekleyen mock — POST endpoint'leri için."""
    mock = MagicMock()
    mock.insert_audit_log = AsyncMock()
    monkeypatch.setattr(app.state, "db", mock, raising=False)
    return mock


def test_knowledge_page_developer_returns_200() -> None:
    """GET /dashboard/knowledge → developer için sayfa render."""
    response = client.get("/dashboard/knowledge")
    assert response.status_code == 200
    # Sayfa iskelet
    assert "Knowledge Base" in response.text
    assert "Git Dokümanları" in response.text
    assert "Lokal Dokümanlar" in response.text


def test_knowledge_page_operator_returns_403() -> None:
    """Operator rolü developer-only sayfaya erişemez (403)."""
    app.dependency_overrides[require_operator] = _fake_op_session
    app.dependency_overrides[require_developer] = _operator_blocked_for_developer
    try:
        response = client.get("/dashboard/knowledge")
    finally:
        app.dependency_overrides.pop(require_operator, None)
        app.dependency_overrides.pop(require_developer, None)
    assert response.status_code == 403


def test_local_doc_create_edit_delete_roundtrip(
    _kb_local_tmp: Path,
    _audit_db: MagicMock,
) -> None:
    """Yeni doküman → düzenle → sil. Her adımda dosya sistemi + audit log."""
    # 1. Create
    create_resp = client.post(
        "/dashboard/knowledge/local/new",
        data={
            "slug": "torunlar_chiller",
            "body": "---\ntitle: Torunlar Chiller\ncategory: ariza\n---\n\n## Saha\n\nÖzel not.\n",
        },
        follow_redirects=False,
    )
    assert create_resp.status_code == 303
    assert create_resp.headers["location"] == "/dashboard/knowledge?ok=created"
    target = _kb_local_tmp / "torunlar_chiller.md"
    assert target.exists()
    assert "Torunlar Chiller" in target.read_text(encoding="utf-8")

    # 2. Edit
    edit_body = (
        "---\ntitle: Torunlar Chiller (rev 2)\ncategory: ariza\n---"
        "\n\n## Saha\n\nGüncellenmiş.\n"
    )
    edit_resp = client.post(
        "/dashboard/knowledge/local/edit/torunlar_chiller",
        data={"body": edit_body},
        follow_redirects=False,
    )
    assert edit_resp.status_code == 303
    assert edit_resp.headers["location"] == "/dashboard/knowledge?ok=updated"
    assert "rev 2" in target.read_text(encoding="utf-8")

    # 3. Delete
    delete_resp = client.post(
        "/dashboard/knowledge/local/delete/torunlar_chiller",
        follow_redirects=False,
    )
    assert delete_resp.status_code == 303
    assert delete_resp.headers["location"] == "/dashboard/knowledge?ok=deleted"
    assert not target.exists()

    # Audit log her CRUD adımında çağrılmış olmalı (3 kez)
    assert _audit_db.insert_audit_log.await_count == 3


def test_invalid_slug_rejected_in_create(
    _kb_local_tmp: Path,
    _audit_db: MagicMock,
) -> None:
    """Path traversal / boşluk / büyük harf içeren slug 303 + invalid_slug error."""
    response = client.post(
        "/dashboard/knowledge/local/new",
        data={
            "slug": "../etc/passwd",
            "body": "x",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "error=invalid_slug" in response.headers["location"]
    # Hiçbir dosya tmp dizinine yazılmamalı
    assert list(_kb_local_tmp.glob("**/*")) == []
    # Audit log da çağrılmamalı (çünkü redirect erken dönüyor)
    _audit_db.insert_audit_log.assert_not_awaited()


def test_duplicate_slug_rejected_in_create(
    _kb_local_tmp: Path,
    _audit_db: MagicMock,
) -> None:
    """Aynı slug ile ikinci yaratım 303 + duplicate error."""
    _kb_local_tmp.mkdir(parents=True, exist_ok=True)
    (_kb_local_tmp / "var.md").write_text("eski içerik", encoding="utf-8")
    response = client.post(
        "/dashboard/knowledge/local/new",
        data={"slug": "var", "body": "yeni içerik"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "error=duplicate" in response.headers["location"]
    # Eski içerik korunmuş olmalı
    assert (_kb_local_tmp / "var.md").read_text(encoding="utf-8") == "eski içerik"


def test_rebuild_index_operator_403(_audit_db: MagicMock) -> None:
    """Operator rolü indeks rebuild edemez."""
    app.dependency_overrides[require_operator] = _fake_op_session
    app.dependency_overrides[require_developer] = _operator_blocked_for_developer
    try:
        response = client.post(
            "/dashboard/knowledge/rebuild-index",
            follow_redirects=False,
        )
    finally:
        app.dependency_overrides.pop(require_operator, None)
        app.dependency_overrides.pop(require_developer, None)
    assert response.status_code == 403
    _audit_db.insert_audit_log.assert_not_awaited()
