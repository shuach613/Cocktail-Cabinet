"""Editorial and taxonomy regression tests for recipe polish v3."""
import json
import re
from pathlib import Path
import db
import app as module
from test_app import client, admin_setup, csrf

ROOT=Path(__file__).resolve().parents[1]


def test_seed_is_natural_english_without_spreadsheet_references():
    recipes=json.loads((ROOT/'seed'/'recipes.json').read_text(encoding='utf-8'))
    assert len(recipes)==22
    assert sum(len(recipe['ingredients']) for recipe in recipes)==105
    german=re.compile(r'\b(?:glas auffüllen|mittel|tropfen|blätter|zweig|schnitz|was es hat|spritzer)\b',re.I)
    for recipe in recipes:
        assert not any(x in (recipe['method_note']+' '+' '.join(recipe['steps'])).lower()
                       for x in ('spreadsheet','workbook','imported from','original drinks'))
        assert not german.search(recipe['method_note']+' '+' '.join(recipe['steps']))
        for item in recipe['ingredients']:
            assert not german.search(item['quantity']), (recipe['name'],item['quantity'])
            assert not item['name'].startswith('Eis')
    shaken={r['name'] for r in recipes if r['technique']=='Shaken'}
    assert shaken=={'Basil Smash','Whiskey Sour'}
    for recipe in recipes:
        if recipe['technique']=='Shaken':
            steps=' '.join(recipe['steps']).lower()
            assert 'shaker' in steps and 'shake' in steps and 'strain' in steps
    assert next(r for r in recipes if r['name']=='Bitter Giuseppe (Giusti)')['technique']=='Stirred'


def test_brand_labels_derive_from_live_taxonomy(client):
    admin_setup(client)
    con=db.connect()
    try:
        r=con.execute("SELECT * FROM recipes WHERE title='Classic Negroni'").fetchone()
        cats=db.ingredient_category_maps(con)
        card=db.recipe_card(con,r,set(),set(),cats)
        assert next(i for i in card['items'] if i['name']=='Tanqueray London Dry Gin')['display_name']=='Tanqueray London Dry Gin (London Dry Gin)'
        r=con.execute("SELECT * FROM recipes WHERE title='Chocolate Negroni'").fetchone()
        card=db.recipe_card(con,r,set(),set(),cats)
        elephant=next(i for i in card['items'] if i['name']=='Elephant Gin Orange & Cocoa')
        assert elephant['display_name']=='Elephant Gin Orange & Cocoa (London Dry Gin, Citrus Gin)'
        assert next(i for i in card['items'] if i['name']=='Ice')['display_name']=='Ice'
        # Updating the taxonomy changes rendered names, not stored ingredient text.
        gin_id=con.execute("SELECT id FROM categories WHERE name='Gin'").fetchone()['id']
        with con:
            con.execute("DELETE FROM ingredient_categories WHERE ingredient_id=?",(elephant['ingredient_id'],))
            con.execute("INSERT INTO ingredient_categories(ingredient_id,category_id) VALUES (?,?)",(elephant['ingredient_id'],gin_id))
        card=db.recipe_card(con,r,set(),set(),db.ingredient_category_maps(con))
        assert next(i for i in card['items'] if i['name']=='Elephant Gin Orange & Cocoa')['display_name']=='Elephant Gin Orange & Cocoa (Gin)'
    finally: con.close()


def test_new_brand_shows_newly_created_category_on_recipe(client):
    admin_setup(client)
    client.get('/recipes/new')
    con=db.connect()
    parent=con.execute("SELECT id FROM categories WHERE name='Scotch Whisky'").fetchone()['id']
    con.close()
    payload={'title':'Singleton Highball','technique':'Built','glass':'Highball','notes':'A refreshing drink with Singleton 12',
             'new_categories':[{'temp_id':'new:1','name':'Single Malt Scotch Whisky','parent_id':parent}],
             'steps':['Add Singleton 12 and ice to a glass.','Top with chilled soda.'],
             'items':[{'name':'Singleton 12','quantity':'4 cl','kind':'ingredient','is_brand':True,'categories':['new:1']},
                      {'name':'Soda Water','quantity':'10 cl','kind':'ingredient','is_brand':False,'categories':[]}]}
    response=client.post('/recipes/new',json=payload,headers={'X-CSRF-Token':csrf(client)})
    assert response.status_code==201,response.data
    recipe=client.get('/recipes/'+str(response.json['id']))
    assert recipe.status_code==200
    assert recipe.data.count(b'Singleton 12 (Single Malt Scotch Whisky)') >= 3
    assert b'Soda Water (Soda water)' not in recipe.data


def test_user_facing_pages_do_not_expose_spreadsheet_or_sources(client):
    admin_setup(client)
    pages=['/','/pantry','/settings','/settings/recipes','/recipes/1','/recipes/2','/recipes/3','/recipes/20']
    for path in pages:
        response=client.get(path)
        assert response.status_code==200,(path,response.status_code)
        html=response.get_data(as_text=True).lower()
        for phrase in ['spreadsheet','workbook','imported from','research reference','view the researched source']:
            assert phrase not in html,(path,phrase)
    page=client.get('/recipes/2').get_data(as_text=True)
    assert 'Tanqueray London Dry Gin (London Dry Gin)' in page


def test_editorial_migration_preserves_customized_fields(client):
    con=db.connect()
    try:
        source=json.loads((ROOT/'seed'/'english_v2_snapshot.json').read_text(encoding='utf-8'))
        old=next(r for r in source if r['name']=='Basil Smash')
        mojito=next(r for r in source if r['name']=='Mojito')
        with con:
            con.execute("DELETE FROM settings WHERE key='recipe_english_copy_v3'")
            con.execute("UPDATE recipes SET notes=?,steps_json=? WHERE title=?",(old['method_note'],json.dumps(old['steps']),old['name']))
            con.execute("UPDATE recipe_items SET quantity='1 Zweig' WHERE id=(SELECT ri.id FROM recipe_items ri JOIN recipes r ON r.id=ri.recipe_id JOIN ingredients i ON i.id=ri.ingredient_id WHERE r.title='Mojito' AND i.name='Peppermint')")
            con.execute("UPDATE recipes SET notes='My private custom story' WHERE title='Mojito'")
            con.execute("UPDATE recipes SET notes='My private custom story' WHERE title='Amaretti'")
            con.execute("UPDATE recipes SET steps_json='[\"My own recipe instructions\"]' WHERE title='Amaretti'")
        from migrations import apply_recipe_polish_v3
        apply_recipe_polish_v3(con)
        row=con.execute("SELECT notes,steps_json FROM recipes WHERE title='Basil Smash'").fetchone()
        assert 'spreadsheet' not in row['notes'].lower()
        assert 'cocktail shaker' in row['steps_json']
        assert con.execute("SELECT quantity FROM recipe_items ri JOIN recipes r ON ri.recipe_id=r.id JOIN ingredients i ON ri.ingredient_id=i.id WHERE r.title='Mojito' AND i.name='Peppermint'").fetchone()[0]=='1 sprig'
        assert con.execute("SELECT notes FROM recipes WHERE title='Mojito'").fetchone()[0]=='My private custom story'
        row=con.execute("SELECT notes,steps_json FROM recipes WHERE title='Amaretti'").fetchone()
        assert row['notes']=='My private custom story'
        assert json.loads(row['steps_json'])==['My own recipe instructions']
        apply_recipe_polish_v3(con)
        assert con.execute("SELECT COUNT(*) FROM settings WHERE key='recipe_english_copy_v3'").fetchone()[0]==1
    finally: con.close()


def test_logo_assets_and_pwa_cache_are_updated():
    logo=(ROOT/'static/icon.svg').read_text(encoding='utf-8')
    dark=(ROOT/'static/icon-dark.svg').read_text(encoding='utf-8')
    assert 'Cocktail Cabinet' in logo and 'Cocktail Cabinet' in dark
    from PIL import Image
    with Image.open(ROOT/'static/app-icon.png') as icon:
        assert icon.size==(192,192)
    with Image.open(ROOT/'static/app-icon-512.png') as icon:
        assert icon.size==(512,512)
    assert 'cabinet-assets-v4' in (ROOT/'static/sw.js').read_text(encoding='utf-8')
