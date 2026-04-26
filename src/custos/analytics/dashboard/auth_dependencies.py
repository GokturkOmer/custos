"""FastAPI auth dependency'leri — V11-101 (Auth + 2 rol).

Üç katmanlı dependency hiyerarşisi:

- ``get_current_session``    : ham getter, cookie yoksa ``None`` döner
- ``require_session_basic``  : geçerli session zorunlu, must_change_password
                               bypass (sadece /change-password endpoint'i için)
- ``require_operator``       : geçerli session + parola değiştirilmiş + rol
                               operator ya da developer
- ``require_developer``      : geçerli session + parola değiştirilmiş + rol
                               sadece developer (Settings, Audit, CRUD)

Davranış:
- Cookie yoksa veya geçersizse → 303 redirect ``/login``
- ``must_change_password=True`` iken /change-password dışına erişim →
  303 redirect ``/change-password``
- ``require_developer`` sadece operator iken → 403 Forbidden
"""

from __future__ import annotations

from typing import Any

from fastapi import Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse

from custos.shared.auth import SESSION_COOKIE_NAME
from custos.shared.database import DatabaseInterface, Session

# Parola değiştirme akışı endpoint yolu — must_change_password kontrolü
# kendisini hariç tutar (yoksa sonsuz redirect).
_CHANGE_PASSWORD_PATH = "/change-password"


def _get_db(request: Request) -> DatabaseInterface:
    """Request state'inden DB instance'ını döndürür."""
    db: DatabaseInterface | None = getattr(request.app.state, "db", None)
    if db is None:
        raise HTTPException(
            status_code=503,
            detail="Veritabanı bağlantısı yok",
        )
    return db


def _redirect(target: str) -> HTTPException:
    """303 See Other redirect HTTPException oluşturur.

    FastAPI'de dependency'den redirect döndürmek için exception olarak
    fırlatılır. Browser POST sonrası GET'e yönlendirme için 303 uygundur;
    HTMX de Location header'ı ile takip eder.
    """
    return HTTPException(
        status_code=status.HTTP_303_SEE_OTHER,
        headers={"Location": target},
    )


async def get_current_session(request: Request) -> Session | None:
    """Cookie'den session token'ı okur, geçerli session'ı döndürür.

    Süresi dolmuş, kullanıcısı devre dışı veya bilinmeyen token → ``None``.
    Çağıran tarafa hiçbir exception fırlatmaz; karar dependency'lere kalır.
    """
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if not token:
        return None
    db = _get_db(request)
    return await db.get_session_by_token(token)


_CURRENT_SESSION_DEP: Any = Depends(get_current_session)


async def require_session_basic(
    request: Request,
    session: Session | None = _CURRENT_SESSION_DEP,
) -> Session:
    """Geçerli session zorunlu — must_change_password kontrolü YOK.

    Sadece /change-password endpoint'i bu dependency'yi kullanır; diğer
    tüm korumalı route'lar ``require_operator`` ya da ``require_developer``
    çağırmalı (ki must_change_password bayrağı orada zorlanır).
    """
    if session is None:
        # Login sonrası kullanıcı geri dönmek istediği sayfaya gitsin diye
        # mevcut path'i `next` query parametresi olarak geçirebiliriz.
        # Şu an sade tutuyoruz; pilot ihtiyacında eklenir.
        raise _redirect("/login")
    return session


_SESSION_BASIC_DEP: Any = Depends(require_session_basic)


async def require_operator(
    request: Request,
    session: Session = _SESSION_BASIC_DEP,
) -> Session:
    """Operator veya Developer kabul eder.

    Pilot kullanım: müşteri operatörünün yapacağı her şey buradan geçer
    (alarm onay, push subscribe, bakım task complete, asistan, görüntüleme).

    Session ``request.state.session``'a yazılır — template'lerde nav rol
    filtresi için kullanılır.
    """
    if session.must_change_password and request.url.path != _CHANGE_PASSWORD_PATH:
        raise _redirect(_CHANGE_PASSWORD_PATH)
    if session.role not in ("operator", "developer"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Yetersiz rol",
        )
    request.state.session = session
    return session


async def require_developer(
    request: Request,
    session: Session = _SESSION_BASIC_DEP,
) -> Session:
    """Sadece Developer (Göktürk) kabul eder.

    Settings, audit log, kullanıcı yönetimi, threshold/sensor/connection/
    asset CRUD, manuel arşiv tetikleme — hepsi developer-only.
    """
    if session.must_change_password and request.url.path != _CHANGE_PASSWORD_PATH:
        raise _redirect(_CHANGE_PASSWORD_PATH)
    if session.role != "developer":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Bu işlem için Geliştirici yetkisi gereklidir",
        )
    request.state.session = session
    return session


def clear_session_cookie(response: RedirectResponse) -> None:
    """Logout sonrası cookie'yi temizler.

    HttpOnly + SameSite=Lax flag'leri set sırasıyla aynı tutulur ki tarayıcı
    cookie'yi gerçekten sıfırlasın (RFC 6265 §5.3 madde 11).
    """
    response.delete_cookie(
        key=SESSION_COOKIE_NAME,
        httponly=True,
        samesite="lax",
    )
