"""Screenshots of the in-car app for the README, taken from a running ``edgecar serve`` by a headless browser.

edgecar serve --model models/edgecar-0.5b-q4_k_m.gguf &
python scripts/screenshots.py            # → docs/img/app-*.png   (pip install playwright; uses Edge or Chrome)
"""

from __future__ import annotations

import argparse
from pathlib import Path

from playwright.sync_api import sync_playwright

# name: (vehicle, view, questions, viewport height)
SHOTS = {
    "app-root-cause": ("i3-tum", "service", ["What happened before the battery overheating warning?"], 1310),
    "app-range": ("leaf-455", "service", ["Why has my driving range dropped over the last two weeks?"], 900),
    "app-driver": (
        "phev-457",
        "driver",
        ["Show all ADAS disengagement events from yesterday.", "Turn off lane keeping assist"],
        760,
    ),
}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default="http://127.0.0.1:8080/")
    ap.add_argument("--out", type=Path, default=Path("docs/img"))
    ap.add_argument("--browser", default="msedge", help="installed browser channel: msedge or chrome")
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(channel=a.browser, headless=True)
        for name, (vehicle, role, questions, height) in SHOTS.items():
            # bypass_csp lets the test harness evaluate its wait conditions; the page itself is unchanged
            page = browser.new_context(viewport={"width": 1440, "height": height}, bypass_csp=True).new_page()
            page.goto(a.url)
            page.wait_for_selector("#vehicle option", state="attached")
            page.select_option("#vehicle", vehicle)
            page.click(f'.roles button[data-role="{role}"]')
            for q in questions:
                page.fill("#q", q)
                page.press("#q", "Enter")
                page.wait_for_function("!document.querySelector('.card.pending')", timeout=60_000)
            page.wait_for_timeout(600)
            page.evaluate("document.querySelector('.q').scrollIntoView({block: 'start'})")
            page.wait_for_timeout(300)
            page.screenshot(path=str(a.out / f"{name}.png"))
            print(a.out / f"{name}.png")
        browser.close()


if __name__ == "__main__":
    main()
