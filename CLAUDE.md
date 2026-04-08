# Claude Code için Kalıcı Talimatlar

Bu dosyayı her oturumun başında oku ve içindeki kurallara uy. Bu kurallar tartışılamaz.

## Proje hakkında

Endüstriyel edge izleme sistemi (proje adı: **Custos**). Modbus üzerinden sensör verisi okur, ML ile anomali tespit eder, alarm üretir. Lokal çalışır, sadece okur (asla yazmaz). Detaylı bilgi için `docs/brief_v1.2.md` dosyasını oku.

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
- `.env` dosyasına ASLA dokunma. Sadece `.env.example` düzenlenebilir.
- `docker-compose.yml` değişiklikleri için kullanıcıdan ONAY al.
- `pyproject.toml` bağımlılık eklemek için kullanıcıdan ONAY al.
- `CLAUDE.md` dosyası SADECE kullanıcı tarafından düzenlenir.
- `docs/brief_*.md` dosyalarını ASLA değiştirme.

### Mimari kurallar
- İki süreçli yapı: Critical loop ve Analytics loop birbirinden bağımsız.
- Critical loop'a (Collector + Threshold) ML kütüphaneleri eklenmeyecek.
- Critical loop'un bağımlılığı minimum tutulacak.
- Veritabanı erişimi `shared/database.py` üzerindeki abstract arayüz ile yapılır. Modüllerden doğrudan SQL/ORM çağrısı ASLA yapılmaz.
- "Sadece okur, asla yazmaz" — Modbus client kodunda yazma fonksiyonları implement edilmeyecek.

### ML kuralları
- Derin öğrenme yok. Sadece scikit-learn ailesi.
- Modeller cihazda eğitilmez, sadece çalıştırılır. Eğitim offline (geliştirici makinesinde).
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
2. İlgili dosyaları oku (özellikle `CLAUDE.md` ve `docs/brief_v1.2.md`)
3. Uygula
4. Test et
5. `ruff check .` ve `mypy src/` temiz mi kontrol et
6. Kullanıcıya sun: ne yaptın, nelerden emin değildin
