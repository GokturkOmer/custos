# Custos Operatör Kılavuzu

**Sürüm:** v1.0
**Tarih:** 2026-04-29
**Hedef kitle:** AVM teknik ekibi (operatör + bakım + yönetim)

---

## 1. Bu kılavuz kim için?

Bu kılavuz, Custos sistemini günlük olarak kullanacak AVM teknik personeli
için yazıldı. Üç tipik kullanıcıyı kapsar:

- **Operatör:** Alarm geldiğinde aksiyon alan, durum panosunu izleyen kişi.
- **Bakım ekibi:** Bakım modunu açıp kapatan, planlı işleri yürüten kişi.
- **AVM yönetimi:** KPI'ları okuyan, periyodik bakım takibini yapan kişi.

Sistemi **kuran** ya da donanım/işletim sistemi tarafıyla ilgilenen kişi
(sysadmin) için ayrı bir doküman var: [`deploy/README_PILOT.md`](../deploy/README_PILOT.md).
Kurulum, yedekleme, sertifika yenileme, sorun durumunda servis yeniden
başlatma gibi konular orada anlatılır.

Kılavuz **kullanım odaklıdır**: nereye tıklayacağınızı, ne anlama
geldiğini ve hata durumunda ne yapacağınızı anlatır. Yazılım terimleri
mümkün olduğunca kullanılmamıştır.

---

## 2. Beş dakikalık kavram sözlüğü

Bu altı terim kılavuz boyunca tekrar tekrar geçecek. İçeriği bir kez
okumanız yeterli.

| Terim | Anlamı |
|---|---|
| **Tag** | Sahadaki tek bir sensör değeri. Örnek: "Soğutma Kulesi 1 — Su Sıcaklığı". |
| **Asset / Cihaz** | Birden fazla tag içeren fiziksel ekipman. Örnek: "Soğutma Kulesi 1" (sıcaklık + akış + titreşim tag'leri). |
| **Eşik (Threshold)** | Bir tag için tanımlanmış normal-anormal sınırı. Eşik aşılırsa alarm üretilir. |
| **Alarm** | Bir eşik aşıldığında üretilen olay. Dört seviyesi var (info, warn, crit, emergency). |
| **Bakım modu** | Bakım sırasında alarm üretiminin geçici olarak durdurulması. |
| **KPI** | Bir veya birden fazla tag'den hesaplanan özet metrik (ortalama sıcaklık, % verim, fark vb.). |
| **Asistan** | Saha ile ilgili soruları yanıtlayan dahili sohbet aracı. |

**Renk kodu** (her sayfada tutarlıdır):

- 🟢 **Yeşil** — Normal. Aksiyon gerekmez.
- 🟡 **Sarı** — Uyarı (warn). Yakından takip edin.
- 🔴 **Kırmızı** — Kritik (crit). Hızlı aksiyon gerekir.
- ⚫ **Siyah / koyu kırmızı** — Acil (emergency). Hayati / büyük operasyon riski; sessiz saatleri bile geçer.

---

## 3. İlk giriş

### 3.1 Adres ve sertifika uyarısı

1. Tarayıcıyı aç.
2. Adres çubuğuna kurulum ekibinden aldığın bağlantıyı yaz (örn:
   `https://192.168.1.10`). Tarayıcıyı dahili adrese yönlendir.
3. İlk açılışta tarayıcı **sertifika uyarısı** gösterebilir. Bu, sistemin
   AVM ağı içinde özel sertifikayla çalıştığı için normaldir.
   "Gelişmiş > Devam et" tıkla. Detaylı açıklama: `deploy/README_PILOT.md` §7.1.

> ⚠️ TODO(operator-doc): Pilot kurulumunda kullanılacak gerçek IP/host
> adresi sysadmin tarafından buraya yazılacak. Şimdilik örnek IP gösteriliyor.

### 3.2 Kullanıcı adı ve parola

1. Kullanıcı adı ve **ilk parolanı** kurulum ekibinden al.
2. Giriş ekranına yaz, "Giriş yap" tıkla.
3. İlk girişte sistem seni **parola değiştirme** ekranına yönlendirecek.
4. Yeni parolan **en az 8 karakter** olmalı. Tahmin edilemeyen bir parola
   seç; parola yöneticisi (Bitwarden, KeePass) önerilir.

### 3.3 Yanlış parola ve kilitlenme

- Aynı IP'den 15 dakika içinde **5 başarısız deneme** olursa o IP geçici
  kilitlenir. Kilitlendiğinde "Çok fazla deneme" mesajı görürsün.
- Aynı kullanıcı adıyla 30 dakika içinde **10 başarısız deneme** olursa
  hesap geçici durur (dağıtık brute-force koruması).
- Kilit otomatik kalkar; acelen varsa sysadmin sıfırlayabilir.

### 3.4 Oturum süresi

- Bir kez giriş yaptıktan sonra **12 saat** boyunca tekrar parola sormaz.
- 12 saat dolduğunda otomatik çıkış olur, tekrar giriş yapman gerekir.
- Tarayıcıyı kapatmak oturumu kapatmaz; başkasının kullanmasını
  istemiyorsan sağ üstten **"Çıkış"** butonuna bas.

---

## 4. Genel Bakış sayfası (Overview)

URL: `/dashboard/overview` — giriş yaptıktan sonra otomatik açılan ana sayfadır.

![Genel Bakış ekranı](images/overview.png)

> ⚠️ TODO(operator-doc): Pilot eğitimi sırasında ekran görüntüleri çekilip
> `docs/images/` altına eklenecek.

### 4.1 Üst bantta neler var?

- **KPI kartları (4 adet):**
  - **Active Alarms** — Anlık aktif alarm sayısı. Sıfırdan büyükse 🔴 kırmızı.
  - **Total Tags** — Sistemde tanımlı sensör sayısı (bilgi amaçlı, nötr).
  - **Total Assets** — Sistemde tanımlı cihaz sayısı (bilgi amaçlı, nötr).
  - **Anomalies (24h)** — Son 24 saatte tespit edilen anomali (ML) sayısı.
    Sıfırdan büyükse 🟡 sarı.

- **Disk doluluk widget'ı:** Sahadaki cihazın sabit disk kullanımı yüzdesi.
  30 saniyede bir kendiliğinden tazelenir. %85 üstü dikkat, %95 üstü acil
  → sysadmin'i ara.

- **Sistem sağlığı widget'ı:** İki dahili servis (`custos-analytics` ve
  `custos-critical`) son 60 saniyede sinyal vermiş mi? Sinyal yoksa
  servislerden biri durmuş demektir → sysadmin'i ara.

- **Yaklaşan Bakım widget'ı:** Önümüzdeki 48 saat içinde planlanmış
  bakım görevleri görünür. Liste yoksa widget gizlenir.

- **Recent Alarms tablosu:** En son tetiklenen alarmlar tek bakışta:
  zaman, sensör, tip, durum.

### 4.2 Grafik panelleri

Genel Bakış sayfasında istediğin tag'leri grafik olarak izleyebilirsin.
"İlk chart'ı oluştur" / "Yeni Chart" butonuyla başla. Daha fazla detay:
[Bölüm 5](#5-tag-listesi-ve-grafikler).

---

## 5. Tag listesi ve grafikler

### 5.1 Tag (sensör) listesini açma

1. Üst menüden **"Sensors"** tıkla. URL: `/dashboard/sensors`.
2. Liste tüm tag'leri gösterir: ad, tip, son okuma değeri, son okuma zamanı.
3. Arama kutusuna tag adının bir parçasını yaz; liste süzülür.
4. **Live Values** bağlantısı (`/dashboard/sensors/live-values`) tüm
   aktif tag'leri canlı değerle gösterir.

### 5.2 Tag detayını ve grafiğini açma

1. Listeden tag adına tıkla.
2. Detay sayfasında üstte tag'in son değeri ve durumu, altta tarihsel
   grafik vardır.
3. Grafik tarih aralığı için iki seçenek:
   - **Hazır aralık:** Son 1 saat / Son 1 gün / Son 7 gün / Son 30 gün.
   - **Özel aralık:** Tarih + saat seç, "Uygula" tıkla.
4. Grafiği soldan sürükleyerek geçmiş veriyi getirebilirsin (otomatik
   yüklenir, beklemeden devam eder).

### 5.3 Grafiği Genel Bakış'a sabitleme

Sık baktığın grafikleri Genel Bakış sayfasında sabit tutmak için
**"Yeni Chart"** butonu üzerinden ekle:

1. `/dashboard/overview/charts/new` ekranını aç.
2. Başlık ver (örn: "Soğutma kulesi sıcaklıkları").
3. Bir veya birden fazla tag seç.
4. Zaman penceresi seç (1 saat ile 1 yıl arası).
5. "Kaydet" tıkla — chart Genel Bakış'a eklenir.

> İpucu: Genel Bakış kompakt görünüm için 2 eksen sınırlıdır. Daha çok
> tag'i tek grafikte görmek istiyorsan grafiği "büyüt" ikonuna tıklayıp
> tam ekrana al.

---

## 6. Alarm yönetimi

### 6.1 Alarm sayfasını açma

URL: `/dashboard/alarms` — üst menüden **"Alarms"**.

Sayfa iki bölüm gösterir:
- **Aktif alarmlar:** Henüz çözülmemiş olanlar (state = triggered veya
  acknowledged).
- **Geçmiş alarmlar:** Otomatik temizlenen son 50 alarm (state = cleared).

### 6.2 Filtreler

Sayfa üstündeki filtre çubuğu ile listeyi daraltabilirsin:

- **Severity:** info / warn / crit / emergency.
- **Source (kaynak):** threshold (klasik eşik), anomaly (ML), liveness
  (sensör donmuş), watchdog (servis kayıp), rate_of_change (ani değişim),
  cross_sensor (iki sensör ilişkisi), spc (istatistiksel proses kontrolü).
- **Yalnızca etiketsiz:** "Henüz hiç sınıflandırılmamış alarmları göster"
  (review queue / inceleme listesi).

### 6.3 Alarmı onaylama (Acknowledge)

"Gördüm, ilgileniyorum" demek için. Alarm listede kalır ama "yeni"
sayılmaz, push bildirimi sustulur.

1. Aktif alarm satırının sağındaki **"Onayla"** butonuna tıkla.
2. Alarm `triggered` → `acknowledged` durumuna geçer; renk soluklaşır.
3. Aksiyon audit log'a yazılır (denetim izi).

> Not: "Çözüldü olarak işaretle" diye ayrı bir buton **yoktur**. Alarm
> kaynağı eşik altına dönünce sistem otomatik olarak `cleared` durumuna
> geçirir. Yani sıkıntıyı sahada düzeltirsen alarm kendiliğinden kapanır.

### 6.4 4 sınıf etiketleme (yanlış alarm bayrağı)

Her alarmın yanında dört etiket butonu vardır. Bu etiket ML'nin gelecekte
yanlış alarm üretmesini önlemek için kullanılır.

- **Gerçek arıza** — Alarm doğru çıktı, sahada gerçek bir sorun vardı.
- **Yanlış alarm** — Alarm tetiklendi ama gerçek bir sorun yoktu.
- **Bakım sırasında** — Bakım/test sırasında üretildi, normal değil ama gerçek arıza da değil.
- **Bilinmiyor** — Sebebini şu an bilemiyorum, ileride bakılsın.

Etiketlemenin faydası:
- "Yalnızca etiketsiz" filtresiyle sayfa başına gözden geçirme listesi
  oluşur.
- Pilot bittikten sonra ML modeli bu etiketlerle yeniden eğitilir; yanlış
  alarmlar azalır.

### 6.5 Otomatik yükseltme (warn → crit)

Bir **warn** seviyesinde alarm 30 dakika boyunca açık kalırsa, sistem
onu otomatik olarak **crit** seviyesine yükseltir + crit kanal push'u
gönderir.

- Süre ayarı: `Settings > Veri saklama > Escalation süresi` (5 ile 240
  dakika arası).
- Acknowledge etmek alarm'ı yükseltmeyi durdurmaz; alarmı kapatan tek
  şey saha düzelmesi (auto-clear).
- Bakım modunda üretilen test alarmları yükseltilmez.

### 6.6 Sık tekrarlayan alarm rozeti

Her aktif alarmın yanında "Son 7 günde N kez" rozeti görünür. 5 ve
üstü tekrar varsa eşiği gözden geçirme zamanı gelmiş demektir
(eşik çok dar ya da gerçekten kronik bir sorun var).

---

## 7. Bakım modu

Bakım sırasında alarmların spam üretmesini önlemek ve veri toplamayı
kirletmemek için iki seviye bakım modu vardır.

### 7.1 Tek cihaz bakım (per-instance)

Tek bir cihazı bakıma alır. Diğer tüm cihazlar normal çalışmaya devam eder.

1. **Processes** menüsünden cihazı aç (`/dashboard/processes/{id}`).
2. "Bakım Modu Başlat" butonuna tıkla.
3. **Süre seç:** 1 saat / 4 saat / 12 saat / 24 saat / 3 gün / Manuel (süresiz).
4. **Sebep yaz:** En az 3 karakter, anlamlı bir cümle (örn: "kompresör
   yağ değişimi"). Sebep audit log'a yazılır.
5. "Başlat" tıkla.

Bakım sürerken:
- Cihazın tag'leri normal akar (veri görünür).
- Eşik aşılsa bile **alarm üretilmez** (push gitmez, dashboard'da
  görünmez).
- Cihaz "BAKIM" rozetiyle işaretlenir.

Bakımı kapatma:
- "Bakım Modu Durdur" butonuna tıkla, ya da
- Süre dolduğunda sistem otomatik olarak kapatır (60 saniye gecikmeli
  arka plan kontrolü).

### 7.2 Genel bakım (global)

**Tüm sistemi** bakıma alır — büyük etkili, dikkatli kullan.

1. **Settings** sayfasını aç.
2. "Genel Bakım Modu" panelinde "Başlat" tıkla.
3. Süre + sebep gir.
4. "Onayla" tıkla.

Genel bakım sürerken **hiçbir cihazdan alarm üretilmez**. Tatil dönemleri,
büyük ekipman değişikliği veya saha eğitimi için uygundur.

### 7.3 Çalışma modu (running / startup / shutdown / idle)

Her cihazın bir **çalışma modu** vardır. Bakım modunun yanında, ML
anomali tespitinin nasıl davranacağını belirler.

| Mod | Anlamı | ML davranışı |
|---|---|---|
| **running** | Normal çalışma | Tüm alarm motorları aktif |
| **startup** | Devreye alınıyor | Anomali alarmı yazılmaz (ısınma sürerken yanlış alarm engellenir) |
| **shutdown** | Devreden çıkarılıyor | Anomali alarmı yazılmaz |
| **idle** | Boşta / bekleme | Tüm alarm motorları aktif |

Modu değiştirmek için cihaz detayında **"Çalışma Modu"** açılır
listesini kullan, "Güncelle" tıkla.

> Önemli ayrım: **Bakım modu** = "alarm üretme"; **Çalışma modu** =
> "ML anomali alarmı için bağlam ipucu". İkisi farklı amaçlar için.

---

## 8. Asistan (sohbet aracı)

URL: `/dashboard/assistant` — üst menüden **"Asistan"**.

### 8.1 Soru sorma

1. Sayfa altındaki kutuya soruyu yaz (Türkçe, doğal cümle).
   Örnek: "Chiller nedir?", "Soğutma kulesi tipik sıcaklığı kaç olmalı?"
2. **"Sor"** tıkla.
3. Cevap üstte görünür. Cevabın kaynağı (hangi dokümandan geldiği) altında
   yazar.

### 8.2 "Bilgi yok" cevabı

Asistan sadece **dahili bilgi tabanını** kullanır. Sorduğun konu kayıtlı
değilse şu mesajı görürsün:

> Bu konuda bilgi tabanında bir kayıt bulamadım.

Bu uyduran cevap vermesindense bilmiyorum demesi tercih edildi.
Bilgi tabanı eksikse sysadmin'e söyle, doküman ekleyebilir
([Bölüm 8.3](#83-yeni-doküman-ekleme-developer-yetkisi)).

### 8.3 Yeni doküman ekleme (developer yetkisi)

> Operatör erişemez — bu bölüm referans niteliğinde. Sysadmin / geliştirici yapar.

1. Üst menüden **"Knowledge Base"** (`/dashboard/knowledge`).
2. "Yeni Lokal Doküman" tıkla.
3. **Slug** ver (küçük harf, rakam, alt çizgi, tire — 1-80 karakter).
   Örnek: `cooling-tower-typical-values`.
4. Markdown gövdesini yapıştır (başlık, paragraf, listeler).
5. Kaydet — asistan birkaç dakika içinde indekse alır.

### 8.4 Sohbet geçmişi

Asistan sohbet geçmişi **sadece o sayfada** durur. Sayfayı yenilersen ya
da başka sayfaya geçip dönersen geçmiş silinir. Önemli bir cevabı
kaydetmek istiyorsan ekran görüntüsü al.

---

## 9. Push bildirimleri (tarayıcıdan)

Custos kritik alarm geldiğinde tarayıcıdan masaüstü/mobil bildirimi
yollayabilir. Tarayıcı sayfası kapalıysa bile çalışır (servis çalışırsa).

### 9.1 İlk kurulum — tarayıcı izni

1. İlk girişte tarayıcı sağ üstten **"Bildirimleri göster"** izni ister.
   "İzin ver" tıkla.
2. **Settings** sayfasını aç → "Bildirimler" paneli.
3. "Bu cihazı kaydet" tıkla — bu tarayıcı + cihaz için bir abonelik oluşur.
4. Hangi seviyeleri almak istediğini seç:
   - **Acil (emergency)** — sessiz saat bile olsa gelir, kapatma önerilmez.
   - **Kritik (crit)** — varsayılan açık.
   - **Uyarı (warn)** — varsayılan açık.
   - **Bilgi (info)** — gürültü olabilir, varsayılan kapalı.

### 9.2 Sessiz saatler

Geceleri uyku için bildirimleri sustur:

1. **Settings > Bildirimler** > "Sessiz Saatler".
2. Başlangıç (örn: 22:00) ve bitiş (örn: 07:00) saatini seç.
3. Saat dilimi yerel saat — Europe/Istanbul.
4. Aralık gece yarısını geçebilir (22:00–07:00 normal çalışır).

> Acil (emergency) seviye sessiz saati **bypass eder**. İnsan / büyük
> operasyon riski varsa tarayıcı sessiz aralıkta bile bildirim verir.

### 9.3 Bildirimi sustur (master switch)

- **Bu cihazı sustur:** Settings'te "Bu Cihazı Aktif/Pasif" toggle'ı.
- **Tüm sistem için sustur:** Yalnızca developer rolü açabilir/kapatabilir.
  Pilot süresince kapalı bırakılmamalı.

### 9.4 Test bildirimi

"Test Gönder" butonuna basarak kendi cihazına örnek bildirim yollayabilirsin.
İletim sorununu (izin verilmemiş, sessiz saat aktif vb.) test etmek için
faydalı.

---

## 10. KPI sayfası

URL: `/dashboard/kpi` — üst menüden **"KPI"**.

KPI'lar her cihaz için tanımlanmış formüllerdir (ortalama sıcaklık,
verim %, sıcaklık farkı, vb.). Sistem arka planda periyodik hesaplar
ve sayfada güncel değer + trend gösterir.

1. Liste tüm cihazları + ana KPI'ları gösterir.
2. Detay için cihaza tıkla (`/dashboard/kpi/{instance_id}`):
   - Anlık değer + son 24 saat trend grafiği.
   - Hedef aralık (varsa) ile karşılaştırma.

> KPI değerleri 60 saniyede bir hesaplanır. Anlık değer son hesaplama
> zamanına aittir; gerçek-zamanlı değil.

---

## 11. ML Hub (anomali izleme)

URL: `/dashboard/ml` — üst menüden **"ML"**.

ML Hub, eşik bazlı klasik alarmlara ek olarak **anomali tespiti**
(makine öğrenmesi) için tek kontrol noktasıdır.

### 11.1 Üst bantta neler var?

- **ML Inference: AÇIK / KAPALI** — sistem geneli ana anahtar.
- **Modelli Instance** — Kaç cihaz için anomali modeli eğitilmiş?
- **ML Açık Instance** — Kaçında ML aktif?
- **Son 24h Anomali** — Son 24 saatte ML kaç anomali yakaladı?
- **Son Eğitim** — Bir önceki model eğitiminden bu yana geçen gün.
  14 günden eskiyse 🔴 kırmızı (yenilenmesi gerekir).

### 11.2 Cihaz başına model durumu

Liste her cihaz için:
- **Model var mı?** Yeşil = var, gri = yok.
- **ML açık/kapalı toggle'ı** (cihaz bazlı geçici susturma).
- **"Eğit"** butonu — 60 saniye içinde küçük bir model eğitir
  (en az 10 satır veri gerekir).

> Operatör için kullanım: Genelde sadece izlersin. Eğitim ve toggle
> işlemleri çoğunlukla sysadmin/geliştirici tarafından yapılır. Bir
> cihazdan çok fazla anomali geliyorsa "ML kapat" geçici çözümdür;
> sysadmin'e haber ver.

### 11.3 Çapraz sensör kuralları

`/dashboard/cross-sensor-rules` — iki sensör arasındaki ilişkiyi izler
(örn: "tag A her zaman tag B'den büyük olmalı"). Geliştirici tarafından
tanımlanır; operatör listede görür ama düzenleyemez.

---

## 12. Günlük rutin (sabah check — 5 dakika)

Her sabah Custos açıldığında bu kısa kontrolü yap:

- [ ] **Genel Bakış** sayfasını aç.
- [ ] Active Alarms = 0 mı? (yeşil) Değilse alarmları gözden geçir.
- [ ] Disk doluluk %85 altında mı?
- [ ] Sistem sağlığı widget'ında iki servis de "OK" mi?
- [ ] Anomalies (24h) gece boyu kabul edilebilir seviyede mi?
- [ ] Yaklaşan Bakım listesinde bugün için planlı iş var mı?
- [ ] Bakım modunda kalmış cihaz var mı (unutulmuş bakım)? **Settings >
      Bakım Modu** veya cihaz listesinde "BAKIM" rozetli olanları kontrol et.

> ⚠️ TODO(operator-doc): Pilot eğitiminde bu liste Göktürk ile birlikte
> daraltılacak (bazı kontrol noktaları AVM operasyonuna göre eklenebilir).

---

## 13. "Alarm geldi, ne yapayım?" — karar ağacı

```
1. Bildirim gelir (push veya dashboard'da kırmızı kart)
        │
        ▼
2. Alarmlar sayfasını aç → ilgili satırı bul
        │
        ▼
3. Severity (renk) kontrolü:
        ├── 🔴 crit / ⚫ emergency → 4. adıma git (acil)
        └── 🟡 warn → 5. adıma git (takip)
        │
        ▼
4. (crit/emergency) Hangi tag'den geldi?
        ├── Tag adına tıkla → grafiği aç
        ├── Trend ne diyor? Ani sıçrama mı, tedrici tırmanış mı?
        ├── Son 7 günde N kez rozetine bak — kronik mi?
        ├── Bakım planlanmış mı? Bakım listesini kontrol et
        └── Aksiyon:
              - Saha sorumlusunu ara
              - Gerekiyorsa cihazı bakım moduna al (Bölüm 7)
              - "Onayla" butonuna bas (Bölüm 6.3)
        │
        ▼
5. (warn) İncele:
        ├── Aynı sahada birden fazla warn var mı?
        ├── 30 dk içinde kapanmazsa otomatik crit'e yükselir
        └── Aksiyon:
              - "Onayla" — push'u sustur, listede tut
              - 30 dk dolmadan saha düzeltmesi yapılırsa otomatik kapanır
        │
        ▼
6. Alarm kapandığında (yeşil "cleared" oldu):
        ├── Etiketle: gerçek arıza / yanlış alarm /
        │   bakım sırasında / bilinmiyor (Bölüm 6.4)
        └── Etiket ML eğitimi için kayıtlanır
```

> **"Etiketleme zorunlu mu?"** Hayır, ama 5 dakika alır ve pilot
> sonrasında ML modelinin yanlış alarm üretmesini ciddi azaltır. Operatör
> rutininin bir parçası olmalı.

---

## 14. Sorun giderme — hızlı referans

| Belirti | İlk kontrol | Sonraki adım |
|---|---|---|
| Dashboard açılmıyor / "site ulaşılamıyor" | Bilgisayarın LAN bağlantısı | Sysadmin → `deploy/README_PILOT.md` §11 sorun giderme |
| Veri akmıyor (grafik düz çizgi) | Genel Bakış > Sistem sağlığı widget'ı: critical servis "OK" mi? | Sysadmin → Modbus bağlantısı veya servis yeniden başlatma |
| Push bildirimi gelmiyor | Tarayıcı izni verildi mi? Sessiz saatte miyiz? Master switch açık mı? | Sysadmin → Bildirim anahtarları (VAPID) yapılandırması |
| Alarm üretilmiyor (eşik aşıldığı halde) | İlgili cihaz / sistem bakım modunda mı? | Bakım modunu kapat (Bölüm 7) |
| "Çok fazla deneme" mesajı (giriş kilidi) | 15 dakika bekle, otomatik kalkar | Sysadmin → Acilse hesap kilidini sıfırlatabilir |
| KPI değeri "—" gösteriyor | Cihaz aktif mi? Tag binding'leri eksik olabilir | Sysadmin → Cihaz şablonu ve tag eşlemesini kontrol eder |
| Asistan "bilgi yok" diyor | Soru bilgi tabanında olmayabilir | Sysadmin → Yeni doküman ekle (Bölüm 8.3) |
| Anomali sayısı çok yükseldi | Cihaz çalışma modu ne? (running / startup / shutdown) | Geçici olarak ML kapat, sysadmin'e bildir |
| Tarayıcı sertifika uyarısı her seferinde | Tek seferlik kabul gerekir (TOFU) | İlk kabul sonrası tekrar etmez; ediyorsa sysadmin |

---

## 15. Yardım ve iletişim

- **Sysadmin desteği (saha):**

  > ⚠️ TODO(operator-doc): İletişim bilgileri pilot kurulum sırasında
  > AVM teknik ekibine verilecek. Pilot hazırlığında doldurulacak.

- **Custos teknik destek:**

  > ⚠️ TODO(operator-doc): Pilot süresince doğrudan iletişim Göktürk
  > üzerinden; pilot sonrası destek modeli netleşince güncellenecek.

- **Detaylı kurulum + sorun giderme:** [`deploy/README_PILOT.md`](../deploy/README_PILOT.md)

---

## Sürüm geçmişi

- **v1.0 (2026-04-29)** — Pilot öncesi ilk sürüm. Pilot eğitiminde
  doğrulanacak yerler `TODO(operator-doc)` ile işaretli; ekran
  görüntüleri eğitim sırasında çekilecek.
