"""Regression checks for taxonomy v4, with recipe and ownership preservation."""
import os
import tempfile
from pathlib import Path

os.environ.setdefault("TESTING", "1")
os.environ.setdefault("SECRET_KEY", "test_secret_" * 6)
os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="cabinet-taxonomy-tests-"))

import db
from taxonomy_cleanup import MIGRATION_KEY, apply_taxonomy_cleanup_v4


def snapshot_recipes(con):
    return [
        tuple(r) for r in con.execute(
            """SELECT ri.id,ri.recipe_id,ri.ingredient_id,ri.quantity,
                      ri.kind,ri.optional,ri.sort_order
               FROM recipe_items ri ORDER BY ri.id"""
        )
    ], [
        tuple(r) for r in con.execute(
            """SELECT id,title,technique,glass,notes,steps_json,source_url,
                      source_label,image_file,created_by,updated_at
               FROM recipes ORDER BY id"""
        )
    ]


def test_migration_preserves_recipes_existing_bottles_and_user_shelf(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "cabinet.sqlite3")
    db.ensure_db()
    with db.connect() as con:
        original = snapshot_recipes(con)
        assert len(original[0]) == 105
        assert len(original[1]) == 22
        con.execute("DELETE FROM settings WHERE key=?", (MIGRATION_KEY,))
        parent = {r["name"]: r["id"] for r in con.execute("SELECT id,name FROM categories")}
        legacy = {
            "Contemporary Gin": "Gin",
            "Flavoured Gin": "Gin",
            "Herbal tonic": "Tonic water",
            "Pink pepper tonic": "Tonic water",
            "Spicy ginger beer": "Ginger beer",
            "Swiss Single Malt Whisky": "Whisky",
            "Artichoke amaro": "Amaro",
            "Pimm’s-style cup liqueur": "Aperitifs & liqueurs",
            "Prosecco": "Sparkling wine",
        }
        for name, family in legacy.items():
            con.execute("INSERT INTO categories(name,parent_id) VALUES(?,?)", (name,parent[family]))
        cat = {r["name"]: r["id"] for r in con.execute("SELECT id,name FROM categories")}
        user = con.execute(
            """INSERT INTO users(username,email,password_hash,role)
               VALUES ('taxonomy-test','taxonomy-test@example.com','x','admin')"""
        ).lastrowid
        expected_ingredients = set()
        for name, classification in [
            ("Thinkers Israeli Sunset Gin", "Contemporary Gin"),
            ("Edinburgh Gin Rhubarb & Ginger", "Flavoured Gin"),
            ("Säntis Malt Edition Dreifaltigkeit", "Swiss Single Malt Whisky"),
        ]:
            iid = con.execute(
                "INSERT INTO ingredients(name,is_brand) VALUES(?,1)", (name,)
            ).lastrowid
            expected_ingredients.add(iid)
            con.execute(
                "INSERT INTO ingredient_categories VALUES (?,?)", (iid,cat[classification])
            )
            con.execute(
                "INSERT INTO pantry(user_id,ingredient_id) VALUES(?,?)", (user,iid)
            )
        con.execute(
            "INSERT INTO pantry(user_id,category_id) VALUES(?,?)",
            (user,cat["Pink pepper tonic"]),
        )
        con.execute(
            "INSERT INTO pantry(user_id,category_id) VALUES(?,?)",
            (user,cat["Prosecco"]),
        )
        con.execute(
            "INSERT INTO pantry(user_id,category_id) VALUES(?,?)",
            (user,cat["Citrus Gin"]),
        )
        # Exercise taxonomy migration for seeded brands too.
        for name, source in [
            ("Cynar", "Artichoke amaro"),
            ("Pimm's", "Pimm’s-style cup liqueur"),
            ("Schweppes Ginger Beer & Chili", "Spicy ginger beer"),
        ]:
            iid = con.execute("SELECT id FROM ingredients WHERE name=?", (name,)).fetchone()["id"]
            con.execute("DELETE FROM ingredient_categories WHERE ingredient_id=?", (iid,))
            con.execute("INSERT INTO ingredient_categories VALUES (?,?)", (iid,cat[source]))
        # Exercise typo fix using an unchanged seed ingredient ID.
        con.execute(
            """UPDATE ingredients SET name='Swiss Moutain Spring Provencale Tonic'
               WHERE name='Swiss Mountain Spring Provencale Tonic'"""
        )
        con.commit()

        apply_taxonomy_cleanup_v4(con)
        assert snapshot_recipes(con) == original
        assert not con.execute("PRAGMA foreign_key_check").fetchall()
        names = {r["name"] for r in con.execute("SELECT name FROM categories")}
        assert {"Specialty Gin", "Citrus Gin", "London Dry Gin",
                "Indian tonic", "Pepper tonic", "Mediterranean tonic",
                "Bourbon", "Single Malt Whisky", "Liqueurs"} <= names
        assert not set(legacy).intersection(names)
        assert "Swiss Mountain Spring Provencale Tonic" in {
            r["name"] for r in con.execute("SELECT name FROM ingredients")
        }
        assert {r["ingredient_id"] for r in con.execute(
            "SELECT ingredient_id FROM pantry WHERE user_id=? AND ingredient_id IS NOT NULL", (user,)
        )} == expected_ingredients
        pantry_names = {r["name"] for r in con.execute(
            """SELECT c.name FROM pantry p JOIN categories c ON c.id=p.category_id
               WHERE p.user_id=?""", (user,)
        )}
        assert pantry_names == {"Pepper tonic", "Sparkling wine", "Citrus Gin"}
        for bottle in ("Thinkers Israeli Sunset Gin", "Edinburgh Gin Rhubarb & Ginger"):
            classifications = {r["name"] for r in con.execute(
                """SELECT c.name FROM ingredient_categories ic
                   JOIN categories c ON c.id=ic.category_id
                   JOIN ingredients i ON i.id=ic.ingredient_id
                   WHERE i.name=?""", (bottle,)
            )}
            assert classifications == {"Specialty Gin"}
        assert {r["name"] for r in con.execute(
            """SELECT c.name FROM ingredient_categories ic JOIN categories c
               ON c.id=ic.category_id JOIN ingredients i ON i.id=ic.ingredient_id
               WHERE i.name='Pimm''s'"""
        )} == {"Liqueurs"}
        before_second_run = list(con.iterdump())
        apply_taxonomy_cleanup_v4(con)
        assert list(con.iterdump()) == before_second_run


def test_fresh_install_has_only_requested_tonic_and_gin_families(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "cabinet.sqlite3")
    db.ensure_db()
    with db.connect() as con:
        names = {r["name"] for r in con.execute("SELECT name FROM categories")}
        assert {"Indian tonic", "Pepper tonic", "Mediterranean tonic",
                "Citrus Gin", "London Dry Gin", "Specialty Gin"} <= names
        assert not {"Herbal tonic", "Pink pepper tonic", "Flavoured Gin",
                    "Contemporary Gin", "Spicy ginger beer", "Prosecco"} & names
        assert len(snapshot_recipes(con)[0]) == 105
        assert not con.execute("PRAGMA foreign_key_check").fetchall()
