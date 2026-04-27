"""TLS deploy artifact'leri smoke testleri (V11-102 / P-03).

Caddy reverse proxy + self-signed cert kurulumu için repo içindeki
şablon ve script'lerin temel sözleşmesini doğrular. Çalışan bir caddy
veya systemd gerekmez — sadece dosya içeriği kontrol edilir.

Manuel runtime testleri (pilot mini PC'de):
    curl -k https://${CUSTOS_HOST_IP}/dashboard/   → 200
    curl -I http://${CUSTOS_HOST_IP}/dashboard/    → 301 https
    Set-Cookie: custos_session=...; Secure; HttpOnly; SameSite=Lax
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_caddyfile_template_exists() -> None:
    """deploy/Caddyfile.template repo'da bulunmalı."""
    caddyfile = REPO_ROOT / "deploy" / "Caddyfile.template"
    assert caddyfile.exists(), f"Caddyfile.template eksik: {caddyfile}"


def test_caddyfile_template_has_host_placeholder() -> None:
    """Caddyfile.template ${CUSTOS_HOST_IP} placeholder'ını içermeli — setup.sh subst eder."""
    caddyfile = REPO_ROOT / "deploy" / "Caddyfile.template"
    content = caddyfile.read_text(encoding="utf-8")
    assert "${CUSTOS_HOST_IP}" in content, "CUSTOS_HOST_IP placeholder eksik"


def test_caddyfile_template_https_redirect_and_proxy() -> None:
    """Caddyfile.template TLS termination + HTTP→HTTPS redirect içermeli."""
    caddyfile = REPO_ROOT / "deploy" / "Caddyfile.template"
    content = caddyfile.read_text(encoding="utf-8")
    # TLS cert path'i
    assert "/etc/custos/tls/cert.pem" in content
    assert "/etc/custos/tls/key.pem" in content
    # uvicorn'a reverse proxy
    assert "reverse_proxy 127.0.0.1:8000" in content
    # HTTP → HTTPS redirect bloğu
    assert "redir https://" in content
    # Güvenlik header'ları
    assert "Strict-Transport-Security" in content
    assert "X-Content-Type-Options" in content
    assert "X-Frame-Options" in content


def test_generate_tls_cert_script_exists() -> None:
    """scripts/generate_tls_cert.sh repo'da olmalı."""
    script = REPO_ROOT / "scripts" / "generate_tls_cert.sh"
    assert script.exists(), f"generate_tls_cert.sh eksik: {script}"


def test_generate_tls_cert_script_has_idempotent_check() -> None:
    """Cert üretici idempotent — mevcut cert varsa atlamalı, --force ile yenilemeli."""
    script = REPO_ROOT / "scripts" / "generate_tls_cert.sh"
    content = script.read_text(encoding="utf-8")
    # CN ve SAN her ikisini içermeli (modern browser SAN şart)
    assert "subjectAltName" in content
    # IP-bazlı SAN (statik IP, K8 mDNS değil)
    assert "IP:${HOST_IP}" in content
    # Idempotent flag
    assert "--force" in content
    # Cert dizini doğru
    assert "/etc/custos/tls" in content


def test_setup_sh_has_tls_step() -> None:
    """deploy/setup.sh TLS + Caddy adımını içermeli (P-03)."""
    setup = REPO_ROOT / "deploy" / "setup.sh"
    content = setup.read_text(encoding="utf-8")
    # Yeni adım numarası — P-06 ile setup.sh adım sayısı 17'ye çıktı
    assert "[12/17] TLS sertifikasi" in content
    # Caddy paketi kurulumu
    assert "apt-get install -y -qq caddy" in content
    # Caddyfile substitution
    assert "Caddyfile.template" in content
    # systemctl enable caddy
    assert "systemctl enable --now caddy" in content


def test_auth_routes_cookie_secure_flag() -> None:
    """auth_routes.py Set-Cookie sırasında Secure=True (default) olmalı."""
    auth_routes = REPO_ROOT / "src" / "custos" / "analytics" / "dashboard" / "auth_routes.py"
    content = auth_routes.read_text(encoding="utf-8")
    # secure=False artık olmamalı (escape hatch dışında)
    # Sadece env override (CUSTOS_DEV_INSECURE_COOKIE=1) ile False olmalı.
    assert "secure=secure_flag" in content
    assert "CUSTOS_DEV_INSECURE_COOKIE" in content


def test_env_example_has_host_ip_placeholder() -> None:
    """.env.example CUSTOS_HOST_IP yorumu içermeli (TLS için)."""
    env_ex = REPO_ROOT / ".env.example"
    content = env_ex.read_text(encoding="utf-8")
    assert "CUSTOS_HOST_IP" in content
