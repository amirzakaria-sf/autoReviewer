"""phase hardening: webhook deliveries, per-repo pages, per-org github app

Revision ID: 0001_phase_hardening
Revises:
Create Date: 2026-09-19

create_all + schema_sync still run at boot and remain the live path for this
deployment. This revision is additive and uses IF NOT EXISTS so `alembic
upgrade head` is safe on a database that already has the columns.
"""

from alembic import op

revision = "0001_phase_hardening"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS webhook_deliveries (
            id UUID PRIMARY KEY,
            delivery_id VARCHAR NOT NULL UNIQUE,
            event VARCHAR NOT NULL DEFAULT '',
            received_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("ALTER TABLE repos ADD COLUMN IF NOT EXISTS cloudflare_pages_project VARCHAR")
    op.execute("ALTER TABLE organizations ADD COLUMN IF NOT EXISTS github_app_installation_id VARCHAR")


def downgrade() -> None:
    # Additive-only by policy: dropping these on a live database is not
    # something this revision will guess at.
    pass
