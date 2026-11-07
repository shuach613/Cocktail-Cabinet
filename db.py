import hashlib
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from flask import g
from catalog import TREE, CLASSIFICATIONS, GENERIC_LABELS
from migrations import apply_english_photos_v2

DATA_DIR = Path(os.getenv('DATA_DIR', '/data'))
DB_PATH = DATA_DIR / 'cabinet.sqlite3'

SCHEMA = '''
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS users(
 id INTEGER PRIMARY KEY, username TEXT NOT NULL UNIQUE COLLATE NOCASE,
 email TEXT NOT NULL UNIQUE COLLATE NOCASE, password_hash TEXT NOT NULL,
 role TEXT NOT NULL CHECK(role IN ('admin','member')),
 active INTEGER NOT NULL DEFAULT 1, must_change_password INTEGER NOT NULL DEFAULT 0,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS invitations(
 id INTEGER PRIMARY KEY, email TEXT NOT NULL, token_hash TEXT NOT NULL UNIQUE,
 expires_at TEXT NOT NULL, used_at TEXT, revoked_at TEXT, used_by INTEGER REFERENCES users(id),
 created_by INTEGER NOT NULL REFERENCES users(id), created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS categories(
 id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE COLLATE NOCASE,
 parent_id INTEGER REFERENCES categories(id) ON DELETE RESTRICT);
CREATE TABLE IF NOT EXISTS ingredients(
 id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE COLLATE NOCASE,
 is_brand INTEGER NOT NULL DEFAULT 0, added_by INTEGER REFERENCES users(id),
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS ingredient_categories(
 ingredient_id INTEGER NOT NULL REFERENCES ingredients(id) ON DELETE CASCADE,
 category_id INTEGER NOT NULL REFERENCES categories(id) ON DELETE RESTRICT,
 PRIMARY KEY(ingredient_id, category_id));
CREATE TABLE IF NOT EXISTS recipes(
 id INTEGER PRIMARY KEY, title TEXT NOT NULL, technique TEXT NOT NULL DEFAULT 'Built',
 glass TEXT NOT NULL DEFAULT '', notes TEXT NOT NULL DEFAULT '', steps_json TEXT NOT NULL DEFAULT '[]',
 source_url TEXT NOT NULL DEFAULT '', source_label TEXT NOT NULL DEFAULT '', image_file TEXT,
 created_by INTEGER REFERENCES users(id), created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS recipe_items(
 id INTEGER PRIMARY KEY, recipe_id INTEGER NOT NULL REFERENCES recipes(id) ON DELETE CASCADE,
 ingredient_id INTEGER NOT NULL REFERENCES ingredients(id), quantity TEXT NOT NULL,
 kind TEXT NOT NULL DEFAULT 'ingredient' CHECK(kind IN ('ingredient','garnish','ice')),
 optional INTEGER NOT NULL DEFAULT 0, sort_order INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS pantry(
 user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 category_id INTEGER REFERENCES categories(id) ON DELETE CASCADE,
 ingredient_id INTEGER REFERENCES ingredients(id) ON DELETE CASCADE,
 CHECK((category_id IS NOT NULL) != (ingredient_id IS NOT NULL)),
 UNIQUE(user_id, category_id), UNIQUE(user_id, ingredient_id));
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_recipes_title ON recipes(title);
CREATE INDEX IF NOT EXISTS idx_recipe_items_recipe ON recipe_items(recipe_id);
CREATE INDEX IF NOT EXISTS idx_invitations_lookup ON invitations(token_hash);
'''

def connect():
    con=sqlite3.connect(DB_PATH, timeout=15)
    con.row_factory=sqlite3.Row
    con.execute('PRAGMA foreign_keys = ON')
    con.execute('PRAGMA busy_timeout=15000')
    return con

def get_db():
    if 'db' not in g: g.db=connect()
    return g.db

def close_db(_=None):
    con=g.pop('db', None)
    if con: con.close()

def ensure_db():
    DATA_DIR.mkdir(parents=True,exist_ok=True)
    (DATA_DIR/'uploads').mkdir(exist_ok=True)
    with connect() as con:
        con.execute('PRAGMA journal_mode=WAL')
        con.executescript(SCHEMA)
        if not con.execute("SELECT 1 FROM settings WHERE key='seeded_v1'").fetchone():
            seed(con)
            con.execute("INSERT INTO settings(key,value) VALUES('seeded_v1','true')")
        apply_english_photos_v2(con)

def seed(con):
    def add_tree(tree,parent=None):
        for name, children in tree.items():
            con.execute('INSERT OR IGNORE INTO categories(name,parent_id) VALUES (?,?)',(name,parent))
            row=con.execute('SELECT id FROM categories WHERE name=?',(name,)).fetchone()
            add_tree(children,row['id'])
    add_tree(TREE)
    cat={x['name']:x['id'] for x in con.execute('SELECT id,name FROM categories')}
    import_path=Path(__file__).parent/'seed'/'recipes.json'
    drinks=json.loads(import_path.read_text(encoding='utf-8'))
    for recipe in drinks:
        cur=con.execute('INSERT INTO recipes(title,technique,glass,notes,steps_json,source_url,source_label,image_file) VALUES (?,?,?,?,?,?,?,?)',
              (recipe['name'],recipe['technique'],recipe['glass'],recipe['method_note'],json.dumps(recipe['steps'],ensure_ascii=False),recipe['source_url'],recipe['source_label'],recipe.get('image_file','')))
        recipe_id=cur.lastrowid
        for ix,item in enumerate(recipe['ingredients']):
            name=item['name']
            row=con.execute('SELECT id FROM ingredients WHERE name=? COLLATE NOCASE',(name,)).fetchone()
            if row: ingredient_id=row['id']
            else:
                ingredient_id=con.execute('INSERT INTO ingredients(name,is_brand) VALUES(?,?)',(name,int(name not in GENERIC_LABELS))).lastrowid
                tags=CLASSIFICATIONS.get(name)
                if not tags:
                    raise ValueError(f'Missing ingredient taxonomy: {name}')
                for tag in tags:
                    con.execute('INSERT INTO ingredient_categories VALUES(?,?)',(ingredient_id,cat[tag]))
            con.execute('INSERT INTO recipe_items(recipe_id,ingredient_id,quantity,kind,optional,sort_order) VALUES (?,?,?,?,?,?)',
                        (recipe_id,ingredient_id,item['quantity'],item['kind'],int(item['optional']),ix))

def category_ancestors(categories):
    parents={r['id']:r['parent_id'] for r in categories}
    def ancestors(cid):
        found=set()
        while cid is not None and cid not in found:
            found.add(cid); cid=parents.get(cid)
        return found
    return ancestors

def ingredient_category_maps(con):
    cats=con.execute('SELECT * FROM categories ORDER BY name').fetchall()
    ancestors=category_ancestors(cats)
    mapping={}
    explicit={}
    for r in con.execute('SELECT ingredient_id,category_id FROM ingredient_categories'):
        explicit.setdefault(r['ingredient_id'],set()).add(r['category_id'])
        mapping.setdefault(r['ingredient_id'],set()).update(ancestors(r['category_id']))
    return cats, mapping, explicit, ancestors

def ingredient_matches(item,p_cat_ids,p_ing_ids,expanded,explicit,ancestors):
    ing_id=item['ingredient_id']
    if ing_id in p_ing_ids: return True
    req_all=expanded.get(ing_id,set())
    if req_all & p_cat_ids: return True
    # A specific bottle of any subtype can satisfy a generic requirement of that family,
    # but not a *different* specific brand or flavor.
    req=explicit.get(ing_id,set())
    if not item['is_brand'] and req:
        for owned_cat in p_cat_ids:
            for generic in req:
                if generic in ancestors(owned_cat): return True
        for owned_ing in p_ing_ids:
            for generic in req:
                if generic in expanded.get(owned_ing,set()): return True
    return False

def recipe_card(con,recipe,pantry_cats,pantry_ings,maps):
    cats,expanded,explicit,ancestors=maps
    items=con.execute('''SELECT ri.*,i.name,i.is_brand FROM recipe_items ri JOIN ingredients i ON i.id=ri.ingredient_id
                          WHERE ri.recipe_id=? ORDER BY ri.sort_order''',(recipe['id'],)).fetchall()
    data=[dict(i) for i in items]
    for item in data:
        item['categories']=sorted(expanded.get(item['ingredient_id'],set()))
        item['matched']=bool(ingredient_matches(item,pantry_cats,pantry_ings,expanded,explicit,ancestors))
    needs=[i for i in data if i['kind']=='ingredient' and not i['optional']]
    missing=[i for i in needs if not i['matched']]
    r=dict(recipe)
    r['items']=data
    r['steps']=json.loads(recipe['steps_json'])
    r['missing']=missing
    r['required_count']=len(needs)
    r['match_count']=len(needs)-len(missing)
    r['can_make']=not missing
    return r
