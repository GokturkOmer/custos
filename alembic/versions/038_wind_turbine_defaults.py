"""Wind turbine default thresholds (Faz 1.1 — sadece custos_wind DB'sine).

Faz 1.1 (2026-05-12). Pivot türbin sahasi icin 25 default threshold ekler.
AVM production (``custos``) DB'sine yanlislikla alembic upgrade head
calistirilirsa bu migration **NO-OP** olur — uzerinde ``current_database()``
kontrolu yapilir. Sadece ``custos_wind`` DB'sinde calisir.

Migration tag'lerin var olmasini gerektirmez: ``INSERT ... WHERE EXISTS
(SELECT 1 FROM tags ...)`` pattern'i ile tag yoksa sessizce atlanir. Bu
sayede migration ``alembic upgrade head`` ile tag bulk-import'undan
**ONCE** kosturulabilir; tag'ler import edildikten sonra ayni migration
yeniden kosulup eksik threshold'lar tamamlanabilir (idempotent).

Threshold dagilimi (25 toplam):
- 6 sicaklik warn (gearbox bearing/oil, gen bearing DE/NDE, HV trafo, hydraulic)
- 4 sicaklik crit (gearbox oil 85°C, gearbox bearing 95°C, HV trafo 110°C, gen stator 130°C)
- 3 stator faz tutarsizlik (warn 130°C 3 faz)
- 2 RPM warn (rotor 22 rpm, generator 2100 rpm)
- 1 wind speed cut-out warn (25 m/s)
- 2 voltage warn (low <380V, high >480V faz 1)
- 1 grid frequency warn (49.5-50.5 Hz)
- 1 nacelle temp warn (50°C)
- 1 hub controller temp warn (70°C)
- 1 nose cone temp warn (60°C)
- 1 grid power motoring (-100 kW lower bound, reverse-power)

Tag adlari ``_personal/wind_pivot/tag_map_farm_a.csv`` ile birebir uyumlu.

Revision ID: 038
Revises: 037
Create Date: 2026-05-12
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "038"
down_revision: str | None = "037"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Migration sadece bu DB adi altinda aktif olur. Diger DB'lerde NO-OP.
_TARGET_DB_NAME = "custos_wind"


# Eklenecek threshold tanimlari (custos_wind icin). Sira: tag_id, name,
# direction, set_point, severity, debounce_seconds, hysteresis.
# DB UNIQUE constraint: (tag_id, name) — ON CONFLICT DO NOTHING idempotent.
_THRESHOLD_ROWS: tuple[tuple[str, str, str, float, str, int, float], ...] = (
    # Disli kutusu — Farm A'da 3 anomaly bu sebepli (gearbox failure)
    ("wind_t_gearbox_oil_temp", "Disli yagi yuksek", "high", 75.0, "warn", 300, 2.0),
    ("wind_t_gearbox_oil_temp", "Disli yagi kritik", "high", 85.0, "crit", 120, 2.0),
    ("wind_t_gearbox_hss_bearing_temp", "Disli yatak yuksek", "high", 85.0, "warn", 300, 2.0),
    ("wind_t_gearbox_hss_bearing_temp", "Disli yatak kritik", "high", 95.0, "crit", 60, 2.0),
    # Jeneratör yataklari — Farm A'da 2 anomaly (generator bearing failure)
    ("wind_t_gen_bearing_de_temp", "Gen yatak DE yuksek", "high", 90.0, "warn", 300, 2.0),
    ("wind_t_gen_bearing_nde_temp", "Gen yatak NDE yuksek", "high", 90.0, "warn", 300, 2.0),
    # Stator sargi sicakliklari (3 faz)
    ("wind_t_gen_stator_phase1_temp", "Stator faz1 kritik", "high", 130.0, "crit", 120, 3.0),
    ("wind_t_gen_stator_phase2_temp", "Stator faz2 kritik", "high", 130.0, "crit", 120, 3.0),
    ("wind_t_gen_stator_phase3_temp", "Stator faz3 kritik", "high", 130.0, "crit", 120, 3.0),
    # HV trafo — Farm A'da 1 anomaly (transformer failure)
    ("wind_t_hv_transformer_l1_temp", "HV trafo L1 yuksek", "high", 90.0, "warn", 600, 3.0),
    ("wind_t_hv_transformer_l1_temp", "HV trafo L1 kritik", "high", 110.0, "crit", 120, 3.0),
    ("wind_t_hv_transformer_l2_temp", "HV trafo L2 yuksek", "high", 90.0, "warn", 600, 3.0),
    ("wind_t_hv_transformer_l3_temp", "HV trafo L3 yuksek", "high", 90.0, "warn", 600, 3.0),
    # Hidrolik grup — Farm A'da 5 anomaly (en sik fault tipi)
    ("wind_t_hydraulic_oil_temp", "Hidrolik yag yuksek", "high", 70.0, "warn", 300, 2.0),
    # RPM overspeed (mekanik koruma)
    ("wind_t_rotor_rpm_avg", "Rotor overspeed", "high", 22.0, "warn", 30, 0.5),
    ("wind_t_generator_rpm_avg", "Jen overspeed", "high", 2100.0, "warn", 30, 20.0),
    # Ruzgar hizi cut-out (turbin durdurma esigi)
    ("wind_t_wind_speed_avg", "Cut-out ruzgar hizi", "high", 25.0, "warn", 60, 1.0),
    # Sebeke voltaji — faz 1 ornek (saha keşfinde 2 ve 3 elle eklenebilir)
    ("wind_t_voltage_phase1", "Faz 1 dusuk gerilim", "low", 380.0, "warn", 30, 5.0),
    ("wind_t_voltage_phase1", "Faz 1 yuksek gerilim", "high", 480.0, "warn", 30, 5.0),
    # Sebeke frekansi (yan band)
    ("wind_t_grid_frequency", "Sebeke frekans yuksek", "high", 50.5, "warn", 30, 0.1),
    ("wind_t_grid_frequency", "Sebeke frekans dusuk", "low", 49.5, "warn", 30, 0.1),
    # Nasel ici (yangin/havalandirma)
    ("wind_t_nacelle_temp", "Nasel yuksek sicaklik", "high", 50.0, "warn", 600, 2.0),
    # Hub PLC sicakligi
    ("wind_t_hub_controller_temp", "Hub PLC sicakligi yuksek", "high", 70.0, "warn", 600, 2.0),
    # Nose cone (spinner) sicakligi
    ("wind_t_nose_cone_temp", "Nose cone sicakligi yuksek", "high", 60.0, "warn", 600, 2.0),
    # Sebeke aktif guc motoring (negatif — reverse-power koruma)
    ("wind_t_grid_power_avg", "Motoring (negatif aktif guc)", "low", -100.0, "warn", 60, 10.0),
)


# Parametrize INSERT — tag yoksa WHERE EXISTS ile sessizce atla.
# Bu pattern migration'i tag bulk-import'tan ONCE veya SONRA kosturulabilir
# kilar: tag'ler eksikken sadece o satir atlanir, transaction rollback olmaz.
_INSERT_SQL = sa.text(
    """
    INSERT INTO thresholds (
        tag_id, name, direction, set_point, severity,
        debounce_seconds, hysteresis, enabled
    )
    SELECT :tag_id, :name, :direction, :set_point, :severity,
           :debounce_seconds, :hysteresis, TRUE
    WHERE EXISTS (SELECT 1 FROM tags WHERE tag_id = :tag_id)
    ON CONFLICT (tag_id, name) DO NOTHING;
    """,
)

_DELETE_SQL = sa.text(
    """
    DELETE FROM thresholds
    WHERE tag_id = :tag_id AND name = :name;
    """,
)

_CURRENT_DB_SQL = sa.text("SELECT current_database()")


def _current_db() -> str:
    """Aktif PostgreSQL DB adini dondurur (production guard'i icin)."""
    bind = op.get_bind()
    return str(bind.execute(_CURRENT_DB_SQL).scalar() or "")


def upgrade() -> None:
    """Wind turbine default threshold'larini ekler — sadece custos_wind'de.

    Diger DB'lerde NO-OP. Idempotent:
    - ON CONFLICT (tag_id, name) DO NOTHING — ayni migration ikinci kez
      kosturulursa duplicate eklemez.
    - WHERE EXISTS (SELECT 1 FROM tags) — tag yoksa o satir atlanir
      (FK constraint hatasi olmaz, transaction rollback olmaz). Bu sayede
      migration tag bulk-import'tan ONCE veya SONRA kosturulabilir.
    """
    current_db = _current_db()
    if current_db != _TARGET_DB_NAME:
        # NO-OP — production custos veya farkli DB. Sessizce gec.
        print(  # noqa: T201
            f"038_wind_turbine_defaults: NO-OP "
            f"(current_database={current_db!r}, target={_TARGET_DB_NAME!r})",
        )
        return

    bind = op.get_bind()
    inserted = 0
    skipped = 0
    for tag_id, name, direction, set_point, severity, debounce, hyst in _THRESHOLD_ROWS:
        result = bind.execute(
            _INSERT_SQL,
            {
                "tag_id": tag_id,
                "name": name,
                "direction": direction,
                "set_point": set_point,
                "severity": severity,
                "debounce_seconds": debounce,
                "hysteresis": hyst,
            },
        )
        # rowcount: 0 → tag yok (WHERE EXISTS) veya zaten var (ON CONFLICT)
        # 1 → yeni satir eklendi.
        if result.rowcount == 1:
            inserted += 1
        else:
            skipped += 1

    print(  # noqa: T201
        f"038_wind_turbine_defaults: DB={current_db!r}, "
        f"{inserted} threshold eklendi, {skipped} atlandi "
        f"(tag yok veya zaten var)",
    )


def downgrade() -> None:
    """Eklenen threshold'lari geri alir — sadece custos_wind'de.

    Diger DB'lerde NO-OP (upgrade hicbir sey eklemedi, downgrade da silmemeli).
    Tag_id + name kombinasyonu sadece bu migration'da eklenmis kayitlari
    hedefler; operatörün manuel eklediği threshold'lar dokunulmaz.
    """
    current_db = _current_db()
    if current_db != _TARGET_DB_NAME:
        print(  # noqa: T201
            f"038_wind_turbine_defaults downgrade: NO-OP "
            f"(current_database={current_db!r})",
        )
        return

    bind = op.get_bind()
    for tag_id, name, *_rest in _THRESHOLD_ROWS:
        bind.execute(_DELETE_SQL, {"tag_id": tag_id, "name": name})
