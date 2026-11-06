"""Integration checks. CI runs with Flask, Werkzeug and Pillow installed."""
import os
import tempfile
from pathlib import Path
os.environ.setdefault('SECRET_KEY','test_secret_'*6)
os.environ.setdefault('SETUP_TOKEN','first_setup_secret')
os.environ.setdefault('TESTING','1')
os.environ.setdefault('COOKIE_SECURE','false')
os.environ.setdefault('TRUST_PROXY','false')
os.environ.setdefault('DATA_DIR',tempfile.mkdtemp(prefix='cabinet-tests-'))

import pytest
import app as module
import db

@pytest.fixture
def client(tmp_path,monkeypatch):
    monkeypatch.setattr(db,'DATA_DIR',tmp_path)
    monkeypatch.setattr(db,'DB_PATH',tmp_path/'cabinet.sqlite3')
    monkeypatch.setattr(module,'DATA_DIR',tmp_path)
    db.ensure_db()
    module.app.config.update(TESTING=True,SESSION_COOKIE_SECURE=False)
    with module.app.test_client() as client:
        yield client

def csrf(client):
    with client.session_transaction() as s: return s['_csrf']

def admin_setup(client):
    client.get('/setup')
    response=client.post('/setup',data={'_csrf':csrf(client),'setup_token':'first_setup_secret','username':'owner',
                                       'email':'owner@example.com','password':'very strong password'})
    assert response.status_code==302
    return response

def logout(client):client.post('/logout',data={'_csrf':csrf(client)})

def test_import_and_taxonomy(client):
    con=db.connect()
    assert con.execute('SELECT COUNT(*) FROM recipes').fetchone()[0]==22
    assert con.execute('SELECT COUNT(*) FROM recipe_items').fetchone()[0]==105
    elephant=con.execute("SELECT id FROM ingredients WHERE name='Elephant Gin Orange & Cocoa'").fetchone()['id']
    cats=con.execute('SELECT c.name FROM categories c JOIN ingredient_categories ic ON ic.category_id=c.id WHERE ic.ingredient_id=?',(elephant,)).fetchall()
    assert {'London Dry Gin','Citrus Gin'}=={r['name'] for r in cats}
    assert all(row['source_url'] for row in con.execute('SELECT source_url FROM recipes'))
    con.close()

def test_private_gating_and_first_admin(client):
    assert client.get('/').status_code==302
    assert client.get('/admin').status_code==302
    assert client.get('/recipes/1').status_code==302
    assert client.get('/pantry').status_code==302
    assert client.get('/login').status_code==302  # setup first
    admin_setup(client)
    assert client.get('/').status_code==200
    assert client.get('/setup').status_code==302
    logout(client)
    assert client.get('/recipes/1').status_code==302

def test_pantry_matching_and_specificity(client):
    admin_setup(client)
    con=db.connect()
    ids={r['name']:r['id'] for r in con.execute('SELECT * FROM categories')}
    ing={r['name']:r['id'] for r in con.execute('SELECT * FROM ingredients')}
    uid=con.execute("SELECT id FROM users WHERE username='owner'").fetchone()['id']
    recipe=con.execute("SELECT * FROM recipes WHERE title='Negroni Schoggi'").fetchone()
    normal=con.execute("SELECT * FROM recipes WHERE title='Negroni normal'").fetchone()
    maps=db.ingredient_category_maps(con)
    with con:
        for cat in ('Gin','Italian red bitter','Sweet red vermouth'):
            con.execute('INSERT INTO pantry(user_id,category_id) VALUES (?,?)',(uid,ids[cat]))
    c,i=module.pantry_ids(con,uid)
    assert db.recipe_card(con,recipe,c,i,maps)['can_make']
    assert db.recipe_card(con,normal,c,i,maps)['can_make']
    with con:
        con.execute('DELETE FROM pantry WHERE user_id=?',(uid,))
        con.execute('INSERT INTO pantry(user_id,category_id) VALUES(?,?)',(uid,ids['London Dry Gin']))
    gin_item=con.execute("SELECT ri.*,ing.is_brand FROM recipe_items ri JOIN ingredients ing ON ri.ingredient_id=ing.id WHERE ing.name='Seventh Sense Gin' LIMIT 1").fetchone()
    c,i=module.pantry_ids(con,uid)
    assert not db.ingredient_matches(gin_item,c,i,maps[1],maps[2],maps[3])
    generic_item=con.execute("SELECT ri.*,ing.is_brand FROM recipe_items ri JOIN ingredients ing ON ri.ingredient_id=ing.id WHERE ing.name='Gin' LIMIT 1").fetchone()
    assert db.ingredient_matches(generic_item,c,i,maps[1],maps[2],maps[3])
    with con:
        con.execute('DELETE FROM pantry WHERE user_id=?',(uid,))
        con.execute('INSERT INTO pantry(user_id,ingredient_id) VALUES(?,?)',(uid,ing["Hendrick's Gin"]))
    c,i=module.pantry_ids(con,uid)
    tanqueray_item=con.execute("SELECT ri.*,ing.is_brand FROM recipe_items ri JOIN ingredients ing ON ri.ingredient_id=ing.id WHERE ing.name='Tanqueray London Dry Gin' LIMIT 1").fetchone()
    assert not db.ingredient_matches(tanqueray_item,c,i,maps[1],maps[2],maps[3])
    assert db.ingredient_matches(generic_item,c,i,maps[1],maps[2],maps[3])
    con.close()

def test_one_time_invitation_and_member_permissions(client):
    admin_setup(client)
    response=client.post('/admin/invitations',data={'_csrf':csrf(client),'email':'guest@example.com'})
    assert response.status_code==200
    import re, html
    link=html.unescape(re.search(rb'id="invite-link"[^>]*value="([^"]+)"',response.data).group(1).decode())
    token=link.rsplit('/',1)[1]
    logout(client)
    assert client.get(f'/invite/{token}').status_code==200
    response=client.post(f'/invite/{token}',data={'_csrf':csrf(client),'username':'guest','password':'a great long passphrase'})
    assert response.status_code==302
    assert client.get('/admin').status_code==403
    logout(client)
    assert client.get(f'/invite/{token}').status_code==410

def test_new_recipe_categories_required_and_csrf(client):
    admin_setup(client)
    payload={'title':'Weekend Gimlet','technique':'Shaken','glass':'Coupe','notes':'',
             'steps':['Shake with ice','Strain into a chilled coupe'],
             'items':[{'name':'Brand New Citrus Gin','quantity':'6 cl','categories':[],'is_brand':True,'kind':'ingredient'},
                      {'name':'Zitronensaft','quantity':'2 cl','categories':[],'kind':'ingredient'}]}
    assert client.post('/recipes/new',json=payload).status_code==400
    assert client.post('/recipes/new',json=payload,headers={'X-CSRF-Token':csrf(client)}).status_code==400
    con=db.connect()
    gin_id=con.execute("SELECT id FROM categories WHERE name='Gin'").fetchone()['id']
    citrus_id=con.execute("SELECT id FROM categories WHERE name='Citrus Gin'").fetchone()['id']
    payload['items'][0]['categories']=[gin_id,citrus_id]
    response=client.post('/recipes/new',json=payload,headers={'X-CSRF-Token':csrf(client)})
    assert response.status_code==201,response.data
    assert client.get('/recipes/'+str(response.json['id'])).status_code==200
    con.close()
