"""Regresja: blokada zapisu nie może zależeć od wątku (FastAPI wykonuje wejście i wyjście zależności
w różnych wątkach puli) — inaczej blokada zostaje osierocona i wszystkie zapisy wiszą."""

from __future__ import annotations

import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import httpx
import uvicorn

from simulator.generate import generate
from tests.conftest import OPERATOR_PASSWORD, make_settings
from wms.db import make_engine, make_sessionmaker
from wms.deps import WRITE_LOCK, get_write_session
from wms.main import create_app


def test_write_session_enter_and_exit_in_different_threads() -> None:
    engine = make_engine("sqlite:///:memory:")
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(session_factory=make_sessionmaker(engine)))
    )
    gen = get_write_session(request)  # type: ignore[arg-type]
    t1 = threading.Thread(target=lambda: next(gen))
    t1.start()
    t1.join()
    errors: list[BaseException] = []

    def finish() -> None:
        try:
            gen.close()
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    t2 = threading.Thread(target=finish)
    t2.start()
    t2.join()
    assert errors == []
    assert WRITE_LOCK.acquire(timeout=1), "blokada zapisu osierocona"
    WRITE_LOCK.release()


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def test_concurrent_writes_under_uvicorn_with_running_simulation(tmp_path) -> None:  # type: ignore[no-untyped-def]
    url = f"sqlite:///{(tmp_path / 'c.db').as_posix()}"
    engine = make_engine(url)
    generate(engine, seed=1, size="small")
    app = create_app(make_settings(database_url=url, sim_tick_seconds=0.05), engine)
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    th = threading.Thread(target=server.run, daemon=True)
    th.start()
    for _ in range(200):
        if server.started:
            break
        time.sleep(0.05)
    base = f"http://127.0.0.1:{port}"
    try:
        tok = httpx.post(
            f"{base}/auth/login", json={"username": "operator", "password": OPERATOR_PASSWORD}
        ).json()
        h = {"Authorization": f"Bearer {tok['access_token']}"}
        httpx.post(
            f"{base}/sim/control", json={"action": "speed", "speed": 120}, headers=h
        ).raise_for_status()
        httpx.post(f"{base}/sim/control", json={"action": "start"}, headers=h).raise_for_status()

        def login(_: int) -> int:
            return httpx.post(
                f"{base}/auth/login", json={"username": "operator", "password": OPERATOR_PASSWORD}, timeout=20
            ).status_code

        t0 = time.perf_counter()
        with ThreadPoolExecutor(max_workers=8) as pool:
            codes = list(pool.map(login, range(24)))
        assert codes == [200] * 24
        assert time.perf_counter() - t0 < 20
        httpx.post(f"{base}/sim/control", json={"action": "pause"}, headers=h, timeout=20).raise_for_status()
    finally:
        server.should_exit = True
        th.join(timeout=10)
        app.state.sim_engine and app.state.sim_engine.stop_thread()
