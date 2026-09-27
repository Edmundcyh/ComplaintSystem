"""Keep transactions when complaint is deleted

Revision ID: 503a98bf8050
Revises: 925eeabcaa89
Create Date: 2026-09-26 15:47:23.153772

"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "503a98bf8050"
down_revision = "925eeabcaa89"
branch_labels = None
depends_on = None


FK_NAME = "transactions_complaint_id_fkey"


def upgrade() -> None:
    op.drop_constraint(FK_NAME, "transactions", type_="foreignkey")
    op.create_foreign_key(
        FK_NAME,
        "transactions",
        "complaints",
        ["complaint_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(FK_NAME, "transactions", type_="foreignkey")
    op.create_foreign_key(
        FK_NAME, "transactions", "complaints", ["complaint_id"], ["id"]
    )
