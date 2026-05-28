"""Custos mimari kural denetim scripti.

CLAUDE.md'deki değişmez kuralları statik analiz ile doğrular. Her kural
bir glob scope içinde bir regex arar; eşleşme varsa ihlal raporlanır.

İstisna mekanizması:
Bir satıra `# allow-arch-check: <sebep>` yorumu eklenirse o satırdaki
ihlaller atlanır. Sebep zorunludur (grep edilebilir olsun diye).

Exit kodu:
- 0: Tüm kurallar yeşil
- 1: Bir veya daha fazla kural ihlali (detay stderr'e yazılır)

Kullanım: python scripts/architecture_check.py
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Final

REPO_ROOT: Final[Path] = Path(__file__).resolve().parent.parent
ALLOW_COMMENT_RE: Final[re.Pattern[str]] = re.compile(
    r"#\s*allow-arch-check\b"
)


@dataclass(frozen=True)
class Rule:
    """Tek bir mimari kural tanımı."""

    id: str
    description: str
    pattern: re.Pattern[str]
    scope: tuple[str, ...]
    scope_exclude: tuple[str, ...] = ()


@dataclass(frozen=True)
class Violation:
    """Bulunan bir kural ihlali kaydı."""

    rule: Rule
    file_path: Path
    line_no: int
    line_text: str


# CLAUDE.md'deki değişmez kurallar. Yeni kural eklerken: id UPPER_SNAKE,
# description tek cümle, scope glob (REPO_ROOT göreli), gerekiyorsa exclude.
RULES: Final[tuple[Rule, ...]] = (
    Rule(
        id="ML_IN_CRITICAL",
        description=(
            "Critical loop'ta ML/numeric kütüphanesi import edilemez "
            "(CLAUDE.md: sadece pymodbus + abstract DB arayüzü)."
        ),
        pattern=re.compile(
            r"^\s*(?:from|import)\s+"
            r"(?:sklearn|numpy|scipy|torch|tensorflow|keras|pandas|"
            r"xgboost|lightgbm|onnx|joblib|sentence_transformers|faiss)"
            r"\b"
        ),
        scope=("src/custos/critical/**/*.py",),
    ),
    Rule(
        id="ASYNCPG_IN_COLLECTOR",
        description=(
            "Collector asyncpg'yi doğrudan kullanamaz; "
            "shared/database.py abstract arayüzü üzerinden DB'ye gider."
        ),
        pattern=re.compile(r"^\s*(?:from|import)\s+asyncpg\b"),
        scope=("src/custos/critical/collector.py",),
    ),
    Rule(
        id="SQL_IN_COLLECTOR",
        description=(
            "Collector SQL string yazamaz; "
            "DatabaseInterface metodlarını çağırır."
        ),
        pattern=re.compile(
            r"\b(?:SELECT\s+\w|INSERT\s+INTO|UPDATE\s+\w+\s+SET|"
            r"DELETE\s+FROM|CREATE\s+TABLE|DROP\s+TABLE)\b"
        ),
        scope=("src/custos/critical/collector.py",),
    ),
    Rule(
        id="MODBUS_WRITE",
        description=(
            "Modbus write fonksiyonu yasak — "
            "sistem sadece okur, asla yazmaz (CLAUDE.md)."
        ),
        pattern=re.compile(r"\.write_(?:register|coil|registers|coils)\s*\("),
        scope=("src/custos/**/*.py",),
    ),
    Rule(
        id="DATETIME_NOW_NAIVE",
        description=(
            "datetime.now() parametresiz yasak; "
            "datetime.now(timezone.utc) kullan (UTC veritabanı)."
        ),
        pattern=re.compile(r"datetime\.now\(\s*\)"),
        scope=("src/custos/**/*.py",),
    ),
    Rule(
        id="DATETIME_UTCNOW",
        description=(
            "datetime.utcnow() yasak (deprecated + timezone-naive); "
            "datetime.now(timezone.utc) kullan."
        ),
        pattern=re.compile(r"datetime\.utcnow\s*\("),
        scope=("src/custos/**/*.py",),
    ),
    Rule(
        id="PRINT_STATEMENT",
        description=(
            "print() yasak; structlog kullan. Geçici `# noqa: T201` "
            "bile olsa temizlik borcu olduğu unutulmasın."
        ),
        pattern=re.compile(r"^\s*print\s*\("),
        scope=("src/custos/**/*.py",),
    ),
    Rule(
        id="ANALYTICS_IMPORTS_CRITICAL",
        description=(
            "Analytics/dashboard kodu critical loop'u import edemez "
            "(iki süreç bağımsızlığı — CLAUDE.md)."
        ),
        pattern=re.compile(r"^\s*(?:from|import)\s+custos\.critical\b"),
        scope=("src/custos/analytics/**/*.py",),
    ),
    Rule(
        id="SQL_OUTSIDE_DATABASE",
        description=(
            "SQL string yalnızca shared/database.py içinde yazılır "
            "(abstract DB arayüzü prensibi)."
        ),
        pattern=re.compile(
            r"\b(?:SELECT\s+\w|INSERT\s+INTO|UPDATE\s+\w+\s+SET|"
            r"DELETE\s+FROM)\b"
        ),
        scope=("src/custos/**/*.py",),
        scope_exclude=(
            "src/custos/shared/database.py",
            # Asistan AYRI süreç; SQL'i tek noktada (repository) toplar (karar B).
            "src/custos/assistant/repository.py",
        ),
    ),
    Rule(
        id="DEEP_LEARNING",
        description=(
            "Derin öğrenme framework'leri yasak "
            "(CLAUDE.md: 'Derin öğrenme yok. Sadece scikit-learn ailesi'). "
            "Sentence-transformers gibi yüksek seviye API'lar dolaylı "
            "PyTorch kullansa da kapsanmaz; doğrudan import yasak."
        ),
        pattern=re.compile(
            r"^\s*(?:from|import)\s+"
            r"(?:torch|tensorflow|keras|jax|flax|mxnet|"
            r"pytorch_lightning|xformers)"
            r"\b"
        ),
        scope=("src/custos/**/*.py",),
    ),
    Rule(
        id="DB_DRIVER_NON_ASYNCPG",
        description=(
            "asyncpg dışı DB driver/ORM import'u yasak "
            "(tek DB erişim noktası: shared/database.py + asyncpg)."
        ),
        pattern=re.compile(
            r"^\s*(?:from|import)\s+"
            r"(?:psycopg2?|sqlalchemy|aiopg|aiosqlite|sqlite3|"
            r"pymysql|mysql\.connector|mysqlclient|"
            r"pymongo|motor|peewee|tortoise|ormar|databases|"
            r"clickhouse_driver)"
            r"\b"
        ),
        scope=("src/custos/**/*.py",),
    ),
    Rule(
        id="CRITICAL_ANALYTICS_IMPORT_ASSISTANT",
        description=(
            "Critical/analytics kodu assistant servisini import edemez "
            "(üç süreç bağımsızlığı — CLAUDE.md / karar 2)."
        ),
        pattern=re.compile(r"^\s*(?:from|import)\s+custos\.assistant\b"),
        scope=(
            "src/custos/critical/**/*.py",
            "src/custos/analytics/**/*.py",
        ),
    ),
    Rule(
        id="ASSISTANT_IMPORTS_CRITICAL_ANALYTICS",
        description=(
            "Assistant servisi critical/analytics'i import edemez "
            "(üç süreç bağımsızlığı — CLAUDE.md / karar 2)."
        ),
        pattern=re.compile(
            r"^\s*(?:from|import)\s+custos\.(?:critical|analytics)\b"
        ),
        scope=("src/custos/assistant/**/*.py",),
    ),
    Rule(
        id="ASSISTANT_IMPORTS_SHARED_DATABASE",
        description=(
            "Assistant servisi shared/database.py'yi import edemez; kendi "
            "repository.py asyncpg pool'unu kullanır (karar B — DB izolasyonu)."
        ),
        pattern=re.compile(
            r"^\s*(?:from|import)\s+custos\.shared\.database\b"
        ),
        scope=("src/custos/assistant/**/*.py",),
    ),
)


def _iter_scope_files(
    scope: tuple[str, ...], excludes: tuple[str, ...]
) -> list[Path]:
    """Glob scope'a eşleşen .py dosyalarını (exclude hariç) sıralı döndür."""
    exclude_set = {(REPO_ROOT / p).resolve() for p in excludes}
    files: set[Path] = set()
    for pattern in scope:
        for match in REPO_ROOT.glob(pattern):
            if match.is_file() and match.resolve() not in exclude_set:
                files.add(match)
    return sorted(files)


def _scan_file(rule: Rule, path: Path) -> list[Violation]:
    """Dosyayı kural regex'ine karşı tara; allow-arch-check'li satırları atla."""
    violations: list[Violation] = []
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return violations
    for line_no, line in enumerate(text.splitlines(), start=1):
        if ALLOW_COMMENT_RE.search(line):
            continue
        if rule.pattern.search(line):
            violations.append(
                Violation(
                    rule=rule,
                    file_path=path,
                    line_no=line_no,
                    line_text=line.rstrip(),
                )
            )
    return violations


def main() -> int:
    """Tüm kuralları çalıştır; bulgu varsa stderr'e dök, exit 1."""
    all_violations: list[Violation] = []
    per_rule_counts: dict[str, int] = {}
    files_scanned: set[Path] = set()

    for rule in RULES:
        if not rule.scope:
            continue
        paths = _iter_scope_files(rule.scope, rule.scope_exclude)
        files_scanned.update(paths)
        rule_violations: list[Violation] = []
        for path in paths:
            rule_violations.extend(_scan_file(rule, path))
        per_rule_counts[rule.id] = len(rule_violations)
        all_violations.extend(rule_violations)

    print(  # noqa: T201
        f"Custos architecture check — {len(RULES)} kural, "
        f"{len(files_scanned)} dosya tarandı."
    )
    for rule in RULES:
        count = per_rule_counts.get(rule.id, 0)
        status = "OK  " if count == 0 else f"FAIL ({count})"
        print(f"  [{status}] {rule.id}")  # noqa: T201

    if not all_violations:
        print("\nTüm mimari kurallar yeşil.")  # noqa: T201
        return 0

    print("\nİhlaller:", file=sys.stderr)  # noqa: T201
    for v in all_violations:
        rel = v.file_path.relative_to(REPO_ROOT).as_posix()
        print(  # noqa: T201
            f"  {rel}:{v.line_no} [{v.rule.id}]\n"
            f"      {v.line_text}\n"
            f"      → {v.rule.description}",
            file=sys.stderr,
        )
    print(  # noqa: T201
        f"\nToplam {len(all_violations)} ihlal. "
        "Gerçek ihlalleri düzelt, false positive için satıra "
        "`# allow-arch-check: <sebep>` yorumu ekle.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
