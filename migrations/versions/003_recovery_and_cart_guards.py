"""Persist original charge parameters and guard carts while payment is live."""

import sqlalchemy as sa
from alembic import op

revision = "003_recovery"
down_revision = "002_payments"
branch_labels = None
depends_on = None


def upgrade():
    # Existing attempts lack a trustworthy historical token; never backfill from
    # a potentially changed payment method. Recovery refuses those legacy rows.
    op.add_column("payments", sa.Column("provider_token", sa.Text(), nullable=True))
    op.execute("""
        CREATE FUNCTION guard_cart_items() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE old_cart UUID; new_cart UUID; locked_cart RECORD;
        BEGIN
            IF TG_OP <> 'INSERT' THEN old_cart := OLD.cart_id; END IF;
            IF TG_OP <> 'DELETE' THEN new_cart := NEW.cart_id; END IF;
            FOR locked_cart IN
                SELECT id, status FROM carts
                WHERE id = old_cart OR id = new_cart ORDER BY id FOR UPDATE
            LOOP
                IF locked_cart.status <> 'active' OR EXISTS (
                    SELECT 1 FROM payments WHERE cart_id = locked_cart.id
                    AND status IN ('pending', 'succeeded')
                ) THEN
                    RAISE EXCEPTION 'Cart is frozen for payment' USING ERRCODE = '23514';
                END IF;
            END LOOP;
            IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
            RETURN NEW;
        END; $$
    """)
    op.execute("""CREATE TRIGGER guard_cart_items BEFORE INSERT OR UPDATE OR DELETE
                  ON cart_items FOR EACH ROW EXECUTE FUNCTION guard_cart_items()""")
    op.execute("""
        CREATE FUNCTION guard_cart_identity() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF EXISTS (SELECT 1 FROM payments WHERE cart_id = OLD.id
                       AND status IN ('pending','succeeded')) THEN
                IF NEW.user_id IS DISTINCT FROM OLD.user_id OR
                   (NEW.status IS DISTINCT FROM OLD.status AND NOT (
                       OLD.status = 'active' AND NEW.status = 'checked_out' AND
                       EXISTS (SELECT 1 FROM payments WHERE cart_id = OLD.id AND status = 'succeeded')
                   )) THEN
                    RAISE EXCEPTION 'Cart is frozen for payment' USING ERRCODE = '23514';
                END IF;
            END IF;
            RETURN NEW;
        END; $$
    """)
    op.execute("""CREATE TRIGGER guard_cart_identity BEFORE UPDATE ON carts
                  FOR EACH ROW EXECUTE FUNCTION guard_cart_identity()""")


def downgrade():
    op.execute("DROP TRIGGER guard_cart_identity ON carts")
    op.execute("DROP FUNCTION guard_cart_identity()")
    op.execute("DROP TRIGGER guard_cart_items ON cart_items")
    op.execute("DROP FUNCTION guard_cart_items()")
    op.drop_column("payments", "provider_token")
