"""Stateless Streamable HTTP MCP transport plus OAuth 2.1 account linking.

- API-key mode: existing ccb_ bearer tokens authenticate /mcp directly.
- ChatGPT mode: OAuth 2.1 authorization-code + S256 PKCE, per-user consent,
  rotating refresh tokens, DCR limited to known ChatGPT HTTPS callbacks.
- Tool execution reuses the normal v1 REST API, including all existing owner,
  administrator, read-only and image-permission checks.

This intentionally implements a narrow MCP request/response-only transport:
JSON-RPC initialize, ping, tools/list and tools/call. No subscriptions, server
notifications, sampling, untrusted URLs or long-lived sockets.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import io
import json
import re
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode, urlsplit, urlunsplit, parse_qsl

from flask import abort, g, jsonify, redirect, render_template, request, session, url_for
from werkzeug.exceptions import HTTPException

from db import get_db

PROTOCOL = '2025-11-25'
SUPPORTED_PROTOCOLS = ('2025-11-25', '2025-06-18', '2025-03-26')
MAX_RPC_BODY = 6_000_000  # permits <= 4 MB image sent as base64
TOKEN_TTL = 3600
REFRESH_TTL = 30 * 24 * 3600
CODE_TTL = 300
SUPPORTED_SCOPES = {'recipes:read', 'recipes:write'}
PKCE_PATTERN = re.compile(r'^[A-Za-z0-9._~-]{43,128}$')
CHATGPT_CALLBACK = re.compile(
    r'^/connector/oauth/[A-Za-z0-9_-]{6,160}$|^/connector_platform_oauth_redirect$'
)


def now():
    return datetime.now(timezone.utc)


def stamp(dt):
    return dt.isoformat(timespec='seconds')


def digest(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def public_origin():
    """Keep OAuth discovery and token audiences aligned with the proxy origin."""
    origin = request.url_root.rstrip('/')
    if not request.is_secure:
        # Local development is intentionally supported. Production requires HTTPS.
        if not request.host.startswith(('localhost', '127.0.0.1')):
            abort(400, 'MCP OAuth requires an HTTPS reverse proxy.')
    return origin


def resource_id():
    return public_origin() + '/mcp'


def validate_client_redirect(uri):
    """Do not build an open OAuth redirector; only ChatGPT callbacks are allowed."""
    if not isinstance(uri, str) or len(uri) > 500:
        return False
    u = urlsplit(uri)
    return (u.scheme == 'https' and u.hostname == 'chatgpt.com' and not u.username
            and not u.password and u.port in (None, 443) and not u.query
            and not u.fragment and bool(CHATGPT_CALLBACK.fullmatch(u.path)))


def oauth_error(message, status=400):
    r = jsonify(error=message)
    r.status_code = status
    r.headers['Cache-Control'] = 'no-store'
    return r


def api_key_record(conn, bearer):
    """Check token, account and scope, always against live DB state."""
    if not isinstance(bearer, str) or not bearer.startswith('ccb_') or len(bearer) > 160:
        return None
    row = conn.execute('''SELECT t.*,u.active,u.must_change_password,u.role
       FROM api_tokens t JOIN users u ON u.id=t.user_id
       WHERE t.token_hash=? AND t.revoked_at IS NULL''', (digest(bearer),)).fetchone()
    if row is None or not row['active'] or row['must_change_password']:
        return None
    if row['expires_at'] and row['expires_at'] <= stamp(now()):
        return None
    return row


def mcp_tools():
    """Stable, machine-readable schemas; labels describe access and risks."""
    def schema(fields=None, required=()):
        return {'type': 'object', 'properties': fields or {}, 'required': list(required),
                'additionalProperties': False}

    def field(typ, description, **kw):
        return {'type': typ, 'description': description, **kw}

    string = lambda description: field('string', description)
    integer = lambda description: field('integer', description)
    item = {'type': 'object', 'description': 'Ingredient with name, quantity, kind, optional, is_brand and categories',
            'properties': {
                'name': string('Ingredient or bottle name'),
                'quantity': string('Amount, e.g. 3 cl'),
                'kind': {'type':'string', 'enum':['ingredient','garnish','ice']},
                'optional':{'type':'boolean'}, 'is_brand':{'type':'boolean'},
                'categories':{'type':'array','items':{'oneOf':[{'type':'integer'}, {'type':'string'}]}}
            }, 'required':['name', 'quantity']}
    pending = {'type':'array','description':'Optional new subcategories created along with a recipe',
               'items':schema({'temp_id':string('Temporary id such as new:1'),
                               'name':string('Category name'),
                               'parent_id':{'oneOf':[{'type':'integer'},{'type':'string'}]}}, ('temp_id','name','parent_id'))}
    recipe = {'title':string('Name of cocktail'),
              'technique':{'type':'string','enum':['Built','Stirred','Shaken','Blended','Layered','Other']},
              'glass':string('Preferred glassware'), 'notes':string('Optional explanatory notes'),
              'steps':{'type':'array','items':{'type':'string'},'description':'Ordered preparation instructions'},
              'items':{'type':'array','items':item,'description':'Exact ingredients and quantities'},
              'new_categories':pending}
    out=[]
    def add(name,description,fields=None,required=(),write=False,destructive=False):
        out.append({'name':name,'description':description,'inputSchema':schema(fields,required),
                    'annotations':{'readOnlyHint':not write,'destructiveHint':destructive,
                                   'idempotentHint':not write,'openWorldHint':False},
                    '_meta':{'securitySchemes':[{'type':'oauth2','scopes':
                               ['recipes:read','recipes:write'] if write else ['recipes:read']}]}})
    add('get_account','Show which Cocktail Cabinet user is connected and their account permissions.')
    add('list_recipes','Search and list your private cocktail recipes with ingredients, instructions and photo status.',
        {'q':string('Optional search term'), 'technique':string('Optional preparation method'),
         'page':integer('Page starting at one'), 'per_page':integer('1-100 recipes per page')})
    add('get_recipe','Get one complete cocktail recipe.',{'recipe_id':integer('Recipe ID')},('recipe_id',))
    add('list_ingredients','List ingredient names, branded bottles and their generic classifications.')
    add('list_categories','List ingredient categories and parent category IDs.')
    shelf_change=schema({
        'ingredient_id':integer('Specific branded product ID; choose this OR category_id'),
        'category_id':integer('Generic ingredient subcategory ID; choose this OR ingredient_id'),
        'owned':{'type':'boolean','description':'True to mark owned; false to mark not owned'}
    },('owned',))
    add('get_my_shelf','Read the connected user\'s personal shelf. Returns every selectable bottle and generic category with owned status and IDs.')
    add('set_shelf_item_owned','Mark one bottle or generic subcategory owned or not owned on the connected user\'s shelf, preserving every other selection.',shelf_change['properties'],('owned',),write=True)
    add('update_my_shelf','Atomically mark multiple bottles/categories owned or unowned without replacing any other shelf choices.',
        {'changes':{'type':'array','items':shelf_change,'minItems':1,'maxItems':100}},('changes',),write=True)
    add('create_recipe','Add a cocktail recipe; categories may be created transactionally with the recipe.',recipe,
        ('title','technique','steps','items'),write=True)
    add('update_recipe','Modify a recipe, subject to ownership and token permissions.',
        {'recipe_id':integer('Recipe to edit'),**recipe},('recipe_id',),write=True)
    add('delete_recipe','Permanently delete a recipe and its associated uploaded photo. Confirm with the user first.',
        {'recipe_id':integer('Recipe to delete')},('recipe_id',),write=True,destructive=True)
    add('create_ingredient','Add a new ingredient or brand and generic categories.',
        {'name':string('Ingredient name'), 'is_brand':{'type':'boolean'},
         'categories':{'type':'array','items':{'oneOf':[{'type':'integer'},{'type':'string'}]}},
         'new_categories':pending},('name','categories'),write=True)
    add('create_category','Create a named category. Only a Cabinet administrator may use this.',
        {'name':string('New category'), 'parent_id':integer('Existing parent category ID')},('name',),write=True)
    add('download_recipe_image','Read the private WebP photograph attached to a recipe as MCP image content.',
        {'recipe_id':integer('Recipe whose photo is wanted')},('recipe_id',))
    add('upload_recipe_image','Upload or replace a recipe photo. Supply JPEG, PNG or WebP as base64, max 4 MB.',
        {'recipe_id':integer('Recipe receiving the photo'),
         'image_base64':string('Base64-encoded JPEG/PNG/WebP bytes, without data URL prefix'),
         'filename':string('Original filename, e.g. photo.jpg')},('recipe_id','image_base64','filename'),write=True)
    add('remove_recipe_image','Remove the recipe photo without deleting the recipe.',
        {'recipe_id':integer('Recipe ID')},('recipe_id',),write=True,destructive=True)
    from mcp_management import management_tool_definitions
    out.extend(management_tool_definitions())
    return out


def _rpc_response(rpc_id, result=None, error=None):
    payload={'jsonrpc':'2.0','id':rpc_id}
    if error is not None: payload['error']=error
    else: payload['result']=result
    return jsonify(payload)


def _rpc_error(rpc_id, code, message):
    return _rpc_response(rpc_id,error={'code':code,'message':message})


def _tool_result(resp):
    """Do not swallow API errors. Bubble them as MCP isError results."""
    if resp.status_code == 204:
        obj={'ok':True}
    elif resp.is_json:
        obj=resp.get_json()
    else:
        obj={'error':{'message':'Unexpected response from Cocktail Cabinet','status':resp.status_code}}
    if not isinstance(obj,dict):obj={'data':obj}
    content={'content':[{'type':'text','text':json.dumps(obj,ensure_ascii=False)}],
             'isError':resp.status_code>=400}
    if resp.status_code < 400:
        content['structuredContent']=obj
    return content


def _call_tool(app, name, args, bearer):
    methods={
        'get_account':('GET','/api/v1/me'),
        'get_my_shelf':('GET','/api/v1/shelf'),
        'set_shelf_item_owned':('POST','/api/v1/shelf/items'),
        'update_my_shelf':('PATCH','/api/v1/shelf/items'),
        'list_recipes':('GET','/api/v1/recipes'),
        'get_recipe':('GET',f"/api/v1/recipes/{args.get('recipe_id')}"),
        'list_ingredients':('GET','/api/v1/ingredients'),
        'list_categories':('GET','/api/v1/categories'),
        'create_recipe':('POST','/api/v1/recipes'),
        'update_recipe':('PATCH',f"/api/v1/recipes/{args.get('recipe_id')}"),
        'delete_recipe':('DELETE',f"/api/v1/recipes/{args.get('recipe_id')}"),
        'create_ingredient':('POST','/api/v1/ingredients'),
        'create_category':('POST','/api/v1/categories'),
        'download_recipe_image':('GET',f"/api/v1/recipes/{args.get('recipe_id')}/image"),
        'upload_recipe_image':('POST',f"/api/v1/recipes/{args.get('recipe_id')}/image"),
        'remove_recipe_image':('DELETE',f"/api/v1/recipes/{args.get('recipe_id')}/image"),
    }
    from mcp_management import management_route
    try:
        extra = management_route(name, args)
    except ValueError as exc:
        return {'content':[{'type':'text','text':str(exc)}], 'isError':True}
    if extra:
        methods[name] = (extra[0], extra[1])
    if name not in methods:
        return {'content':[{'type':'text','text':'Unknown Cocktail Cabinet tool.'}],'isError':True}
    omit={'recipe_id'} | (set(extra[2]) if extra else set())
    route_args={k:v for k,v in args.items() if k not in omit}
    method,path=methods[name]
    if 'recipe_id' in args and (type(args['recipe_id']) is not int or args['recipe_id'] < 1):
        return {'content':[{'type':'text','text':'recipe_id must be a positive integer.'}],'isError':True}
    headers={'Authorization':'Bearer '+bearer}
    # Keep the original request's public URL in any generated response links.
    with app.test_client() as test:
        if name=='list_recipes':
            resp=test.get(path,query_string={k:v for k,v in route_args.items() if v is not None},headers=headers)
        elif name=='download_recipe_image':
            resp=test.get(path,headers=headers)
            if resp.status_code==200:
                image_data=resp.get_data()
                return {'content':[{'type':'image','data':base64.b64encode(image_data).decode('ascii'),
                                    'mimeType':'image/webp'}],'isError':False,
                        'structuredContent':{'recipe_id':args['recipe_id'],'mime_type':'image/webp',
                                             'bytes':len(image_data)}}
        elif name=='upload_recipe_image':
            encoded=route_args.get('image_base64','')
            if not isinstance(encoded,str) or len(encoded)>5_600_000:
                return {'content':[{'type':'text','text':'Image is too large (4 MB maximum).'}],'isError':True}
            try:data=base64.b64decode(encoded,validate=True)
            except (ValueError,base64.binascii.Error):
                return {'content':[{'type':'text','text':'Invalid base64 image data.'}],'isError':True}
            if not 1<=len(data)<=4*1024*1024:
                return {'content':[{'type':'text','text':'Image is too large (4 MB maximum).'}],'isError':True}
            filename=route_args.get('filename','image.png')
            if not isinstance(filename,str) or not re.fullmatch(r'[a-zA-Z0-9_.-]{1,100}\.(jpe?g|png|webp)',filename,re.I):
                return {'content':[{'type':'text','text':'Upload a JPEG, PNG or WebP filename.'}],'isError':True}
            resp=test.post(path,data={'image':(io.BytesIO(data),filename)},
                           content_type='multipart/form-data',headers=headers)
        elif method in ('POST','PATCH'):
            resp=test.open(path,method=method,json=route_args,headers=headers)
        else:
            resp=test.open(path,method=method,headers=headers)
    return _tool_result(resp)


def register_mcp(app):
    """Register OAuth and MCP transport without affecting the website routes."""
    @app.get('/.well-known/oauth-protected-resource')
    def mcp_resource_metadata():
        origin=public_origin()
        return jsonify({'resource':origin+'/mcp','authorization_servers':[origin],
                        'scopes_supported':['recipes:read','recipes:write']})

    @app.get('/.well-known/oauth-authorization-server')
    def mcp_authorization_server():
        origin=public_origin()
        return jsonify({'issuer':origin,'authorization_endpoint':origin+'/oauth/authorize',
                        'token_endpoint':origin+'/oauth/token',
                        'registration_endpoint':origin+'/oauth/register',
                        'revocation_endpoint':origin+'/oauth/revoke',
                        'response_types_supported':['code'],
                        'grant_types_supported':['authorization_code','refresh_token'],
                        'token_endpoint_auth_methods_supported':['none'],
                        'code_challenge_methods_supported':['S256'],
                        'authorization_response_iss_parameter_supported':True,
                        'scopes_supported':['recipes:read','recipes:write']})

    @app.post('/oauth/register')
    def mcp_register_client():
        payload=request.get_json(silent=True)
        if not isinstance(payload,dict):return oauth_error('invalid_client_metadata')
        uri_list=payload.get('redirect_uris',[])
        if not isinstance(uri_list,list) or not 1<=len(uri_list)<=5 or any(not validate_client_redirect(x) for x in uri_list):
            return oauth_error('invalid_redirect_uri')
        if payload.get('token_endpoint_auth_method','none')!='none':return oauth_error('invalid_client_metadata')
        grants=payload.get('grant_types')
        if grants is not None and (not isinstance(grants,list) or not set(grants)<= {'authorization_code','refresh_token'}):
            return oauth_error('invalid_client_metadata')
        client_id='ccb_mcp_'+secrets.token_urlsafe(22)
        name=str(payload.get('client_name') or 'ChatGPT').strip()[:80]
        conn=get_db()
        with conn:
            conn.execute('INSERT INTO mcp_oauth_clients(client_id,client_name,redirect_uris_json) VALUES(?,?,?)',
                         (client_id,name,json.dumps(uri_list)))
        return jsonify({'client_id':client_id,'client_name':name,'redirect_uris':uri_list,
                        'token_endpoint_auth_method':'none','client_id_issued_at':int(now().timestamp()),
                        'grant_types':['authorization_code','refresh_token'],
                        'response_types':['code']}),201

    def validate_authorization():
        p=request.values
        if p.get('response_type')!='code':return None
        client=get_db().execute('SELECT * FROM mcp_oauth_clients WHERE client_id=?',
                                (p.get('client_id',''),)).fetchone()
        if not client:return None
        uri=p.get('redirect_uri','')
        if uri not in json.loads(client['redirect_uris_json']) or not validate_client_redirect(uri):return None
        if p.get('code_challenge_method')!='S256':return None
        challenge=p.get('code_challenge','')
        if not re.fullmatch(r'[A-Za-z0-9_-]{43}',challenge):return None
        scope=set(p.get('scope','recipes:read').split())
        if not scope or not scope<=SUPPORTED_SCOPES:return None
        resource=p.get('resource',resource_id())
        if resource!=resource_id():return None
        state=p.get('state','')
        if len(state)>1024:return None
        return client,uri,challenge,scope,resource,state

    @app.route('/oauth/authorize',methods=['GET','POST'])
    def mcp_authorize():
        valid=validate_authorization()
        if not valid:return oauth_error('invalid_request',400)
        client,uri,challenge,scope,resource,state=valid
        if not getattr(g,'user',None):
            # The OAuth request must survive the login round-trip. The signed,
            # same-site Flask session is a fallback if a login form loses its
            # hidden `next` field; authorization is revalidated on return.
            session['pending_oauth_return'] = request.full_path
            return redirect(url_for('login',next=request.full_path))
        if g.user['must_change_password']:
            return redirect(url_for('account'))
        if request.method=='POST':
            decision=request.form.get('decision')
            if decision not in ('allow','deny'):return oauth_error('invalid_request')
            if decision=='deny':
                parameters={'error':'access_denied'}
            else:
                code=secrets.token_urlsafe(40)
                with get_db() as conn:
                    conn.execute('INSERT INTO mcp_oauth_codes(code_hash,client_id,user_id,redirect_uri,code_challenge,scopes,resource,expires_at) VALUES (?,?,?,?,?,?,?,?)',
                                 (digest(code),client['client_id'],g.user['id'],uri,challenge,
                                  ' '.join(sorted(scope)),resource,stamp(now()+timedelta(seconds=CODE_TTL))))
                parameters={'code':code}
            if state:parameters['state']=state
            # RFC 9207 issuer identification is required for ChatGPT's stable
            # /connector_platform_oauth_redirect callback. Echo it for both
            # approvals and denials to prevent authorization-server mix-ups.
            parameters['iss']=public_origin()
            session.pop('pending_oauth_return',None)
            u=urlsplit(uri);q=list(parse_qsl(u.query,keep_blank_values=True));q.extend(parameters.items())
            return redirect(urlunsplit((u.scheme,u.netloc,u.path,urlencode(q),'')))
        return render_template('mcp_consent.html',client=client,resource=resource,
                               scopes=scope,query=request.args.to_dict(),viewer=g.user)

    def issue_oauth_token(conn,user_id,client_id,scopes,resource):
        value='ccb_'+secrets.token_urlsafe(40)
        refresh='ccr_'+secrets.token_urlsafe(40)
        scope='write' if 'recipes:write' in scopes.split() else 'read'
        expires=stamp(now()+timedelta(seconds=TOKEN_TTL))
        token_id=conn.execute('''INSERT INTO api_tokens(user_id,name,token_prefix,token_hash,scope,expires_at)
                     VALUES(?,?,?,?,?,?)''', (user_id,'Cocktail Cabinet MCP',value[:16],digest(value),scope,expires)).lastrowid
        conn.execute('''INSERT INTO mcp_oauth_refresh(refresh_hash,client_id,user_id,access_id,scopes,resource,expires_at)
             VALUES(?,?,?,?,?,?,?)''',(digest(refresh),client_id,user_id,token_id,scopes,resource,
                                       stamp(now()+timedelta(seconds=REFRESH_TTL))))
        return {'access_token':value,'refresh_token':refresh,'token_type':'Bearer',
                'expires_in':TOKEN_TTL,'scope':scopes}

    @app.post('/oauth/token')
    def mcp_exchange_token():
        grant=request.form.get('grant_type')
        client_id=request.form.get('client_id','')
        resource=request.form.get('resource',resource_id())
        if resource!=resource_id():return oauth_error('invalid_target')
        conn=get_db()
        if not conn.execute('SELECT 1 FROM mcp_oauth_clients WHERE client_id=?',(client_id,)).fetchone():
            return oauth_error('invalid_client',401)
        if grant=='authorization_code':
            code=request.form.get('code','')
            verifier=request.form.get('code_verifier','')
            if not PKCE_PATTERN.fullmatch(verifier):return oauth_error('invalid_grant')
            with conn:
                conn.execute('BEGIN IMMEDIATE')
                record=conn.execute('''SELECT c.*,u.active,u.must_change_password FROM mcp_oauth_codes c
                         JOIN users u ON u.id=c.user_id WHERE c.code_hash=? AND c.client_id=?''',
                         (digest(code),client_id)).fetchone()
                if not record or record['expires_at']<=stamp(now()) or not record['active'] or record['must_change_password']:
                    return oauth_error('invalid_grant')
                expected=base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
                if not hmac.compare_digest(expected,record['code_challenge']) or record['redirect_uri']!=request.form.get('redirect_uri','') or record['resource']!=resource:
                    return oauth_error('invalid_grant')
                conn.execute('DELETE FROM mcp_oauth_codes WHERE code_hash=?',(digest(code),))
                result=issue_oauth_token(conn,record['user_id'],client_id,record['scopes'],resource)
            result_response=jsonify(result);result_response.headers['Cache-Control']='no-store'
            return result_response
        if grant=='refresh_token':
            refresh=request.form.get('refresh_token','')
            with conn:
                conn.execute('BEGIN IMMEDIATE')
                record=conn.execute('''SELECT r.*,u.active,u.must_change_password FROM mcp_oauth_refresh r
                         JOIN users u ON u.id=r.user_id WHERE r.refresh_hash=? AND r.client_id=?''',
                         (digest(refresh),client_id)).fetchone()
                if (not record or record['revoked_at'] or record['expires_at']<=stamp(now()) or
                        not record['active'] or record['must_change_password'] or record['resource']!=resource):
                    return oauth_error('invalid_grant')
                conn.execute('UPDATE mcp_oauth_refresh SET revoked_at=? WHERE refresh_hash=?',(stamp(now()),digest(refresh)))
                if record['access_id']:
                    conn.execute('UPDATE api_tokens SET revoked_at=? WHERE id=?',(stamp(now()),record['access_id']))
                result=issue_oauth_token(conn,record['user_id'],client_id,record['scopes'],resource)
            response=jsonify(result);response.headers['Cache-Control']='no-store'
            return response
        return oauth_error('unsupported_grant_type')

    @app.post('/oauth/revoke')
    def mcp_revoke():
        client_id=request.form.get('client_id','')
        refresh=request.form.get('token','')
        with get_db() as conn:
            row=conn.execute('SELECT * FROM mcp_oauth_refresh WHERE refresh_hash=? AND client_id=?',
                             (digest(refresh),client_id)).fetchone()
            if row:
                conn.execute('UPDATE mcp_oauth_refresh SET revoked_at=? WHERE refresh_hash=?',
                             (stamp(now()),digest(refresh)))
                if row['access_id']:conn.execute('UPDATE api_tokens SET revoked_at=? WHERE id=?',
                                                (stamp(now()),row['access_id']))
        return '',200

    def challenge():
        response=oauth_error('authentication_required',401)
        response.headers['WWW-Authenticate'] = (
            'Bearer resource_metadata="'+public_origin()+
            '/.well-known/oauth-protected-resource", scope="recipes:read"')
        return response

    @app.route('/mcp',methods=['GET','POST','DELETE'])
    def mcp_endpoint():
        if request.method!='POST':
            return '',405,{'Allow':'POST'}
        if request.content_length and request.content_length>MAX_RPC_BODY:
            return oauth_error('request_too_large',413)
        value=request.headers.get('Authorization','')
        if not value.startswith('Bearer '):return challenge()
        bearer=value[7:].strip()
        record=api_key_record(get_db(),bearer)
        if record is None:return challenge()
        if not record['last_used_at'] or record['last_used_at'] < stamp(now()-timedelta(hours=1)):
            with get_db() as conn:
                conn.execute('UPDATE api_tokens SET last_used_at=? WHERE id=?', (stamp(now()),record['id']))
        incoming=request.get_json(silent=True)
        if not isinstance(incoming,dict) or incoming.get('jsonrpc')!='2.0':
            return _rpc_error(incoming.get('id') if isinstance(incoming,dict) else None,-32600,'Invalid JSON-RPC request')
        rpc_id=incoming.get('id')
        method=incoming.get('method','')
        if not isinstance(method,str):return _rpc_error(rpc_id,-32600,'Invalid JSON-RPC method')
        # Notifications have no reply, as per MCP stateless JSON mode.
        if 'id' not in incoming and method.startswith('notifications/'):
            return '',202
        if method=='initialize':
            requested=(incoming.get('params') or {}).get('protocolVersion')
            version=requested if requested in SUPPORTED_PROTOCOLS else PROTOCOL
            return _rpc_response(rpc_id,{
                'protocolVersion':version,
                'capabilities':{'tools':{'listChanged':False}},
                'serverInfo':{'name':'Cocktail Cabinet','version':'1.0.0'},
                'instructions':'Private cocktail collection. Only edit recipes with user approval. Ingredient categories and exact quantities matter.'})
        if method=='ping':return _rpc_response(rpc_id,{})
        if method=='tools/list':
            return _rpc_response(rpc_id,{'tools':mcp_tools()})
        if method=='tools/call':
            params=incoming.get('params') or {}
            if not isinstance(params,dict):return _rpc_error(rpc_id,-32602,'Invalid tool parameters')
            name=params.get('name')
            args=params.get('arguments',{})
            if not isinstance(name,str) or not isinstance(args,dict):
                return _rpc_error(rpc_id,-32602,'Provide tool name and arguments object')
            if name not in {x['name'] for x in mcp_tools()}:
                return _rpc_error(rpc_id,-32602,'Unknown tool')
            try:result=_call_tool(app,name,args,bearer)
            except Exception:
                app.logger.exception('MCP tool failure: %s',name)
                result={'content':[{'type':'text','text':'The tool failed. Please retry.'}], 'isError':True}
            return _rpc_response(rpc_id,result)
        return _rpc_error(rpc_id,-32601,'Method not found')
