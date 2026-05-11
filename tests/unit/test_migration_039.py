"""Migration 039 (wind_event_metadata) unit testleri.

Bu testler DB calistirmaz — alembic op API'sini ve guard'i mock'lar:
- ``custos_wind`` DB adi geldiginde ``op.create_table`` + 2 ``op.create_index``
  cagrilir; tablo adi/kolon listesi/indeks isimleri dogrulanir.
- Farkli DB adi (production ``custos``, ``postgres``, vb.) NO-OP olur —
  hicbir DDL cagrilmaz.
- ``downgrade`` simetrik davranir (custos_wind → drop, diger → NO-OP).
- ``upgrade`` + ``downgrade`` ardarda kosturuldugunda durum baslangic
  noktasina doner ("revertable").
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any
from unittest.mock import MagicMock, patch


def _load_migration_module() -> ModuleType:
    """Alembic migration dosyasini path-by-path yukle.

    Dosya adi rakamla basladigi icin ``import alembic.versions.039_...`` calismaz.
    """
    repo_root = Path(__file__).resolve().parents[2]
    migration_path = repo_root / "alembic" / "versions" / "039_wind_event_metadata.py"
    spec = importlib.util.spec_from_file_location(
        "wind_event_metadata_039",
        migration_path,
    )
    if spec is None or spec.loader is None:
        msg = f"Migration modulu yuklenemedi: {migration_path}"
        raise RuntimeError(msg)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


migration = _load_migration_module()


def _make_get_bind_with_db(db_name: str) -> Any:
    """``op.get_bind`` mock'u — istenen DB adini ``scalar()`` ile dondurur."""

    def _factory() -> MagicMock:
        bind = MagicMock()
        bind.execute.return_value.scalar.return_value = db_name
        return bind

    return _factory


def test_upgrade_in_custos_wind_creates_table_and_two_indexes() -> None:
    """custos_wind DB'sinde upgrade → 1 create_table + 2 create_index cagrilir."""
    with (
        patch.object(migration.op, "get_bind", _make_get_bind_with_db("custos_wind")),
        patch.object(migration.op, "create_table") as mock_create_table,
        patch.object(migration.op, "create_index") as mock_create_index,
    ):
        migration.upgrade()

        # Tablo bir kez ve dogru isimle olusturuldu.
        assert mock_create_table.call_count == 1
        args, _kwargs = mock_create_table.call_args
        assert args[0] == "wind_event_metadata"

        # Tabloda beklenen kolonlar var (Column nesnelerinin .name'i ile dogrula).
        column_names = [c.name for c in args[1:] if hasattr(c, "name")]
        for expected in (
            "id",
            "asset_instance_id",
            "timestamp",
            "status_type_id",
            "train_test",
            "original_event_id",
            "original_asset_id",
        ):
            assert expected in column_names, f"Eksik kolon: {expected}"

        # 2 indeks olusturuldu ve birinde compound (asset_instance_id, timestamp).
        assert mock_create_index.call_count == 2
        index_names = [call.args[0] for call in mock_create_index.call_args_list]
        assert "ix_wind_event_meta_asset_ts" in index_names
        assert "ix_wind_event_meta_status" in index_names

        # Compound indeks kolon listesi (asset_instance_id, timestamp) sirasinda olmali.
        asset_ts_call = next(
            c for c in mock_create_index.call_args_list
            if c.args[0] == "ix_wind_event_meta_asset_ts"
        )
        assert asset_ts_call.args[2] == ["asset_instance_id", "timestamp"]


def test_upgrade_in_custos_production_is_noop() -> None:
    """Production custos DB'sinde upgrade NO-OP — hicbir DDL cagrilmaz."""
    with (
        patch.object(migration.op, "get_bind", _make_get_bind_with_db("custos")),
        patch.object(migration.op, "create_table") as mock_create_table,
        patch.object(migration.op, "create_index") as mock_create_index,
    ):
        migration.upgrade()

        assert mock_create_table.call_count == 0
        assert mock_create_index.call_count == 0


def test_upgrade_in_unknown_db_is_noop() -> None:
    """Beklenmeyen DB adi (ornegin postgres) icin de NO-OP davranis."""
    with (
        patch.object(migration.op, "get_bind", _make_get_bind_with_db("postgres")),
        patch.object(migration.op, "create_table") as mock_create_table,
        patch.object(migration.op, "create_index") as mock_create_index,
    ):
        migration.upgrade()

        assert mock_create_table.call_count == 0
        assert mock_create_index.call_count == 0


def test_downgrade_in_custos_wind_drops_table_and_indexes() -> None:
    """custos_wind'de downgrade → drop_index 2x + drop_table cagrilir."""
    with (
        patch.object(migration.op, "get_bind", _make_get_bind_with_db("custos_wind")),
        patch.object(migration.op, "drop_index") as mock_drop_index,
        patch.object(migration.op, "drop_table") as mock_drop_table,
    ):
        migration.downgrade()

        assert mock_drop_table.call_count == 1
        assert mock_drop_table.call_args.args[0] == "wind_event_metadata"
        assert mock_drop_index.call_count == 2


def test_downgrade_in_custos_production_is_noop() -> None:
    """Production custos DB'sinde downgrade NO-OP."""
    with (
        patch.object(migration.op, "get_bind", _make_get_bind_with_db("custos")),
        patch.object(migration.op, "drop_index") as mock_drop_index,
        patch.object(migration.op, "drop_table") as mock_drop_table,
    ):
        migration.downgrade()

        assert mock_drop_table.call_count == 0
        assert mock_drop_index.call_count == 0


def test_upgrade_then_downgrade_is_symmetric() -> None:
    """Revert testi: upgrade + downgrade ardarda kosulunca simetri korunur.

    Mock'tan kaynaklanan calismayi yakalamayiz (gercek DB olmadan idempotency
    sadece DDL cagri sayisi ile dogrulanir). Asil amac: downgrade'in upgrade'in
    her yarattigi nesneye karsi bir drop cagirisi yaptigini gostermek.
    """
    create_calls: list[str] = []
    drop_calls: list[str] = []

    def _track_create_table(name: str, *_args: Any, **_kwargs: Any) -> None:
        create_calls.append(f"table:{name}")

    def _track_create_index(name: str, *_args: Any, **_kwargs: Any) -> None:
        create_calls.append(f"index:{name}")

    def _track_drop_table(name: str, *_args: Any, **_kwargs: Any) -> None:
        drop_calls.append(f"table:{name}")

    def _track_drop_index(name: str, *_args: Any, **_kwargs: Any) -> None:
        drop_calls.append(f"index:{name}")

    with (
        patch.object(migration.op, "get_bind", _make_get_bind_with_db("custos_wind")),
        patch.object(migration.op, "create_table", side_effect=_track_create_table),
        patch.object(migration.op, "create_index", side_effect=_track_create_index),
        patch.object(migration.op, "drop_table", side_effect=_track_drop_table),
        patch.object(migration.op, "drop_index", side_effect=_track_drop_index),
    ):
        migration.upgrade()
        migration.downgrade()

    # Her create icin bir drop var.
    assert sorted(create_calls) == sorted(drop_calls), (
        f"Asimetrik degisim:\n  create: {create_calls}\n  drop:   {drop_calls}"
    )


def test_target_db_name_is_custos_wind() -> None:
    """Sabit dogrulama: hedef DB adi prod schema'sina kazara dokunmasin."""
    assert migration._TARGET_DB_NAME == "custos_wind"
