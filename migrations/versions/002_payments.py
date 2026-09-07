"""Payment attempts and duplicate-charge protection."""

import sqlalchemy as sa
from alembic import op

revision = "002_payments"
down_revision = "001_base"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "payments",
        sa.Column(
            "id", sa.UUID(), primary_key=True, server_default=sa.func.gen_random_uuid()
        ),
        sa.Column("cart_id", sa.UUID(), sa.ForeignKey("carts.id"), nullable=False),
        sa.Column(
            "payment_method_id",
            sa.UUID(),
            sa.ForeignKey("user_payment_methods.id"),
            nullable=False,
        ),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("currency", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="pending"),
        sa.Column("provider_reference", sa.Text(), unique=True),
        sa.Column("failure_code", sa.Text()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("cart_id", "idempotency_key"),
        sa.CheckConstraint(
            "length(idempotency_key) BETWEEN 1 AND 128",
            name="payments_idempotency_key_check",
        ),
        sa.CheckConstraint(
            "amount > 0 AND amount < 10000000000", name="payments_amount_check"
        ),
        sa.CheckConstraint("currency ~ '^[A-Z]{3}$'", name="payments_currency_check"),
        sa.CheckConstraint(
            "status IN ('pending','succeeded','failed')", name="payments_status_check"
        ),
        sa.CheckConstraint(
            "(status = 'pending' AND provider_reference IS NULL AND failure_code IS NULL) OR "
            "(status = 'succeeded' AND provider_reference IS NOT NULL AND failure_code IS NULL) OR "
            "(status = 'failed' AND provider_reference IS NULL AND failure_code IS NOT NULL)",
            name="payments_check",
        ),
    )
    op.create_index(
        "one_live_payment_per_cart",
        "payments",
        ["cart_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('pending','succeeded')"),
    )


def downgrade():
    op.drop_table("payments")
