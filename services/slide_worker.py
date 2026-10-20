"""Separate process that screenshots each .slide element. Called by services/slides.py.

Usage: python -m services.slide_worker '<job json>'
Prints one JSON line: {"paths": [...], "overflow": [slide numbers that didn't fit]}
"""
import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright


def main(job: dict) -> dict:
    out_dir = Path(job["out_dir"])
    with sync_playwright() as p:
        browser = p.chromium.launch(channel=job.get("channel") or None)
        try:
            page = browser.new_page(viewport={"width": job["width"], "height": job["height"]},
                                    device_scale_factor=1)
            page.goto(Path(job["html"]).as_uri())
            page.wait_for_function("window.__ready === true", timeout=30000)
            paths, overflow = [], []
            for i, slide in enumerate(page.query_selector_all(".slide"), start=1):
                path = out_dir / f"slide_{i}.png"
                slide.screenshot(path=str(path))
                paths.append(str(path))
                if slide.get_attribute("data-overflow") == "1":
                    overflow.append(i)
        finally:
            browser.close()
    return {"paths": paths, "overflow": overflow}


if __name__ == "__main__":
    print(json.dumps(main(json.loads(sys.argv[1]))))
