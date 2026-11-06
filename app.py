"""The Cocktail Cabinet — private, self-hosted Flask application."""
from __future__ import annotations
import hashlib
import hmac
import io
import json
import os
import re
import secrets
import smtplib
import sqlite3
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from functools import wraps
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

from flask import (Flask, abort, flash, g, jsonify, redirect, render_template,
                   request, send_from_directory, session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.middleware.proxy_fix import ProxyFix
from PIL import Image, UnidentifiedImageError
from db import (DATA_DIR, get_db, close_db, ensure_db, connect, ingredient_category_maps,
                recipe_card)
from bootstrap import persistent_secret

app = Flask(__name__)
# If the operator has not explicitly configured a secret, generate one once
# inside the persistent data volume. Never put a session secret in Compose.
app.secret_key = os.getenv('SECRET_KEY') or persistent_secret(DATA_DIR / '.session-secret')
if os.getenv('TESTING') != '1':
    if len(app.secret_key) < 32 or 'CHANGE_ME' in app.secret_key:
        raise RuntimeError('SECRET_KEY, when overridden, must be random and at least 32 characters.')
    override = os.getenv('SETUP_TOKEN', '')
    if override and (len(override) < 24 or 'CHANGE_ME' in override):
        raise RuntimeError('SETUP_TOKEN, when overridden, must be random and at least 24 characters.')
app.config.update(
    MAX_CONTENT_LENGTH=4*1024*1024,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
    SESSION_COOKIE_SECURE=os.getenv('COOKIE_SECURE','true').lower()=='true',
    PERMANENT_SESSION_LIFETIME=timedelta(days=21),
    PREFERRED_URL_SCHEME='https' if os.getenv('COOKIE_SECURE','true').lower()=='true' else 'http',
)
# This deployment expects one trusted reverse proxy; do not connect an untrusted proxy.
if os.getenv('TRUST_PROXY','false').lower()=='true':
    app.wsgi_app=ProxyFix(app.wsgi_app,x_for=1,x_proto=1,x_host=1)
ensure_db()
app.teardown_appcontext(close_db)


def first_run_setup_key():
    """Bootstrap key for the only initial administrator; never sent to browsers."""
    override = os.getenv('SETUP_TOKEN', '').strip()
    return override or persistent_secret(DATA_DIR / '.admin-setup-token')


def announce_first_run():
    # An unclaimed instance requires a strong randomly generated setup key.
    # Only NAS/container administrators should have access to the container logs.
    con = connect()
    try:
        if con.execute('SELECT 1 FROM users LIMIT 1').fetchone():
            return
    finally:
        con.close()
    if not os.getenv('SETUP_TOKEN', '').strip():
        key = first_run_setup_key()
        print('\n' + '=' * 68, flush=True)
        print('COCKTAIL CABINET — FIRST-RUN ADMINISTRATION', flush=True)
        print('Open https://YOUR-HOSTNAME/setup after configuring HTTPS.', flush=True)
        print('One-time administrator setup key: ' + key, flush=True)
        print('This key is required only to create the first administrator.', flush=True)
        print('Do not share it. It becomes invalid after administrator setup.', flush=True)
        print('=' * 68 + '\n', flush=True)


announce_first_run()


def now(): return datetime.now(timezone.utc)
def iso(dt): return dt.isoformat(timespec='seconds')
def token_hash(value): return hashlib.sha256(value.encode()).hexdigest()
def required_string(value, limit=180):
    s=str(value or '').strip()
    if not s or len(s)>limit: raise ValueError(f'A value is required (maximum {limit} characters).')
    return s

def logged_in(): return getattr(g,'user',None) is not None

def login_required(fn):
    @wraps(fn)
    def wrapped(*args,**kwargs):
        if not logged_in(): return redirect(url_for('login',next=request.path))
        return fn(*args,**kwargs)
    return wrapped

def admin_required(fn):
    @wraps(fn)
    @login_required
    def wrapped(*args,**kwargs):
        if g.user['role']!='admin': abort(403)
        return fn(*args,**kwargs)
    return wrapped

@app.before_request
def security():
    con=get_db()
    uid=session.get('user_id')
    g.user=con.execute('SELECT * FROM users WHERE id=? AND active=1',(uid,)).fetchone() if uid else None
    if uid and not g.user: session.clear()
    if not session.get('_csrf'): session['_csrf']=secrets.token_urlsafe(32)
    if request.method in ('POST','PUT','PATCH','DELETE'):
        submitted=request.headers.get('X-CSRF-Token','') or request.form.get('_csrf','')
        if not hmac.compare_digest(str(submitted),str(session['_csrf'])):
            abort(400, description='Invalid CSRF token. Reload the page and try again.')
    if g.user and g.user['must_change_password'] and request.endpoint not in ('account','logout','static'):
        return redirect(url_for('account'))

@app.after_request
def headers(resp):
    # Never cache private HTML or recipe JSON, even in the PWA service worker.
    if request.path.startswith('/static/'):
        resp.headers['Cache-Control']='public, max-age=3600'
    else:
        resp.headers['Cache-Control']='no-store, private'
    resp.headers['X-Content-Type-Options']='nosniff'
    resp.headers['Referrer-Policy']='strict-origin-when-cross-origin'
    resp.headers['X-Frame-Options']='DENY'
    if request.is_secure: resp.headers['Strict-Transport-Security']='max-age=15552000'
    resp.headers['Content-Security-Policy']=("default-src 'self'; img-src 'self' data:; font-src 'self' https://fonts.gstatic.com; "
                                            "style-src 'self' https://fonts.googleapis.com; script-src 'self'; "
                                            "connect-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'self'")
    return resp

@app.context_processor
def ctx(): return {'csrf_token':session.get('_csrf'),'viewer':getattr(g,'user',None)}

@app.get('/health')
def health(): return {'status':'ok'}

@app.route('/setup',methods=['GET','POST'])
def setup():
    con=get_db()
    if con.execute('SELECT 1 FROM users LIMIT 1').fetchone(): return redirect(url_for('index'))
    if request.method=='POST':
        expected=first_run_setup_key()
        if not expected or not hmac.compare_digest(request.form.get('setup_token',''),expected):
            flash('The setup key is incorrect.','error')
        else:
            try:
                username=required_string(request.form.get('username'),50)
                email=required_string(request.form.get('email'),150).lower()
                password=required_string(request.form.get('password'),256)
                if '@' not in email or len(password)<12: raise ValueError('Enter a valid email and a password of at least 12 characters.')
                with con:
                    # Single-writer transaction avoids two competing first-run admin signups.
                    con.execute('BEGIN IMMEDIATE')
                    if con.execute('SELECT 1 FROM users LIMIT 1').fetchone(): abort(409)
                    uid=con.execute('INSERT INTO users(username,email,password_hash,role) VALUES(?,?,?,?)',
                        (username,email,generate_password_hash(password),'admin')).lastrowid
                # The bootstrap token is no longer needed once the first
                # administrator exists. The setup route is now closed forever
                # while the persistent user database contains an account.
                if not os.getenv('SETUP_TOKEN', '').strip():
                    (DATA_DIR / '.admin-setup-token').unlink(missing_ok=True)
                session.clear(); session['user_id']=uid; session.permanent=True
                return redirect(url_for('index'))
            except (ValueError,sqlite3.IntegrityError) as exc: flash(str(exc),'error')
    return render_template('auth.html',mode='setup')

@app.route('/login',methods=['GET','POST'])
def login():
    con=get_db()
    if not con.execute('SELECT 1 FROM users LIMIT 1').fetchone(): return redirect(url_for('setup'))
    if logged_in(): return redirect(url_for('index'))
    if request.method=='POST':
        user=con.execute('SELECT * FROM users WHERE email=? COLLATE NOCASE OR username=? COLLATE NOCASE',
                         (request.form.get('identity',''),request.form.get('identity',''))).fetchone()
        if user and user['active'] and check_password_hash(user['password_hash'],request.form.get('password','')):
            session.clear();session.permanent=True;session['user_id']=user['id']
            nxt=request.form.get('next','')
            if nxt.startswith('/') and not nxt.startswith('//') and not urlsplit(nxt).netloc:
                return redirect(nxt)
            return redirect(url_for('index'))
        flash('Incorrect login details.','error')
    return render_template('auth.html',mode='login',next=request.args.get('next',''))

@app.post('/logout')
@login_required
def logout():
    session.clear();return redirect(url_for('login'))

@app.route('/account',methods=['GET','POST'])
@login_required
def account():
    if request.method=='POST':
        pw=request.form.get('password','')
        if len(pw)<12: flash('Choose a password with at least 12 characters.','error')
        elif pw!=request.form.get('confirm',''): flash('The passwords do not match.','error')
        else:
            with get_db():get_db().execute('UPDATE users SET password_hash=?,must_change_password=0 WHERE id=?',(generate_password_hash(pw),g.user['id']))
            session.clear();flash('Password changed. Please sign in again.','success');return redirect(url_for('login'))
    return render_template('account.html')


def pantry_ids(con,uid):
    rows=con.execute('SELECT category_id,ingredient_id FROM pantry WHERE user_id=?',(uid,)).fetchall()
    return ({r['category_id'] for r in rows if r['category_id'] is not None},
            {r['ingredient_id'] for r in rows if r['ingredient_id'] is not None})

def all_recipes(con): return con.execute('SELECT r.*,u.username AS author FROM recipes r LEFT JOIN users u ON u.id=r.created_by ORDER BY r.title COLLATE NOCASE').fetchall()

def families(con):
    cats=con.execute('SELECT * FROM categories ORDER BY name COLLATE NOCASE').fetchall()
    by_id={r['id']:dict(r,children=[]) for r in cats}
    roots=[]
    for row in by_id.values():
        if row['parent_id'] is None: roots.append(row)
        else: by_id[row['parent_id']]['children'].append(row)
    return roots

@app.get('/')
@login_required
def index():
    con=get_db()
    pc,pi=pantry_ids(con,g.user['id']); maps=ingredient_category_maps(con)
    items=[recipe_card(con,r,pc,pi,maps) for r in all_recipes(con)]
    q=request.args.get('q','').strip().casefold()
    if q: items=[r for r in items if q in r['title'].casefold() or any(q in i['name'].casefold() for i in r['items'])]
    selected=request.args.get('category',type=int)
    if selected: items=[r for r in items if any(selected in i['categories'] for i in r['items'])]
    tech=request.args.get('technique','').strip()
    if tech: items=[r for r in items if r['technique']==tech]
    return render_template('index.html',recipes=items,recipe_total=len(all_recipes(con)),categories=maps[0],selected_category=selected,tech=tech,q=request.args.get('q',''))

@app.get('/recipes/<int:recipe_id>')
@login_required
def recipe_view(recipe_id):
    con=get_db()
    r=con.execute('SELECT r.*,u.username AS author FROM recipes r LEFT JOIN users u ON u.id=r.created_by WHERE r.id=?',(recipe_id,)).fetchone()
    if not r: abort(404)
    pc,pi=pantry_ids(con,g.user['id'])
    return render_template('recipe.html',recipe=recipe_card(con,r,pc,pi,ingredient_category_maps(con)),
                           can_edit=(g.user['role']=='admin' or r['created_by']==g.user['id']))

def editor_context(con,recipe=None):
    cats,expanded,explicit,_=ingredient_category_maps(con)
    ingredients=[]
    for ing in con.execute('SELECT * FROM ingredients ORDER BY name COLLATE NOCASE'):
        ingredients.append(dict(ing,category_ids=sorted(explicit.get(ing['id'],set()))))
    return {'categories':[dict(x) for x in cats], 'ingredient_catalog':ingredients,'editing':recipe}

def validate_recipe(payload, con):
    title=required_string(payload.get('title'),120)
    technique=required_string(payload.get('technique','Built'),50)
    if technique not in ('Built','Stirred','Shaken','Blended','Layered','Other'): raise ValueError('Select a valid preparation method.')
    glass=str(payload.get('glass','')).strip()[:90]
    notes=str(payload.get('notes','')).strip()[:1200]
    steps=[str(s).strip() for s in payload.get('steps',[]) if str(s).strip()]
    if not (1<=len(steps)<=15) or any(len(s)>500 for s in steps):raise ValueError('Provide 1–15 preparation steps (up to 500 characters each).')
    items=payload.get('items',[])
    if not isinstance(items,list) or not 1<=len(items)<=32: raise ValueError('Provide 1–32 ingredients.')
    category_rows=con.execute('SELECT id,parent_id FROM categories').fetchall()
    valid_categories={r['id'] for r in category_rows}
    specific_categories={r['id'] for r in category_rows if r['parent_id'] is not None}
    normalized=[]
    for item in items:
        name=required_string(item.get('name'),130)
        qty=str(item.get('quantity') or '').strip()[:60]
        kind=item.get('kind','ingredient')
        if kind not in ('ingredient','garnish','ice'):raise ValueError('Invalid ingredient kind.')
        if kind=='ingredient' and not qty:raise ValueError(f'Enter an amount for essential ingredient "{name}".')
        cat_ids=item.get('categories',[])
        if not isinstance(cat_ids,list):raise ValueError('Ingredient categories must be a list.')
        cat_ids=set(int(v) for v in cat_ids)
        existing=con.execute('SELECT id FROM ingredients WHERE name=? COLLATE NOCASE',(name,)).fetchone()
        if not existing and (not cat_ids or not cat_ids<=valid_categories):
            raise ValueError(f'Classify new ingredient "{name}" with at least one valid category/subcategory.')
        if not existing and bool(item.get('is_brand', True)) and not (cat_ids & specific_categories):
            raise ValueError(f'Give branded ingredient "{name}" a specific generic category, not only a top-level family.')
        normalized.append((name,qty,kind,bool(item.get('optional')),cat_ids,existing['id'] if existing else None,bool(item.get('is_brand',True))))
    return title,technique,glass,notes,steps,normalized

def save_recipe(con,payload,recipe_id=None):
    title,technique,glass,notes,steps,items=validate_recipe(payload,con)
    with con:
        if recipe_id:
            con.execute('UPDATE recipes SET title=?,technique=?,glass=?,notes=?,steps_json=?,updated_at=CURRENT_TIMESTAMP WHERE id=?',
                        (title,technique,glass,notes,json.dumps(steps,ensure_ascii=False),recipe_id))
            con.execute('DELETE FROM recipe_items WHERE recipe_id=?',(recipe_id,))
        else:
            recipe_id=con.execute('INSERT INTO recipes(title,technique,glass,notes,steps_json,created_by) VALUES (?,?,?,?,?,?)',
                        (title,technique,glass,notes,json.dumps(steps,ensure_ascii=False),g.user['id'])).lastrowid
        for ix,(name,qty,kind,optional,cat_ids,existing_id,is_brand) in enumerate(items):
            ing_id=existing_id
            if ing_id is None:
                ing_id=con.execute('INSERT INTO ingredients(name,is_brand,added_by) VALUES (?,?,?)',(name,int(is_brand),g.user['id'])).lastrowid
                for cat in cat_ids:con.execute('INSERT INTO ingredient_categories(ingredient_id,category_id) VALUES(?,?)',(ing_id,cat))
            con.execute('INSERT INTO recipe_items(recipe_id,ingredient_id,quantity,kind,optional,sort_order) VALUES(?,?,?,?,?,?)',
                        (recipe_id,ing_id,qty,kind,int(optional),ix))
    return recipe_id

@app.route('/recipes/new',methods=['GET','POST'])
@login_required
def recipe_new():
    if request.method=='POST':
        try:return jsonify({'ok':True,'id':save_recipe(get_db(),request.get_json(force=True) or {})}),201
        except (ValueError,sqlite3.IntegrityError) as e:return jsonify({'error':str(e)}),400
    return render_template('editor.html',**editor_context(get_db()))

@app.route('/recipes/<int:recipe_id>/edit',methods=['GET','POST'])
@login_required
def recipe_edit(recipe_id):
    con=get_db();r=con.execute('SELECT * FROM recipes WHERE id=?',(recipe_id,)).fetchone()
    if not r: abort(404)
    if r['created_by']!=g.user['id'] and g.user['role']!='admin':abort(403)
    if request.method=='POST':
        try:return jsonify({'ok':True,'id':save_recipe(con,request.get_json(force=True) or {},recipe_id)})
        except (ValueError,sqlite3.IntegrityError) as e:return jsonify({'error':str(e)}),400
    pc,pi=pantry_ids(con,g.user['id']);card=recipe_card(con,r,pc,pi,ingredient_category_maps(con))
    return render_template('editor.html',**editor_context(con,card))

@app.post('/recipes/<int:recipe_id>/delete')
@login_required
def recipe_delete(recipe_id):
    con=get_db();r=con.execute('SELECT created_by FROM recipes WHERE id=?',(recipe_id,)).fetchone()
    if not r:abort(404)
    if r['created_by']!=g.user['id'] and g.user['role']!='admin':abort(403)
    with con:con.execute('DELETE FROM recipes WHERE id=?',(recipe_id,))
    flash('Recipe deleted.','success');return redirect(url_for('index'))

@app.post('/recipes/<int:recipe_id>/image')
@login_required
def recipe_image(recipe_id):
    con=get_db();r=con.execute('SELECT * FROM recipes WHERE id=?',(recipe_id,)).fetchone()
    if not r:abort(404)
    if r['created_by']!=g.user['id'] and g.user['role']!='admin':abort(403)
    upload=request.files.get('image')
    if not upload: abort(400)
    try:
        im=Image.open(upload.stream)
        if im.format not in ('JPEG','PNG','WEBP') or im.width*im.height>20_000_000:raise ValueError()
        im.thumbnail((1200,1200))
        im=im.convert('RGB')
        fname=uuid4().hex+'.webp'
        im.save(DATA_DIR/'uploads'/fname,format='WEBP',quality=85)
    except (UnidentifiedImageError,Image.DecompressionBombError,ValueError,OSError):
        flash('Use a valid JPG, PNG or WebP image under 4 MB.','error')
        return redirect(url_for('recipe_view',recipe_id=recipe_id))
    with con: con.execute('UPDATE recipes SET image_file=? WHERE id=?',(fname,recipe_id))
    if r['image_file']:
        try:(DATA_DIR/'uploads'/r['image_file']).unlink(missing_ok=True)
        except OSError: pass
    return redirect(url_for('recipe_view',recipe_id=recipe_id))

@app.get('/media/<filename>')
@login_required
def media(filename):
    if not re.fullmatch(r'[a-f0-9]{32}\.webp',filename):abort(404)
    return send_from_directory(DATA_DIR/'uploads',filename)

@app.route('/pantry',methods=['GET','POST'])
@login_required
def pantry():
    con=get_db()
    if request.method=='POST':
        try:
            categories={int(v) for v in request.form.getlist('category')}
            products={int(v) for v in request.form.getlist('ingredient')}
        except ValueError:abort(400)
        existing_c={r['id'] for r in con.execute('SELECT id FROM categories')}
        existing_i={r['id'] for r in con.execute('SELECT id FROM ingredients')}
        if not categories<=existing_c or not products<=existing_i:abort(400)
        with con:
            con.execute('DELETE FROM pantry WHERE user_id=?',(g.user['id'],))
            con.executemany('INSERT INTO pantry(user_id,category_id) VALUES (?,?)',[(g.user['id'],cid) for cid in categories])
            con.executemany('INSERT INTO pantry(user_id,ingredient_id) VALUES (?,?)',[(g.user['id'],iid) for iid in products])
        flash('Your shelf was saved.','success')
        return redirect(url_for('pantry'))
    pc,pi=pantry_ids(con,g.user['id']);maps=ingredient_category_maps(con)
    recipes=[recipe_card(con,r,pc,pi,maps) for r in all_recipes(con)]
    recipes.sort(key=lambda r:(len(r['missing']),r['title'].casefold()))
    return render_template('pantry.html',families=families(con),products=[dict(r) for r in con.execute('SELECT * FROM ingredients WHERE is_brand=1 ORDER BY name COLLATE NOCASE')],
                           selected_categories=pc,selected_products=pi,recipes=recipes,ready=sum(r['can_make'] for r in recipes))

@app.route('/ingredients',methods=['GET','POST'])
@login_required
def ingredients():
    con=get_db()
    if request.method=='POST':
        ing_id=request.form.get('ingredient_id',type=int)
        ing=con.execute('SELECT * FROM ingredients WHERE id=?',(ing_id,)).fetchone()
        if not ing:abort(404)
        cats={int(v) for v in request.form.getlist('category')}
        allowed={r['id'] for r in con.execute('SELECT id FROM categories')}
        if not cats or not cats<=allowed:abort(400)
        # Only admins or the creator can reclassify a shared item.
        if g.user['role']!='admin' and ing['added_by']!=g.user['id']:abort(403)
        with con:
            con.execute('DELETE FROM ingredient_categories WHERE ingredient_id=?',(ing_id,))
            con.executemany('INSERT INTO ingredient_categories VALUES(?,?)',[(ing_id,c) for c in cats])
        flash('Ingredient classifications updated.','success')
        return redirect(url_for('ingredients',edit=ing_id))
    cats,mapping,explicit,anc=ingredient_category_maps(con)
    return render_template('ingredients.html',categories=cats,ingredients=[dict(x,category_ids=explicit.get(x['id'],set())) for x in con.execute('SELECT * FROM ingredients ORDER BY name COLLATE NOCASE')],edit=request.args.get('edit',type=int))

@app.post('/categories/new')
@login_required
def category_new():
    con=get_db()
    try:
        name=required_string(request.form.get('name'),65)
        parent=request.form.get('parent_id',type=int)
        if parent and not con.execute('SELECT 1 FROM categories WHERE id=?',(parent,)).fetchone():raise ValueError('Invalid parent category')
        with con:con.execute('INSERT INTO categories(name,parent_id) VALUES (?,?)',(name,parent))
        flash('Category added.','success')
    except (ValueError,sqlite3.IntegrityError) as e:flash(str(e),'error')
    return redirect(url_for('ingredients'))

@app.get('/admin')
@admin_required
def admin():
    con=get_db()
    users=con.execute('SELECT id,username,email,role,active,must_change_password,created_at FROM users ORDER BY role,username').fetchall()
    invitations=con.execute('SELECT * FROM invitations ORDER BY created_at DESC LIMIT 35').fetchall()
    return render_template('admin.html',users=users,invitations=invitations,smtp_enabled=bool(os.getenv('SMTP_HOST')))

@app.post('/admin/users')
@admin_required
def user_create():
    try:
        username=required_string(request.form.get('username'),50)
        email=required_string(request.form.get('email'),150).lower()
        pw=required_string(request.form.get('password'),256)
        if '@' not in email or len(pw)<12:raise ValueError('Enter an email and a temporary password of at least 12 characters.')
        with get_db():get_db().execute('INSERT INTO users(username,email,password_hash,role,must_change_password) VALUES (?,?,?,?,1)',
                                       (username,email,generate_password_hash(pw),'member'))
        flash('Member created. Share the temporary password privately; the member must change it at first login.','success')
    except (ValueError,sqlite3.IntegrityError) as e:flash(str(e),'error')
    return redirect(url_for('admin'))

@app.post('/admin/users/<int:user_id>/toggle')
@admin_required
def user_toggle(user_id):
    if user_id==g.user['id']:abort(400,'You cannot deactivate yourself.')
    con=get_db();u=con.execute('SELECT id,role FROM users WHERE id=?',(user_id,)).fetchone()
    if not u:abort(404)
    if u['role']=='admin':abort(403,'Admins cannot deactivate other admins here.')
    with con:con.execute('UPDATE users SET active=1-active WHERE id=?',(user_id,))
    return redirect(url_for('admin'))

def smtp_invite(email,link):
    host=os.getenv('SMTP_HOST')
    if not host:return False
    msg=EmailMessage();msg['Subject']='Your invitation to The Cocktail Cabinet';msg['From']=os.getenv('SMTP_FROM','cocktails@localhost');msg['To']=email
    msg.set_content('You have been invited to The Cocktail Cabinet. This one-time link expires in 7 days:\n\n'+link+'\n')
    with smtplib.SMTP(host,int(os.getenv('SMTP_PORT','587')),timeout=12) as s:
        if os.getenv('SMTP_STARTTLS','true')=='true':s.starttls()
        if os.getenv('SMTP_USERNAME'):s.login(os.getenv('SMTP_USERNAME'),os.getenv('SMTP_PASSWORD',''))
        s.send_message(msg)
    return True

@app.post('/admin/invitations')
@admin_required
def invitation_create():
    email=request.form.get('email','').strip().lower()
    if not email or len(email)>150 or '@' not in email:
        flash('Enter a valid email for the invitation.','error');return redirect(url_for('admin'))
    token=secrets.token_urlsafe(32)
    with get_db():get_db().execute('INSERT INTO invitations(email,token_hash,expires_at,created_by) VALUES(?,?,?,?)',
                (email,token_hash(token),iso(now()+timedelta(days=7)),g.user['id']))
    link=url_for('invite',token=token,_external=True)
    sent=False
    try:sent=smtp_invite(email,link)
    except Exception: app.logger.exception('SMTP invitation failed')
    # Only display the plaintext token to the creating admin in this response, not saved to DB or logs.
    return render_template('invite_created.html',link=link,email=email,sent=sent)

@app.post('/admin/invitations/<int:invite_id>/revoke')
@admin_required
def invitation_revoke(invite_id):
    with get_db():get_db().execute('UPDATE invitations SET revoked_at=? WHERE id=? AND used_at IS NULL',(iso(now()),invite_id))
    return redirect(url_for('admin'))

@app.route('/invite/<token>',methods=['GET','POST'])
def invite(token):
    con=get_db()
    inv=con.execute('SELECT * FROM invitations WHERE token_hash=?',(token_hash(token),)).fetchone()
    if not inv or inv['used_at'] or inv['revoked_at'] or inv['expires_at']<=iso(now()):
        return render_template('auth.html',mode='expired'),410
    if request.method=='POST':
        try:
            username=required_string(request.form.get('username'),50)
            pw=required_string(request.form.get('password'),256)
            if len(pw)<12:raise ValueError('Password must have at least 12 characters.')
            with con:
                con.execute('BEGIN IMMEDIATE')
                invitation=con.execute('SELECT * FROM invitations WHERE id=?',(inv['id'],)).fetchone()
                if invitation['used_at'] or invitation['revoked_at'] or invitation['expires_at']<=iso(now()):abort(410)
                uid=con.execute('INSERT INTO users(username,email,password_hash,role) VALUES (?,?,?,?)',
                                (username,inv['email'],generate_password_hash(pw),'member')).lastrowid
                con.execute('UPDATE invitations SET used_at=?,used_by=? WHERE id=?',(iso(now()),uid,inv['id']))
            session.clear();session.permanent=True;session['user_id']=uid
            return redirect(url_for('index'))
        except (ValueError,sqlite3.IntegrityError) as e: flash(str(e),'error')
    return render_template('auth.html',mode='invite',invite=inv)

@app.get('/manifest.webmanifest')
def manifest():
    resp=app.send_static_file('manifest.webmanifest');resp.mimetype='application/manifest+json';return resp

@app.get('/sw.js')
def sw():
    resp=app.send_static_file('sw.js');resp.mimetype='text/javascript';resp.headers['Service-Worker-Allowed']='/';return resp

@app.errorhandler(413)
def too_large(_):
    return 'Image or request too large (4 MB maximum).',413

if __name__=='__main__': app.run(host='0.0.0.0',port=int(os.getenv('PORT','8080')),debug=False)
