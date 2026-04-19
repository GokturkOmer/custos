"""Maintenance dashboard route testleri.

TestClient ile route smoke test'i — DB ayakta ise 200/303 (redirect),
yoksa 503 döndürmeli. Yok olan kaynak için 404.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from custos.__main__ import app

client = TestClient(app)


def test_maintenance_root_returns_503_or_200() -> None:
    """Ana maintenance sayfası (calendar tab) DB yoksa 503, varsa 200."""
    response = client.get("/dashboard/maintenance")
    assert response.status_code in (200, 503)


def test_maintenance_tab_checklists_returns_503_or_200() -> None:
    """Checklists sekmesi."""
    response = client.get("/dashboard/maintenance?tab=checklists")
    assert response.status_code in (200, 503)


def test_maintenance_tab_history_returns_503_or_200() -> None:
    """Geçmiş sekmesi."""
    response = client.get("/dashboard/maintenance?tab=history")
    assert response.status_code in (200, 503)


def test_maintenance_tab_invalid_falls_back_to_calendar() -> None:
    """Geçersiz tab query → calendar'a düşmeli (200/503)."""
    response = client.get("/dashboard/maintenance?tab=xyz")
    assert response.status_code in (200, 503)


def test_checklist_new_form_returns_503_or_200() -> None:
    """Yeni checklist formu."""
    response = client.get("/dashboard/maintenance/checklists/new")
    assert response.status_code in (200, 503)


def test_checklist_edit_nonexistent_returns_404_or_503() -> None:
    """Var olmayan checklist düzenleme → 404/503."""
    response = client.get("/dashboard/maintenance/checklists/999999/edit")
    assert response.status_code in (404, 503)


def test_checklist_delete_nonexistent_returns_404_or_503() -> None:
    """Var olmayan checklist silme → 404/503."""
    response = client.post(
        "/dashboard/maintenance/checklists/999999/delete",
        follow_redirects=False,
    )
    assert response.status_code in (404, 503)


def test_checklist_create_missing_title_returns_400() -> None:
    """Başlık eksik → 400 veya 503 (DB yoksa)."""
    response = client.post(
        "/dashboard/maintenance/checklists",
        data={"title": "   "},
        follow_redirects=False,
    )
    assert response.status_code in (400, 422, 503)


def test_schedule_new_form_returns_503_or_200() -> None:
    """Yeni schedule formu."""
    response = client.get("/dashboard/maintenance/schedules/new")
    assert response.status_code in (200, 503)


def test_schedule_edit_nonexistent_returns_404_or_503() -> None:
    """Var olmayan schedule düzenleme → 404/503."""
    response = client.get("/dashboard/maintenance/schedules/999999/edit")
    assert response.status_code in (404, 503)


def test_schedule_delete_nonexistent_returns_404_or_503() -> None:
    """Var olmayan schedule silme → 404/503."""
    response = client.post(
        "/dashboard/maintenance/schedules/999999/delete",
        follow_redirects=False,
    )
    assert response.status_code in (404, 503)


def test_schedule_toggle_nonexistent_returns_404_or_503() -> None:
    """Var olmayan schedule toggle → 404/503."""
    response = client.post(
        "/dashboard/maintenance/schedules/999999/toggle",
        follow_redirects=False,
    )
    assert response.status_code in (404, 503)


def test_task_detail_nonexistent_returns_404_or_503() -> None:
    """Var olmayan task detay → 404/503."""
    response = client.get("/dashboard/maintenance/tasks/999999")
    assert response.status_code in (404, 503)


def test_task_complete_nonexistent_returns_404_or_503() -> None:
    """Var olmayan task complete → 404/503."""
    response = client.post(
        "/dashboard/maintenance/tasks/999999/complete",
        follow_redirects=False,
    )
    assert response.status_code in (404, 503)


def test_task_skip_nonexistent_returns_404_or_503() -> None:
    """Var olmayan task skip → 404/503."""
    response = client.post(
        "/dashboard/maintenance/tasks/999999/skip",
        follow_redirects=False,
    )
    assert response.status_code in (404, 503)


def test_maintenance_appears_in_sidebar_via_overview() -> None:
    """Overview sayfası render ediliyorsa sidebar'da 'Maintenance' linki olmalı."""
    response = client.get("/dashboard/overview")
    if response.status_code == 200:
        assert "/dashboard/maintenance" in response.text


def test_alarm_start_checklist_nonexistent_returns_404_or_503() -> None:
    """Var olmayan alarm event için start-checklist → 404/503."""
    response = client.post(
        "/dashboard/alarms/999999/start-checklist",
        follow_redirects=False,
    )
    assert response.status_code in (404, 503)
