"""diagslave Modbus register'larına 'yaşayan' veri yazan drift simülatörü.

Kapsam:
    Endurance test ortamı için 3rd-party diagslave (Modbus TCP slave)'e
    sürekli değişen register değerleri yazar. Custos kendi simülatörü
    KAPALI olduğu için sadece bu daemon "veri kaynağı" rolünü üstlenir.

Register Layout (custos.simulator.sensors.build_endurance_sensors uyumlu):
    Reg 1-50    Sıcaklık (T001-T050)   gain=0.1, raw 200-250 → 20-25 °C
    Reg 51-100  Basınç   (T051-T100)   gain=0.01, raw 100-1000 → 1-10 bar
    Reg 101-150 Enerji   (T101-T150)   gain=1.0, monoton artan kWh sayacı
    Reg 151-180 RPM      (T151-T180)   gain=1.0, raw 1000-2000 (30 tag)
    Reg 181-200 Status   (T181-T200)   gain=1.0, 0/1 boolean (20 tag)

Tick: 1 saniye. Custos collector polling 100ms-10sn → her sorguda yeni değer.

NOT (mimari kural istisnası):
    CLAUDE.md "Modbus client kodunda write_register/write_registers ASLA"
    kuralı production Custos collector için geçerlidir. Bu dosya bir
    simülatör yardımcısı (test ortamı), kontrolümüzdeki diagslave'e
    yazar. architecture_check.py için her write_registers çağrısında
    `# allow-arch-check: simulator helper` yorumu eklenmiştir.
"""
from __future__ import annotations

import logging
import math
import random
import signal
import sys
import time
from datetime import UTC, datetime

from pymodbus.client import ModbusTcpClient

# --- Yapılandırma sabitleri ---
DIAGSLAVE_HOST = "127.0.0.1"
DIAGSLAVE_PORT = 502
SLAVE_UNIT_ID = 1
TICK_SECONDS = 1.0

# Register layout (0-tabanlı offset; Modbus PDU adresleri)
TEMP_OFFSET = 0      # Reg 1-50 (50 tag)
PRES_OFFSET = 50     # Reg 51-100 (50 tag)
ENERGY_OFFSET = 100  # Reg 101-150 (50 tag, monoton artan)
RPM_OFFSET = 150     # Reg 151-180 (30 tag)
STATUS_OFFSET = 180  # Reg 181-200 (20 tag)

TEMP_COUNT = 50
PRES_COUNT = 50
ENERGY_COUNT = 50
RPM_COUNT = 30
STATUS_COUNT = 20

# Enerji sayacı uint16 max (65535) — yaklaşırsa rollover (gerçek hayatta meter reset)
ENERGY_ROLLOVER = 65000

logger = logging.getLogger("drift_simulator")


def _temp_raw(reg_idx: int, t: float) -> int:
    """Sıcaklık raw: sinüs 200-250 (gain 0.1 → 20-25 °C), 30 dk periyot."""
    base_celsius = 20.0 + 5.0 * math.sin(2 * math.pi * t / 1800 + reg_idx * 0.1)
    return int(base_celsius * 10)  # gain=0.1 ters çevirme


def _pressure_step(prev: int) -> int:
    """Basınç raw: random walk 100-1000 (gain 0.01 → 1-10 bar)."""
    delta = random.gauss(0, 3)
    new_val = prev + int(delta)
    return max(100, min(1000, new_val))


def _energy_step(prev: int) -> int:
    """Enerji raw: monoton artan kWh sayacı, tick başına 0-2 artış.

    uint16 sınırına yaklaşınca rollover (sayaç reset davranışı).
    """
    inc = random.choices([0, 1, 2], weights=[60, 35, 5])[0]
    new_val = prev + inc
    if new_val >= ENERGY_ROLLOVER:
        return 0
    return new_val


def _rpm(reg_idx: int, t: float) -> int:
    """RPM: sinüs 1000-2000 (10 dk periyot) + %5 olasılıkla spike noise."""
    base = 1500 + 500 * math.sin(2 * math.pi * t / 600 + reg_idx * 0.05)
    spike = random.randint(-50, 50) if random.random() < 0.05 else 0
    return max(0, int(base + spike))


# Status tag tipi gruplari (STATUS_TAGS sirasiyla, 0-tabanli reg_idx 180-199)
# RUNNING: cihaz çalışma durumu (5dk on / 5dk off)
_STATUS_RUNNING_INDICES = {0, 1, 4, 5, 6, 7, 8, 9, 14, 15, 16, 17}
# ALARM: alarm bitleri (normalde 0, nadiren 1) — FIRE_ALARM, EMERGENCY_STOP, vb.
_STATUS_ALARM_INDICES = {2, 3, 10, 11, 19}
# POWER: güç/güvenlik bitleri (normalde 1, nadiren 0) — POWER_OK, UPS_RUNNING, SECURITY
_STATUS_POWER_INDICES = {12, 13, 18}


def _status_step(prev: int, t: float, reg_idx: int) -> int:
    """Status bit: tag tipine göre 3 farklı pattern.

    - RUNNING (12 tag): 5 dk on / 5 dk off + reg_idx ile faz kayması, %2 noise
    - ALARM   ( 5 tag): %99.9 = 0 (nadiren 1) — yangın, gaz, alarm
    - POWER   ( 3 tag): %99.5 = 1 (nadiren 0) — güç, UPS, güvenlik
    """
    if reg_idx in _STATUS_ALARM_INDICES:
        # Alarm: çok nadir 1 (binlerce tick'te bir)
        return 1 if random.random() < 0.001 else 0
    if reg_idx in _STATUS_POWER_INDICES:
        # Power: çok nadir 0 (kısa kesinti simülasyonu)
        return 0 if random.random() < 0.005 else 1
    # RUNNING — cihaz on/off (default)
    period = 600
    deterministic = 1 if (t + reg_idx * 30) % period < 300 else 0
    if random.random() < 0.02:
        return 1 - deterministic
    return deterministic


def _setup_logging() -> None:
    """Stdout'a yapılandırılmış log."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s.%(msecs)03d [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
        stream=sys.stdout,
    )


_running = True


def _handle_sigterm(signum: int, frame: object) -> None:
    """systemd stop için graceful shutdown."""
    global _running
    logger.info("SIGTERM/SIGINT alindi, simulator duruyor.")
    _running = False


def main() -> int:
    """Ana döngü: her tick 5 register grubunu yazar, durana kadar devam eder."""
    _setup_logging()
    signal.signal(signal.SIGTERM, _handle_sigterm)
    signal.signal(signal.SIGINT, _handle_sigterm)

    client = ModbusTcpClient(DIAGSLAVE_HOST, port=DIAGSLAVE_PORT, timeout=3)
    if not client.connect():  # type: ignore[no-untyped-call]
        logger.error("diagslave'e baglanilamadi: %s:%s", DIAGSLAVE_HOST, DIAGSLAVE_PORT)
        return 2

    logger.info(
        "Drift simulator basladi: %s:%s slave_id=%d tick=%.1fs",
        DIAGSLAVE_HOST, DIAGSLAVE_PORT, SLAVE_UNIT_ID, TICK_SECONDS,
    )
    logger.info(
        "Layout: temp[1-50] pres[51-100] energy[101-150] rpm[151-180] status[181-200]"
    )

    # Persistent state init — diagslave RAM'inden mevcut degerleri oku.
    # Bu sayede mutator restart edildiginde enerji sayaci geri gitmez (Custos
    # Liveness Counter pipeline'i geri-giden sayac alarmi tetikliyor).
    pressure_state = [500] * PRES_COUNT     # Init mid-range (5 bar)
    energy_state = [0] * ENERGY_COUNT
    status_state = [0] * STATUS_COUNT
    try:
        existing_energy = client.read_holding_registers(
            ENERGY_OFFSET, count=ENERGY_COUNT, device_id=SLAVE_UNIT_ID,
        )
        if not existing_energy.isError() and existing_energy.registers:
            energy_state = list(existing_energy.registers)
            logger.info(
                "Energy state diagslave'den okundu (ilk: %d, son: %d, restart-safe)",
                energy_state[0], energy_state[-1],
            )
        else:
            energy_state = [random.randint(0, 1000) for _ in range(ENERGY_COUNT)]
            logger.warning("Energy state diagslave'den okunamadi, random init")
    except Exception as exc:  # noqa: BLE001
        energy_state = [random.randint(0, 1000) for _ in range(ENERGY_COUNT)]
        logger.warning("Energy state okuma istisnasi (%s), random init", exc)

    start_ts = time.time()
    tick_count = 0

    try:
        while _running:
            t = time.time() - start_ts

            # --- Sıcaklık (Reg 1-50, offset 0-49) ---
            temps = [_temp_raw(i, t) for i in range(TEMP_COUNT)]
            # allow-arch-check: simulator helper
            client.write_registers(TEMP_OFFSET, temps, device_id=SLAVE_UNIT_ID)

            # --- Basınç (Reg 51-100, offset 50-99) ---
            for i in range(PRES_COUNT):
                pressure_state[i] = _pressure_step(pressure_state[i])
            # allow-arch-check: simulator helper
            client.write_registers(PRES_OFFSET, pressure_state, device_id=SLAVE_UNIT_ID)

            # --- Enerji (Reg 101-150, offset 100-149) — monoton artan ---
            for i in range(ENERGY_COUNT):
                energy_state[i] = _energy_step(energy_state[i])
            # allow-arch-check: simulator helper
            client.write_registers(ENERGY_OFFSET, energy_state, device_id=SLAVE_UNIT_ID)

            # --- RPM (Reg 151-180, offset 150-179) — 30 tag ---
            rpms = [_rpm(i, t) for i in range(RPM_COUNT)]
            # allow-arch-check: simulator helper
            client.write_registers(RPM_OFFSET, rpms, device_id=SLAVE_UNIT_ID)

            # --- Status bits (Reg 181-200, offset 180-199) — 20 tag ---
            for i in range(STATUS_COUNT):
                status_state[i] = _status_step(status_state[i], t, i)
            # allow-arch-check: simulator helper
            client.write_registers(STATUS_OFFSET, status_state, device_id=SLAVE_UNIT_ID)

            tick_count += 1
            if tick_count % 60 == 0:
                logger.info(
                    "Tick=%d t=%.1fs sample temp=%d pres=%d energy=%d rpm=%d status=%d",
                    tick_count, t, temps[0], pressure_state[0],
                    energy_state[0], rpms[0], status_state[0],
                )

            time.sleep(TICK_SECONDS)
    finally:
        client.close()  # type: ignore[no-untyped-call]
        end_ts = datetime.now(UTC).isoformat()
        logger.info("Simulator durdu, toplam tick=%d, kapanis=%s", tick_count, end_ts)

    return 0


if __name__ == "__main__":
    sys.exit(main())
