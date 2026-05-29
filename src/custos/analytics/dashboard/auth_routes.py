"""Auth route'ları — login, logout, parola değiştirme (V11-101).

URL düzeni (root level, ``/dashboard`` prefix'inden BAĞIMSIZ):

- ``GET  /login``             : login formu (form-only sayfa, nav yok)
- ``POST /login``             : kimlik doğrula, cookie set, /dashboard'a redirect
- ``POST /logout``            : session sil, cookie temizle, /login'e redirect
- ``GET  /change-password``   : parola değiştirme formu (must_change_password=True)
- ``POST /change-password``   : parolayı güncelle, /dashboard'a redirect

Cookie: ``custos_session``, HttpOnly, SameSite=Lax, Secure (P-03 / V11-102
sonrası TLS zorunlu — Caddy reverse proxy 80 → 443 redirect ediyor, plain
HTTP'de cookie set edilmesin diye Secure=True).
"""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import structlog
from fastapi import APIRouter, Depends, Form, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from custos.analytics.dashboard.auth_dependencies import (
    _get_db,
    require_operator,
    require_session_basic,
)
from custos.shared.auth import (
    SESSION_COOKIE_NAME,
    SESSION_TTL_SECONDS,
    create_session_token,
    hash_password,
    verify_password,
)
from custos.shared.database import AuditLogEntry, Session

logger = structlog.get_logger(logger_name="auth_routes")

# B008 yasağını çözmek için module-level singleton — app.py'daki
# _ASSISTANT_RETRIEVER_DEP ile aynı pattern.
_SESSION_BASIC_DEP: Any = Depends(require_session_basic)
# forward_auth ucu (GET /auth/validate) için operator+ koruması.
_OPERATOR_DEP: Any = Depends(require_operator)

# Aynı template dizini app.py ile paylaşılır — tek Jinja2Templates örneği
# yerine dashboard modülünün dizinini import etmek daha temiz olurdu, ama
# sade bir paralel örnekleme runtime'da pahalı değil.
_TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

auth_router = APIRouter(tags=["auth"])

# Login sonrası varsayılan iniş sayfası
_POST_LOGIN_TARGET = "/dashboard/overview"

# PP-06 (29 Nis 2026): IP-bazlı login rate limit. Pencere boyunca yapılan
# başarısız denemeler audit_log'tan sayılır; eşiği geçen IP 'rate_limited'
# error ile reddedilir. Pencere TTL geçince sayım sıfırlanır (sliding window).
LOGIN_RATE_LIMIT_WINDOW_MINUTES = 15
LOGIN_RATE_LIMIT_MAX_ATTEMPTS = 5

# H-2 (29 Nis 2026 denetim): Username-bazlı rate limit — IP-bazlı sayımı
# tamamlar. Dağıtık brute-force (her istek farklı IP) bu eşikle yakalanır.
# Pencere daha geniş (30 dk) çünkü tek hesaba odaklı saldırılar düşük
# tempolu olabilir. Eşik biraz yüksek (10) çünkü meşru kullanıcı
# parolasını birkaç kez yanlış girebilir; saldırı imzası 10+ tek hesap.
LOGIN_RATE_LIMIT_USERNAME_WINDOW_MINUTES = 30
LOGIN_RATE_LIMIT_USERNAME_MAX_ATTEMPTS = 10

# --- Asistan forward_auth sözleşmesi (karar A) ---
# Caddy `/assistant/*` isteğini geçirmeden önce `GET /auth/validate`'i
# forward_auth ile çağırır; 200 yanıtında bu header üretilir. Asistan servisi
# (custos.assistant.middleware.parse_custos_user_header) bu header'ı çözer.
# Header adı + base64url(JSON) şeması iki süreç arasındaki SÖZLEŞMEDİR — asistan
# ayrı paket/süreç olduğu (import paylaşımı mimari kurala aykırı) için kod
# paylaşılmaz, sözleşme paylaşılır. Değişirse İKİSİ birlikte güncellenmeli.
CUSTOS_USER_HEADER = "X-Custos-User"


def encode_custos_user_header(*, user_id: int, username: str, role: str) -> str:
    """Oturum kullanıcısını `X-Custos-User` header değerine kodlar.

    Şema: ``base64url(JSON {"id": int, "username": str, "role": str})``.
    base64url ZORUNLU — Türkçe kullanıcı adları (ş/ğ/ı/ö/ü/ç) non-ASCII; HTTP
    header değerleri pratikte ASCII/latin-1 ile sınırlı, base64url ham JSON'u
    güvenle ASCII'ye taşır.

    ``id`` alanı oturumun KULLANICI kimliğidir (``Session.user_id``), session
    satır id'si (``Session.id``) DEĞİL — asistan ``queries_log.user_id`` bunu
    metrik amaçlı saklar. Üretilen değeri
    ``custos.assistant.middleware.parse_custos_user_header`` çözer (padding'li ve
    padding'siz ikisini de tolere eder; biz standart ``urlsafe_b64encode`` ile
    padding'i koruruz).
    """
    payload = json.dumps(
        {"id": user_id, "username": username, "role": role},
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii")


def _set_session_cookie(response: RedirectResponse, token: str) -> None:
    """Session cookie'yi response'a ekler.

    HttpOnly: JS erişemez (XSS savunması). SameSite=Lax: cross-site POST
    engellenir (CSRF temel savunması). Secure: P-03 (V11-102 TLS) sonrası
    True — cookie sadece HTTPS üzerinden gönderilir; plain HTTP isteğine
    iliştirilmez (LAN sniff koruması).

    Geliştirme ortamında HTTPS yoksa (lokal `python -m custos.analytics`
    gibi), Secure cookie tarayıcı tarafından gönderilmez ve login akışı
    bozulur — `CUSTOS_DEV_INSECURE_COOKIE=1` çevresel değişkeni Secure
    flag'ini kapatmak için escape hatch sunar.

    Max-Age 12 saat — TTL sabitiyle senkron.
    """
    # Geliştirme escape hatch: CUSTOS_DEV_INSECURE_COOKIE=1 ise Secure kapanır.
    # Pilot deploy'da set edilmez (default güvenli).
    import os

    secure_flag = os.environ.get("CUSTOS_DEV_INSECURE_COOKIE", "").strip() != "1"
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        max_age=SESSION_TTL_SECONDS,
        httponly=True,
        samesite="lax",
        secure=secure_flag,
        path="/",
    )


# --- Login ---


@auth_router.get("/login", response_class=HTMLResponse)
async def login_form(
    request: Request,
    error: str | None = None,
    next: str | None = None,  # noqa: A002
) -> HTMLResponse:
    """Login formunu render eder. ``error`` ile hata mesajı gösterilebilir."""
    return templates.TemplateResponse(
        request,
        "pages/login.html",
        {"error": error, "next": next, "page_title": "Giriş"},
    )


@auth_router.post("/login")
async def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    next: str = Form(default=_POST_LOGIN_TARGET),  # noqa: A002
) -> RedirectResponse:
    """Kimlik doğrulama: bcrypt karşılaştırma + session oluştur + cookie set."""
    db = _get_db(request)
    ip = request.client.host if request.client else ""

    # PP-06: IP-bazlı brute-force koruması. audit_log'taki son
    # LOGIN_RATE_LIMIT_WINDOW_MINUTES içindeki başarısız deneme sayısı
    # eşiği aşmışsa giriş hesaplaması bile yapılmaz.
    now = datetime.now(UTC)
    since_ip = now - timedelta(minutes=LOGIN_RATE_LIMIT_WINDOW_MINUTES)
    failed_count = await db.count_recent_failed_logins(ip, since_ip)
    if failed_count >= LOGIN_RATE_LIMIT_MAX_ATTEMPTS:
        await logger.awarning(
            "Login rate limit aşıldı (IP)",
            ip=ip,
            failed_count=failed_count,
            window_minutes=LOGIN_RATE_LIMIT_WINDOW_MINUTES,
        )
        return RedirectResponse(
            url="/login?error=rate_limited",
            status_code=303,
        )

    # H-2 (29 Nis 2026 denetim): Username-bazlı rate limit. IP sayımı
    # dağıtık saldırıyı yakalayamaz (her istek farklı kaynaktan), bu yüzden
    # tek hesaba odaklı brute-force için ayrı bir pencere uygulanır.
    since_user = now - timedelta(minutes=LOGIN_RATE_LIMIT_USERNAME_WINDOW_MINUTES)
    user_failed_count = await db.count_recent_failed_logins_by_username(
        username.strip(),
        since_user,
    )
    if user_failed_count >= LOGIN_RATE_LIMIT_USERNAME_MAX_ATTEMPTS:
        await logger.awarning(
            "Login rate limit aşıldı (username)",
            username=username,
            failed_count=user_failed_count,
            window_minutes=LOGIN_RATE_LIMIT_USERNAME_WINDOW_MINUTES,
        )
        return RedirectResponse(
            url="/login?error=rate_limited",
            status_code=303,
        )

    user = await db.get_user_by_username(username.strip())
    pw_hash = await db.get_user_password_hash(username.strip())

    async def _audit_failed(reason: str, user_id: str = "") -> None:
        """Başarısız login'i audit_log'a yazar (rate limit sayımı için)."""
        await db.insert_audit_log(
            AuditLogEntry(
                category="auth",
                action="login_failed",
                entity_type="user",
                entity_id=user_id,
                detail=f"ip={ip} user={username} reason={reason}",
            )
        )

    # Sabit-zaman karşılaştırma için kullanıcı yoksa da bir bcrypt karşılaştırması
    # yapıyormuş gibi davranabilirdik; pilot ihtiyacında basit tutuyoruz.
    if user is None or pw_hash is None or not user.enabled:
        await logger.awarning(
            "Login başarısız: kullanıcı bulunamadı veya devre dışı",
            username=username,
        )
        await _audit_failed(
            reason="invalid_user" if user is None or pw_hash is None else "disabled",
            user_id=str(user.id) if user is not None else "",
        )
        return RedirectResponse(
            url="/login?error=invalid",
            status_code=303,
        )
    if not verify_password(password, pw_hash):
        await logger.awarning("Login başarısız: hatalı parola", username=username)
        await _audit_failed(reason="invalid_password", user_id=str(user.id))
        return RedirectResponse(
            url="/login?error=invalid",
            status_code=303,
        )

    # Session oluştur
    token = create_session_token()
    expires_at = datetime.now(UTC) + timedelta(seconds=SESSION_TTL_SECONDS)
    ip = request.client.host if request.client else ""
    ua = request.headers.get("user-agent", "")[:512]
    await db.create_session(
        user_id=user.id,
        token=token,
        expires_at=expires_at,
        ip_addr=ip,
        user_agent=ua,
    )
    await db.update_last_login(user.id)
    await db.insert_audit_log(
        AuditLogEntry(
            category="auth",
            action="login",
            entity_type="user",
            entity_id=str(user.id),
            detail=f"{user.username} ({user.role})",
        )
    )

    # must_change_password ise change-password'e zorla; yoksa next ya da varsayılan
    target = "/change-password" if user.must_change_password else next
    if not target.startswith("/"):
        target = _POST_LOGIN_TARGET
    response = RedirectResponse(url=target, status_code=303)
    _set_session_cookie(response, token)
    return response


# --- Logout ---


@auth_router.post("/logout")
async def logout_submit(request: Request) -> RedirectResponse:
    """Session'ı DB'den siler ve cookie'yi temizler."""
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if token:
        db = _get_db(request)
        session = await db.get_session_by_token(token)
        await db.delete_session(token)
        if session is not None:
            await db.insert_audit_log(
                AuditLogEntry(
                    category="auth",
                    action="logout",
                    entity_type="user",
                    entity_id=str(session.user_id),
                    detail=session.username,
                )
            )

    response = RedirectResponse(url="/login", status_code=303)
    # Set ile aynı flag'lerle sil — RFC 6265 §5.3 madde 11 gereği tarayıcının
    # cookie'yi gerçekten unutması için. Secure flag'i .env override'ı ile
    # senkron tutuyoruz.
    import os

    secure_flag = os.environ.get("CUSTOS_DEV_INSECURE_COOKIE", "").strip() != "1"
    response.delete_cookie(
        key=SESSION_COOKIE_NAME,
        httponly=True,
        samesite="lax",
        secure=secure_flag,
        path="/",
    )
    return response


# --- Parola değiştirme (ilk giriş + opsiyonel) ---


@auth_router.get("/change-password", response_class=HTMLResponse)
async def change_password_form(
    request: Request,
    session: Session = _SESSION_BASIC_DEP,
    error: str | None = None,
) -> HTMLResponse:
    """Parola değiştirme formu."""
    return templates.TemplateResponse(
        request,
        "pages/change_password.html",
        {
            "error": error,
            "must_change": session.must_change_password,
            "page_title": "Parola Değiştir",
            "username": session.username,
        },
    )


@auth_router.post("/change-password")
async def change_password_submit(
    request: Request,
    session: Session = _SESSION_BASIC_DEP,
    current_password: str = Form(...),
    new_password: str = Form(...),
    new_password_confirm: str = Form(...),
) -> RedirectResponse:
    """Parolayı günceller. Mevcut parolayı doğrular, yeni parola en az 8 karakter."""
    db = _get_db(request)

    if new_password != new_password_confirm:
        return RedirectResponse(
            url="/change-password?error=mismatch",
            status_code=303,
        )
    if len(new_password) < 8:
        return RedirectResponse(
            url="/change-password?error=tooshort",
            status_code=303,
        )

    current_hash = await db.get_user_password_hash(session.username)
    if current_hash is None or not verify_password(
        current_password,
        current_hash,
    ):
        return RedirectResponse(
            url="/change-password?error=invalid",
            status_code=303,
        )

    new_hash = hash_password(new_password)
    await db.update_user_password(session.user_id, new_hash)
    await db.insert_audit_log(
        AuditLogEntry(
            category="auth",
            action="password_changed",
            entity_type="user",
            entity_id=str(session.user_id),
            detail=session.username,
        )
    )

    return RedirectResponse(url=_POST_LOGIN_TARGET, status_code=303)


# --- forward_auth ucu (asistan servisi — karar A) ---


@auth_router.get("/auth/validate")
async def auth_validate(
    response: Response,
    session: Session = _OPERATOR_DEP,
) -> dict[str, bool]:
    """Caddy forward_auth doğrulama ucu — `/assistant/*` öncesi yetki kapısı.

    Caddy `/assistant/*` isteğini asistan servisine (8001) geçirmeden ÖNCE bu
    ucu `require_operator` ile çağırır. Davranış:

    - Geçerli operator/developer session → **200 + `X-Custos-User` response
      header** (``base64url(JSON {id,username,role})``). Caddy header'ı kopyalar
      (``copy_headers``) ve asistan isteğine ekler; asistan middleware'i
      ``request.state.user``'a çözer.
    - Session yok/geçersiz → ``require_operator`` 303 ``/login`` fırlatır; Caddy
      bunu istemciye iletir (yetkisiz → login'e yönlendirilir).
    - operator/developer dışı rol → 403; Caddy iletir.

    Yanıt gövdesi minimaldir: forward_auth yalnızca status'a bakar (2xx → geçir),
    gövdeyi kullanmaz. ``id`` alanı ``Session.user_id``'dir (kullanıcı kimliği),
    ``Session.id`` (session satırı) DEĞİL.
    """
    response.headers[CUSTOS_USER_HEADER] = encode_custos_user_header(
        user_id=session.user_id,
        username=session.username,
        role=session.role,
    )
    return {"ok": True}
