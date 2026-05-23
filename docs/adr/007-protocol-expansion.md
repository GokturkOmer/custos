# ADR-007: Protokol genişleme stratejisi (BACnet, OPC UA, Profinet)

**Tarih:** 2026-05-22
**Durum:** Kabul (yön benimsendi)
**Uygulama durumu:** Planlandı (v1.1+) — **henüz kodda yok.** Bugün yalnızca Modbus
TCP collector mevcuttur.

## Bağlam

Custos bugün yalnızca **Modbus TCP** okur ([ADR-002](002-read-only-modbus.md)).
Ancak hedeflenen sahalar farklı protokoller içerir:

- **Ticari bina / AVM otomasyonu:** BACnet/IP çok yaygın.
- **Modern endüstri / fabrika:** OPC UA yaygın.
- **Fabrika saha ağı:** Profinet (gerçek-zamanlı endüstriyel Ethernet).

Modbus tek protokol kalırsa bu sahaların bir kısmına erişilemez veya müşteri ek
dönüştürücü almak zorunda kalır. Soru: Protokol kapsamı, **read-only garantisini**
([ADR-002](002-read-only-modbus.md)) ve **iki-süreç / minimum-bağımlılık**
ilkelerini ([ADR-001](001-two-process-architecture.md)) bozmadan nasıl büyütülür?

## Karar

Üç bileşenli strateji:

**1. Native read-only collector'lar (orta vade, tercih edilen yol).**
- **BACnet/IP collector** — Bina dünyasının ana dili. Python tarafı olgun
  (BAC0 / bacpypes). `ReadProperty`, `ReadPropertyMultiple`, COV subscribe —
  hepsi okuma işlemleri.
- **OPC UA collector** — `asyncua` ile read + monitored-item subscribe.
- Bunlar `src/custos/critical/` altında Modbus collector'a **paralel** yeni
  collector modülleridir; ortak bir collector soyutlamasıyla aynı soyut DB
  arayüzüne (`DatabaseInterface`) yazarlar. İki süreçli mimari ve sadece-okuma
  ilkesi aynen korunur.

**2. Profinet — doğrudan client değil, PLC üzerinden.**
Profinet IO gerçek-zamanlı cyclic Ethernet'tir ve IO-controller/PLC modeline
dayanır; üzerine bir izleme aracı "client" olarak takılmaz. Profinet verisi,
**PLC'nin ek Modbus veya OPC UA çıktısından** okunur: PLC zaten IO-controller
olduğu için ilgili değişkenleri bir Modbus register bloğuna veya OPC UA node'una
expose eder, Custos da bunları normal collector'ıyla okur. Bu, saha başına bir
kurulum/konfig kararıdır (PLC'de ilgili çıktının açılması).

**3. Gateway — kısa vade köprü.**
Acil bir sahada native client hazır değilse, BACnet/OPC UA → Modbus TCP çeviren
bir donanım gateway (Moxa, HMS/Anybus vb.) Custos kodunu hiç değiştirmeden
devreye alınabilir. Bedeli: saha başına ek kutu + konfig + güvenlik yüzeyi.

**Read-only her yolda korunur.** Protokol genişlemesi **yazma anlamına gelmez.**
Yeni collector'lar yalnızca okuma çağrıları yapar; mevcut Modbus `MODBUS_WRITE`
kuralı gibi, [`architecture_check.py`](../../scripts/architecture_check.py)'a
protokol-özel yazma yasakları eklenir (ör. OPC UA `write_value`, BACnet
`WriteProperty`). Böylece "asla yazmaz" güvencesi her protokolde makineyle korunur.

## Sonuçlar

**Pozitif:**
- Hedef pazar kapsamı genişler (BACnet → binalar, OPC UA → fabrikalar) — native
  yolda donanımsız, marjı yüksek.
- Mimari ilkeler korunur: read-only + iki süreç + tek DB erişim noktası.
- Gateway seçeneği "hızlı evet" için her zaman elde tutulur.

**Negatif:**
- Her yeni native collector = yeni bağımlılık (BAC0/bacpypes, asyncua) + test +
  bakım yükü. "Minimum hareketli parça" ilkesiyle dengelenmeli: bir protokol
  ancak **gerçek müşteri talebiyle** eklenir, spekülatif değil.
- Profinet, PLC'nin ilgili değişkenleri expose etmesine bağımlıdır; expose
  edilmezse o veri alınamaz (saha keşfinde netleşir).
- Gateway yolu saha başına donanım ve ek güvenlik yüzeyi getirir.

## Alternatifler

- **Her protokole zorunlu gateway:** Her sahada kutu = maliyet + bakım; native
  client mümkünken gereksiz. Yalnızca kısa vade köprü olarak tutulur, varsayılan
  değil.
- **Profinet'e doğrudan client yazmak:** Gerçek-zamanlı stack + IO-controller
  rolü gerektirir; bir izleme aracı için orantısız ve riskli. Reddedildi.
- **Tek protokol (yalnızca Modbus) kalmak:** Pazar kapsamını daraltır; BACnet
  ağırlıklı binalar erişilemez. Reddedildi.

## İlgili

- [ADR-001](001-two-process-architecture.md) — iki süreçli mimari (collector'lar
  Kritik Döngü'de yaşar).
- [ADR-002](002-read-only-modbus.md) — sadece-okuma ilkesi (bu ADR onu çok
  protokollü hâle taşır, bozmadan).
- Brief: protokol genişlemesi v1.1+ kapsamındadır; brief ayrı bir versiyon olarak
  yalnızca kullanıcı tarafından güncellenir (CLAUDE.md gereği bu repoda brief'e
  dokunulmaz).
