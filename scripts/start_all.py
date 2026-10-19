"""Uruchamia cały projekt jednym poleceniem: WMS API (+ symulator), frontend i agenta planującego.

    python scripts/start_all.py [--llm] [--speed 10] [--no-agent] [--no-browser] [--no-sim-start]

Na Windows wystarczy dwuklik w `Uruchom.bat` w katalogu projektu (Linux/macOS: `./uruchom.sh`).

Co robi:
1. tworzy `.env` z losowymi sekretami, jeśli go nie ma, i dane demo, jeśli baza jest pusta,
2. instaluje zależności frontendu przy pierwszym uruchomieniu,
3. wybiera wolne porty (WMS: 8000, a gdy zajęty — 8010, 8020…; frontend: 5173…),
4. startuje WMS, czeka na /health, startuje frontend, włącza symulację, startuje agenta (heurystyka albo
   Claude z --llm; agent łączy się z WMS wyłącznie przez mcp-wms), otwiera przeglądarkę,
5. Ctrl+C (albo zamknięcie okna) zatrzymuje wszystkie procesy. Logi: var/logs/.
"""

from __future__ import annotations

import argparse
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
LOGS = ROOT / "var" / "logs"
IS_WIN = os.name == "nt"


def info(msg: str) -> None:
    print(f"[start] {msg}", flush=True)


def port_free(port: int) -> bool:
    with socket.socket() as s:
        try:
            s.bind(("127.0.0.1", port))
        except OSError:
            return False
        return True


def pick_port(candidates: list[int]) -> int:
    for p in candidates:
        if port_free(p):
            return p
    raise SystemExit(f"Brak wolnego portu wśród {candidates}")


def spawn(name: str, cmd: list[str], env: dict[str, str], cwd: Path = ROOT) -> subprocess.Popen[bytes]:
    LOGS.mkdir(parents=True, exist_ok=True)
    log = open(LOGS / f"{name}.log", "ab")  # noqa: SIM115 - plik żyje tyle co proces
    kwargs: dict = {"cwd": str(cwd), "env": env, "stdout": log, "stderr": subprocess.STDOUT}
    if IS_WIN:
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    info(f"start: {name} (log: var/logs/{name}.log)")
    return subprocess.Popen(cmd, **kwargs)  # noqa: S603 - stałe polecenia, bez powłoki


def stop(proc: subprocess.Popen[bytes]) -> None:
    if proc.poll() is not None:
        return
    if IS_WIN:  # całe drzewo procesów (npm -> node, python -> mcp-wms)
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)  # noqa: S603, S607
    else:
        try:
            os.killpg(proc.pid, signal.SIGTERM)  # type: ignore[attr-defined,unused-ignore]
        except ProcessLookupError:
            return
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()


def wait_until(check, what: str, timeout: float, procs: list[subprocess.Popen[bytes]]) -> None:  # type: ignore[no-untyped-def]
    deadline = time.time() + timeout
    while time.time() < deadline:
        if any(p.poll() is not None for p in procs):
            raise SystemExit(f"Proces zakończył się podczas startu ({what}) — sprawdź var/logs/.")
        try:
            if check():
                return
        except (httpx.HTTPError, ValueError):
            pass
        time.sleep(1)
    raise SystemExit(f"Przekroczono czas oczekiwania na: {what} — sprawdź var/logs/.")


def ensure_env_and_data(python: str) -> None:
    if not (ROOT / ".env").exists():
        info("brak .env — tworzę z losowymi sekretami")
        subprocess.run([python, str(ROOT / "scripts" / "gen_secrets.py"), "--init"], check=True)  # noqa: S603
    from sqlalchemy import inspect, text

    from wms.config import get_settings
    from wms.db import make_engine

    engine = make_engine(get_settings().database_url)
    with engine.connect() as conn:
        has_data = (
            inspect(conn).has_table("sim_state")
            and conn.execute(text("SELECT count(*) FROM sim_state")).scalar()
        )
    engine.dispose()
    if not has_data:
        info("brak danych — generuję dane demo (scenariusz: zwykły dzień, rozmiar medium)")
        subprocess.run(  # noqa: S603
            [python, "-m", "simulator.generate", "--seed", "42", "--size", "medium"], cwd=ROOT, check=True
        )


def ensure_frontend(npm: str) -> None:
    if not (ROOT / "frontend" / "node_modules").exists():
        info("pierwsze uruchomienie — instaluję zależności frontendu (npm install)")
        subprocess.run([npm, "install", "--no-audit", "--no-fund"], cwd=ROOT / "frontend", check=True)  # noqa: S603


def main() -> None:
    ap = argparse.ArgumentParser(description="Uruchamia WMS, frontend i agenta")
    ap.add_argument(
        "--llm", action="store_true", help="agent Claude zamiast heurystyki (wymaga ANTHROPIC_API_KEY w .env)"
    )
    ap.add_argument("--speed", type=int, default=10, help="tempo symulacji (1–600)")
    ap.add_argument("--no-agent", action="store_true")
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--no-sim-start", action="store_true", help="nie włączaj symulacji automatycznie")
    ap.add_argument(
        "--nowy-dzien",
        nargs="?",
        const="zwykly_dzien",
        metavar="SCENARIUSZ",
        help="zacznij nowy dzień symulacji (audyt i wnioski zostają); scenariusze: zwykly_dzien, black_friday, "
        "brak_pracownikow, rozbieznosc_stanow, zamowienie_podejrzane, notatka_injection",
    )
    args = ap.parse_args()

    python = sys.executable
    npm = shutil.which("npm")
    if npm is None:
        raise SystemExit("Nie znaleziono npm — zainstaluj Node.js 20+ (https://nodejs.org).")
    ensure_env_and_data(python)
    ensure_frontend(npm)

    wms_port = pick_port([8000, 8010, 8020, 8030])
    ui_port = pick_port([5173, 5174, 5175, 5180])
    wms_url = f"http://127.0.0.1:{wms_port}"
    ui_url = f"http://127.0.0.1:{ui_port}"
    env = {**os.environ, "PYTHONUTF8": "1", "PYTHONUNBUFFERED": "1"}
    # Zmienne środowiskowe mają pierwszeństwo przed .env — porty ustawiamy bez edycji .env.
    wms_env = {**env, "CORS_ORIGINS": f"http://127.0.0.1:{ui_port},http://localhost:{ui_port}"}
    ui_env = {**env, "VITE_API_URL": wms_url}
    agent_env = {**env, "WMS_URL": wms_url}

    procs: list[subprocess.Popen[bytes]] = []
    try:
        procs.append(
            spawn(
                "wms",
                [python, "-m", "uvicorn", "wms.main:create_app", "--factory", "--host", "127.0.0.1",
                 "--port", str(wms_port), "--log-level", "warning"],
                wms_env,
            )
        )  # fmt: skip
        wait_until(
            lambda: httpx.get(f"{wms_url}/health", timeout=2).json().get("status") == "ok",
            "WMS API",
            90,
            procs,
        )
        procs.append(
            spawn(
                "frontend",
                [npm, "run", "dev", "--", "--port", str(ui_port), "--strictPort"],
                ui_env,
                ROOT / "frontend",
            )
        )
        wait_until(lambda: httpx.get(ui_url, timeout=2).status_code == 200, "frontend", 90, procs)

        from simulator.seed_users import demo_users

        users = {u.username: u for u in demo_users()}
        op = users["operator"]
        with httpx.Client(base_url=wms_url, timeout=120) as c:
            tok = c.post("/auth/login", json={"username": op.username, "password": op.password}).json()
            h = {"Authorization": f"Bearer {tok['access_token']}"}
            if args.nowy_dzien:
                info(f"nowy dzień symulacji: {args.nowy_dzien}")
                c.post(
                    "/sim/control", json={"action": "reset", "scenario": args.nowy_dzien}, headers=h
                ).raise_for_status()
            if not args.no_sim_start:
                c.post(
                    "/sim/control", json={"action": "speed", "speed": args.speed}, headers=h
                ).raise_for_status()
                c.post("/sim/control", json={"action": "start"}, headers=h).raise_for_status()
                info(f"symulacja włączona (tempo ×{args.speed})")
            state = c.get("/sim/state", headers=h).json()
        sim_info = f"{state['sim_now'].replace('T', ' ')[:16]}, scenariusz: {state['scenario']}"

        if not args.no_agent:
            mode = "llm" if args.llm else "heuristic"
            procs.append(
                spawn(
                    "agent",
                    [
                        python,
                        "-m",
                        "orchestrator",
                        "--mode",
                        mode,
                        "--cycles",
                        "100000",
                        "--every-sim-min",
                        "15",
                    ],
                    agent_env,
                )
            )

        mgr, mgr2, op = users["kierownik"], users["kierownik2"], users["operator"]
        print(
            f"""
============================================================
  Magazyn Centralny Alfa — uruchomiono
  Aplikacja:  {ui_url}
  WMS API:    {wms_url}/docs
  Agent:      {"wyłączony" if args.no_agent else ("Claude (LLM)" if args.llm else "heurystyka (bez LLM)")}
  Symulacja:  {sim_info}

  Konta demo:
    kierownik  / {mgr.password}   PIN: {mgr.pin}
    kierownik2 / {mgr2.password}  PIN: {mgr2.pin}
    operator   / {op.password}

  Ctrl+C lub zamknięcie tego okna zatrzymuje wszystko.
============================================================
""",
            flush=True,
        )
        if not args.no_browser:
            webbrowser.open(ui_url)

        while True:
            for p, name in zip(procs, ["wms", "frontend", "agent"], strict=False):
                if p.poll() is not None:
                    raise SystemExit(
                        f"Proces '{name}' zakończył się (kod {p.returncode}) — sprawdź var/logs/{name}.log"
                    )
            time.sleep(2)
    except KeyboardInterrupt:
        info("zatrzymywanie…")
    finally:
        for p in reversed(procs):
            stop(p)
        info("zatrzymano wszystkie procesy")


if __name__ == "__main__":
    main()
