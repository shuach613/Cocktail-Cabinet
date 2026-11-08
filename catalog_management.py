"""Safe, atomic management operations for the shared ingredient catalogue.

Recipes and user shelves are *referential data*. Never quietly cascade-delete
them: reject destructive deletion when in use, or explicitly merge into another
existing record while retaining recipe lines, measurements and ownership.
All functions accept a live SQLite connection and leave transaction ownership
to their caller (REST route or batch).
"""
from __future__ import annotations

import sqlite3


class CatalogConflict(ValueError):
    """Requested edit would break a recipe, owned shelf item or category tree."""


def positive_id(value, label="id"):
    if type(value) is not int or value <= 0:
        raise ValueError(f"{label} must be a positive integer.")
    return value


def strict_bool(value, label):
    if type(value) is not bool:
        raise ValueError(f"{label} must be a boolean.")
    return value


def get_ingredient(con, ingredient_id):
    ingredient_id = positive_id(ingredient_id, "ingredient_id")
    r = con.execute("SELECT id,name,is_brand FROM ingredients WHERE id=?", (ingredient_id,)).fetchone()
    if r is None:
        raise LookupError("Ingredient not found.")
    return r


def get_category(con, category_id):
    category_id = positive_id(category_id, "category_id")
    r = con.execute("SELECT id,name,parent_id FROM categories WHERE id=?", (category_id,)).fetchone()
    if r is None:
        raise LookupError("Category not found.")
    return r


def counts_ingredient(con, ingredient_id):
    get_ingredient(con, ingredient_id)
    refs = con.execute(
        """SELECT r.id,r.title,ri.id AS recipe_item_id,ri.quantity,ri.kind,ri.optional
           FROM recipe_items ri JOIN recipes r ON r.id=ri.recipe_id
           WHERE ri.ingredient_id=? ORDER BY r.id,ri.sort_order""",
        (ingredient_id,)
    ).fetchall()
    owners = con.execute(
        "SELECT COUNT(*) FROM pantry WHERE ingredient_id=?", (ingredient_id,)
    ).fetchone()[0]
    categories = con.execute(
        "SELECT category_id FROM ingredient_categories WHERE ingredient_id=? ORDER BY category_id",
        (ingredient_id,)
    ).fetchall()
    return {
        "ingredient_id": ingredient_id,
        "recipes": [dict(x) for x in refs],
        "recipe_item_count": len(refs),
        "owned_by_users": owners,
        "category_ids": [r["category_id"] for r in categories]
    }


def counts_category(con, category_id):
    get_category(con, category_id)
    children = con.execute(
        "SELECT id,name FROM categories WHERE parent_id=? ORDER BY name", (category_id,)
    ).fetchall()
    ingredients = con.execute(
        """SELECT i.id,i.name FROM ingredient_categories ic
           JOIN ingredients i ON i.id=ic.ingredient_id
           WHERE ic.category_id=? ORDER BY i.name""", (category_id,)
    ).fetchall()
    users = con.execute(
        "SELECT COUNT(*) FROM pantry WHERE category_id=?", (category_id,)
    ).fetchone()[0]
    return {
        "category_id": category_id,
        "children": [dict(x) for x in children],
        "ingredients": [dict(x) for x in ingredients],
        "owned_by_users": users
    }


def valid_category_set(con, values, is_brand):
    if not isinstance(values, list) or not values or any(type(v) is not int or v < 1 for v in values):
        raise ValueError("categories must be a nonempty array of positive integer IDs.")
    ids = set(values)
    rows = con.execute("SELECT id,parent_id,name FROM categories").fetchall()
    by_id = {r["id"]: r for r in rows}
    if not ids <= by_id.keys():
        raise ValueError("Unknown category ID.")
    if any(by_id[c]["name"].casefold() == "ice" for c in ids) and is_brand:
        raise ValueError("A branded bottle cannot be classified as Ice.")
    if is_brand and not any(by_id[c]["parent_id"] is not None for c in ids):
        raise ValueError("Branded ingredients require at least one non-root category.")
    return sorted(ids)


def update_ingredient(con, ingredient_id, data):
    old = get_ingredient(con, ingredient_id)
    if not isinstance(data, dict) or not data or set(data) - {"name", "is_brand", "categories"}:
        raise ValueError("Supply one or more of name, is_brand, categories.")
    name = data.get("name", old["name"])
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > 130:
        raise ValueError("Ingredient name must be 1–130 characters.")
    new_brand = strict_bool(data["is_brand"], "is_brand") if "is_brand" in data else bool(old["is_brand"])
    ids = valid_category_set(con, data["categories"], new_brand) if "categories" in data else [
        r["category_id"] for r in con.execute(
            "SELECT category_id FROM ingredient_categories WHERE ingredient_id=?", (ingredient_id,)
        )
    ]
    valid_category_set(con, ids, new_brand)
    con.execute("UPDATE ingredients SET name=?,is_brand=? WHERE id=?",
                (name.strip(), int(new_brand), ingredient_id))
    if "categories" in data:
        con.execute("DELETE FROM ingredient_categories WHERE ingredient_id=?", (ingredient_id,))
        con.executemany("INSERT INTO ingredient_categories(ingredient_id,category_id) VALUES(?,?)",
                        [(ingredient_id, c) for c in ids])
    # Demoting a brand to a generic item must not silently invalidate owned
    # branded bottle selections; require the admin to unown/merge first.
    if old["is_brand"] and not new_brand and con.execute(
        "SELECT 1 FROM pantry WHERE ingredient_id=? LIMIT 1", (ingredient_id,)
    ).fetchone():
        raise CatalogConflict("Bottle is owned on a shelf; transfer ownership first.")
    return {"id": ingredient_id, "name": name.strip(), "is_brand": new_brand, "category_ids": sorted(ids)}


def delete_ingredient(con, ingredient_id):
    refs = counts_ingredient(con, ingredient_id)
    if refs["recipe_item_count"] or refs["owned_by_users"]:
        raise CatalogConflict("Ingredient is referenced by recipes or shelves. Merge or remove references first.")
    con.execute("DELETE FROM ingredients WHERE id=?", (ingredient_id,))
    return {"deleted_ingredient_id": ingredient_id}


def merge_ingredient(con, source_id, target_id):
    source_id = positive_id(source_id, "source_id")
    target_id = positive_id(target_id, "target_id")
    if source_id == target_id:
        raise ValueError("Choose two different ingredients.")
    source = get_ingredient(con, source_id)
    dest = get_ingredient(con, target_id)
    if bool(source["is_brand"]) and not bool(dest["is_brand"]):
        owners = con.execute("SELECT 1 FROM pantry WHERE ingredient_id=? LIMIT 1", (source_id,)).fetchone()
        if owners:
            raise CatalogConflict("Cannot transfer a named owned bottle to a generic ingredient.")
    before = counts_ingredient(con, source_id)
    con.execute("UPDATE recipe_items SET ingredient_id=? WHERE ingredient_id=?", (target_id, source_id))
    con.execute("""INSERT OR IGNORE INTO ingredient_categories(ingredient_id,category_id)
                   SELECT ?,category_id FROM ingredient_categories WHERE ingredient_id=?""",
                (target_id, source_id))
    if bool(dest["is_brand"]):
        con.execute("""INSERT OR IGNORE INTO pantry(user_id,ingredient_id)
                       SELECT user_id,? FROM pantry WHERE ingredient_id=?""", (target_id, source_id))
    con.execute("DELETE FROM ingredients WHERE id=?", (source_id,))
    return {"source_id": source_id, "target_id": target_id,
            "recipe_items_moved": before["recipe_item_count"],
            "shelf_selections_migrated": before["owned_by_users"]}


def update_category(con, category_id, data):
    old = get_category(con, category_id)
    if not isinstance(data, dict) or not data or set(data) - {"name", "parent_id"}:
        raise ValueError("Supply name and/or parent_id.")
    name = data.get("name", old["name"])
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > 65:
        raise ValueError("Category name must be 1–65 characters.")
    parent_id = data.get("parent_id", old["parent_id"])
    if parent_id is not None:
        positive_id(parent_id, "parent_id")
        ancestor = parent_id
        seen = {category_id}
        while ancestor is not None:
            if ancestor in seen:
                raise CatalogConflict("Category cannot become its own descendant.")
            seen.add(ancestor)
            ancestor = get_category(con, ancestor)["parent_id"]
    # Do not turn a root category into a child or vice-versa while user category
    # choices exist; this changes which values are selectable in My Shelf.
    if (old["parent_id"] is None) != (parent_id is None):
        if con.execute("SELECT 1 FROM pantry WHERE category_id=? LIMIT 1", (category_id,)).fetchone():
            raise CatalogConflict("Clear owned category selections before changing root status.")
    con.execute("UPDATE categories SET name=?,parent_id=? WHERE id=?",
                (name.strip(), parent_id, category_id))
    return {"id": category_id, "name": name.strip(), "parent_id": parent_id}


def delete_category(con, category_id):
    refs = counts_category(con, category_id)
    if refs["children"] or refs["ingredients"] or refs["owned_by_users"]:
        raise CatalogConflict("Category has children, classified ingredients or shelf owners. Merge or reassign first.")
    con.execute("DELETE FROM categories WHERE id=?", (category_id,))
    return {"deleted_category_id": category_id}


def merge_category(con, source_id, target_id):
    source_id = positive_id(source_id, "source_id")
    target_id = positive_id(target_id, "target_id")
    if source_id == target_id:
        raise ValueError("Choose two different categories.")
    source = get_category(con, source_id)
    target = get_category(con, target_id)
    if (source["parent_id"] is None) != (target["parent_id"] is None):
        raise CatalogConflict("Root and subcategories cannot be merged; select compatible destinations.")
    # Merging a parent into a descendant would create a cycle.
    ancestor = target_id
    while ancestor is not None:
        if ancestor == source_id:
            raise CatalogConflict("Cannot merge a category into one of its descendants.")
        ancestor = get_category(con, ancestor)["parent_id"]
    refs = counts_category(con, source_id)
    con.execute("""INSERT OR IGNORE INTO ingredient_categories(ingredient_id,category_id)
                   SELECT ingredient_id,? FROM ingredient_categories WHERE category_id=?""",
                (target_id, source_id))
    con.execute("""INSERT OR IGNORE INTO pantry(user_id,category_id)
                   SELECT user_id,? FROM pantry WHERE category_id=?""",
                (target_id, source_id))
    con.execute("DELETE FROM pantry WHERE category_id=?", (source_id,))
    con.execute("DELETE FROM ingredient_categories WHERE category_id=?", (source_id,))
    con.execute("UPDATE categories SET parent_id=? WHERE parent_id=?", (target_id, source_id))
    con.execute("DELETE FROM categories WHERE id=?", (source_id,))
    return {"source_id": source_id, "target_id": target_id,
            "children_reparented": len(refs["children"]),
            "ingredients_reclassified": len(refs["ingredients"]),
            "shelf_selections_migrated": refs["owned_by_users"]}


def dispatch_catalog_operation(con, op):
    if not isinstance(op, dict):
        raise ValueError("Each operation must be an object.")
    action = op.get("action")
    ids = {
        "update_ingredient": ({"ingredient_id", "name", "is_brand", "categories"}, update_ingredient),
        "delete_ingredient": ({"ingredient_id"}, delete_ingredient),
        "merge_ingredient": ({"source_id", "target_id"}, merge_ingredient),
        "update_category": ({"category_id", "name", "parent_id"}, update_category),
        "delete_category": ({"category_id"}, delete_category),
        "merge_category": ({"source_id", "target_id"}, merge_category),
    }
    if action not in ids:
        raise ValueError("Unknown catalog action.")
    allowed, fn = ids[action]
    if set(op) - allowed - {"action"}:
        raise ValueError("Unknown fields for catalog action.")
    if action.startswith("update_"):
        key = "ingredient_id" if action == "update_ingredient" else "category_id"
        return fn(con, op[key], {k: v for k, v in op.items() if k not in {"action", key}})
    if action.startswith("delete_"):
        key = "ingredient_id" if action == "delete_ingredient" else "category_id"
        return fn(con, op[key])
    return fn(con, op["source_id"], op["target_id"])


def catalog_batch(con, operations):
    if not isinstance(operations, list) or not 1 <= len(operations) <= 50:
        raise ValueError("Supply 1–50 catalog operations.")
    results = []
    with con:
        for index, op in enumerate(operations):
            try:
                results.append(dispatch_catalog_operation(con, op))
            except (ValueError, LookupError, sqlite3.IntegrityError, KeyError) as e:
                raise CatalogConflict(f"Operation #{index+1}: {e}") from e
        broken = con.execute("PRAGMA foreign_key_check").fetchall()
        if broken:
            raise CatalogConflict("Foreign-key constraint check failed.")
    return results
