"""Idempotent migrations for the original, privately deployed recipe database.

Only import-owned rows with unchanged legacy fields are translated. User-owned
recipes and custom uploads are never overwritten. The migration is transactional
with the version marker so a failed upgrade can be safely retried.
"""
from __future__ import annotations
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MIGRATION_KEY = 'english_photos_v2'


def apply_english_photos_v2(con):
    if con.execute('SELECT 1 FROM settings WHERE key=?', (MIGRATION_KEY,)).fetchone():
        return
    original = json.loads((ROOT / 'seed' / 'legacy_v1.json').read_text(encoding='utf-8'))
    updated = json.loads((ROOT / 'seed' / 'recipes.json').read_text(encoding='utf-8'))
    if len(original) != len(updated):
        raise RuntimeError('Seed migration snapshot and current seed are out of sync.')
    # Existing database may contain edits to original seeded recipes; compare
    # each field before translating it to avoid overwriting user changes.
    with con:
        for position, (old, new) in enumerate(zip(original, updated), start=1):
            row = con.execute('''SELECT id,title,notes,steps_json,image_file FROM recipes
                                 WHERE created_by IS NULL AND (title IN (?,?) OR id=?)
                                 ORDER BY CASE WHEN title=? THEN 0 ELSE 1 END LIMIT 1''',
                              (old['name'], new['name'],position,old['name'])).fetchone()
            if row is None:
                continue
            values = {}
            if row['title'] == old['name']:
                values['title'] = new['name']
            if row['notes'] == old['method_note']:
                values['notes'] = new['method_note']
            if json.loads(row['steps_json']) == old['steps']:
                values['steps_json'] = json.dumps(new['steps'], ensure_ascii=False)
            if not row['image_file']:
                values['image_file'] = new['image_file']
            if values:
                con.execute('UPDATE recipes SET ' + ','.join(f'{field}=?' for field in values) +
                            ' WHERE id=?', (*values.values(), row['id']))
        # Map old stock ingredient names to idiomatic English. Product names
        # are left unchanged; the original taxonomies remain attached to IDs.
        original_seed = json.loads((ROOT / 'seed' / 'legacy_ingredients_v1.json').read_text(encoding='utf-8'))
        replacements = {name: translated for name, translated in original_seed.items() if name != translated}
        for old, translated in replacements.items():
            source = con.execute('SELECT id,added_by FROM ingredients WHERE name=? COLLATE NOCASE',(old,)).fetchone()
            if source is None or source['added_by'] is not None:
                continue
            # If someone already added an English-language equivalent, reuse
            # its ID instead of creating a confusing duplicate.
            dest = con.execute('SELECT id FROM ingredients WHERE name=? COLLATE NOCASE',(translated,)).fetchone()
            if dest is None or dest['id'] == source['id']:
                con.execute('UPDATE ingredients SET name=? WHERE id=?',(translated,source['id']))
                continue
            old_id,new_id=source['id'],dest['id']
            con.execute('UPDATE recipe_items SET ingredient_id=? WHERE ingredient_id=?',(new_id,old_id))
            con.execute('''INSERT OR IGNORE INTO pantry(user_id,ingredient_id)
                           SELECT user_id,? FROM pantry WHERE ingredient_id=?''',(new_id,old_id))
            con.execute('''INSERT OR IGNORE INTO ingredient_categories(ingredient_id,category_id)
                           SELECT ?,category_id FROM ingredient_categories WHERE ingredient_id=?''',(new_id,old_id))
            con.execute('DELETE FROM ingredients WHERE id=?',(old_id,))
        # Only the initial, system-owned recipe quantities are normalized.
        con.execute('''UPDATE recipe_items SET quantity='Plenty'
                       WHERE quantity='Viel' AND recipe_id IN
                       (SELECT id FROM recipes WHERE created_by IS NULL)''')
        con.execute('''UPDATE recipe_items SET quantity='Many'
                       WHERE quantity='Viele' AND recipe_id IN
                       (SELECT id FROM recipes WHERE created_by IS NULL)''')
        # Ice does not belong to the shelf; ignore legacy checked ice values.
        con.execute('''DELETE FROM pantry WHERE category_id IN
                       (SELECT id FROM categories WHERE name='Ice' COLLATE NOCASE)''')
        con.execute('''DELETE FROM pantry WHERE ingredient_id IN
                       (SELECT id FROM ingredients WHERE name='Ice' COLLATE NOCASE)''')
        con.execute('INSERT INTO settings(key,value) VALUES(?,?)',(MIGRATION_KEY,'done'))
