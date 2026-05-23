# ADR-002: Sadece-okuma Modbus mimarisi

**Tarih:** 2026-05-22
**Durum:** Kabul

## Bağlam

Karar proje başlangıcından (brief v1.0) beri geçerlidir ve ürünün en temel
güvenlik vaadidir; bu ADR onu yazıya geçirir.

Custos, **çalışan endüstriyel/bina ekipmanına** (chiller, AHU, pompa, soğutma
kulesi, enerji analizörü) Modbus TCP ile bağlanır. OT (Operasyonel Teknoloji)
dünyasında bir izleme aracının prosese **yazması** ciddi sonuçlar doğurabilir:
yanlış bir register yazımı bir ekipmanı durdurabilir, setpoint'i bozabilir veya
güvenlik kilidini etkileyebilir. Böyle bir olayın teknik, ticari ve hukuki
sorumluluğu taşınamaz.

Bu kısıt aynı zamanda **ticari sözleşmenin temelidir**: pilot sözleşmesindeki
sorumluluk sınırı maddesi "sistem yalnızca okur, asla yazmaz" güvencesine
dayanır. Yani bu yalnızca bir mühendislik tercihi değil, sözleşmesel bir
taahhüttür.

## Karar

**Modbus istemci kodu yalnızca okuma fonksiyonlarını kullanır:**

- `read_holding_registers` (FC03)
- `read_input_registers` (FC04)
- `read_coils` (FC01)
- `read_discrete_inputs` (FC02)

Yazma fonksiyonları (`write_register`, `write_coil`, `write_registers`,
`write_coils`) **kod tabanında hiç implement edilmez ve hiç çağrılmaz.**

Bu, yorumla veya runtime bayrağıyla değil, **statik denetimle** garanti altına
alınır. [`architecture_check.py`](../../scripts/architecture_check.py)
`MODBUS_WRITE` kuralı, tüm `src/custos/**/*.py` dosyalarında
`.write_(register|coil|registers|coils)(` çağrısını arar ve bulursa CI ile
pre-commit'i kırar. Yani yazma kodu repoya **giremez**.

## Sonuçlar

**Pozitif:**
- Müşteriye ve sözleşmeye net, doğrulanabilir bir "okur, yazmaz" garantisi.
- Yanlışlıkla yazma fiziksel olarak imkânsız: yazma fonksiyonu kod tabanında
  bulunmaz, CI buna izin vermez.
- Saldırı güvenliği: Bir saldırgan uygulama sunucusuna erişse bile, prosese
  komut göndermek için kullanılabilecek bir yazma yolu mevcut değildir.
- Devreye alma güveni: Müşteri otomasyon ekibi, izleme aracının kontrol
  sistemine müdahale etmeyeceğinden emin olarak izin verir.

**Negatif:**
- Custos ile uzaktan kontrol/otomasyon yapılamaz. Bu **bilinçli** bir sınırdır:
  Custos bir izleme ve historian aracıdır, bir kontrolör değildir.
- "Önce yaz, sonra oku" gerektiren nadir cihaz protokollerinde ilgili veri
  okunamaz. Pilot kapsamındaki ekipmanlar için bu durum yoktur (kabul edildi).

## Alternatifler

- **Okuma+yazma istemci + "write-disable" bayrağı:** Bir bayrak çevrilebilir ya
  da bir hata sonucu bypass edilebilir; garanti yalnızca disipline dayanır,
  denetlenemez. Sözleşmesel taahhüt için yeterince güçlü değil. Reddedildi.
- **Whitelist'li sınırlı yazma:** Ek karmaşıklık ve her durumda bir yazma yolu
  açar; "asla yazmaz" vaadini ortadan kaldırır. Reddedildi.

## İlgili

- Protokol kapsamının (BACnet, OPC UA, Profinet) bu sadece-okuma ilkesini
  bozmadan nasıl genişletileceği: [ADR-007](007-protocol-expansion.md).
