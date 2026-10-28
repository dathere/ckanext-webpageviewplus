"""view counts

Daily totals per view for opt-in view counting. Nothing about viewers is
stored: no IP, user, user agent or referrer.

Revision ID: 35c91ad0b38c
Revises:
Create Date: 2026-10-28 14:50:42.523247

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '35c91ad0b38c'
down_revision = None
branch_labels = None
depends_on = None

COUNTS = ("impressions", "seen", "loaded", "engaged", "failed")


def upgrade():
    op.create_table(
        "webpage_view_plus_view_counts",
        sa.Column(
            "view_id", sa.UnicodeText,
            sa.ForeignKey("resource_view.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("day", sa.Date, primary_key=True),
        *[
            sa.Column(name, sa.Integer, nullable=False, server_default="0")
            for name in COUNTS
        ],
    )


def downgrade():
    op.drop_table("webpage_view_plus_view_counts")
