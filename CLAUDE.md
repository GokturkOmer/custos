# Claude Code için Kalıcı Talimatlar

Bu dosyayı her oturumun başında oku ve içindeki kurallara uy. Bu kurallar tartışılamaz.

## Proje hakkında

Endüstriyel edge izleme sistemi (proje adı: **Custos**). Modbus üzerinden sensör verisi okur, ML ile anomali tespit eder, alarm üretir. Lokal çalışır, sadece okur (asla yazmaz). Detaylı bilgi için `docs/brief_v1.7.md` dosyasını oku.

## Değişmez kurallar

### Dil ve yorum
- Tüm kod yorumları **Türkçe**.
- Tüm git commit mesajları **Türkçe**.
- Değişken/fonksiyon isimleri **İngilizce** (standart Python pratiği).
- Docstring'ler **Türkçe**.

### Datetime
- ASLA `datetime.now()` kullanma.
- ASLA `datetime.utcnow()` kullanma.
- Her zaman `datetime.now(timezone.utc)` kullan.
- Veritabanına yazılan tüm zaman damgaları UTC.
- Kullanıcıya gösterirken yerel saate çevir.

### Dosya kısıtlamaları
- `.env` dosyasının içeriğini ASLA değiştirme veya silme. Sadece `.env.example` düzenlenebilir. Eğer `.env` dosyası mevcut değilse, `.env.example`'dan birebir kopya oluşturmana izin verilir (yalnızca kopyalama, içerik değiştirmek yasak).
- `docker-compose.yml` değişiklikleri için kullanıcıdan ONAY al.
- `pyproject.toml` bağımlılık eklemek için kullanıcıdan ONAY al. (Asistan servisi için `pymupdf`, `pytesseract`, `rank_bm25`, `pdfplumber` 2026-05-28'de ONAYLANDI.)
- `CLAUDE.md` dosyası SADECE kullanıcı tarafından düzenlenir.
- `docs/brief_*.md` dosyalarını ASLA değiştirme.

### Mimari kurallar
- **Üç süreçli yapı:** Critical loop, Analytics loop ve **Asistan servisi** birbirinden bağımsız ayrı süreçlerdir. Asistan servisi kendi systemd unit'i + kendi portu (8001) ile çalışır, Caddy `/assistant/*` → 8001 reverse proxy. (Asistan modülü genişletmesi — bkz. `docs/brief_v1.7.md` §4.9 ve `docs/custos_asistan_is_plani_v1.md`.)
- Critical loop'a (Collector + Threshold) ML kütüphaneleri eklenmeyecek.
- Critical loop'un bağımlılığı minimum tutulacak.
- Veritabanı erişimi `shared/database.py` üzerindeki abstract arayüz ile yapılır. Modüllerden doğrudan SQL/ORM çağrısı ASLA yapılmaz. **İstisna — Asistan servisi:** ayrı süreç olduğu için kendi `assistant` PostgreSQL şemasına kendi data-access (`repository`) katmanı üzerinden erişir. Yine de "ham SQL iş mantığına serpiştirilmez, erişim soyutlama üzerinden yapılır" ilkesi geçerlidir; SQL tek bir repository modülünde toplanır. Critical ve Analytics süreçleri `shared/database.py`'yi kullanmaya devam eder.
- "Sadece okur, asla yazmaz" — Modbus client kodunda yazma fonksiyonları implement edilmeyecek.
- Collector modülü (critical/collector.py) SADECE `pymodbus` ve abstract DB arayüzünü kullanır. `asyncpg`, SQL string'leri, veya ORM kodu Collector içinde YAZILMAZ.
- Modbus client kodunda `write_register`, `write_coil`, `write_registers`, `write_coils` çağrıları ASLA yapılmaz. Sadece read fonksiyonları.

### ML kuralları
- Derin öğrenme yok. Sadece scikit-learn ailesi. **İstisna — Asistan servisi:** semantic retrieval için `sentence-transformers` (embedding; torch backend) + `faiss`, PDF işleme için `pymupdf`/`pytesseract`/`rank_bm25`/`pdfplumber` kullanılabilir. Bu istisna SADECE asistan servisine aittir; Critical ve Analytics loop'larında derin öğrenme hâlâ yasak.
- Modeller cihazda eğitilmez, sadece çalıştırılır. Eğitim offline (geliştirici makinesinde). Asistan servisinde embedding modeli yalnızca **inference** amaçlı çalışır (cihazda eğitim yok).
- Test seti ASLA eğitim setine karıştırılmaz.

### Kod kalitesi
- Her commit'te `ruff check` ve `mypy` temiz olmalı.
- Her yeni fonksiyonun en az bir testi olmalı.
- Type hint'ler zorunlu (mypy strict mode).

### Loglama
- `print()` kullanma. Sadece `structlog` (henüz kurulmadı, gelecek aşamada).
- Her önemli olay loglanır. "Loglanmayan şey olmamış sayılır."

### Bilmediğin şeyler için
- **Uydurma.** Sor veya en güvenli varsayılanı seç ve açıkça belirt.
- Brief'te olmayan bir karar gerekirse, kullanıcıya sor.
- "Daha iyi olur" diye ek özellik ekleme. Sadece istenen iş yapılır.

## Çalışma akışı

1. Görev al
2. **Önce `_personal/knowledge/HANDOFF.md` oku** — projenin güncel durumu, aktif öncelik ve sıradaki iş oradadır. Sonra ilgili dosyaları oku (özellikle `CLAUDE.md`, `docs/brief_v1.7.md`; gerekiyorsa `_personal/knowledge/DECISIONS.md` + `OPEN_THREADS.md`).
3. Uygula
4. Test et
5. `ruff check .` ve `mypy src/` temiz mi kontrol et
6. Kullanıcıya sun: ne yaptın, nelerden emin değildin
7. **Oturum sonunda merkezi bilgi tabanını güncelle:** `_personal/knowledge/HANDOFF.md` (durum + "Sıradaki" + tarih) ve `SESSIONS_INDEX.md`'ye 1 satır ekle. Kalıcı karar çıktıysa `DECISIONS.md`'ye, yeni/kapanan açık iş varsa `OPEN_THREADS.md`'ye işle. Paralel çalışırken yalnızca "kontrol kulesi" oturumu DECISIONS/HANDOFF/OPEN_THREADS'i günceller; diğerleri sadece kendi SESSIONS_INDEX satırını ekler. (`_personal/` gitignore'da — commit edilmez.)

## Walking skeleton doğrulaması
Aşama 3'ten itibaren, veri akışı değiştiren bir commit'ten önce şu manuel test yapılır:
1. `docker compose up -d`
2. Ayrı bir terminalde: `python -m custos.simulator`
3. Başka bir terminalde: `python -m custos.critical`
4. 10 saniye çalıştır, Ctrl+C
5. `python scripts/query_last_readings.py` (Aşama 3'te oluşturuldu)
