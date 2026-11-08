"""Per-user shelf state and safe additive ownership updates.

Catalog entries remain shared. Ownership is always scoped to the authenticated
user; the UI and MCP use the same pantry table and category semantics.
"""
from __future__ import annotations

from api_schema import shelf_revision
from db import ingredient_category_maps, branded_display_name


def catalog(con):
    categories = [dict(row) for row in con.execute(
        'SELECT id,name,parent_id FROM categories ORDER BY name COLLATE NOCASE')]
    forbidden = {row['id'] for row in categories if row['name'].casefold() == 'ice'}
    while True:
        old_len = len(forbidden)
        forbidden.update(row['id'] for row in categories if row['parent_id'] in forbidden)
        if old_len == len(forbidden):
            break
    selectable = [
        row for row in categories
        if row['parent_id'] is not None and row['id'] not in forbidden
    ]
    all_cats, expanded, explicit, ancestors = ingredient_category_maps(con)
    products = []
    for ingredient in con.execute(
        'SELECT id,name,is_brand FROM ingredients WHERE is_brand=1 ORDER BY name COLLATE NOCASE'
    ):
        classifications = explicit.get(ingredient['id'], set())
        usable = sorted(c for c in classifications if c not in forbidden)
        if not usable:
            continue
        products.append({
            'id': ingredient['id'], 'name': ingredient['name'],
            'display_name': branded_display_name(
                ingredient['name'], ingredient['is_brand'],
                classifications, all_cats, ancestors),
            'category_ids': usable
        })
    return selectable, products, {r['id'] for r in selectable}, {p['id'] for p in products}


def shelf_state(con, user_id):
    cats, products, _, _ = catalog(con)
    owned = con.execute(
        'SELECT category_id,ingredient_id FROM pantry WHERE user_id=?', (user_id,)
    ).fetchall()
    selected_c = {r['category_id'] for r in owned if r['category_id'] is not None}
    selected_p = {r['ingredient_id'] for r in owned if r['ingredient_id'] is not None}
    return {
        'user_id': user_id,
        'owned_category_ids': sorted(selected_c),
        'owned_ingredient_ids': sorted(selected_p),
        'categories': [dict(row, owned=row['id'] in selected_c) for row in cats],
        'ingredients': [dict(row, owned=row['id'] in selected_p) for row in products],
        'revision': shelf_revision(con, user_id)
    }


def apply_shelf_changes(con, user_id, changes):
    """Validate the whole batch first and then commit atomically.

    Repeated operations are idempotent and never replace unrelated selections.
    """
    if not isinstance(changes,list) or not 1 <= len(changes) <= 100:
        raise ValueError('Provide 1 to 100 shelf ownership changes.')
    _, _, allowed_c, allowed_p = catalog(con)
    operations, seen = [], set()
    for change in changes:
        if not isinstance(change,dict) or set(change)-{'ingredient_id','category_id','owned'}:
            raise ValueError('Use ingredient_id or category_id and an owned boolean.')
        is_product = 'ingredient_id' in change
        is_category = 'category_id' in change
        if is_product == is_category or type(change.get('owned')) is not bool:
            raise ValueError('Specify exactly one item ID and owned=true/false.')
        key = 'ingredient_id' if is_product else 'category_id'
        item_id = change[key]
        if type(item_id) is not int or item_id <= 0:
            raise ValueError('Shelf item IDs must be positive integers.')
        if item_id not in (allowed_p if is_product else allowed_c):
            raise ValueError('Unknown or nonselectable shelf item.')
        identity = (key,item_id)
        if identity in seen:
            raise ValueError('A shelf item may occur only once per batch.')
        seen.add(identity)
        operations.append((key,item_id,change['owned']))
    with con:
        for key,item_id,owned in operations:
            if owned:
                con.execute(
                    'INSERT OR IGNORE INTO pantry(user_id,category_id,ingredient_id) VALUES(?,?,?)',
                    (user_id, item_id if key=='category_id' else None,
                     item_id if key=='ingredient_id' else None))
            else:
                con.execute(f'DELETE FROM pantry WHERE user_id=? AND {key}=?',
                            (user_id,item_id))
    return shelf_state(con,user_id)
