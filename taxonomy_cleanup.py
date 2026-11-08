"""One-time, ownership-preserving simplification of the shared shelf taxonomy.

Do not rewrite recipe records or their ingredient references. Mint / Mint leaves /
Peppermint, and Lime / Lime (4 pieces) are deliberately retained as distinct
recipe labels that *already* map to the same generic shelf categories.
"""
from __future__ import annotations

MIGRATION_KEY = "taxonomy_cleanup_v4"


def _id(con, name):
    row = con.execute("SELECT id FROM categories WHERE name=? COLLATE NOCASE", (name,)).fetchone()
    return row["id"] if row else None


def _merge_category(con, source, destination):
    """Replace a redundant class without orphaning ingredients or owned choices."""
    old_id = _id(con, source)
    new_id = _id(con, destination)
    if old_id is None:
        return
    if new_id is None or old_id == new_id:
        raise RuntimeError(f"Cannot migrate {source!r}: missing or invalid destination {destination!r}")

    # INSERT OR IGNORE prevents duplicate taxonomy and per-user pantry entries.
    con.execute(
        """INSERT OR IGNORE INTO ingredient_categories(ingredient_id, category_id)
           SELECT ingredient_id, ? FROM ingredient_categories WHERE category_id=?""",
        (new_id, old_id),
    )
    con.execute(
        """INSERT OR IGNORE INTO pantry(user_id, category_id)
           SELECT user_id, ? FROM pantry WHERE category_id=?""",
        (new_id, old_id),
    )
    con.execute("DELETE FROM pantry WHERE category_id=?", (old_id,))
    con.execute("DELETE FROM ingredient_categories WHERE category_id=?", (old_id,))
    con.execute("UPDATE categories SET parent_id=? WHERE parent_id=?", (new_id, old_id))
    con.execute("DELETE FROM categories WHERE id=?", (old_id,))


def _rename_or_merge(con, source, destination):
    old_id = _id(con, source)
    if old_id is None:
        return
    if _id(con, destination) is not None:
        _merge_category(con, source, destination)
    else:
        # Keep category IDs stable whenever possible, including selected pantry IDs.
        con.execute("UPDATE categories SET name=? WHERE id=?", (destination, old_id))


def apply_taxonomy_cleanup_v4(con):
    if con.execute("SELECT 1 FROM settings WHERE key=?", (MIGRATION_KEY,)).fetchone():
        return

    # Transaction and marker commit together. On error no recipe/shelf changes persist.
    with con:
        # Keep the citrus and London Dry gin families distinct.
        _rename_or_merge(con, "Contemporary Gin", "Specialty Gin")
        _merge_category(con, "Flavoured Gin", "Specialty Gin")

        # Exactly Indian / Pepper / Mediterranean tonic subtypes under Tonic water.
        _rename_or_merge(con, "Pink pepper tonic", "Pepper tonic")
        _rename_or_merge(con, "Herbal tonic", "Mediterranean tonic")

        # Bourbon remains its own family; Swiss single malt becomes generic single malt.
        _rename_or_merge(con, "Swiss Single Malt Whisky", "Single Malt Whisky")

        # Collapse overly narrow categories, while preserving their branded products.
        _merge_category(con, "Spicy ginger beer", "Ginger beer")
        _merge_category(con, "Artichoke amaro", "Amaro")
        _merge_category(con, "Pimm’s-style cup liqueur", "Aperitifs & liqueurs")

        # Sparkling wine / prosecco is one flexible generic ingredient.
        _merge_category(con, "Prosecco", "Sparkling wine")

        # Correct an obvious product-name typo (same ingredient ID and recipe refs).
        old = con.execute(
            "SELECT id FROM ingredients WHERE name=? COLLATE NOCASE",
            ("Swiss Moutain Spring Provencale Tonic",),
        ).fetchone()
        exists = con.execute(
            "SELECT id FROM ingredients WHERE name=? COLLATE NOCASE",
            ("Swiss Mountain Spring Provencale Tonic",),
        ).fetchone()
        if old and not exists:
            con.execute(
                "UPDATE ingredients SET name=? WHERE id=?",
                ("Swiss Mountain Spring Provencale Tonic", old["id"]),
            )

        violations = con.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise RuntimeError(f"Taxonomy cleanup would break references: {len(violations)} foreign-key violations")
        con.execute("INSERT INTO settings(key,value) VALUES (?,?)", (MIGRATION_KEY, "done"))
