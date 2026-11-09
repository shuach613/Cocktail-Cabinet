"""Visual regression contracts for the shared desktop/mobile UI.

These tests verify server-rendered structure, interaction affordances, asset
cache busting and responsive CSS. They are not a replacement for screenshot
comparison in actual browsers.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

os.environ.setdefault("SECRET_KEY","test_secret_"*6)
os.environ.setdefault("SETUP_TOKEN","first_setup_secret")
os.environ.setdefault("TESTING","1")
os.environ.setdefault("COOKIE_SECURE","false")
os.environ.setdefault("TRUST_PROXY","false")
os.environ.setdefault("DATA_DIR",tempfile.mkdtemp(prefix="cabinet-visual-qa-"))

import pytest
import app as module
import db
from werkzeug.security import generate_password_hash


ROOT=Path(__file__).resolve().parents[1]


@pytest.fixture
def client(tmp_path,monkeypatch):
    monkeypatch.setattr(db,"DATA_DIR",tmp_path)
    monkeypatch.setattr(db,"DB_PATH",tmp_path/"cabinet.sqlite3")
    monkeypatch.setattr(module,"DATA_DIR",tmp_path)
    db.ensure_db()
    module.app.config.update(TESTING=True,SESSION_COOKIE_SECURE=False)
    with module.app.test_client() as c:
        c.get("/setup")
        with c.session_transaction() as sess:
            csrf=sess["_csrf"]
        response=c.post("/setup",data={
            "_csrf":csrf,"setup_token":"first_setup_secret",
            "username":"owner","email":"owner@example.com",
            "password":"very strong password"
        })
        assert response.status_code==302
        with db.connect() as con:
            con.execute("""INSERT INTO users(username,email,password_hash,role,active)
                           VALUES (?,?,?,?,0)""",
                        ("disabled_member","disabled@example.com",
                         generate_password_hash("strong password 123"),"member"))
        yield c


@pytest.mark.parametrize("route", [
    "/", "/recipes/1", "/pantry", "/recipes/new", "/settings",
    "/settings/recipes", "/settings/ingredients",
    "/admin", "/account", "/settings/api"
])
def test_all_key_pages_render_with_consistent_theme_and_versioned_assets(client,route):
    response=client.get(route)
    assert response.status_code==200,(route,response.data[:200])
    html=response.get_data(as_text=True)
    assert 'finishing.css?v=3' in html
    assert 'app.js?v=2' in html
    assert 'theme-init.js' in html
    assert 'data-theme-toggle' in html
    assert '<nav class="bottom-nav"' in html
    assert 'name="viewport"' in html
    assert 'name="theme-color"' in html


def test_recipe_status_icon_has_stable_secondary_line_separate_from_preparation(client):
    with db.connect() as con:
        user_id=con.execute("SELECT id FROM users WHERE username='owner'").fetchone()["id"]
        gin_id=con.execute("SELECT id FROM categories WHERE name='Gin'").fetchone()["id"]
        con.execute("INSERT INTO pantry(user_id,category_id) VALUES (?,?)",(user_id,gin_id))
        rid=con.execute("SELECT id FROM recipes WHERE title='Classic Negroni'").fetchone()["id"]
    html=client.get(f"/recipes/{rid}").get_data(as_text=True)
    assert 'class="ingredient-main"' in html
    assert 'class="ingredient-quantity"' in html
    assert 'class="ingredient-shelf-status"' in html
    assert 'class="shelf-status-symbol"' in html
    assert 'class="shelf-status-label"' in html
    assert 'data-shelf-state="category"' in html
    assert 'data-prep-item="' in html
    assert 'On your shelf' not in html  # the user selected a generic Gin category


def test_admin_account_status_and_catalogue_tabs_are_available_on_mobile(client):
    html=client.get("/admin").get_data(as_text=True)
    assert 'class="member-status is-disabled"' in html
    assert "disabled_member" in html
    assert "Disabled" in html
    html=client.get("/settings/ingredients").get_data(as_text=True)
    for expected in (
        'role="tablist"',
        'role="tab" aria-controls="categories-panel"',
        'role="tab" aria-controls="products-panel"',
        'role="tabpanel" aria-labelledby="categories-tab"',
        'role="tabpanel" aria-labelledby="products-tab"',
    ):
        assert expected in html


def test_visual_finish_covers_critical_breakpoints_and_keeps_theme_tokens():
    css=(ROOT/"static"/"finishing.css").read_text(encoding="utf-8")
    assert css.count("{")==css.count("}")
    for rule in (
        "@media(max-width:900px)", "@media(max-width:760px)",
        "@media(max-width:590px)", "@media(max-width:420px)",
        "@media(prefers-reduced-motion:reduce)",
        ".ingredient-main", ".ingredient-shelf-status",
        ".shelf-status-symbol .ui-icon", ".ingredient-line .ingredient-quantity",
        ".member-row", ".management-photo-upload form",
        ".settings-appearance", ".category-dialog", ".api-token-row",
    ):
        assert rule in css,rule
    assert "grid-template-columns:20px minmax(0,1fr) auto" in css
    # No palette variable overrides in this component-level finishing sheet.
    import re
    assert not re.search(r'--(?:bg|surface|ink|muted|line|accent|champagne|brand-wine|focus)\s*:',css)


def test_app_shell_refresh_and_keyboard_navigation_assets_are_available(client):
    js=(ROOT/"static"/"app.js").read_text(encoding="utf-8")
    assert "event.key==='ArrowRight'" in js
    assert "event.key==='ArrowLeft'" in js
    assert "button.tabIndex=isActive?0:-1" in js
    sw=(ROOT/"static"/"sw.js").read_text(encoding="utf-8")
    assert "cabinet-assets-v7-atelier1" in sw
    assert "/static/finishing.css?v=3" in sw
    assert "/static/app.js?v=2" in sw
    assert client.get("/static/finishing.css").status_code==200
