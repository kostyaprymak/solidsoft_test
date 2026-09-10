"""Persist demo provider idempotency results across process restarts."""

import sqlalchemy as sa
from alembic import op

revision = "004_mock_provider"
down_revision = "003_recovery"
branch_labels = None
depends_on = None


def upgrade():
    connection = op.get_bind()
    schema = connection.exec_driver_sql("SELECT current_schema()").scalar_one()
    if sa.inspect(connection).has_table("mock_charges", schema=schema):
        return
    op.create_table(
        "mock_charges",
        sa.Column(
            "id", sa.UUID(), primary_key=True, server_default=sa.func.gen_random_uuid()
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("token_fingerprint", sa.Text(), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("currency", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "status IN ('succeeded','failed')", name="mock_charges_status_check"
        ),
        schema=schema,
    )


def downgrade():
    schema = op.get_bind().exec_driver_sql("SELECT current_schema()").scalar_one()
    op.drop_table("mock_charges", schema=schema)
