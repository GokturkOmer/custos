"""AVM asset template paketi (F9).

YAML dosyalarından asset template tanımlarını okur, pydantic ile doğrular,
DB abstract arayüzü üzerinden seed eder. Tag binding instance seviyesinde
yapıldığı için bu modül generic (rol anahtarı bazlı) şablonlar üretir.
"""

from __future__ import annotations

from custos.analytics.templates.loader import (
    AlarmDefaultSchema,
    KpiSchema,
    MaintenanceDefaultSchema,
    RoleSchema,
    TemplateLoadError,
    TemplateSchema,
    default_template_dir,
    load_template_file,
    load_templates,
)

__all__ = [
    "AlarmDefaultSchema",
    "KpiSchema",
    "MaintenanceDefaultSchema",
    "RoleSchema",
    "TemplateLoadError",
    "TemplateSchema",
    "default_template_dir",
    "load_template_file",
    "load_templates",
]
