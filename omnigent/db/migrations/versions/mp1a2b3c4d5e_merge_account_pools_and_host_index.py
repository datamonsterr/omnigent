"""Merge account pools and the host recovery index into one migration head.

Deployment: no schema changes. Databases on either branch apply the other
branch's migrations before reaching this revision; existing revisions remain
unchanged.
"""

revision = "mp1a2b3c4d5e"
down_revision = ("mo1a2b3c4d5e", "nf12ab34cd56")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
