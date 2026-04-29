# Güvenlik Politikası

## Desteklenen Sürümler

Custos endüstriyel saha kurulumu için yapılmış lokal bir edge izleme ürünüdür.
Güvenlik yamaları yalnızca aktif sürüm serisine uygulanır.

| Sürüm | Destek Durumu |
| ----- | ------------- |
| 1.x.x | ✅ Aktif destek (güvenlik yamaları) |
| < 1.0 | ❌ Destek dışı |

## Güvenlik Açığı Bildirimi

Custos'ta bir güvenlik açığı keşfederseniz lütfen **public issue açmayın**.
Açığın sahalarda istismar edilmesini engellemek için bildirim koordineli yapılır.

Bunun yerine doğrudan e-posta ile iletişime geçin:

- **E-posta:** omerarli63@gmail.com
- **Yanıt süresi:** 72 saat içinde ilk yanıt; kritik (uzaktan kod çalıştırma, yetki yükseltme, kimlik doğrulama bypass) açıklar için 24 saat.

Lütfen bildirimde şunları içerin:

- Açığın tipi (örn: SQL injection, XSS, auth bypass, SSRF, path traversal)
- Etkilenen dosya / modül (varsa: `src/custos/...` yolu)
- Yeniden üretim adımları (PoC kod parçası, HTTP isteği, log çıktısı)
- Olası etki değerlendirmesi (veri sızıntısı, yetki yükseltme, hizmet kesintisi)
- Custos sürüm numarası ve deploy ortamı (pilot mini PC / geliştirme / endurance)

## Güvenlik Otomasyonu

Bu proje aşağıdaki otomatik kontrollere sahiptir:

- **pip-audit** (haftalık, GitHub Actions) — Bağımlılık CVE taraması.
- **Dependabot** (haftalık) — Bağımlılık sürüm güncellemeleri.
- **Pre-commit hooks** — `ruff check` + `mypy --strict` + mimari kural denetimi
  (`scripts/architecture_check.py`, 11 kural).
- **Bandit** (manuel) — Statik kod güvenlik taraması.
- **Mimari kural denetimi** — Critical loop'a ML kütüphanesi eklenmesi, Modbus
  yazma fonksiyonu kullanımı ve modüllerden doğrudan SQL çağrısı CI tarafından
  reddedilir.

## Sorumlu Açıklama

- Açığın doğrulanması ve düzeltme süresi: kritik için 7 gün, yüksek için 30 gün, orta için 90 gün, düşük için en fazla 180 gün.
- Düzeltmeden önce açık public yapılmaz.
- Düzeltme yayınlandıktan sonra `CHANGELOG.md` "Yayınlanmamış" → "Düzeltildi" bölümünde anonim ya da kredi atılmış olarak listelenir.
- Bildiren araştırmacı CVE atamasında kredi alır (istemezse anonim kalır).

## Pilot Saha Konfigürasyonu Güvenliği

Pilot deploy'da (`deploy/setup.sh` ile kurulan mini PC) şu kontroller varsayılan olarak aktiftir:

- **TLS** — Caddy reverse proxy + self-signed sertifika (Paket 03).
- **bcrypt parola hash** (12 round, V11-101).
- **IP + kullanıcı adı bazlı login rate limit** (PP-06 + G-A).
- **HttpOnly + Secure + SameSite=Lax session cookie**.
- **TrustedHostMiddleware** — Host header injection koruması.
- **CSP + Permissions-Policy + COOP + CORP başlıkları** (PP-07).
- **UFW host firewall** — 22 / 80 / 443 / 5353 allow, 8000 deny.
- **PostgreSQL localhost-only listen + dual user** (admin migration / app runtime, Paket 02).
- **SSH password auth disabled** (yalnızca anahtarla giriş, `setup.sh` zorlar).
- **LUKS disk şifrelemesi** — OS kurulum aşamasında etkinleştirilir (V11-112).
- **NTP zorunlu** — Saat senkronu olmadan servis ayağa kalkmaz (V11-113, Paket 06).
- **Watchdog** — Heartbeat + auto-restart (Paket 02).

Saha kurulum prosedürü ve doğrulama adımları için
[deploy/README_PILOT.md](deploy/README_PILOT.md) okunmalıdır.

## Tehdit Modeli (Özet)

Custos'un tehdit modeli üç ana kategoriye odaklanır:

1. **Lokal ağ saldırganı** — PLC ağına erişimi olan ama Custos host'una erişimi olmayan biri. Modbus VLAN izolasyonu ve "sadece okur, asla yazmaz" mimari kuralı ile sınırlandırılır.
2. **Fiziksel hırsızlık** — Mini PC çalınması. LUKS disk şifrelemesi ile diskteki veri offline okunamaz.
3. **Kötü niyetli kullanıcı** — Geçerli giriş yapmış operator hesabı ile yetki yükseltmek isteyen kullanıcı. İki rol (operator / developer) ayrımı, audit log ve session yönetimi ile sınırlandırılır.

**Kapsam dışı:** Anthropic / Claude API anahtar sızıntıları (Custos bunu kullanmaz),
TLS sertifika otoritesi saldırıları (self-signed sertifika kullanılır), tedarik
zinciri saldırıları (yalnızca pip-audit ve Dependabot ile en iyi gayret).
