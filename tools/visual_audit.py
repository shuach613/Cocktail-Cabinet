#!/usr/bin/env python3
"""Capture and smoke-test the complete Cocktail Cabinet UI at real breakpoints.

Run after:
  pip install -r requirements-dev.txt playwright
  python -m playwright install chromium
  python tools/visual_audit.py

Uses a throwaway SQLite database and a loopback-only Flask server. It never
contacts a production instance, writes personal data, or changes live recipes.
PNG screenshots are saved under artifacts/ui-review (ignored by Git).
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
import threading
from pathlib import Path
from werkzeug.security import generate_password_hash
from werkzeug.serving import make_server

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
DATA_DIR = tempfile.mkdtemp(prefix="cabinet-ui-audit-")
os.environ.update({
    "DATA_DIR": DATA_DIR,
    "SECRET_KEY": "local-only-responsive-ui-test-secret",
    "COOKIE_SECURE": "false",
    "TRUST_PROXY": "false",
    "TESTING": "1",
})

import app as module  # noqa:E402
import db  # noqa:E402

from playwright.sync_api import sync_playwright  # noqa:E402

HOST = "http://127.0.0.1:18741"
SHOTS = ROOT / "artifacts" / "ui-review"
VIEWPORTS = {
    "desktop": (1440, 900),
    "laptop": (1024, 768),
    "tablet": (768, 1024),
    "mobile": (390, 844),
    "small-mobile": (360, 780),
}
SCREENS = {
    "discover": "/",
    "recipe": "/recipes/1",
    "shelf": "/pantry",
    "editor": "/recipes/new",
    "settings": "/settings",
    "manage-recipes": "/settings/recipes",
    "ingredients": "/settings/ingredients",
    "admin": "/admin",
    "account": "/account",
    "api": "/settings/api",
}
ERRORS = []


def prepare():
    db.ensure_db()
    with db.connect() as con:
        con.execute(
            "INSERT INTO users(username,email,password_hash,role) VALUES (?,?,?,?)",
            ("ui-review", "visual-review@example.test",
             generate_password_hash("local visual audit password"), "admin"),
        )
        # Make the availability badge visible in the recipe screenshots.
        uid = con.execute("SELECT id FROM users WHERE username='ui-review'").fetchone()["id"]
        for name in ("Gin", "Italian red bitter", "Sweet red vermouth"):
            cat = con.execute("SELECT id FROM categories WHERE name=?", (name,)).fetchone()
            if cat:
                con.execute("INSERT OR IGNORE INTO pantry(user_id,category_id) VALUES (?,?)",
                            (uid, cat["id"]))


def authenticate(page):
    page.goto(HOST + "/login", wait_until="domcontentloaded")
    page.locator('input[name="identity"]').fill("ui-review")
    page.locator('input[name="password"]').fill("local visual audit password")
    page.locator('button[type="submit"]').click()
    page.wait_for_url(lambda url: "/login" not in url and "/setup" not in url, timeout=10000)


def capture(playwright):
    browser = playwright.chromium.launch(headless=True, args=["--no-sandbox"])
    try:
        for theme in ("light", "dark"):
            for label, (width, height) in VIEWPORTS.items():
                context = browser.new_context(
                    viewport={"width": width, "height": height},
                    device_scale_factor=1,
                    is_mobile=width < 900,
                    has_touch=width < 900,
                    reduced_motion="reduce",
                )
                context.add_init_script(
                    f"window.localStorage.setItem('cabinet-theme', '{theme}');"
                )
                page = context.new_page()
                page.on("pageerror", lambda error, t=theme, v=label:
                        ERRORS.append(f"{t}/{v} JavaScript: {error}"))
                authenticate(page)
                for screen, route in SCREENS.items():
                    response = page.goto(HOST + route, wait_until="domcontentloaded")
                    if not response or response.status >= 400:
                        ERRORS.append(f"{theme}/{label}/{screen}: HTTP {response.status if response else 'none'}")
                        continue
                    page.evaluate("async () => { await document.fonts.ready; return true; }")
                    try:
                        page.wait_for_function(
                            "Array.from(document.images).every(i => i.complete)",
                            timeout=4000,
                        )
                    except Exception:
                        # Report any failed/missing images below; still capture
                        # the screen if an unrelated remote font/photo stalls.
                        pass
                    page.screenshot(
                        path=str(SHOTS / f"{theme}-{label}-{screen}.png"),
                        full_page=True, animations="disabled",
                    )
                    info = page.evaluate("""() => ({
                        width: window.innerWidth,
                        docWidth: document.documentElement.scrollWidth,
                        bodyWidth: document.body.scrollWidth,
                        theme: document.documentElement.dataset.theme,
                        font: getComputedStyle(document.body).fontFamily,
                        nav: getComputedStyle(document.querySelector('.bottom-nav')).display,
                        missingPhotos: Array.from(document.images).filter(i =>
                            i.complete && i.naturalWidth === 0 && !i.closest('.brand')
                        ).length
                    })""")
                    if info["docWidth"] > info["width"] + 2:
                        ERRORS.append(
                            f"{theme}/{label}/{screen}: horizontal overflow "
                            f"{info['docWidth']}px > {info['width']}px"
                        )
                    if info["theme"] != theme:
                        ERRORS.append(f"{theme}/{label}/{screen}: incorrect theme {info['theme']}")
                    if "lato" not in info["font"].lower():
                        ERRORS.append(f"{theme}/{label}/{screen}: wrong font {info['font']}")
                    if (width < 900) != (info["nav"] != "none"):
                        ERRORS.append(f"{theme}/{label}/{screen}: incorrect mobile-nav visibility")
                    if info["missingPhotos"]:
                        ERRORS.append(f"{theme}/{label}/{screen}: broken images ({info['missingPhotos']})")
                context.close()
    finally:
        browser.close()


def main():
    SHOTS.mkdir(parents=True, exist_ok=True)
    prepare()
    server = make_server("127.0.0.1", 18741, module.app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with sync_playwright() as p:
            capture(p)
    finally:
        server.shutdown()
    summary = SHOTS / "REPORT.txt"
    summary.write_text(
        f"Breakpoints: {', '.join(VIEWPORTS)}\n"
        f"Themes: light, dark\nScreens: {', '.join(SCREENS)}\n"
        f"Snapshots: {len(list(SHOTS.glob('*.png')))}\n"
        + ("Failures:\n" + "\n".join(ERRORS) if ERRORS else "No layout overflows or page errors detected.\n"),
        encoding="utf-8",
    )
    print(summary.read_text())
    if ERRORS:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
