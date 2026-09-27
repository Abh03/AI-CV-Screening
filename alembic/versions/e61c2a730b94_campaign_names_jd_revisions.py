"""Campaign names and immutable JD revision history."""
from alembic import op
import sqlalchemy as sa

revision = "e61c2a730b94"
down_revision = "a27c9d410e62"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("campaigns", sa.Column("name", sa.String(255), nullable=True))
    # The original constraint was unnamed; resolve its database-assigned name.
    constraints = sa.inspect(op.get_bind()).get_unique_constraints("approved_jds")
    for constraint in constraints:
        if constraint["column_names"] == ["draft_id"]:
            op.drop_constraint(constraint["name"], "approved_jds", type_="unique")
    op.create_index("ix_approved_jds_draft_id", "approved_jds", ["draft_id"])


def downgrade():
    # Revision history must be removed explicitly before reverting to one approval per draft.
    op.drop_index("ix_approved_jds_draft_id", table_name="approved_jds")
    op.create_unique_constraint("approved_jds_draft_id_key", "approved_jds", ["draft_id"])
    op.drop_column("campaigns", "name")
