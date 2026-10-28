"""connections

The hosts uploaded pages were seen contacting, per resource, from
Content-Security-Policy-Report-Only reports on the content origin. Only the
host, a count and the last day it was seen are kept.

Revision ID: 929956801471
Revises: 35c91ad0b38c
Create Date: 2026-10-28 16:01:17.938688

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '929956801471'
down_revision = '35c91ad0b38c'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "webpage_view_plus_connections",
        sa.Column(
            "resource_id", sa.UnicodeText,
            sa.ForeignKey("resource.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("host", sa.UnicodeText, primary_key=True),
        sa.Column("reports", sa.Integer, nullable=False, server_default="0"),
        sa.Column("last_seen", sa.Date, nullable=False),
    )


def downgrade():
    op.drop_table("webpage_view_plus_connections")
