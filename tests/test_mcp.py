"""MCP integration: bearer-per-user auth, real API actions, image IO, OAuth PKCE.

No external server or Synology access required. CI uses Flask's test client and
an isolated instance volume, so user recipes remain untouched.
"""
from __future__ import annotations

import base64
import hashlib
import io
import os
import re
import tempfile
from pathlib import Path

os.environ.setdefault('SECRET_KEY','test_secret_'*6)
os.environ.setdefault('SETUP_TOKEN','first_setup_secret')
os.environ.setdefault('TESTING','1')
os.environ.setdefault('COOKIE_SECURE','false')
os.environ.setdefault('TRUST_PROXY','false')
os.environ.setdefault('DATA_DIR',tempfile.mkdtemp(prefix='cabinet-mcp-tests-'))

import pytest
from PIL import Image
from werkzeug.security import generate_password_hash
import db
import app as module


@pytest.fixture
def client(tmp_path,monkeypatch):
    monkeypatch.setattr(db,'DATA_DIR',tmp_path)
    monkeypatch.setattr(db,'DB_PATH',tmp_path/'cabinet.sqlite3')
    monkeypatch.setattr(module,'DATA_DIR',tmp_path)
    db.ensure_db()
    module.app.config.update(TESTING=True,SESSION_COOKIE_SECURE=False)
    with module.app.test_client() as c:
        yield c


def csrf(c):
    with c.session_transaction() as session:
        return session['_csrf']


def setup(c):
    c.get('/setup')
    response=c.post('/setup',data={'_csrf':csrf(c),'setup_token':'first_setup_secret',
                                   'username':'owner','email':'owner@example.com',
                                   'password':'very strong password'})
    assert response.status_code==302
    c.get('/account')


def token(c,scope='write'):
    c.get('/settings/api')
    r=c.post('/settings/api/tokens',data={'_csrf':csrf(c),'name':'MCP Integration',
                                         'scope':scope,'expires':'90'})
    assert r.status_code==200
    match=re.search(rb'id="issued-api-token"[^>]+value="(ccb_[^"]+)"',r.data)
    assert match, r.data[-1500:]
    return match.group(1).decode()


def rpc(c,key,method,params=None,rpc_id=1):
    return c.post('/mcp',headers={'Authorization':'Bearer '+key,
                                   'Accept':'application/json, text/event-stream',
                                   'MCP-Protocol-Version':'2025-11-25'},
                  json={'jsonrpc':'2.0','id':rpc_id,'method':method,'params':params or {}})


def tool(c,key,name,args=None):
    resp=rpc(c,key,'tools/call',{'name':name,'arguments':args or {}})
    assert resp.status_code==200,resp.data
    return resp.json['result']


def sample_recipe():
    return {'title':'MCP Gin Sour','technique':'Shaken','glass':'Coupe',
            'notes':'A gin sour managed by MCP',
            'steps':['Shake all ingredients with ice','Double strain into a coupe'],
            'items':[{'name':'Gin','quantity':'6 cl','kind':'ingredient','is_brand':False},
                     {'name':'Lemon juice','quantity':'2 cl','kind':'ingredient','is_brand':False}]}


def sample_image():
    buffer=io.BytesIO()
    Image.new('RGB',(48,48),'#763447').save(buffer,'PNG')
    return base64.b64encode(buffer.getvalue()).decode()


def test_mcp_private_bearer_and_discovery(client):
    setup(client)
    assert client.get('/mcp').status_code==405
    unauth=client.post('/mcp',json={'jsonrpc':'2.0','id':1,'method':'tools/list'})
    assert unauth.status_code==401
    assert 'resource_metadata=' in unauth.headers['WWW-Authenticate']
    assert client.get('/.well-known/oauth-protected-resource').json['resource'].endswith('/mcp')
    assert 'S256' in client.get('/.well-known/oauth-authorization-server').json['code_challenge_methods_supported']
    key=token(client,'read')
    response=rpc(client,key,'initialize',{'protocolVersion':'2025-06-18','clientInfo':{'name':'test','version':'1'},'capabilities':{}})
    assert response.json['result']['protocolVersion']=='2025-06-18'
    listed=rpc(client,key,'tools/list')
    names={tool['name'] for tool in listed.json['result']['tools']}
    assert {'list_recipes','get_recipe','create_recipe','upload_recipe_image','download_recipe_image','update_recipe'}<=names
    assert rpc(client,key,'tools/list').status_code==200
    assert client.post('/mcp',headers={'Authorization':'Bearer invalid'},json={}).status_code==401


def test_mcp_tools_reuse_permissions_and_live_database(client):
    setup(client)
    read=token(client,'read')
    write=token(client,'write')
    collection=tool(client,read,'list_recipes',{'per_page':100})
    assert collection['structuredContent']['pagination']['total']==22
    assert tool(client,read,'create_recipe',sample_recipe())['isError']
    start=client.get('/live/revision').json['revision']
    new=tool(client,write,'create_recipe',sample_recipe())
    assert not new['isError'],new
    rid=new['structuredContent']['data']['id']
    assert client.get('/live/revision').json['revision']>start
    assert b'MCP Gin Sour' in client.get(f'/recipes/{rid}').data
    revised=tool(client,write,'update_recipe',{'recipe_id':rid,'title':'MCP Improved Sour'})
    assert revised['structuredContent']['data']['title']=='MCP Improved Sour'
    assert tool(client,read,'get_recipe',{'recipe_id':rid})['structuredContent']['data']['title']=='MCP Improved Sour'
    photo=tool(client,write,'upload_recipe_image',{'recipe_id':rid,'filename':'sour.png','image_base64':sample_image()})
    assert not photo['isError'],photo
    image=tool(client,read,'download_recipe_image',{'recipe_id':rid})
    assert image['content'][0]['type']=='image'
    assert base64.b64decode(image['content'][0]['data']).startswith(b'RIFF')
    assert not tool(client,write,'remove_recipe_image',{'recipe_id':rid})['isError']
    assert tool(client,read,'download_recipe_image',{'recipe_id':rid})['isError']
    assert not tool(client,write,'delete_recipe',{'recipe_id':rid})['isError']
    assert client.get(f'/recipes/{rid}').status_code==404


def test_mcp_owner_permissions_and_api_token_revocation(client):
    setup(client)
    root_token=token(client,'write')
    with db.connect() as con:
        con.execute('INSERT INTO users(username,email,password_hash,role) VALUES (?,?,?,?)',
                    ('member','member@example.com',generate_password_hash('strong passphrase 123'),'member'))
    client.post('/logout',data={'_csrf':csrf(client)})
    client.get('/login')
    assert client.post('/login',data={'_csrf':csrf(client),'identity':'member','password':'strong passphrase 123'}).status_code==302
    member_token=token(client,'write')
    assert tool(client,member_token,'update_recipe',{'recipe_id':1,'notes':'Not mine'})['isError']
    own=tool(client,member_token,'create_recipe',sample_recipe())
    assert not own['isError']
    rid=own['structuredContent']['data']['id']
    assert not tool(client,root_token,'update_recipe',{'recipe_id':rid,'notes':'Admin can edit'})['isError']
    assert tool(client,member_token,'create_category',{'name':'Unauthorized'})['isError']
    with db.connect() as con:
        con.execute('UPDATE users SET active=0 WHERE username=?',('member',))
    assert client.post('/mcp',headers={'Authorization':'Bearer '+member_token},json={'jsonrpc':'2.0','id':3,'method':'tools/list'}).status_code==401


def test_oauth_dynamic_registration_pkce_consent_and_refresh(client):
    setup(client)
    # Only HTTPS ChatGPT callbacks may register; no arbitrary redirect URLs.
    denied=client.post('/oauth/register',json={'redirect_uris':['https://attacker.example/collect']})
    assert denied.status_code==400
    callback='https://chatgpt.com/connector/oauth/callback12345'
    registered=client.post('/oauth/register',json={'client_name':'Cocktail Cabinet','redirect_uris':[callback],
                                                  'token_endpoint_auth_method':'none'})
    assert registered.status_code==201,registered.data
    client_id=registered.json['client_id']
    verifier='a'*55
    challenge=base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
    args={'client_id':client_id,'redirect_uri':callback,'response_type':'code',
          'state':'xyz','code_challenge_method':'S256','code_challenge':challenge,
          'scope':'recipes:read recipes:write','resource':'http://localhost/mcp'}
    screen=client.get('/oauth/authorize',query_string=args)
    assert screen.status_code==200
    assert b'Allow connection' in screen.data
    approved=client.post('/oauth/authorize',data={'_csrf':csrf(client),**args,'decision':'allow'})
    assert approved.status_code==302,approved.data
    from urllib.parse import urlsplit,parse_qs
    query=parse_qs(urlsplit(approved.headers['Location']).query)
    assert query['state']==['xyz']
    authorization_code=query['code'][0]
    invalid=client.post('/oauth/token',data={'grant_type':'authorization_code','client_id':client_id,
                     'code':authorization_code,'code_verifier':'bad','redirect_uri':callback,
                     'resource':'http://localhost/mcp'})
    assert invalid.status_code==400
    result=client.post('/oauth/token',data={'grant_type':'authorization_code','client_id':client_id,
                    'code':authorization_code,'code_verifier':verifier,'redirect_uri':callback,
                    'resource':'http://localhost/mcp'})
    assert result.status_code==200,result.data
    oauth_token=result.json['access_token']
    assert not tool(client,oauth_token,'get_recipe',{'recipe_id':1})['isError']
    assert not tool(client,oauth_token,'create_recipe',sample_recipe())['isError']
    replay=client.post('/oauth/token',data={'grant_type':'authorization_code','client_id':client_id,
                     'code':authorization_code,'code_verifier':verifier,'redirect_uri':callback,
                     'resource':'http://localhost/mcp'})
    assert replay.status_code==400
    old_refresh=result.json['refresh_token']
    new=client.post('/oauth/token',data={'grant_type':'refresh_token','client_id':client_id,
                             'refresh_token':old_refresh,'resource':'http://localhost/mcp'})
    assert new.status_code==200,new.data
    assert client.post('/mcp',headers={'Authorization':'Bearer '+oauth_token},json={'jsonrpc':'2.0','id':4,'method':'tools/list'}).status_code==401
    new_token=new.json['access_token']
    assert not tool(client,new_token,'get_recipe',{'recipe_id':1})['isError']
    revoked=client.post('/oauth/revoke',data={'client_id':client_id,'token':new.json['refresh_token']})
    assert revoked.status_code==200
    assert client.post('/mcp',headers={'Authorization':'Bearer '+new_token},json={'jsonrpc':'2.0','id':5,'method':'tools/list'}).status_code==401
    assert client.get('/api/v1/recipes').status_code==401


def test_mcp_schema_persists_through_container_restart(client):
    setup(client)
    key=token(client,'read')
    db.ensure_db()
    assert rpc(client,key,'tools/list').status_code==200
    with db.connect() as con:
        assert con.execute("SELECT name FROM sqlite_master WHERE name='mcp_oauth_clients'").fetchone()
