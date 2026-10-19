"""Zrzuty ekranu UI do dokumentacji (opcjonalne: `pip install playwright && playwright install chromium`).

    python scripts/screenshots.py --ui http://localhost:5173 --out docs/img

Loguje się kontem kierownika demo (dane z seedu) i zapisuje widoki w trybie jasnym i ciemnym.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from simulator.seed_users import demo_users  # noqa: E402

TABS = [
    ("map", "Mapa"),
    ("waves", "Fale"),
    ("kpi", "Wąskie gardła"),
    ("manager", "Panel kierownika"),
    ("audit", "Audyt"),
]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ui", default="http://localhost:5173")
    ap.add_argument("--out", default=str(ROOT / "docs" / "img"))
    ap.add_argument("--themes", default="light,dark")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    user = next(u for u in demo_users() if u.username == "kierownik")
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for theme in args.themes.split(","):
            page = browser.new_page(viewport={"width": 1500, "height": 950}, color_scheme=theme)  # type: ignore[arg-type]
            page.goto(args.ui)
            page.evaluate(f"localStorage.setItem('alfa.theme', '{theme}')")
            page.reload()
            if page.get_by_label("Login").count():
                page.get_by_label("Login").fill(user.username)
                page.get_by_label("Hasło").fill(user.password)
                page.get_by_role("button", name="Zaloguj").click()
            page.wait_for_selector("nav[aria-label='Widoki']")
            for tab_id, label in TABS:
                page.get_by_role("button", name=label, exact=True).click()
                page.wait_for_timeout(2500)
                if tab_id == "manager":
                    queue = page.locator("section:has(h2:text('Kolejka wniosków')) ul li button")
                    flagged = queue.filter(has_text="podejrzane źródło")
                    first = flagged.first if flagged.count() else queue.first
                    if first.count():
                        first.click()
                        page.wait_for_timeout(1500)
                page.screenshot(path=str(out / f"{tab_id}-{theme}.png"), full_page=tab_id != "audit")
                print("zapisano", out / f"{tab_id}-{theme}.png")
            page.close()
        browser.close()


if __name__ == "__main__":
    main()
