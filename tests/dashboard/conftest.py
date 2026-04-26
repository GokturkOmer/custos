"""Dashboard test paketi conftest — V11-101 sonrası auth bypass.

V11-101 ile dashboard route'larına ``require_operator`` / ``require_developer``
dependency'leri eklendi. Bu eklemeden önce yazılmış 86 dashboard testi
auth-naive — her biri için login fixture eklemek paket scope'unu aşar.

Bu conftest, **test_auth.py hariç** tüm dashboard testleri için FastAPI
``dependency_overrides`` mekanizmasıyla auth dependency'lerini bypass eder
(sahte developer session döndürür). Üretim app'i etkilenmez; override
yalnızca pytest çalıştığında aktiftir.

test_auth.py kendi auth akışını uçtan uca test eder, bu yüzden override
edilmez.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from custos.__main__ import app
from custos.analytics.dashboard.auth_dependencies import (
    require_developer,
    require_operator,
)
from custos.shared.database import Session

_FAR_FUTURE = datetime(2099, 1, 1, tzinfo=UTC)

# Sahte developer session — test'lerde tüm rol kontrollerini geçer.
_TEST_DEV_SESSION = Session(
    id=1,
    user_id=1,
    username="test_dev",
    role="developer",
    enabled=True,
    must_change_password=False,
    expires_at=_FAR_FUTURE,
)


def _fake_dev_session() -> Session:
    """Test override için sahte developer session."""
    return _TEST_DEV_SESSION


@pytest.fixture(autouse=True)
def _bypass_auth_for_legacy_tests(
    request: pytest.FixtureRequest,
) -> object:
    """V11-101 öncesi testler için auth bypass.

    test_auth.py auth akışının kendisini test ettiği için bu override
    uygulanmaz; o dosya gerçek dependency'leri çağırır.
    """
    if "test_auth" in request.node.fspath.basename:
        yield
        return

    app.dependency_overrides[require_operator] = _fake_dev_session
    app.dependency_overrides[require_developer] = _fake_dev_session
    try:
        yield
    finally:
        app.dependency_overrides.pop(require_operator, None)
        app.dependency_overrides.pop(require_developer, None)
