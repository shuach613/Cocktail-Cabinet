"""Feature regression coverage for the shelf/settings/PWA overhaul."""
import json
from pathlib import Path
from test_app import client, admin_setup, csrf
import db
import app as module


def test_import_english_and_photos(client):
    con=db.connect()
    titles={r['title'] for r in con.execute('SELECT title FROM recipes')}
    assert 'Classic Negroni' in titles and 'Chocolate Negroni' in titles
    assert 'Negroni normal' not in titles and 'Negroni Schoggi' not in titles
    assert con.execute('SELECT COUNT(*) FROM recipes WHERE image_file LIKE "seed_photos/%"').fetchone()[0]==22
    for row in con.execute('SELECT image_file FROM recipes'):
        path=Path(module.app.root_path)/'seed'/'photos'/row['image_file'].removeprefix('seed_photos/')
        assert path.is_file() and path.stat().st_size>4000
    con.close()
    assert client.get('/recipe-art/negroni.webp').status_code==302
    admin_setup(client)
    image=client.get('/recipe-art/negroni.webp')
    assert image.status_code==200 and image.mimetype=='image/webp'
    recipe=client.get('/recipes/1')
    assert recipe.status_code==200
    assert b'View the researched source' not in recipe.data
    assert b'Preparation reference' not in recipe.data
    assert b'Edit recipe' not in recipe.data
    assert b'/recipe-art/negroni.webp' in recipe.data


def test_pantry_groups_products_and_excludes_ice(client):
    admin_setup(client)
    response=client.get('/pantry')
    assert response.status_code==200
    assert b'Specific bottles & brands' not in response.data
    assert b'Tanqueray London Dry Gin' in response.data
    assert b'Ice is always assumed available' not in response.data
    assert b'icons.svg#chevron-down' in response.data
    assert b'"Ice"' not in response.data  # no ice shelf input label
    con=db.connect()
    ice=con.execute("SELECT id FROM categories WHERE name='Ice'").fetchone()['id']
    con.close()
    bad=client.post('/pantry',data={'_csrf':csrf(client),'category':str(ice)})
    assert bad.status_code==400


def test_inline_new_category_and_recipe_are_atomic(client):
    admin_setup(client)
    client.get('/recipes/new')  # establish fresh CSRF token after admin setup
    con=db.connect()
    parent=con.execute("SELECT id FROM categories WHERE name='Scotch Whisky'").fetchone()['id']
    con.close()
    payload={'title':'Singleton Ginger','technique':'Built','glass':'Highball','notes':'',
             'new_categories':[{'temp_id':'new:1','name':'Single Malt Scotch Whisky','parent_id':parent}],
             'steps':['Add whisky to ice','Top with mixer'],
             'items':[{'name':'Singleton 12','quantity':'4 cl','kind':'ingredient','is_brand':True,'categories':['new:1']}]}
    response=client.post('/recipes/new',json=payload,headers={'X-CSRF-Token':csrf(client)})
    assert response.status_code==201,response.data
    con=db.connect()
    cat=con.execute("SELECT id,parent_id FROM categories WHERE name='Single Malt Scotch Whisky'").fetchone()
    assert cat and cat['parent_id']==parent
    ingredient=con.execute("SELECT id FROM ingredients WHERE name='Singleton 12'").fetchone()
    assert ingredient
    assert con.execute('SELECT 1 FROM ingredient_categories WHERE ingredient_id=? AND category_id=?',(ingredient['id'],cat['id'])).fetchone()
    con.close()
    invalid={**payload,'title':'Never save','steps':[],'new_categories':[{'temp_id':'new:2','name':'Unsaved New Category','parent_id':parent}]}
    invalid['items']=[{**payload['items'][0],'name':'NeverSaveGin','categories':['new:2']}]
    response=client.post('/recipes/new',json=invalid,headers={'X-CSRF-Token':csrf(client)})
    assert response.status_code==400
    con=db.connect()
    assert con.execute("SELECT COUNT(*) FROM categories WHERE name='Unsaved New Category'").fetchone()[0]==0
    assert con.execute("SELECT COUNT(*) FROM recipes WHERE title='Never save'").fetchone()[0]==0
    con.close()


def test_settings_admin_and_recipe_management(client):
    admin_setup(client)
    assert client.get('/settings').status_code==200
    assert client.get('/settings/recipes').status_code==200
    assert client.get('/settings/ingredients').status_code==200
    response=client.get('/recipes/1')
    assert b'/settings/recipes' not in response.data or b'Edit recipe' not in response.data
    assert b'Research' not in response.data


def test_no_public_photos_and_no_placeholder_elements(client):
    admin_setup(client)
    home=client.get('/')
    assert home.status_code==200
    assert b'glass-illustration' not in home.data
    assert b'/recipe-art/' in home.data

