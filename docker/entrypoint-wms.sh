#!/bin/sh
# WMS API: migracje (PostgreSQL), opcjonalne dane demo przy pierwszym starcie, serwer.
set -e
alembic upgrade head
if [ "${SEED_ON_START:-1}" = "1" ]; then
  python - <<'PY'
import os
from sqlalchemy import inspect, text
from wms.db import make_engine
engine = make_engine(os.environ["DATABASE_URL"])
with engine.connect() as c:
    has = inspect(c).has_table("sim_state") and c.execute(text("SELECT count(*) FROM sim_state")).scalar()
if not has:
    from simulator.generate import generate
    s = generate(engine, seed=int(os.getenv("SEED", "42")), size=os.getenv("SIZE", "small"), scenario=os.getenv("SCENARIO", "zwykly_dzien"))
    print("Wygenerowano dane demo:", s)
PY
fi
exec uvicorn wms.main:create_app --factory --host 0.0.0.0 --port 8000
