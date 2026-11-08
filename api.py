"""Authenticated v1 API for Cocktail Cabinet.

Every /api/v1 request requires a personal bearer token. Session cookies are
never accepted as API credentials, and permission checks are applied per user
and per token scope. User-facing token management uses the existing web login.
"""
from __future__ import annotations

import hashlib
import io
import json
import re
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from functools import wraps
from pathlib import Path
from uuid import uuid4

from flask import Blueprint, abort, flash, g, jsonify, redirect, render_template, request, send_file, send_from_directory, url_for
from PIL import Image, ImageOps, UnidentifiedImageError
from werkzeug.exceptions import HTTPException

from api_schema import collection_revision
import db as db_module
from db import get_db, ingredient_category_maps, recipe_card

API_PREFIX = '/api/v1'
MAX_UPLOAD_PIXELS = 20_000_000
TOKENS_MAX_PER_USER = 30


def utcnow():
    return datetime.now(timezone.utc)


def stamp(dt):
    return dt.isoformat(timespec='seconds')


def digest(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def is_uploaded_image(value):
    return isinstance(value, str) and bool(re.fullmatch(r'[a-f0-9]{32}\.webp', value))


def error(message, code):
    return jsonify({'error': {'message': message, 'status': code}}), code


def register_api(app):
    # Deferred imports avoid circular references during Flask's startup.
    from app import (login_required, required_string, save_recipe, create_pending_categories)

    bp = Blueprint('api_v1', __name__, url_prefix=API_PREFIX)

    @bp.before_request
    def authenticate():
        value = request.headers.get('Authorization', '')
        if not value.startswith('Bearer ') or not value[7:].strip():
            return unauthorized()
        token = value[7:].strip()
        if len(token) > 160 or not token.startswith('ccb_'):
            return unauthorized()
        con = get_db()
        record = con.execute('''SELECT t.*, u.username, u.email, u.role, u.active,
                         u.must_change_password FROM api_tokens t
                         JOIN users u ON u.id=t.user_id
                         WHERE t.token_hash=? AND t.revoked_at IS NULL''', (digest(token),)).fetchone()
        if (record is None or not record['active'] or record['must_change_password'] or
                (record['expires_at'] and record['expires_at'] <= stamp(utcnow()))):
            return unauthorized()
        # Never fall back to browser session identity; this is the token's user.
        g.user = con.execute('SELECT * FROM users WHERE id=?', (record['user_id'],)).fetchone()
        g.api_token = record
        if not record['last_used_at'] or record['last_used_at'] < stamp(utcnow() - timedelta(hours=1)):
            with con:
                con.execute('UPDATE api_tokens SET last_used_at=? WHERE id=?',
                            (stamp(utcnow()), record['id']))

    def unauthorized():
        response, status = error('A valid personal API bearer token is required.', 401)
        response.headers['WWW-Authenticate'] = 'Bearer realm="Cocktail Cabinet API"'
        return response, status

    def permitted(scope):
        def outer(fn):
            @wraps(fn)
            def inner(*args, **kwargs):
                if scope == 'write' and g.api_token['scope'] != 'write':
                    return error('This token has read-only access.', 403)
                return fn(*args, **kwargs)
            return inner
        return outer

    def admin_only():
        if g.user['role'] != 'admin':
            return error('Administrator access is required.', 403)
        return None

    def can_edit(record):
        return g.user['role'] == 'admin' or record['created_by'] == g.user['id']

    def get_recipe(con, recipe_id):
        return con.execute('''SELECT r.*,u.username AS author FROM recipes r
             LEFT JOIN users u ON u.id=r.created_by WHERE r.id=?''', (recipe_id,)).fetchone()

    def payload_recipe(con, rec):
        categories = ingredient_category_maps(con)
        card = recipe_card(con, rec, set(), set(), categories)
        items = []
        for item in card['items']:
            items.append({
                'id': item['id'], 'ingredient_id': item['ingredient_id'],
                'name': item['name'], 'display_name': item['display_name'],
                'quantity': item['quantity'], 'kind': item['kind'],
                'optional': bool(item['optional']), 'is_brand': bool(item['is_brand']),
                'category_ids': sorted(categories[2].get(item['ingredient_id'], set()))
            })
        return {
            'id': card['id'], 'title': card['title'],
            'technique': card['technique'], 'glass': card['glass'],
            'notes': card['notes'], 'steps': card['steps'], 'ingredients': items,
            'created_by': card['created_by'], 'created_at': card['created_at'],
            'updated_at': card['updated_at'], 'author': card.get('author'),
            'can_edit': can_edit(card),
            'image_url': url_for('api_v1.recipe_image', recipe_id=card['id']) if card.get('image_file') else None,
            'image_present': bool(card.get('image_file'))
        }

    def user_recipe_input(con, rec):
        card = recipe_card(con, rec, set(), set(), ingredient_category_maps(con))
        return {
            'title': rec['title'], 'technique': rec['technique'],
            'glass': rec['glass'], 'notes': rec['notes'], 'steps': card['steps'],
            'items': [{
                'name': v['name'], 'quantity': v['quantity'],
                'kind': v['kind'], 'optional': bool(v['optional']),
                'is_brand': bool(v['is_brand']),
                'categories': [],
            } for v in card['items']]
        }

    @bp.errorhandler(HTTPException)
    def handle_http_error(exc):
        if exc.code == 404:
            return error('Resource not found.', 404)
        if exc.code == 413:
            return error('Image or request too large (4 MB maximum).', 413)
        return error(exc.description, exc.code or 500)

    @bp.get('/me')
    @permitted('read')
    def me():
        return jsonify({'user': {'id': g.user['id'], 'username': g.user['username'],
                                 'role': g.user['role'], 'email': g.user['email']},
                        'token': {'id': g.api_token['id'], 'scope': g.api_token['scope'],
                                  'expires_at': g.api_token['expires_at']}})

    @bp.get('/recipes')
    @permitted('read')
    def recipes():
        con = get_db()
        try:
            page = int(request.args.get('page', '1'))
            per_page = int(request.args.get('per_page', '50'))
        except ValueError:
            return error('page and per_page must be integers.', 400)
        if page < 1 or not 1 <= per_page <= 100 or page > 1000000:
            return error('Use page >= 1 and per_page between 1 and 100.', 400)
        search = request.args.get('q', '').strip()
        technique = request.args.get('technique', '').strip()
        if len(search) > 120 or len(technique) > 50:
            return error('Search filters are too long.', 400)
        where = []
        args = []
        if search:
            where.append('''(r.title LIKE ? OR EXISTS (
                SELECT 1 FROM recipe_items ri JOIN ingredients i ON i.id=ri.ingredient_id
                WHERE ri.recipe_id=r.id AND i.name LIKE ?))''')
            args.extend([f'%{search}%', f'%{search}%'])
        if technique:
            where.append('r.technique=?')
            args.append(technique)
        where_sql = (' WHERE ' + ' AND '.join(where)) if where else ''
        total = con.execute('SELECT COUNT(*) AS n FROM recipes r' + where_sql, args).fetchone()['n']
        rows = con.execute('''SELECT r.*,u.username AS author FROM recipes r
                        LEFT JOIN users u ON u.id=r.created_by''' + where_sql +
                        ' ORDER BY r.title COLLATE NOCASE, r.id LIMIT ? OFFSET ?',
                        [*args, per_page, (page-1)*per_page]).fetchall()
        return jsonify({'data': [payload_recipe(con, rec) for rec in rows],
                        'pagination': {'page': page, 'per_page': per_page,
                                       'total': total, 'has_more': page*per_page < total},
                        'revision': collection_revision(con)})

    @bp.get('/recipes/<int:recipe_id>')
    @permitted('read')
    def recipe_detail(recipe_id):
        con = get_db()
        rec = get_recipe(con, recipe_id)
        if rec is None:
            abort(404)
        return jsonify({'data': payload_recipe(con, rec)})

    @bp.post('/recipes')
    @permitted('write')
    def recipe_create():
        try:
            payload = request.get_json(silent=True)
            if not isinstance(payload, dict):
                return error('Send a JSON object containing the recipe.', 400)
            recipe_id = save_recipe(get_db(), payload)
            rec = get_recipe(get_db(), recipe_id)
            response = jsonify({'data': payload_recipe(get_db(), rec)})
            response.status_code = 201
            response.headers['Location'] = url_for('api_v1.recipe_detail', recipe_id=recipe_id)
            return response
        except (ValueError, sqlite3.IntegrityError) as exc:
            return error(str(exc), 400)

    @bp.route('/recipes/<int:recipe_id>', methods=['PATCH', 'PUT'])
    @permitted('write')
    def recipe_update(recipe_id):
        con = get_db()
        rec = get_recipe(con, recipe_id)
        if rec is None:
            abort(404)
        if not can_edit(rec):
            return error('You can edit only your own recipes.', 403)
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return error('Send a JSON object.', 400)
        if request.method == 'PATCH':
            merged = user_recipe_input(con, rec)
            merged.update(payload)
            payload = merged
        try:
            save_recipe(con, payload, recipe_id)
            return jsonify({'data': payload_recipe(con, get_recipe(con, recipe_id))})
        except (ValueError, sqlite3.IntegrityError) as exc:
            return error(str(exc), 400)

    @bp.delete('/recipes/<int:recipe_id>')
    @permitted('write')
    def recipe_delete(recipe_id):
        con = get_db()
        rec = get_recipe(con, recipe_id)
        if rec is None:
            abort(404)
        if not can_edit(rec):
            return error('You can delete only your own recipes.', 403)
        with con:
            con.execute('DELETE FROM recipes WHERE id=?', (recipe_id,))
        if is_uploaded_image(rec['image_file']):
            try:
                (db_module.DATA_DIR / 'uploads' / rec['image_file']).unlink(missing_ok=True)
            except OSError:
                app.logger.warning('Cannot clean up old recipe photo', exc_info=True)
        return '', 204

    @bp.get('/recipes/<int:recipe_id>/image')
    @permitted('read')
    def recipe_image(recipe_id):
        rec = get_recipe(get_db(), recipe_id)
        if rec is None or not rec['image_file']:
            abort(404)
        value = rec['image_file']
        if isinstance(value,str) and value.startswith('seed_photos/'):
            name = value.removeprefix('seed_photos/')
            if not re.fullmatch(r'[a-z0-9-]+\.webp', name):
                abort(404)
            folder = Path(__file__).resolve().parent / 'seed' / 'photos'
        elif is_uploaded_image(value):
            name = value
            folder = db_module.DATA_DIR / 'uploads'
        else:
            abort(404)
        return send_from_directory(folder, name, mimetype='image/webp',
                                   as_attachment=request.args.get('download') == '1')

    @bp.post('/recipes/<int:recipe_id>/image')
    @permitted('write')
    def recipe_image_upload(recipe_id):
        con = get_db()
        rec = get_recipe(con, recipe_id)
        if rec is None:
            abort(404)
        if not can_edit(rec):
            return error('You can upload photos only for your own recipes.', 403)
        image = request.files.get('image')
        if not image:
            return error('Upload the photo as multipart field "image".', 400)
        try:
            with Image.open(image.stream) as im:
                if im.format not in ('JPEG', 'PNG', 'WEBP') or im.width*im.height > MAX_UPLOAD_PIXELS:
                    raise ValueError('Unsupported image format or dimensions.')
                normalized = ImageOps.exif_transpose(im)
                normalized.thumbnail((1200, 1200))
                output = normalized.convert('RGB')
                fname = uuid4().hex + '.webp'
                dest = db_module.DATA_DIR / 'uploads' / fname
                output.save(dest, 'WEBP', quality=85)
        except (UnidentifiedImageError, Image.DecompressionBombError, ValueError, OSError) as exc:
            return error('Use a valid JPEG, PNG or WebP image smaller than 4 MB.', 400)
        try:
            with con:
                con.execute('''UPDATE recipes SET image_file=?,updated_at=CURRENT_TIMESTAMP
                               WHERE id=?''', (fname, recipe_id))
        except Exception:
            dest.unlink(missing_ok=True)
            raise
        if is_uploaded_image(rec['image_file']):
            try:
                (db_module.DATA_DIR / 'uploads' / rec['image_file']).unlink(missing_ok=True)
            except OSError:
                app.logger.warning('Cannot clean up replaced photo', exc_info=True)
        response = jsonify({'data': {'recipe_id': recipe_id, 'image_url':
                           url_for('api_v1.recipe_image', recipe_id=recipe_id)}})
        response.status_code = 201
        return response

    @bp.delete('/recipes/<int:recipe_id>/image')
    @permitted('write')
    def recipe_image_delete(recipe_id):
        con = get_db()
        rec = get_recipe(con, recipe_id)
        if rec is None:
            abort(404)
        if not can_edit(rec):
            return error('You can remove photos only from your own recipes.', 403)
        with con:
            con.execute('''UPDATE recipes SET image_file=NULL,updated_at=CURRENT_TIMESTAMP
                           WHERE id=?''', (recipe_id,))
        if is_uploaded_image(rec['image_file']):
            (db_module.DATA_DIR / 'uploads' / rec['image_file']).unlink(missing_ok=True)
        return '', 204

    @bp.get('/ingredients')
    @permitted('read')
    def ingredients():
        con = get_db()
        cats, expanded, explicit, ancestors = ingredient_category_maps(con)
        category_names = {row['id']: row['name'] for row in cats}
        from db import branded_display_name
        return jsonify({'data': [
            {'id': ing['id'], 'name': ing['name'], 'is_brand': bool(ing['is_brand']),
             'display_name': branded_display_name(ing['name'], ing['is_brand'],
                             explicit.get(ing['id'], set()), cats, ancestors),
             'category_ids': sorted(explicit.get(ing['id'], set())),
             'categories': [category_names[cid] for cid in sorted(explicit.get(ing['id'],set()))]}
            for ing in con.execute('SELECT * FROM ingredients ORDER BY name COLLATE NOCASE')
            ]})

    @bp.post('/ingredients')
    @permitted('write')
    def ingredient_create():
        con = get_db()
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return error('Send an ingredient JSON object.', 400)
        try:
            name = required_string(payload.get('name'), 130)
            is_brand = bool(payload.get('is_brand', True))
            with con:
                refs = create_pending_categories(con, payload)
                categories = payload.get('categories', [])
                if not isinstance(categories, list) or not categories:
                    raise ValueError('Provide at least one ingredient category.')
                result = []
                for cat in categories:
                    if isinstance(cat, str) and cat.startswith('new:'):
                        if cat not in refs:
                            raise ValueError('Unknown new category.')
                        result.append(refs[cat])
                    else:
                        try:
                            result.append(int(cat))
                        except (TypeError, ValueError):
                            raise ValueError('Invalid category identifier.')
                all_cats = con.execute('SELECT id,parent_id FROM categories').fetchall()
                valid = {r['id'] for r in all_cats}
                if not set(result) <= valid:
                    raise ValueError('Unknown ingredient category.')
                if is_brand and not any(r['id'] in result and r['parent_id'] is not None for r in all_cats):
                    raise ValueError('Choose a specific category for branded ingredients.')
                ing_id = con.execute('INSERT INTO ingredients(name,is_brand,added_by) VALUES(?,?,?)',
                                     (name, int(is_brand), g.user['id'])).lastrowid
                con.executemany('INSERT INTO ingredient_categories(ingredient_id,category_id) VALUES(?,?)',
                                [(ing_id,cid) for cid in set(result)])
            return jsonify({'data': {'id': ing_id, 'name': name, 'category_ids': sorted(set(result))}}), 201
        except (ValueError, sqlite3.IntegrityError) as exc:
            return error(str(exc), 400)

    @bp.patch('/ingredients/<int:ingredient_id>')
    @permitted('write')
    def ingredient_update(ingredient_id):
        denied = admin_only()
        if denied:
            return denied
        con = get_db()
        ing = con.execute('SELECT * FROM ingredients WHERE id=?', (ingredient_id,)).fetchone()
        if ing is None:
            abort(404)
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return error('Send an ingredient JSON object.', 400)
        try:
            name = required_string(payload.get('name', ing['name']), 130)
            categories = payload.get('categories')
            with con:
                con.execute('UPDATE ingredients SET name=? WHERE id=?', (name,ingredient_id))
                if categories is not None:
                    if not isinstance(categories, list) or not categories:
                        raise ValueError('Provide category ids as a nonempty list.')
                    try:
                        ids = {int(v) for v in categories}
                    except (TypeError, ValueError):
                        raise ValueError('Invalid ingredient category identifier.')
                    valid = {r['id'] for r in con.execute('SELECT id FROM categories')}
                    if not ids <= valid:
                        raise ValueError('Invalid category id.')
                    con.execute('DELETE FROM ingredient_categories WHERE ingredient_id=?', (ingredient_id,))
                    con.executemany('INSERT INTO ingredient_categories(ingredient_id,category_id) VALUES(?,?)',
                                    [(ingredient_id, cid) for cid in ids])
            return jsonify({'data': {'id': ingredient_id, 'name': name}})
        except (ValueError, sqlite3.IntegrityError) as exc:
            return error(str(exc), 400)

    @bp.get('/categories')
    @permitted('read')
    def categories():
        con = get_db()
        return jsonify({'data': [dict(row) for row in con.execute(
            'SELECT id,name,parent_id FROM categories ORDER BY name COLLATE NOCASE')]})

    @bp.post('/categories')
    @permitted('write')
    def category_create():
        denied = admin_only()
        if denied:
            return denied
        con = get_db()
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return error('Send a category JSON object.', 400)
        try:
            name = required_string(payload.get('name'), 65)
            parent = payload.get('parent_id')
            if parent is not None and parent != '':
                parent = int(parent)
                if not con.execute('SELECT 1 FROM categories WHERE id=?', (parent,)).fetchone():
                    raise ValueError('Invalid parent category.')
            else:
                parent = None
            with con:
                cid = con.execute('INSERT INTO categories(name,parent_id) VALUES(?,?)',
                                  (name, parent)).lastrowid
            return jsonify({'data': {'id': cid, 'name': name, 'parent_id': parent}}), 201
        except (ValueError, sqlite3.IntegrityError) as exc:
            return error(str(exc), 400)

    @bp.patch('/categories/<int:category_id>')
    @permitted('write')
    def category_update(category_id):
        denied = admin_only()
        if denied:
            return denied
        con = get_db()
        row = con.execute('SELECT * FROM categories WHERE id=?', (category_id,)).fetchone()
        if row is None:
            abort(404)
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return error('Send a category JSON object.', 400)
        try:
            name = required_string(payload.get('name', row['name']), 65)
            parent = payload.get('parent_id', row['parent_id'])
            if parent not in (None, ''):
                parent = int(parent)
                ids = {category_id}
                while parent is not None:
                    if parent in ids:
                        raise ValueError('Cannot make a category its own descendant.')
                    ids.add(parent)
                    p = con.execute('SELECT parent_id FROM categories WHERE id=?', (parent,)).fetchone()
                    if p is None:
                        raise ValueError('Invalid parent category.')
                    parent = p['parent_id']
                parent = int(payload.get('parent_id', row['parent_id']))
            else:
                parent = None
            with con:
                con.execute('UPDATE categories SET name=?,parent_id=? WHERE id=?',
                            (name,parent,category_id))
            return jsonify({'data': {'id':category_id,'name':name,'parent_id':parent}})
        except (ValueError, sqlite3.IntegrityError) as exc:
            return error(str(exc), 400)

    @bp.get('/changes')
    @permitted('read')
    def changes():
        return jsonify({'revision': collection_revision(get_db())})

    @bp.get('/openapi.json')
    @permitted('read')
    def openapi_spec():
        return send_from_directory(Path(__file__).resolve().parent / 'static',
                                   'openapi.json', mimetype='application/json')

    app.register_blueprint(bp)

    # These pages rely on ordinary logged-in browser sessions and CSRF checks.
    @app.get('/settings/api')
    @login_required
    def api_access():
        tokens = get_db().execute('''SELECT id,name,token_prefix,scope,created_at,expires_at,
                         last_used_at,revoked_at FROM api_tokens WHERE user_id=?
                         ORDER BY created_at DESC,id DESC''', (g.user['id'],)).fetchall()
        return render_template('api_access.html', tokens=tokens)

    @app.post('/settings/api/tokens')
    @login_required
    def api_token_create():
        con = get_db()
        try:
            name = required_string(request.form.get('name'), 80)
            scope = request.form.get('scope', 'read')
            if scope not in ('read', 'write'):
                raise ValueError('Choose read-only or read/write access.')
            expiry = request.form.get('expires', '90')
            if expiry not in ('0','30','90','365'):
                raise ValueError('Choose a valid token expiration.')
            active = con.execute('''SELECT COUNT(*) AS n FROM api_tokens WHERE user_id=?
                    AND revoked_at IS NULL AND (expires_at IS NULL OR expires_at>?)''',
                    (g.user['id'], stamp(utcnow()))).fetchone()['n']
            if active >= TOKENS_MAX_PER_USER:
                raise ValueError('Maximum 30 active API tokens per account.')
            secret = 'ccb_' + secrets.token_urlsafe(40)
            expires = stamp(utcnow()+timedelta(days=int(expiry))) if expiry!='0' else None
            with con:
                con.execute('''INSERT INTO api_tokens(user_id,name,token_prefix,token_hash,scope,expires_at)
                               VALUES(?,?,?,?,?,?)''',
                            (g.user['id'],name,secret[:16],digest(secret),scope,expires))
            return render_template('api_token_created.html', token=secret, name=name,
                                   scope=scope, expires=expires)
        except (ValueError,sqlite3.IntegrityError) as exc:
            flash(str(exc), 'error')
            return redirect(url_for('api_access'))

    @app.post('/settings/api/tokens/<int:token_id>/revoke')
    @login_required
    def api_token_revoke(token_id):
        con = get_db()
        with con:
            con.execute('''UPDATE api_tokens SET revoked_at=?
                   WHERE id=? AND user_id=? AND revoked_at IS NULL''',
                   (stamp(utcnow()), token_id, g.user['id']))
        flash('API token revoked.', 'success')
        return redirect(url_for('api_access'))

    @app.get('/live/revision')
    @login_required
    def browser_revision():
        return jsonify({'revision': collection_revision(get_db())})
