"""Personal inventory API and MCP integration; all database writes are isolated."""
from __future__ import annotations

import os
import re
import tempfile

os.environ.setdefault('SECRET_KEY','test_secret_'*6)
os.environ.setdefault('SETUP_TOKEN','first_setup_secret')
os.environ.setdefault('TESTING','1')
os.environ.setdefault('COOKIE_SECURE','false')
os.environ.setdefault('TRUST_PROXY','false')
os.environ.setdefault('DATA_DIR',tempfile.mkdtemp(prefix='cabinet-shelf-tests-'))

import pytest
from werkzeug.security import generate_password_hash
import app as module
import db
from api_schema import shelf_revision


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db,'DATA_DIR',tmp_path)
    monkeypatch.setattr(db,'DB_PATH',tmp_path/'cabinet.sqlite3')
    monkeypatch.setattr(module,'DATA_DIR',tmp_path)
    db.ensure_db()
    module.app.config.update(TESTING=True,SESSION_COOKIE_SECURE=False)
    with module.app.test_client() as c:
        yield c


def csrf(c):
    with c.session_transaction() as sess:
        return sess['_csrf']


def setup(c):
    c.get('/setup')
    assert c.post('/setup',data={
        '_csrf':csrf(c),'setup_token':'first_setup_secret',
        'username':'owner','email':'owner@example.com','password':'very strong password'
    }).status_code==302
    c.get('/account')


def token(c,scope='write'):
    c.get('/settings/api')
    r=c.post('/settings/api/tokens',data={
        '_csrf':csrf(c),'name':'Shelf integration','scope':scope,'expires':'90'})
    assert r.status_code==200,r.data
    found=re.search(rb'id="issued-api-token"[^>]+value="(ccb_[^"]+)"',r.data)
    assert found
    return found.group(1).decode()


def h(token):
    return {'Authorization':'Bearer '+token}


def items(c,key):
    r=c.get('/api/v1/shelf',headers=h(key))
    assert r.status_code==200,r.data
    return r.json['data']


def rpc(c,key,name,args=None):
    r=c.post('/mcp',headers={
        'Authorization':'Bearer '+key,'Accept':'application/json, text/event-stream',
        'MCP-Protocol-Version':'2025-11-25'
    },json={'jsonrpc':'2.0','id':4,'method':'tools/call',
            'params':{'name':name,'arguments':args or {}}})
    assert r.status_code==200,r.data
    return r.json['result']


def choose(state):
    product=state['ingredients'][0]
    category=next(c for c in state['categories'] if c['id'] in product['category_ids'])
    return product['id'],category['id']


def test_read_only_token_and_own_shelf_catalog(client):
    setup(client)
    read=token(client,'read')
    assert client.get('/api/v1/shelf').status_code==401  # browser cookie is not API auth
    initial=items(client,read)
    assert initial['ingredients'] and initial['categories']
    assert initial['owned_ingredient_ids']==[]
    assert initial['owned_category_ids']==[]
    assert all('owned' in entry for entry in initial['ingredients'])
    assert all(not c['name'].casefold()=='ice' for c in initial['categories'])
    product,category=choose(initial)
    assert client.post('/api/v1/shelf/items',headers=h(read),
                       json={'ingredient_id':product,'owned':True}).status_code==403
    assert client.patch('/api/v1/shelf/items',headers=h(read),json={
        'changes':[{'category_id':category,'owned':True}]}).status_code==403
    assert items(client,read)['revision']==initial['revision']


def test_single_and_batch_changes_preserve_other_shelf_items_and_idempotence(client):
    setup(client)
    write=token(client)
    state=items(client,write)
    product,category=choose(state)
    start_rev=state['revision']
    result=client.post('/api/v1/shelf/items',headers=h(write),
                      json={'ingredient_id':product,'owned':True})
    assert result.status_code==200,result.json
    changed=result.json['data']
    assert product in changed['owned_ingredient_ids']
    assert changed['revision']>start_rev
    second=client.post('/api/v1/shelf/items',headers=h(write),
                       json={'ingredient_id':product,'owned':True})
    assert second.status_code==200
    assert second.json['data']['revision']==changed['revision']  # no-op not a new change
    bulk=client.patch('/api/v1/shelf/items',headers=h(write),
                      json={'changes':[{'category_id':category,'owned':True},
                                       {'ingredient_id':product,'owned':False}]})
    assert bulk.status_code==200,bulk.json
    state=bulk.json['data']
    assert category in state['owned_category_ids']
    assert product not in state['owned_ingredient_ids']
    assert any(c['id']==category and c['owned'] for c in state['categories'])
    assert any(i['id']==product and not i['owned'] for i in state['ingredients'])
    add=client.post('/api/v1/shelf/items',headers=h(write),
                    json={'ingredient_id':product,'owned':True})
    assert add.status_code==200
    assert category in add.json['data']['owned_category_ids']  # untouched by addition
    assert client.get('/api/v1/changes',headers=h(write)).json['shelf_revision']==add.json['data']['revision']
    assert client.get('/live/revision').json['shelf_revision']==add.json['data']['revision']
    assert b'name="shelf-revision"' in client.get('/pantry').data


def test_invalid_shelf_changes_are_rejected_atomically(client):
    setup(client)
    key=token(client)
    state=items(client,key)
    product,category=choose(state)
    for bad in [
        {'ingredient_id':product,'owned':1},
        {'ingredient_id':True,'owned':True},
        {'ingredient_id':product,'category_id':category,'owned':True},
        {'ingredient_id':-1,'owned':True},
        {'ingredient_id':999999,'owned':True},
        {'category_id':999999,'owned':True},
        {'ingredient_id':product,'owned':True,'user_id':2},
        {'owned':True},
    ]:
        assert client.post('/api/v1/shelf/items',headers=h(key),json=bad).status_code==400
    assert client.patch('/api/v1/shelf/items',headers=h(key),json={
        'changes':[{'ingredient_id':product,'owned':True},
                   {'ingredient_id':999999,'owned':True}]}).status_code==400
    assert client.patch('/api/v1/shelf/items',headers=h(key),json={
        'changes':[{'ingredient_id':product,'owned':True},
                   {'ingredient_id':product,'owned':False}]}).status_code==400
    assert client.patch('/api/v1/shelf/items',headers=h(key),json={'changes':[]}).status_code==400
    assert items(client,key)['owned_ingredient_ids']==[]
    assert items(client,key)['revision']==state['revision']
    # Root categories and the ignored Ice category are not user-selectable.
    with db.connect() as con:
        root=con.execute('SELECT id FROM categories WHERE parent_id IS NULL LIMIT 1').fetchone()
        ice=con.execute("SELECT id FROM categories WHERE name='Ice'").fetchone()
    assert client.post('/api/v1/shelf/items',headers=h(key),json={
        'category_id':root['id'],'owned':True}).status_code==400
    if ice:
        assert client.post('/api/v1/shelf/items',headers=h(key),json={
            'category_id':ice['id'],'owned':True}).status_code==400


def test_user_isolation_admin_cannot_change_other_users_shelf(client):
    setup(client)
    owner=token(client)
    state=items(client,owner)
    product,_=choose(state)
    assert client.post('/api/v1/shelf/items',headers=h(owner),
                       json={'ingredient_id':product,'owned':True}).status_code==200
    owner_revision=items(client,owner)['revision']
    with db.connect() as con:
        con.execute('INSERT INTO users(username,email,password_hash,role) VALUES(?,?,?,?)',
                    ('guest','guest@example.com',generate_password_hash('strong password 123'),'member'))
    client.post('/logout',data={'_csrf':csrf(client)})
    client.get('/login')
    assert client.post('/login',data={
        '_csrf':csrf(client),'identity':'guest','password':'strong password 123'
    }).status_code==302
    guest=token(client)
    assert product not in items(client,guest)['owned_ingredient_ids']
    assert items(client,guest)['revision']==0
    assert client.post('/api/v1/shelf/items',headers=h(guest),
                       json={'ingredient_id':product,'owned':True}).status_code==200
    assert items(client,guest)['revision']>0
    assert client.delete('/api/v1/shelf/items',headers=h(guest)).status_code==405
    assert items(client,owner)['revision']==owner_revision
    assert product in items(client,owner)['owned_ingredient_ids']
    assert product in items(client,guest)['owned_ingredient_ids']
    assert items(client,owner)['user_id'] != items(client,guest)['user_id']


def test_web_form_updates_shelf_revision_and_survives_migration(client):
    setup(client)
    key=token(client)
    state=items(client,key)
    product,category=choose(state)
    original=client.get('/live/revision').json['shelf_revision']
    saved=client.post('/pantry',data={'_csrf':csrf(client),
                                    'ingredient':str(product),'category':str(category)})
    assert saved.status_code==302
    after_web=items(client,key)
    assert product in after_web['owned_ingredient_ids']
    assert category in after_web['owned_category_ids']
    assert after_web['revision']>original
    with db.connect() as con:
        assert shelf_revision(con,1)==after_web['revision']
    db.ensure_db()  # schema is additive, no reset on Docker restart
    assert items(client,key)['owned_ingredient_ids']==after_web['owned_ingredient_ids']
    assert items(client,key)['revision']==after_web['revision']


def test_mcp_shelf_tools_use_same_scopes_and_personal_data(client):
    setup(client)
    key=token(client,'write')
    read=token(client,'read')
    state=rpc(client,read,'get_my_shelf')
    assert not state['isError'],state
    product,category=choose(state['structuredContent']['data'])
    refused=rpc(client,read,'set_shelf_item_owned',{'ingredient_id':product,'owned':True})
    assert refused['isError']
    updated=rpc(client,key,'set_shelf_item_owned',{'ingredient_id':product,'owned':True})
    assert not updated['isError'],updated
    assert product in updated['structuredContent']['data']['owned_ingredient_ids']
    bulk=rpc(client,key,'update_my_shelf',{'changes':[
        {'ingredient_id':product,'owned':False},
        {'category_id':category,'owned':True}
    ]})
    assert not bulk['isError'],bulk
    assert product not in bulk['structuredContent']['data']['owned_ingredient_ids']
    assert category in bulk['structuredContent']['data']['owned_category_ids']
    tools=client.post('/mcp',headers={'Authorization':'Bearer '+key},json={
        'jsonrpc':'2.0','id':1,'method':'tools/list'}).json['result']['tools']
    names={item['name'] for item in tools}
    assert {'get_my_shelf','set_shelf_item_owned','update_my_shelf'} <= names


def test_openapi_shelf_contract_is_private_and_live_js_tracks_user_revision(client):
    setup(client)
    key=token(client,'read')
    assert client.get('/api/v1/openapi.json').status_code==401
    spec=client.get('/api/v1/openapi.json',headers=h(key)).json
    assert '/shelf' in spec['paths']
    assert {'post','patch'} <= set(spec['paths']['/shelf/items'])
    assert 'shelf_revision' in client.get('/static/live.js').data.decode()
