"""Batch Modbus read için register gruplama algoritması (F11 Paket I).

Mevcut collector her tag için ayrı `read_holding_registers(address, count=1)`
çağrısı yapıyor. Saha'da 200 tag varsa PLC başına saniyede yüzlerce TCP
round-trip Modbus slave'i yorar (tipik 8-32 concurrent connection sınırı).

Bu modül komşu register'ları tek batch okuma çağrısında birleştirir:
    - Aynı (modbus_host, modbus_port, unit_id) içinde grupla
    - Register adresine göre sırala
    - gap_tolerance eşiğine kadar ardışık/yakın adresler birleşir
    - Her batch max 125 register (Modbus TCP sınırı)
    - uint32/int32/float32 tagleri 2 register yer kaplar; word_span ile hesap

Saha'da register haritası öğrenildikten sonra `collector_batch_gap_tolerance`
config parametresi ile optimize edilir, re-deploy gerekmez.

Mimari kural (CLAUDE.md): Bu modül critical loop içinde olduğu için
sadece pymodbus ve abstract DB arayüzüne (TagRecord) bağımlıdır.
asyncpg/SQL/ORM kullanımı YASAK.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from custos.shared.database import TagRecord

# Modbus TCP tek read_holding_registers çağrısında max register sayısı.
# Standart 125 (0x7D); bazı slave'ler daha az kabul eder ama 125 güvenli.
MAX_REGISTERS_PER_BATCH = 125

# 2-register tipleri (uint32, int32, float32) 1 tag için 2 adres işgal eder.
_WORD_SPAN_2 = {"uint32", "int32", "float32"}
# 1-register tipleri (uint16, int16) 1 tag için 1 adres işgal eder.
_WORD_SPAN_1 = {"uint16", "int16"}


def _word_span(register_type: str) -> int:
    """Verilen register tipi kaç 16-bit register yer kaplar döndürür.

    Bilinmeyen tip -> 1 (güvenli varsayılan; decoder aşamasında hata verir).
    """
    if register_type in _WORD_SPAN_2:
        return 2
    return 1


@dataclass(frozen=True)
class BatchGroup:
    """Tek bir batch read çağrısını temsil eder.

    Alanlar:
        modbus_host: PLC IP/hostname
        modbus_port: Modbus TCP port
        unit_id: Modbus slave unit ID
        start_address: İlk register adresi (dahil)
        count: Okunacak register sayısı (Modbus TCP'de max 125)
        tags: Bu batch'teki tag'ler; register_address'e göre artık sıralı
              değil, çünkü gap'te "dummy" register'lar gelebilir. Decoder
              her tag'in register_address - start_address offset'inden
              değer okur.
    """

    modbus_host: str
    modbus_port: int
    unit_id: int
    start_address: int
    count: int
    tags: list[TagRecord] = field(default_factory=list)

    @property
    def end_address(self) -> int:
        """Son register adresi (dahil)."""
        return self.start_address + self.count - 1

    @property
    def tag_count(self) -> int:
        """Bu batch'teki tag sayısı."""
        return len(self.tags)


def group_tags_for_batch_read(
    tags: list[TagRecord],
    gap_tolerance: int = 8,
) -> list[BatchGroup]:
    """Tag listesini batch gruplara ayırır.

    Algoritma:
        1. Tag'leri (modbus_host, modbus_port, unit_id) anahtarıyla grupla.
        2. Her grup içinde register_address'e göre sırala.
        3. Sıralı tag'leri soldan sağa gez, ardışık veya ``gap_tolerance``
           kadar yakın olanları tek batch'e ekle. Uzak adresler yeni batch
           başlatır.
        4. Bir batch MAX_REGISTERS_PER_BATCH sınırını geçerse ikiye böl.
        5. uint32/int32/float32 için word_span=2 dikkate alınır; batch'in
           son register adresi = tag.register_address + word_span - 1.

    Args:
        tags: Gruplanacak aktif tag listesi.
        gap_tolerance: İki komşu tag arasında izin verilen max register
            boşluğu. 0 → sadece tam ardışıklar birleşir, 8 → aradaki 8
            register'lık boşluk tek batch'te okunur (dummy register'lar
            decode edilmez, sadece offset'ten atlanır).

    Returns:
        BatchGroup listesi. Aynı host için sıralı adresleme yapılmış.
        Boş tag listesi için boş liste döner.

    Not:
        gap_tolerance negatif verilirse ValueError atar.
    """
    if gap_tolerance < 0:
        msg = f"gap_tolerance negatif olamaz: {gap_tolerance}"
        raise ValueError(msg)

    if not tags:
        return []

    # 1. Adım: host/port/unit_id bazlı gruplama
    by_host: dict[tuple[str, int, int], list[TagRecord]] = {}
    for tag in tags:
        key = (tag.modbus_host, tag.modbus_port, tag.unit_id)
        by_host.setdefault(key, []).append(tag)

    batches: list[BatchGroup] = []

    # 2-4. Adım: her host grubu içinde adrese göre sırala + gap toleransı
    # ile birleştir + sınır aşımında böl.
    for (host, port, unit_id), host_tags in by_host.items():
        sorted_tags = sorted(host_tags, key=lambda t: t.register_address)
        batches.extend(
            _group_sorted_host_tags(
                host=host,
                port=port,
                unit_id=unit_id,
                sorted_tags=sorted_tags,
                gap_tolerance=gap_tolerance,
            )
        )

    return batches


def _group_sorted_host_tags(
    *,
    host: str,
    port: int,
    unit_id: int,
    sorted_tags: list[TagRecord],
    gap_tolerance: int,
) -> list[BatchGroup]:
    """Aynı host altında adrese göre sıralı tag'leri batch'lere böler.

    Greedy algoritma: her yeni tag, mevcut batch'in son adresine yeterince
    yakınsa batch'e eklenir; değilse yeni batch başlatır. MAX_REGISTERS_PER_BATCH
    sınırını aşan durumda da yeni batch açılır.
    """
    if not sorted_tags:
        return []

    batches: list[BatchGroup] = []
    current_tags: list[TagRecord] = []
    current_start: int = sorted_tags[0].register_address
    current_end: int = current_start - 1  # henüz hiç tag eklenmedi

    for tag in sorted_tags:
        tag_span = _word_span(tag.register_type)
        tag_last = tag.register_address + tag_span - 1

        if not current_tags:
            # İlk tag'i yeni batch'e yerleştir
            current_start = tag.register_address
            current_end = tag_last
            current_tags = [tag]
            continue

        # Ardışıklık/gap kontrolü — mevcut batch'in son adresinden sonraki
        # register'ı hesaplayıp gap hesabı.
        gap = tag.register_address - (current_end + 1)
        prospective_last = max(current_end, tag_last)
        prospective_count = prospective_last - current_start + 1

        fits_gap = gap <= gap_tolerance
        fits_size = prospective_count <= MAX_REGISTERS_PER_BATCH

        if fits_gap and fits_size:
            # Mevcut batch'e ekle
            current_tags.append(tag)
            current_end = prospective_last
        else:
            # Mevcut batch'i kapat, yenisini başlat
            batches.append(
                BatchGroup(
                    modbus_host=host,
                    modbus_port=port,
                    unit_id=unit_id,
                    start_address=current_start,
                    count=current_end - current_start + 1,
                    tags=current_tags,
                )
            )
            current_start = tag.register_address
            current_end = tag_last
            current_tags = [tag]

    # Son batch'i kapat
    if current_tags:
        batches.append(
            BatchGroup(
                modbus_host=host,
                modbus_port=port,
                unit_id=unit_id,
                start_address=current_start,
                count=current_end - current_start + 1,
                tags=current_tags,
            )
        )

    return batches


__all__ = [
    "MAX_REGISTERS_PER_BATCH",
    "BatchGroup",
    "group_tags_for_batch_read",
]
