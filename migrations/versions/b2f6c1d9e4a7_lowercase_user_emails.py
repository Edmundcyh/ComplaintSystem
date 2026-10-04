"""Lowercase user emails

Emails are now treated as case-insensitive: new ones are stored in lower
case and logins look them up that way, so existing addresses are lowercased
too. The upgrade stops with a list of the affected users if two accounts
would end up with the same address; keep one of each pair first.

Downgrading leaves the addresses as they are.

Revision ID: b2f6c1d9e4a7
Revises: 84c70b91eeac
Create Date: 2026-10-04 10:12:31.000000

"""

from alembic import context, op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "b2f6c1d9e4a7"
down_revision = "84c70b91eeac"
branch_labels = None
depends_on = None


def check_duplicates() -> None:
    if context.is_offline_mode():
        return
    rows = op.get_bind().execute(
        sa.text(
            "SELECT lower(email) AS email, array_agg(id ORDER BY id) AS ids "
            "FROM users WHERE email IS NOT NULL "
            "GROUP BY lower(email) HAVING count(*) > 1"
        )
    )
    bad = [f"{row.email} (user ids {', '.join(map(str, row.ids))})" for row in rows]
    if bad:
        raise RuntimeError(
            "These accounts differ only by the case of their email; remove or "
            "change all but one of each and run the upgrade again: " + "; ".join(bad)
        )


def upgrade() -> None:
    check_duplicates()
    op.execute("UPDATE users SET email = lower(email) WHERE email <> lower(email)")


def downgrade() -> None:
    pass
