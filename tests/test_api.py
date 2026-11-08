"""External REST API, scoped per-user credentials and live PWA synchronization."""
from __future__ import annotations

import io
import os
import re
import tempfile
from pathlib import Path

os.environ.setdefault('SECRET_KEY', 'test_secret_' * 6)
os.environ.setdefault('SETUP_TOKEN', 'first_setup_secret')
os.environ.setdefault('TESTING', '1')
os.environ.setdefault('COOKIE_SECURE', 'false')
os.environ.setdefault('TRUST_PROXY', 'false')
os.environ.setdefault('DATA_DIR', tempfile.mkdtemp(prefix='cabinet-api-tests-'))

import pytest
from PIL import Image
from werkzeug.security import generate_password_hash

import app as module
import db
from api_schema import collection_revision


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DATA_DIR', tmp_path)
    monkeypatch.setattr(db, 'DB_PATH', tmp_path/'cabinet.sqlite3')
    monkeypatch.setattr(module, 'DATA_DIR', tmp_path)
    db.ensure_db()
    module.app.config.update(TESTING=True, SESSION_COOKIE_SECURE=False)
    with module.app.test_client() as client:
        yield client


def csrf(client):
    with client.session_transaction() as sess:
        return sess['_csrf']


def setup(client):
    client.get('/setup')
    response = client.post('/setup', data={
        '_csrf': csrf(client), 'setup_token': 'first_setup_secret',
        'username': 'owner', 'email': 'owner@example.com',
        'password': 'very strong password'})
    assert response.status_code == 302
    client.get('/account')


def create_token(client, name='test integration', scope='read', expires='90'):
    client.get('/settings/api')
    response = client.post('/settings/api/tokens', data={
        '_csrf': csrf(client), 'name': name, 'scope': scope, 'expires': expires})
    assert response.status_code == 200, response.data[:800]
    match = re.search(rb'id="issued-api-token"[^>]+value="(ccb_[^"]+)"', response.data)
    assert match, response.data[-1000:]
    return match.group(1).decode()


def headers(token):
    return {'Authorization': 'Bearer '+token}


def sample_recipe(name='Gimlet for API'):
    return {'title':name,'technique':'Shaken','glass':'Coupe','notes':'A measured gin sour.',
            'steps':['Combine ingredients in a shaker with ice.','Shake and strain into a coupe.'],
            'items':[{'name':'Gin','quantity':'6 cl','kind':'ingredient','optional':False,'is_brand':False},
                     {'name':'Lemon juice','quantity':'2 cl','kind':'ingredient','optional':False,'is_brand':False}]}


def photo_bytes():
    b = io.BytesIO()
    Image.new('RGB', (100, 100), '#7c304b').save(b, 'PNG')
    b.seek(0)
    return b


def test_session_cookie_cannot_authenticate_api(client):
    setup(client)
    assert client.get('/api/v1/recipes').status_code == 401
    assert client.post('/api/v1/recipes', json=sample_recipe()).status_code == 401
    assert client.get('/api/v1/openapi.json').status_code == 401
    assert client.get('/static/openapi.json').status_code == 404  # docs are not public static assets
    assert client.get('/api/v1/recipes/1/image').status_code == 401
    assert client.get('/live/revision').status_code == 200


def test_scoped_tokens_read_ingredients_and_download_images(client):
    setup(client)
    token = create_token(client)
    h = headers(token)
    collection = client.get('/api/v1/recipes?per_page=7&page=1',headers=h)
    assert collection.status_code == 200
    assert collection.json['pagination']['total'] == 22
    assert len(collection.json['data']) == 7
    assert collection.json['pagination']['has_more']
    assert collection.json['data'][0]['ingredients'][0]['display_name']
    assert client.get('/api/v1/recipes?per_page=99&page=1',headers=h).status_code == 200
    assert client.get('/api/v1/recipes?per_page=500',headers=h).status_code == 400
    assert client.get('/api/v1/ingredients',headers=h).status_code == 200
    assert client.get('/api/v1/categories',headers=h).status_code == 200
    assert client.get('/api/v1/me',headers=h).json['user']['username'] == 'owner'
    assert client.get('/api/v1/changes',headers=h).status_code == 200
    specification = client.get('/api/v1/openapi.json',headers=h)
    assert specification.status_code == 200
    assert specification.json['paths']['/recipes/{recipe_id}/image']['post']
    image = client.get('/api/v1/recipes/1/image?download=1', headers=h)
    assert image.status_code == 200 and image.mimetype == 'image/webp'
    assert 'attachment' in image.headers['Content-Disposition']
    assert client.post('/api/v1/recipes',json=sample_recipe(),headers=h).status_code == 403
    assert client.post('/api/v1/categories',json={'name':'Secret'} ,headers=h).status_code == 403


def test_write_token_crud_images_and_live_revision(client, tmp_path):
    setup(client)
    token = create_token(client,scope='write')
    h = headers(token)
    initial = client.get('/live/revision').json['revision']
    created = client.post('/api/v1/recipes',headers=h,json=sample_recipe())
    assert created.status_code == 201, created.json
    recipe_id = created.json['data']['id']
    assert created.json['data']['can_edit']
    assert client.get('/live/revision').json['revision'] > initial
    assert client.get(f'/recipes/{recipe_id}').status_code == 200
    patch = client.patch(f'/api/v1/recipes/{recipe_id}',headers=h,json={'title':'A new API name'})
    assert patch.status_code == 200,patch.json
    assert patch.json['data']['title'] == 'A new API name'
    assert len(patch.json['data']['ingredients']) == 2
    assert b'A new API name' in client.get(f'/recipes/{recipe_id}').data
    revision_before_image = client.get('/live/revision').json['revision']
    uploaded = client.post(f'/api/v1/recipes/{recipe_id}/image',headers=h,
                           data={'image':(photo_bytes(),'glass.png')},content_type='multipart/form-data')
    assert uploaded.status_code == 201,uploaded.json
    assert client.get('/live/revision').json['revision'] > revision_before_image
    received = client.get(f'/api/v1/recipes/{recipe_id}/image',headers=h)
    assert received.status_code == 200 and received.data.startswith(b'RIFF')
    assert client.get(f'/api/v1/recipes/{recipe_id}/image').status_code == 401
    assert client.delete(f'/api/v1/recipes/{recipe_id}/image',headers=h).status_code == 204
    assert client.get(f'/api/v1/recipes/{recipe_id}/image',headers=h).status_code == 404
    assert client.delete(f'/api/v1/recipes/{recipe_id}',headers=h).status_code == 204
    assert client.get(f'/api/v1/recipes/{recipe_id}',headers=h).status_code == 404


def test_per_user_ownership_and_account_deactivation(client):
    setup(client)
    admin_token = create_token(client,scope='write')
    con = db.connect()
    with con:
        member_id = con.execute('''INSERT INTO users(username,email,password_hash,role)
                                   VALUES(?,?,?,?)''',('guest','guest@example.com',generate_password_hash('twelve char password'),'member')).lastrowid
    con.close()
    client.post('/logout',data={'_csrf':csrf(client)})
    client.get('/login')
    login = client.post('/login',data={'_csrf':csrf(client),'identity':'guest','password':'twelve char password'})
    assert login.status_code == 302
    member_token = create_token(client,scope='write')
    mh = headers(member_token)
    assert client.get('/api/v1/recipes/1',headers=mh).status_code == 200
    assert client.patch('/api/v1/recipes/1',headers=mh,json={'notes':'Unauthorized'}).status_code == 403
    assert client.delete('/api/v1/recipes/1',headers=mh).status_code == 403
    assert client.post('/api/v1/recipes/1/image',headers=mh,
                       data={'image':(photo_bytes(),'x.png')},content_type='multipart/form-data').status_code == 403
    new = client.post('/api/v1/recipes',headers=mh,json=sample_recipe('Member recipe'))
    assert new.status_code == 201,new.json
    rid = new.json['data']['id']
    assert client.patch(f'/api/v1/recipes/{rid}',headers=mh,json={'notes':'Modified'}).status_code == 200
    assert client.get('/api/v1/me',headers=mh).json['user']['id'] == member_id
    assert client.post('/api/v1/categories',headers=mh,json={'name':'Odd spirits','parent_id':1}).status_code == 403
    # An administrator can update a member-owned recipe.
    assert client.patch(f'/api/v1/recipes/{rid}',headers=headers(admin_token),json={'notes':'Admin edit'}).status_code == 200
    with db.connect() as con:
        con.execute('UPDATE users SET active=0 WHERE id=?',(member_id,))
    assert client.get('/api/v1/recipes',headers=mh).status_code == 401


def test_new_classification_and_category_created_atomically_by_api(client):
    setup(client)
    token = create_token(client,scope='write')
    con = db.connect()
    gin_id = con.execute("SELECT id FROM categories WHERE name='Gin'").fetchone()['id']
    con.close()
    bad = sample_recipe('Broken Gin')
    bad['new_categories']=[{'temp_id':'new:1','name':'New Botanical Gin','parent_id':gin_id}]
    bad['items'][0] = {'name':'New Test Gin Bottle','quantity':'4 cl','kind':'ingredient',
                        'is_brand':True,'categories':['new:1']}
    bad['steps'] = []
    failed = client.post('/api/v1/recipes',headers=headers(token),json=bad)
    assert failed.status_code == 400
    with db.connect() as con:
        assert not con.execute("SELECT id FROM categories WHERE name='New Botanical Gin'").fetchone()
    bad['steps']=['Shake with ice','Strain']
    success = client.post('/api/v1/recipes',headers=headers(token),json=bad)
    assert success.status_code == 201,success.json
    display = success.json['data']['ingredients'][0]['display_name']
    assert 'New Botanical Gin' in display
    con = db.connect()
    cat = con.execute("SELECT * FROM categories WHERE name='New Botanical Gin'").fetchone()
    assert cat['parent_id']==gin_id
    con.close()


def test_token_revoke_expiration_and_schema_persistence(client):
    setup(client)
    token = create_token(client,scope='read')
    assert client.get('/api/v1/recipes',headers=headers(token)).status_code == 200
    con = db.connect()
    token_id = con.execute('SELECT id FROM api_tokens WHERE user_id=1').fetchone()['id']
    con.close()
    db.ensure_db()  # safe to rerun on the same Synology volume
    assert client.get('/api/v1/recipes',headers=headers(token)).status_code == 200
    client.get('/settings/api')
    revoked=client.post(f'/settings/api/tokens/{token_id}/revoke',data={'_csrf':csrf(client)})
    assert revoked.status_code == 302
    assert client.get('/api/v1/recipes',headers=headers(token)).status_code == 401
    expired = create_token(client,scope='read')
    with db.connect() as con:
        con.execute("UPDATE api_tokens SET expires_at='2000-01-01T00:00:00+00:00' WHERE token_hash=?",
                    (__import__('hashlib').sha256(expired.encode()).hexdigest(),))
    assert client.get('/api/v1/recipes',headers=headers(expired)).status_code == 401


def test_api_browser_change_counter_reacts_to_web_and_api_edits(client):
    setup(client)
    token = create_token(client,scope='write')
    baseline = client.get('/live/revision').json['revision']
    web = client.get('/recipes/new')
    assert web.status_code == 200
    web_update = client.post('/recipes/new',headers={'X-CSRF-Token':csrf(client)},json=sample_recipe())
    # Web editor uses same write service and increments the same database revision.
    assert web_update.status_code == 201,web_update.json
    after_web = client.get('/live/revision').json['revision']
    assert after_web > baseline
    api_update = client.patch(f"/api/v1/recipes/{web_update.json['id']}",
                              headers=headers(token),json={'notes':'Visible via live refresh'})
    assert api_update.status_code == 200
    after_api = client.get('/api/v1/changes',headers=headers(token)).json['revision']
    assert after_api > after_web
    assert b'collection-revision' in client.get('/').data
    assert client.get('/static/live.js').status_code == 200
