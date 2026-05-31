"""alarm_events tablosuna pushed_at kolonu ekle (push-dispatch ayrımı).

Eşik tabanlı alarm üretimi Critical loop'a taşındı (review H1 / ADR-001).
Critical alarm'ı yazar ama PUSH GÖNDERMEZ — pywebpush + VAPID + abonelikler
Analytics'e ait kalır (Critical minimal bağımlılık ilkesi). Analytics'teki
push-dispatch loop'u henüz iletilmemiş (``pushed_at IS NULL``), test olmayan
``source='threshold'`` alarm'larını çekip gönderir ve ``pushed_at``'i set ederek
tek-sefer iletim sağlar.

Diğer kaynaklar (rate_of_change / cross_sensor / liveness / spc / escalation)
mevcut davranışlarını korur — push'ları kendi yollarında inline kalır; bu
yüzden kısmi indeks ``source='threshold'`` ile sınırlanır (yalnız dispatcher'ın
çektiği satırları kapsar → steady-state'te ~0 satır, indeks bloat'u yok).

Geri-alınabilir: ``downgrade`` indeks + kolonu kaldırır.

Revision ID: 039
Revises: 038
Create Date: 2026-05-31
"""

from collections.abc import Sequence

from alembic import op

revision: str = "039"
down_revision: str | None = "038"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """pushed_at kolonu + bekleyen-threshold-push kısmi indeksi ekle."""
    op.execute(
        """
        ALTER TABLE alarm_events ADD COLUMN pushed_at TIMESTAMPTZ;

        CREATE INDEX idx_alarm_events_pending_push
            ON alarm_events (triggered_at)
            WHERE pushed_at IS NULL AND is_test = false AND source = 'threshold';
        """
    )


def downgrade() -> None:
    """Kısmi indeksi + pushed_at kolonunu kaldır (geri-alınabilir)."""
    op.execute(
        """
        DROP INDEX IF EXISTS idx_alarm_events_pending_push;
        ALTER TABLE alarm_events DROP COLUMN pushed_at;
        """
    )
