"""Authenticated API parity for catalogue, shelf, member and admin operations.

All routes are registered on the *existing* api_v1 blueprint, inheriting its
personal bearer-token check and read/write permission enforcement.
"""
from __future__ import annotations

import hashlib
import secrets
import sqlite3
from datetime import timedelta

from flask import abort, g, jsonify, request, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from catalog_management import (
    CatalogConflict, positive_id, strict_bool, get_ingredient, get_category,
    counts_ingredient, counts_category, update_ingredient, delete_ingredient,
    merge_ingredient, update_category, delete_category, merge_category, catalog_batch,
)
from db import get_db, ingredient_category_maps, recipe_card


def err(message, status):
    return jsonify({"error": {"status": status, "message": str(message)}}), status


def json_object(allowed=None, required=()):
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ValueError("Request body must be a JSON object.")
    if allowed is not None and set(data) - set(allowed):
        raise ValueError("Unknown request field.")
    if any(k not in data for k in required):
        raise ValueError("Missing required request fields: " + ", ".join(k for k in required if k not in data))
    return data


def name_value(value, label, maximum):
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > maximum:
        raise ValueError(f"{label} must contain 1–{maximum} characters.")
    return value.strip()


def execute(op):
    """Keep all mutations transactional, mapping dependency conflicts to 409."""
    con = get_db()
    try:
        with con:
            result = op(con)
        return jsonify({"data": result})
    except CatalogConflict as exc:
        return err(str(exc), 409)
    except LookupError as exc:
        return err(str(exc), 404)
    except (ValueError, KeyError) as exc:
        return err(str(exc), 400)
    except sqlite3.IntegrityError as exc:
        return err("Conflicting data or database reference: " + str(exc), 409)


def register_management_routes(bp, permitted, admin_only):
    from api import digest, stamp, utcnow, TOKENS_MAX_PER_USER

    def denied():
        return admin_only()

    # Catalogue metadata makes a deletion/merge review possible *before* edits.
    @bp.get('/ingredients/<int:ingredient_id>')
    @permitted('read')
    def catalog_ingredient_detail(ingredient_id):
        if (response := denied()): return response
        con = get_db()
        try:
            item = get_ingredient(con, ingredient_id)
            refs = counts_ingredient(con, ingredient_id)
        except (ValueError, LookupError) as exc:
            return err(str(exc), 404)
        return jsonify({"data": {**dict(item), **refs}})

    @bp.get('/categories/<int:category_id>')
    @permitted('read')
    def catalog_category_detail(category_id):
        if (response := denied()): return response
        try:
            item = get_category(get_db(), category_id)
            refs = counts_category(get_db(), category_id)
        except (ValueError, LookupError) as exc:
            return err(str(exc), 404)
        return jsonify({"data": {**dict(item), **refs}})

    # Existing PATCH routes in api.py delegate to these same operations.
    @bp.post('/ingredients/<int:ingredient_id>/merge')
    @permitted('write')
    def catalog_ingredient_merge(ingredient_id):
        if (response := denied()): return response
        def op(con):
            body = json_object({"target_id"}, ("target_id",))
            return merge_ingredient(con, ingredient_id, body["target_id"])
        return execute(op)

    @bp.delete('/ingredients/<int:ingredient_id>')
    @permitted('write')
    def catalog_ingredient_delete(ingredient_id):
        if (response := denied()): return response
        return execute(lambda con: delete_ingredient(con, ingredient_id))

    @bp.post('/categories/<int:category_id>/merge')
    @permitted('write')
    def catalog_category_merge(category_id):
        if (response := denied()): return response
        def op(con):
            body = json_object({"target_id"}, ("target_id",))
            return merge_category(con, category_id, body["target_id"])
        return execute(op)

    @bp.delete('/categories/<int:category_id>')
    @permitted('write')
    def catalog_category_delete(category_id):
        if (response := denied()): return response
        return execute(lambda con: delete_category(con, category_id))

    @bp.post('/catalog/batch')
    @permitted('write')
    def catalog_transaction():
        if (response := denied()): return response
        def op(con):
            body = json_object({"operations"}, ("operations",))
            return catalog_batch(con, body["operations"])
        return execute(op)

    @bp.get('/recommendations')
    @permitted('read')
    def shelf_recommendations():
        from app import all_recipes, pantry_ids
        con = get_db()
        categories, ingredients = pantry_ids(con, g.user['id'])
        mappings = ingredient_category_maps(con)
        groups = {"ready": [], "missing_one": [], "missing_more": []}
        for recipe in all_recipes(con):
            card = recipe_card(con, recipe, categories, ingredients, mappings)
            preview = {
                "id": card["id"], "title": card["title"],
                "match_count": card["match_count"],
                "required_count": card["required_count"],
                "missing": [
                    {"ingredient_id": row["ingredient_id"], "name": row["display_name"]}
                    for row in card["missing"]
                ],
            }
            key = "ready" if not card["missing"] else ("missing_one" if len(card["missing"]) == 1 else "missing_more")
            groups[key].append(preview)
        return jsonify({"data": groups})

    @bp.get('/recipes/<int:recipe_id>/availability')
    @permitted('read')
    def recipe_availability(recipe_id):
        from app import pantry_ids
        con = get_db()
        row = con.execute("SELECT * FROM recipes WHERE id=?", (recipe_id,)).fetchone()
        if not row: abort(404)
        categories, ingredients = pantry_ids(con, g.user["id"])
        card = recipe_card(con, row, categories, ingredients, ingredient_category_maps(con))
        return jsonify({"data": {
            "recipe_id": recipe_id, "can_make": card["can_make"],
            "match_count": card["match_count"], "required_count": card["required_count"],
            "ingredients": [
                {"id": item["ingredient_id"], "name": item["display_name"],
                 "matched": bool(item["matched"]), "optional": bool(item["optional"]),
                 "kind": item["kind"]}
                for item in card["items"]
            ],
        }})

    # Personal account: per-user self-service, with password proof for sensitive
    # changes. This intentionally does not expose any password in GET responses.
    @bp.patch('/me')
    @permitted('write')
    def update_me():
        def op(con):
            body = json_object({"username", "email", "current_password"})
            if not body or not set(body).intersection({"username", "email"}):
                raise ValueError("Provide username and/or email.")
            if not isinstance(body.get("current_password"), str) or not check_password_hash(
                g.user["password_hash"], body["current_password"]
            ):
                raise CatalogConflict("Current password is required for identity changes.")
            username = name_value(body.get("username", g.user["username"]), "username", 50)
            email = name_value(body.get("email", g.user["email"]), "email", 150).lower()
            if "@" not in email:
                raise ValueError("Provide a valid email.")
            con.execute("UPDATE users SET username=?,email=? WHERE id=?", (username, email, g.user["id"]))
            return {"id": g.user["id"], "username": username, "email": email}
        return execute(op)

    @bp.post('/me/password')
    @permitted('write')
    def change_own_password():
        def op(con):
            body = json_object({"current_password", "new_password"}, ("current_password", "new_password"))
            if not isinstance(body["current_password"], str) or not check_password_hash(
                g.user["password_hash"], body["current_password"]
            ):
                raise CatalogConflict("Current password did not match.")
            password = name_value(body["new_password"], "new_password", 256)
            if len(password) < 12:
                raise ValueError("Password must have at least 12 characters.")
            con.execute("UPDATE users SET password_hash=?,must_change_password=0 WHERE id=?",
                        (generate_password_hash(password), g.user["id"]))
            # Existing web sessions and remote tokens should not silently retain
            # access after a password change through an integration.
            con.execute("UPDATE api_tokens SET revoked_at=? WHERE user_id=? AND revoked_at IS NULL",
                        (stamp(utcnow()), g.user["id"]))
            con.execute("UPDATE mcp_oauth_refresh SET revoked_at=? WHERE user_id=? AND revoked_at IS NULL",
                        (stamp(utcnow()), g.user["id"]))
            return {"password_changed": True, "tokens_revoked": True}
        return execute(op)

    # Personal API token management parallels Settings > API access. Token
    # secrets are returned *once* on issue and never listed subsequently.
    @bp.get('/tokens')
    @permitted('read')
    def personal_tokens():
        records = get_db().execute(
            """SELECT id,name,token_prefix,scope,created_at,expires_at,last_used_at,revoked_at
               FROM api_tokens WHERE user_id=? ORDER BY id DESC""", (g.user["id"],)
        ).fetchall()
        return jsonify({"data": [dict(x) for x in records]})

    @bp.post('/tokens')
    @permitted('write')
    def issue_personal_token():
        def op(con):
            body = json_object({"name", "scope", "expires_days"}, ("name", "scope"))
            name = name_value(body["name"], "name", 80)
            scope = body["scope"]
            expires_days = body.get("expires_days", 90)
            if scope not in ("read", "write") or type(expires_days) is not int or expires_days not in (0, 30, 90, 365):
                raise ValueError("scope must be read/write; expires_days must be 0,30,90,365.")
            active = con.execute("""SELECT COUNT(*) FROM api_tokens WHERE user_id=? AND revoked_at IS NULL
                    AND (expires_at IS NULL OR expires_at>?)""", (g.user["id"], stamp(utcnow()))).fetchone()[0]
            if active >= TOKENS_MAX_PER_USER:
                raise CatalogConflict("Maximum active API tokens reached.")
            secret = "ccb_" + secrets.token_urlsafe(40)
            expires = stamp(utcnow()+timedelta(days=expires_days)) if expires_days else None
            token_id = con.execute(
                """INSERT INTO api_tokens(user_id,name,token_prefix,token_hash,scope,expires_at)
                   VALUES(?,?,?,?,?,?)""",
                (g.user["id"], name, secret[:16], digest(secret), scope, expires)
            ).lastrowid
            return {"id": token_id, "name": name, "scope": scope, "expires_at": expires,
                    "token": secret, "warning": "This secret will not be shown again."}
        response = execute(op)
        return (response, 201) if not isinstance(response, tuple) else response

    @bp.delete('/tokens/<int:token_id>')
    @permitted('write')
    def revoke_personal_token(token_id):
        def op(con):
            found = con.execute("SELECT id FROM api_tokens WHERE id=? AND user_id=?", (token_id,g.user["id"])).fetchone()
            if not found:
                raise LookupError("Token not found.")
            con.execute("UPDATE api_tokens SET revoked_at=? WHERE id=? AND user_id=? AND revoked_at IS NULL",
                        (stamp(utcnow()), token_id, g.user["id"]))
            con.execute("UPDATE mcp_oauth_refresh SET revoked_at=? WHERE access_id=? AND user_id=? AND revoked_at IS NULL",
                        (stamp(utcnow()), token_id, g.user["id"]))
            return {"revoked_token_id": token_id}
        return execute(op)

    # Administrative identity/invitation actions. Passwords and full tokens are
    # never included in list responses. Members cannot query other users.
    @bp.get('/users')
    @permitted('read')
    def management_users():
        if (response := denied()): return response
        users = get_db().execute(
            """SELECT id,username,email,role,active,must_change_password,created_at
               FROM users ORDER BY role,username COLLATE NOCASE"""
        ).fetchall()
        return jsonify({"data": [dict(row) for row in users]})

    @bp.post('/users')
    @permitted('write')
    def management_create_user():
        if (response := denied()): return response
        def op(con):
            body = json_object({"username", "email", "temporary_password", "role", "confirm_admin"},
                               ("username", "email", "temporary_password"))
            username = name_value(body["username"], "username", 50)
            email = name_value(body["email"], "email", 150).lower()
            password = name_value(body["temporary_password"], "temporary_password", 256)
            role = body.get("role", "member")
            if "@" not in email or len(password) < 12 or role not in ("member", "admin"):
                raise ValueError("Provide valid email, password of at least 12 characters, and member/admin role.")
            if role == "admin" and body.get("confirm_admin") is not True:
                raise ValueError("To create an admin, explicitly set confirm_admin=true.")
            user_id = con.execute(
                """INSERT INTO users(username,email,password_hash,role,must_change_password)
                   VALUES (?,?,?,?,1)""",
                (username,email,generate_password_hash(password),role)
            ).lastrowid
            return {"id": user_id, "username": username, "email": email, "role": role,
                    "must_change_password": True}
        response = execute(op)
        return (response, 201) if not isinstance(response, tuple) else response

    @bp.patch('/users/<int:user_id>')
    @permitted('write')
    def management_update_user(user_id):
        if (response := denied()): return response
        def op(con):
            body = json_object({"active", "role", "username", "email", "confirm_admin"})
            if not body:
                raise ValueError("Provide at least one field.")
            row = con.execute(
                "SELECT id,active,role,username,email FROM users WHERE id=?", (user_id,)
            ).fetchone()
            if not row:
                raise LookupError("User not found.")
            active = strict_bool(body["active"], "active") if "active" in body else bool(row["active"])
            role = body.get("role", row["role"])
            if role not in ("admin", "member"):
                raise ValueError("role must be admin or member.")
            if row["role"] != "admin" and role == "admin" and body.get("confirm_admin") is not True:
                raise ValueError("Promoting a member to admin requires confirm_admin=true.")
            if user_id == g.user["id"] and (not active or role != "admin"):
                raise CatalogConflict("Cannot deactivate or demote your own administrator account.")
            if row["role"] == "admin" and (role != "admin" or not active):
                n = con.execute("SELECT COUNT(*) FROM users WHERE role='admin' AND active=1").fetchone()[0]
                if n <= 1:
                    raise CatalogConflict("At least one active administrator must remain.")
            username = name_value(body.get("username", row["username"]), "username", 50)
            email = name_value(body.get("email", row["email"]), "email", 150).lower()
            if "@" not in email:
                raise ValueError("Provide a valid email.")
            con.execute("UPDATE users SET username=?,email=?,active=?,role=? WHERE id=?",
                        (username,email,int(active),role,user_id))
            if not active or role != row["role"]:
                # No lingering elevated or disabled-user API sessions.
                con.execute("UPDATE api_tokens SET revoked_at=? WHERE user_id=? AND revoked_at IS NULL",
                            (stamp(utcnow()),user_id))
                con.execute("UPDATE mcp_oauth_refresh SET revoked_at=? WHERE user_id=? AND revoked_at IS NULL",
                            (stamp(utcnow()),user_id))
            return {"id": user_id,"username":username,"email":email,"active":active,"role":role}
        return execute(op)

    @bp.post('/users/<int:user_id>/reset-password')
    @permitted('write')
    def management_reset_password(user_id):
        if (response := denied()): return response
        def op(con):
            body = json_object({"temporary_password"}, ("temporary_password",))
            password = name_value(body["temporary_password"], "temporary_password", 256)
            if len(password) < 12:
                raise ValueError("Temporary password must have at least 12 characters.")
            if user_id == g.user["id"]:
                raise CatalogConflict("Use /me/password to change your own password.")
            found = con.execute("SELECT id FROM users WHERE id=?", (user_id,)).fetchone()
            if not found:
                raise LookupError("User not found.")
            con.execute("UPDATE users SET password_hash=?,must_change_password=1 WHERE id=?",
                        (generate_password_hash(password),user_id))
            con.execute("UPDATE api_tokens SET revoked_at=? WHERE user_id=? AND revoked_at IS NULL",
                        (stamp(utcnow()),user_id))
            con.execute("UPDATE mcp_oauth_refresh SET revoked_at=? WHERE user_id=? AND revoked_at IS NULL",
                        (stamp(utcnow()),user_id))
            return {"id": user_id, "must_change_password": True,"tokens_revoked":True}
        return execute(op)

    @bp.get('/invitations')
    @permitted('read')
    def management_invitations():
        if (response := denied()): return response
        rows = get_db().execute(
            """SELECT id,email,expires_at,used_at,revoked_at,used_by,created_by,created_at
               FROM invitations ORDER BY id DESC LIMIT 200"""
        ).fetchall()
        return jsonify({"data": [dict(row) for row in rows]})

    @bp.post('/invitations')
    @permitted('write')
    def management_create_invitation():
        if (response := denied()): return response
        def op(con):
            body = json_object({"email", "send_email"}, ("email",))
            email = name_value(body["email"], "email", 150).lower()
            send_email = strict_bool(body.get("send_email", False), "send_email")
            if "@" not in email:
                raise ValueError("Provide a valid email.")
            token = secrets.token_urlsafe(32)
            from app import iso, now
            invite_id = con.execute(
                "INSERT INTO invitations(email,token_hash,expires_at,created_by) VALUES(?,?,?,?)",
                (email,digest(token),iso(now()+timedelta(days=7)),g.user["id"])
            ).lastrowid
            return {"id": invite_id, "email": email,
                    "invitation_url": url_for("invite",token=token,_external=True),
                    "send_email": send_email}
        reply = execute(op)
        if isinstance(reply, tuple):
            return reply
        data = reply.get_json()["data"]
        if data.pop("send_email", False):
            try:
                from app import smtp_invite
                data["email_sent"] = smtp_invite(data["email"],data["invitation_url"])
            except Exception:
                data["email_sent"] = False
                data["email_warning"] = "Could not send email; use the invitation_url."
        return jsonify({'data': data}), 201

    @bp.delete('/invitations/<int:invite_id>')
    @permitted('write')
    def management_revoke_invitation(invite_id):
        if (response := denied()): return response
        def op(con):
            row = con.execute("SELECT id,used_at FROM invitations WHERE id=?", (invite_id,)).fetchone()
            if not row: raise LookupError("Invitation not found.")
            if row["used_at"]: raise CatalogConflict("An already-used invitation cannot be revoked.")
            from app import iso, now
            con.execute("UPDATE invitations SET revoked_at=? WHERE id=? AND revoked_at IS NULL",
                        (iso(now()),invite_id))
            return {"revoked_invitation_id":invite_id}
        return execute(op)
