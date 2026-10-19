"""wyzwalacze append-only dla audit_log

Revision ID: 0002
Revises: 0001
"""

from alembic import op

from wms.db import guard_statements

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for stmt in guard_statements(op.get_bind().dialect.name):
        op.execute(stmt)


def downgrade() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "sqlite":
        op.execute("DROP TRIGGER IF EXISTS audit_log_no_update")
        op.execute("DROP TRIGGER IF EXISTS audit_log_no_delete")
    elif dialect.startswith("postgres"):
        op.execute("DROP TRIGGER IF EXISTS audit_log_no_modify ON audit_log")
        op.execute("DROP FUNCTION IF EXISTS audit_log_append_only()")
