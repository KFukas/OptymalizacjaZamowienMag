from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, text

from wms import models  # noqa: F401
from wms.db import Base

ROOT = Path(__file__).resolve().parents[2]


def test_migrations_match_models_and_install_guards(tmp_path: Path) -> None:
    url = f"sqlite:///{(tmp_path / 'm.db').as_posix()}"
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "wms" / "migrations"))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")
    engine = create_engine(url)
    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
        assert diff == [], f"Migracje nie odpowiadają modelom: {diff}"
        triggers = {r[0] for r in conn.execute(text("SELECT name FROM sqlite_master WHERE type='trigger'"))}
    assert {"audit_log_no_update", "audit_log_no_delete"} <= triggers
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO audit_log (ts, source, actor, actor_type, action, target, args, decision, outcome,"
                " reason, prev_hash, hash) VALUES ('2026-01-01', 'wms', 'a', 'system', 'x', '', '{}', 'allow',"
                " 'ok', '', 'p', 'h')"
            )
        )
    with pytest.raises(Exception, match="append-only"), engine.begin() as conn:
        conn.execute(text("DELETE FROM audit_log"))
    engine.dispose()
