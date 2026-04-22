# Bulk Tag Import — Dosya Formatı

Dashboard → **Sensors → Dosyadan İçe Aktar** akışı için CSV ve YAML format
açıklaması. 200 tag'lık bir dosya ~2 saniyede işlenir (elle giriş karşılığı
~7 saat).

## Zorunlu alanlar

| Alan              | Tür    | Açıklama                                           |
|-------------------|--------|----------------------------------------------------|
| `tag_id`          | string | Tag'in benzersiz kimliği, 1–128 karakter           |
| `name`            | string | Gösterim adı, 1–256 karakter                       |
| `modbus_host`     | string | PLC IP adresi ya da hostname                       |
| `register_address`| int    | Modbus register adresi (40001+ ya da 0-based)      |

## Opsiyonel alanlar + varsayılanlar

| Alan                  | Varsayılan | Kabul edilen değerler                            |
|-----------------------|------------|--------------------------------------------------|
| `modbus_port`         | `502`      | 1 – 65535                                        |
| `unit_id`             | `1`        | 1 – 247 (Modbus RTU slave ID aralığı)            |
| `register_type`       | `uint16`   | `uint16`, `int16`, `uint32`, `int32`, `float32`  |
| `byte_order`          | `big`      | `big`, `little`                                  |
| `gain`                | `1.0`      | Float                                            |
| `offset`              | `0.0`      | Float                                            |
| `unit`                | `""`       | Gösterim birimi (°C, bar, rpm, vs.)              |
| `polling_interval_ms` | `10000`    | `100` (fast), `1000` (normal), `10000` (slow)    |

## Adresleme

Hem **Modbus konvansiyonel** (40001 – 65535) hem de **0-based** (0 – 65535)
adresler kabul edilir. Konvansiyonel adresler sistem içinde 0-based
protokol adresine indirgenir (örn. 40042 → 41).

## CSV formatı

- UTF-8 kodlama. Excel export'un eklediği BOM otomatik tespit edilir.
- İlk satır başlık (header); sıralama önemli değildir.
- Zorunlu kolonların hepsi bulunmalı; eksikse dosya reddedilir.
- Boş hücreler varsayılan değere düşer.

Örnek için [`tag_import_example.csv`](tag_import_example.csv) dosyasına bakın.

## YAML formatı

- `tags:` anahtarı altında liste ya da doğrudan kök liste olarak verilebilir.
- `yaml.safe_load` ile okunur (RCE güvenli — Python object enjekte edilemez).

Örnek için [`tag_import_example.yaml`](tag_import_example.yaml) dosyasına bakın.

## Duplicate tag_id davranışı (mode)

Dosyadaki bir `tag_id` DB'de zaten varsa üç seçenek sunulur:

- **reject** (varsayılan): Hiçbir satır yazılmaz, kullanıcıya çakışan
  tag_id'ler listelenir. Dosyayı düzeltip tekrar yükleyebilir.
- **update**: Mevcut kayıt dosyadaki değerlerle güncellenir. Yeniler eklenir.
  Tag'in **durum (status)** alanına dokunulmaz.
- **insert**: Mevcut tag_id'ler atlanır (silinmez, değişmez), yalnız yeniler
  eklenir.

## Polling preset ipuçları

- `fast` (100 ms) — sadece kritik analog sinyaller (kompressor akım, bypass
  sıcaklığı). Eş zamanlı fast tag sayısı bütçeyle sınırlıdır
  (`collector_fast_polling_budget`, varsayılan 20).
- `normal` (1000 ms) — hızlı sıcaklık / basınç değerleri.
- `slow` (10000 ms) — statik veriler (setpoint, sayaçlar, durum).

Bütçe aşımı durumunda modal'da uyarı çıkar; bulk import bütçeye uymak için
tag'lerin bir kısmını `slow` yapmanız gerekebilir.

## Hata raporlama

Her doğrulama hatası **(satır no, alan adı, mesaj)** üçlüsüyle raporlanır.
Önizleme tablosunda hatalı satırlar kırmızı rozet ile gösterilir;
dosyanın tamamı düzeltilmeden yükleme aktifleşmez.
