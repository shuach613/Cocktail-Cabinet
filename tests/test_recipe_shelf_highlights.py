"""Recipe availability presentation follows existing per-user shelf rules.

No recipe data is written by viewing the detail page.
"""
from __future__ import annotations
import os
import tempfile
from html.parser import HTMLParser

os.environ.setdefault("SECRET_KEY", "test_secret_" * 6)
os.environ.setdefault("SETUP_TOKEN", "first_setup_secret")
os.environ.setdefault("TESTING", "1")
os.environ.setdefault("COOKIE_SECURE", "false")
os.environ.setdefault("TRUST_PROXY", "false")
os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="cabinet-recipe-shelf-tests-"))

import pytest
import app as module
import db


class IngredientRows(HTMLParser):
    def __init__(self):
        super().__init__()
        self.rows = {}
        self.current = None
        self.depth = 0

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "label" and "ingredient-line" in attrs.get("class", "").split():
            self.current = attrs.get("data-ingredient-id")
            self.rows[self.current] = {
                "classes": attrs.get("class", "").split(),
                "status": False, "prep_checked": False, "text": "",
            }
            self.depth = 1
        elif self.current is not None:
            self.depth += 1
            if tag == "span" and "ingredient-shelf-status" in attrs.get("class", "").split():
                self.rows[self.current]["status"] = True
            if tag == "input" and attrs.get("data-prep-item"):
                self.rows[self.current]["prep_checked"] = "checked" in attrs

    def handle_endtag(self, tag):
        if self.current is not None:
            self.depth -= 1
            if self.depth == 0:
                self.current = None

    def handle_data(self, data):
        if self.current is not None:
            self.rows[self.current]["text"] += data


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "cabinet.sqlite3")
    monkeypatch.setattr(module, "DATA_DIR", tmp_path)
    db.ensure_db()
    module.app.config.update(TESTING=True, SESSION_COOKIE_SECURE=False)
    with module.app.test_client() as c:
        yield c


def setup(client):
    client.get("/setup")
    with client.session_transaction() as sess:
        csrf = sess["_csrf"]
    result = client.post("/setup", data={
        "_csrf": csrf, "setup_token": "first_setup_secret",
        "username": "owner", "email": "owner@example.com",
        "password": "very strong password",
    })
    assert result.status_code == 302


def recipe_rows(client, title):
    with db.connect() as con:
        recipe = con.execute("SELECT id FROM recipes WHERE title=?", (title,)).fetchone()
        assert recipe
    response = client.get(f"/recipes/{recipe['id']}")
    assert response.status_code == 200
    view = IngredientRows()
    view.feed(response.get_data(as_text=True))
    return view.rows, response.get_data(as_text=True)


def item_ids(con):
    return {r["name"]: str(r["id"]) for r in con.execute("SELECT id,name FROM ingredients")}


def test_recipe_highlights_owned_generic_matches_and_preserves_manual_checklist(client):
    setup(client)
    with db.connect() as con:
        names = {r["name"]: r["id"] for r in con.execute("SELECT id,name FROM categories")}
        user_id = con.execute("SELECT id FROM users WHERE username='owner'").fetchone()["id"]
        with con:
            for name in ("Gin", "Italian red bitter", "Sweet red vermouth"):
                con.execute("INSERT INTO pantry(user_id,category_id) VALUES(?,?)", (user_id, names[name]))
        ingredients = item_ids(con)
        recipes_before = [tuple(r) for r in con.execute("SELECT * FROM recipes ORDER BY id")]
        lines_before = [tuple(r) for r in con.execute("SELECT * FROM recipe_items ORDER BY id")]

    rows, html = recipe_rows(client, "Classic Negroni")
    for ingredient in ("Tanqueray London Dry Gin", "Campari", "Carpano Antica Formula"):
        row = rows[ingredients[ingredient]]
        assert "is-on-shelf" in row["classes"]
        assert row["status"]
        assert not row["prep_checked"]
    assert "3 of 3" in html
    assert "Missing from your shelf:" not in html
    # Optional garnish and ice are not counted as missing, and ice is never highlighted.
    assert "is-on-shelf" not in rows[ingredients["Ice"]]["classes"]
    assert not rows[ingredients["Ice"]]["status"]
    with db.connect() as con:
        assert recipes_before == [tuple(r) for r in con.execute("SELECT * FROM recipes ORDER BY id")]
        assert lines_before == [tuple(r) for r in con.execute("SELECT * FROM recipe_items ORDER BY id")]


def test_exact_brand_and_generic_shelf_match_distinction(client):
    setup(client)
    with db.connect() as con:
        ids = {r["name"]: r["id"] for r in con.execute("SELECT id,name FROM categories")}
        ingredient = item_ids(con)
        uid = con.execute("SELECT id FROM users WHERE username='owner'").fetchone()["id"]
        with con:
            con.execute("INSERT INTO pantry(user_id,category_id) VALUES(?,?)", (uid, ids["Gin"]))
    rows, html = recipe_rows(client, "Gin & Tonic Seventh Sense")
    assert "is-on-shelf" in rows[ingredient["Seventh Sense Gin"]]["classes"]
    assert "Shelf match" in rows[ingredient["Seventh Sense Gin"]]["text"]
    with db.connect() as con:
        with con:
            con.execute("INSERT INTO pantry(user_id,ingredient_id) VALUES(?,?)",
                        (uid, int(ingredient["Seventh Sense Gin"])))
    rows, _ = recipe_rows(client, "Gin & Tonic Seventh Sense")
    assert rows[ingredient["Seventh Sense Gin"]]["status"]
    assert "On your shelf" in rows[ingredient["Seventh Sense Gin"]]["text"]


def test_no_shelf_highlights_for_empty_ownership_and_login_required(client):
    assert client.get("/recipes/1").status_code == 302
    setup(client)
    rows, _ = recipe_rows(client, "Classic Negroni")
    assert not any(row["status"] for row in rows.values())
