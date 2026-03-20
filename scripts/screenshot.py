#!/usr/bin/env python3
"""
Posterity Screenshots - Capture the state of the app at key moments.
Saves to static/images/posterity/<timestamp>_<pagename>.png

Usage:
    python scripts/screenshot.py                     # All pages
    python scripts/screenshot.py --page dashboard    # Just dashboard
    python scripts/screenshot.py --label "first-thread"  # Custom label
"""

import argparse
import os
import sys
from datetime import datetime
from playwright.sync_api import sync_playwright

BASE_URL = "http://127.0.0.1:5001"
OUTPUT_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static", "images", "posterity")

PAGES = {
    "dashboard": "/",
    "agents": "/agents",
    "about": "/about",
}


def take_screenshots(pages=None, label=None, base_url=None):
    base_url = base_url or BASE_URL
    pages = pages or PAGES
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 900})

        for name, path in pages.items():
            url = f"{base_url}{path}"
            page.goto(url, wait_until="networkidle")
            # Wait a beat for Chart.js to render
            page.wait_for_timeout(1500)

            suffix = f"_{label}" if label else ""
            filename = f"{timestamp}_{name}{suffix}.png"
            filepath = os.path.join(OUTPUT_DIR, filename)

            page.screenshot(path=filepath, full_page=True)
            print(f"Saved: {filepath}")

        browser.close()

    print(f"\nDone! {len(pages)} screenshots saved to {OUTPUT_DIR}")


def main():
    parser = argparse.ArgumentParser(description="Posterity screenshots")
    parser.add_argument("--page", type=str, help="Single page to screenshot")
    parser.add_argument("--label", type=str, help="Custom label suffix")
    parser.add_argument("--url", type=str, default=BASE_URL, help="Base URL")
    args = parser.parse_args()

    if args.page:
        if args.page in PAGES:
            pages = {args.page: PAGES[args.page]}
        else:
            pages = {args.page: f"/{args.page}"}
    else:
        pages = PAGES

    take_screenshots(pages=pages, label=args.label, base_url=args.url)


if __name__ == "__main__":
    main()
