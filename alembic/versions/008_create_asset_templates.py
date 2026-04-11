"""Asset template, role ve KPI tanım tablolarını oluştur.

Her template bir endüstriyel ekipman tipini temsil eder (pompa, chiller vb.).
Template role'ler beklenen tag yuvalarını, KPI definitions ise hesaplanacak
formülleri tanımlar. Seed verisi olarak 6 temel template eklenir.

Revision ID: 008
Revises: 007
Create Date: 2026-04-11
"""

from collections.abc import Sequence

from alembic import op

revision: str = "008"
down_revision: str | None = "007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """asset_templates, template_roles ve kpi_definitions tablolarını oluştur + seed data."""
    # --- Tablolar ---
    op.execute(
        """
        CREATE TABLE asset_templates (
            id SERIAL PRIMARY KEY,
            slug TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            icon TEXT NOT NULL DEFAULT 'cpu',
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        """
    )

    op.execute(
        """
        CREATE TABLE template_roles (
            id SERIAL PRIMARY KEY,
            template_id INTEGER NOT NULL REFERENCES asset_templates(id) ON DELETE CASCADE,
            role_key TEXT NOT NULL,
            label TEXT NOT NULL,
            unit_hint TEXT NOT NULL DEFAULT '',
            required BOOLEAN NOT NULL DEFAULT TRUE,
            sort_order INTEGER NOT NULL DEFAULT 0,
            UNIQUE(template_id, role_key)
        );
        """
    )

    op.execute(
        """
        CREATE TABLE kpi_definitions (
            id SERIAL PRIMARY KEY,
            template_id INTEGER NOT NULL REFERENCES asset_templates(id) ON DELETE CASCADE,
            name TEXT NOT NULL,
            formula TEXT NOT NULL,
            unit TEXT NOT NULL DEFAULT '',
            description TEXT NOT NULL DEFAULT '',
            UNIQUE(template_id, name)
        );
        """
    )

    # --- Seed: Pompa ---
    op.execute(
        """
        INSERT INTO asset_templates (slug, name, description, icon) VALUES
        ('pump', 'Pompa', 'Sirkülasyon, besleme veya transfer pompaları', 'activity');
        """
    )
    op.execute(
        """
        INSERT INTO template_roles (template_id, role_key, label, unit_hint, required, sort_order)
        SELECT id, r.role_key, r.label, r.unit_hint, r.required, r.sort_order
        FROM asset_templates, (VALUES
            ('suction_pressure',   'Emme Basıncı',    'bar', TRUE,  1),
            ('discharge_pressure', 'Basma Basıncı',   'bar', TRUE,  2),
            ('motor_current',      'Motor Akımı',     'A',   TRUE,  3),
            ('flow_rate',          'Debi',            'm³/h', TRUE, 4),
            ('winding_temperature','Sargı Sıcaklığı', '°C',  FALSE, 5)
        ) AS r(role_key, label, unit_hint, required, sort_order)
        WHERE slug = 'pump';
        """
    )
    op.execute(
        """
        INSERT INTO kpi_definitions (template_id, name, formula, unit, description)
        SELECT id, k.name, k.formula, k.unit, k.description
        FROM asset_templates, (VALUES
            ('specific_energy',       'motor_current * 400 / flow_rate',
             'kWh/m³', 'Spesifik enerji tüketimi'),
            ('differential_pressure', 'discharge_pressure - suction_pressure',
             'bar',    'Basma-emme basınç farkı')
        ) AS k(name, formula, unit, description)
        WHERE slug = 'pump';
        """
    )

    # --- Seed: Chiller ---
    op.execute(
        """
        INSERT INTO asset_templates (slug, name, description, icon) VALUES
        ('chiller', 'Chiller', 'Soğutma grubu / chiller ünitesi', 'activity');
        """
    )
    op.execute(
        """
        INSERT INTO template_roles (template_id, role_key, label, unit_hint, required, sort_order)
        SELECT id, r.role_key, r.label, r.unit_hint, r.required, r.sort_order
        FROM asset_templates, (VALUES
            ('supply_temp',          'Gidiş Sıcaklığı',    '°C',  TRUE,  1),
            ('return_temp',          'Dönüş Sıcaklığı',    '°C',  TRUE,  2),
            ('compressor_current',   'Kompresör Akımı',     'A',   TRUE,  3),
            ('refrigerant_pressure', 'Soğutucu Basıncı',   'bar', FALSE, 4),
            ('ambient_temp',         'Ortam Sıcaklığı',    '°C',  FALSE, 5)
        ) AS r(role_key, label, unit_hint, required, sort_order)
        WHERE slug = 'chiller';
        """
    )
    op.execute(
        """
        INSERT INTO kpi_definitions (template_id, name, formula, unit, description)
        SELECT id, k.name, k.formula, k.unit, k.description
        FROM asset_templates, (VALUES
            ('delta_t',   'return_temp - supply_temp',
             '°C',  'Gidiş-dönüş sıcaklık farkı'),
            ('cop_proxy', 'delta_t / compressor_current',
             '°C/A', 'COP yaklaşık göstergesi')
        ) AS k(name, formula, unit, description)
        WHERE slug = 'chiller';
        """
    )

    # --- Seed: Plakalı Eşanjör ---
    op.execute(
        """
        INSERT INTO asset_templates (slug, name, description, icon) VALUES
        ('plate_heat_exchanger', 'Plakalı Eşanjör',
         'Plakalı ısı değiştiricisi', 'activity');
        """
    )
    op.execute(
        """
        INSERT INTO template_roles (template_id, role_key, label, unit_hint, required, sort_order)
        SELECT id, r.role_key, r.label, r.unit_hint, r.required, r.sort_order
        FROM asset_templates, (VALUES
            ('hot_in',    'Sıcak Giriş',  '°C',   TRUE,  1),
            ('hot_out',   'Sıcak Çıkış',  '°C',   TRUE,  2),
            ('cold_in',   'Soğuk Giriş',  '°C',   TRUE,  3),
            ('cold_out',  'Soğuk Çıkış',  '°C',   TRUE,  4),
            ('flow_rate', 'Debi',         'm³/h', FALSE, 5)
        ) AS r(role_key, label, unit_hint, required, sort_order)
        WHERE slug = 'plate_heat_exchanger';
        """
    )
    op.execute(
        """
        INSERT INTO kpi_definitions (template_id, name, formula, unit, description)
        SELECT id, k.name, k.formula, k.unit, k.description
        FROM asset_templates, (VALUES
            ('effectiveness',  '(hot_in - hot_out) / (hot_in - cold_in)',
             'ratio', 'Isı değiştirici etkinliği'),
            ('approach_temp',  'hot_out - cold_in',
             '°C',    'Yaklaşım sıcaklığı')
        ) AS k(name, formula, unit, description)
        WHERE slug = 'plate_heat_exchanger';
        """
    )

    # --- Seed: Hava Kompresörü ---
    op.execute(
        """
        INSERT INTO asset_templates (slug, name, description, icon) VALUES
        ('air_compressor', 'Hava Kompresörü',
         'Endüstriyel basınçlı hava kompresörü', 'activity');
        """
    )
    op.execute(
        """
        INSERT INTO template_roles (template_id, role_key, label, unit_hint, required, sort_order)
        SELECT id, r.role_key, r.label, r.unit_hint, r.required, r.sort_order
        FROM asset_templates, (VALUES
            ('discharge_pressure', 'Çıkış Basıncı',    'bar', TRUE,  1),
            ('motor_current',      'Motor Akımı',       'A',   TRUE,  2),
            ('oil_temp',           'Yağ Sıcaklığı',    '°C',  FALSE, 3),
            ('ambient_temp',       'Ortam Sıcaklığı',  '°C',  FALSE, 4)
        ) AS r(role_key, label, unit_hint, required, sort_order)
        WHERE slug = 'air_compressor';
        """
    )
    op.execute(
        """
        INSERT INTO kpi_definitions (template_id, name, formula, unit, description)
        SELECT id, k.name, k.formula, k.unit, k.description
        FROM asset_templates, (VALUES
            ('specific_power', 'motor_current * 400 / discharge_pressure',
             'W/bar', 'Spesifik güç tüketimi')
        ) AS k(name, formula, unit, description)
        WHERE slug = 'air_compressor';
        """
    )

    # --- Seed: Generic Motor ---
    op.execute(
        """
        INSERT INTO asset_templates (slug, name, description, icon) VALUES
        ('generic_motor', 'Generic Motor',
         'Genel amaçlı elektrik motoru', 'activity');
        """
    )
    op.execute(
        """
        INSERT INTO template_roles (template_id, role_key, label, unit_hint, required, sort_order)
        SELECT id, r.role_key, r.label, r.unit_hint, r.required, r.sort_order
        FROM asset_templates, (VALUES
            ('current',      'Akım',            'A',    TRUE,  1),
            ('voltage',      'Gerilim',         'V',    FALSE, 2),
            ('winding_temp', 'Sargı Sıcaklığı', '°C',  FALSE, 3),
            ('vibration',    'Titreşim',        'mm/s', FALSE, 4)
        ) AS r(role_key, label, unit_hint, required, sort_order)
        WHERE slug = 'generic_motor';
        """
    )
    op.execute(
        """
        INSERT INTO kpi_definitions (template_id, name, formula, unit, description)
        SELECT id, k.name, k.formula, k.unit, k.description
        FROM asset_templates, (VALUES
            ('apparent_power', 'current * voltage',
             'VA', 'Görünür güç')
        ) AS k(name, formula, unit, description)
        WHERE slug = 'generic_motor';
        """
    )

    # --- Seed: Generic Tank ---
    op.execute(
        """
        INSERT INTO asset_templates (slug, name, description, icon) VALUES
        ('generic_tank', 'Generic Tank',
         'Genel amaçlı depolama tankı', 'sliders');
        """
    )
    op.execute(
        """
        INSERT INTO template_roles (template_id, role_key, label, unit_hint, required, sort_order)
        SELECT id, r.role_key, r.label, r.unit_hint, r.required, r.sort_order
        FROM asset_templates, (VALUES
            ('level',       'Seviye',    '%',   TRUE,  1),
            ('temperature', 'Sıcaklık',  '°C',  FALSE, 2),
            ('pressure',    'Basınç',    'bar', FALSE, 3)
        ) AS r(role_key, label, unit_hint, required, sort_order)
        WHERE slug = 'generic_tank';
        """
    )
    # Generic Tank için KPI tanımı yok (eşik alarmları yeterli)


def downgrade() -> None:
    """asset_templates, template_roles ve kpi_definitions tablolarını kaldır."""
    op.execute("DROP TABLE IF EXISTS kpi_definitions;")
    op.execute("DROP TABLE IF EXISTS template_roles;")
    op.execute("DROP TABLE IF EXISTS asset_templates;")
