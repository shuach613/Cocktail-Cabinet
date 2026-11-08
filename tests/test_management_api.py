"""Full REST/MCP management parity: atomicity, ownership and role security."""
from __future__ import annotations

import hashlib
import os
import tempfile

os.environ.setdefault("SECRET_KEY", "test_secret_" * 6)
os.environ.setdefault("SETUP_TOKEN", "first_setup_secret")
os.environ.setdefault("TESTING", "1")
os.environ.setdefault("COOKIE_SECURE", "false")
os.environ.setdefault("TRUST_PROXY", "false")
os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="cabinet-management-tests-"))

import pytest
from werkzeug.security import generate_password_hash

import app as module
import db
from api_schema import collection_revision, shelf_revision


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "cabinet.sqlite3")
    monkeypatch.setattr(module, "DATA_DIR", tmp_path)
    db.ensure_db()
    with db.connect() as con:
        admin_id = con.execute(
            "INSERT INTO users(username,email,password_hash,role) VALUES(?,?,?,?)",
            ("owner", "owner@example.com", generate_password_hash("correct horse battery staple"), "admin")
        ).lastrowid
        member_id = con.execute(
            "INSERT INTO users(username,email,password_hash,role) VALUES(?,?,?,?)",
            ("guest", "guest@example.com", generate_password_hash("guest strong password"), "member")
        ).lastrowid
        for user_id, secret, scope in [
            (admin_id, "ccb_admin_write", "write"),
            (admin_id, "ccb_admin_read", "read"),
            (member_id, "ccb_member_write", "write"),
        ]:
            con.execute(
                "INSERT INTO api_tokens(user_id,name,token_prefix,token_hash,scope) VALUES(?,?,?,?,?)",
                (user_id, "Test", secret[:16], hashlib.sha256(secret.encode()).hexdigest(), scope)
            )
    module.app.config.update(TESTING=True, SESSION_COOKIE_SECURE=False)
    with module.app.test_client() as c:
        yield c


def h(token="ccb_admin_write"):
    return {"Authorization": "Bearer " + token}


def api(client, method, path, payload=None, token="ccb_admin_write"):
    kw = {"headers": h(token)}
    if payload is not None:
        kw["json"] = payload
    return client.open("/api/v1" + path, method=method, **kw)


def mcp(client, name, data=None, token="ccb_admin_write"):
    res = client.post("/mcp", headers={
        **h(token),
        "Accept": "application/json, text/event-stream",
        "MCP-Protocol-Version": "2025-11-25",
    }, json={"jsonrpc":"2.0","id":1,"method":"tools/call","params":{
        "name":name,"arguments":data or {},
    }})
    assert res.status_code == 200
    return res.json["result"]


def cat(con, name):
    return con.execute("SELECT id FROM categories WHERE name=?", (name,)).fetchone()["id"]


def new_ingredient(client, name, category_id):
    result = api(client, "POST", "/ingredients", {
        "name":name,"is_brand":True,"categories":[category_id],
    })
    assert result.status_code == 201, result.json
    return result.json["data"]["id"]


def test_dependency_preview_rejects_unsafe_deletion_and_read_only_access(client):
    with db.connect() as con:
        gin = con.execute("SELECT id FROM ingredients WHERE name='Gin'").fetchone()["id"]
        spirit = cat(con, "Spirits")
        revision = collection_revision(con)
    preview = api(client, "GET", f"/ingredients/{gin}")
    assert preview.status_code == 200
    assert preview.json["data"]["recipe_item_count"] > 0
    assert api(client, "DELETE", f"/ingredients/{gin}").status_code == 409
    assert api(client, "DELETE", f"/categories/{spirit}").status_code == 409
    assert api(client, "DELETE", f"/ingredients/{gin}", token="ccb_member_write").status_code == 403
    assert api(client, "DELETE", f"/ingredients/{gin}", token="ccb_admin_read").status_code == 403
    assert api(client, "GET", f"/categories/{spirit}", token="ccb_member_write").status_code == 403
    assert api(client, "GET", "/users", token="ccb_member_write").status_code == 403
    assert api(client, "GET", "/users", token="ccb_admin_read").status_code == 200
    with db.connect() as con:
        assert collection_revision(con) == revision
        assert not con.execute("PRAGMA foreign_key_check").fetchall()


def test_ingredient_merge_retains_recipe_lines_classifications_and_all_user_shelves(client):
    with db.connect() as con:
        specialty = cat(con, "Specialty Gin")
        citrus = cat(con, "Citrus Gin")
    source = new_ingredient(client, "Test Source Gin", specialty)
    target = new_ingredient(client, "Test Target Gin", citrus)
    recipe = api(client, "POST", "/recipes", {
        "title":"Merge integrity test","technique":"Stirred","glass":"Rocks",
        "steps":["Stir and strain."],
        "items":[{"name":"Test Source Gin","quantity":"4 cl","kind":"ingredient","optional":False,
                  "is_brand":True}],
    })
    assert recipe.status_code == 201, recipe.json
    rid = recipe.json["data"]["id"]
    assert api(client,"POST","/shelf/items",{"ingredient_id":source,"owned":True}).status_code == 200
    with db.connect() as con:
        member = con.execute("SELECT id FROM users WHERE username='guest'").fetchone()["id"]
        con.execute("INSERT INTO pantry(user_id,ingredient_id) VALUES (?,?)",(member,source))
        before = con.execute(
            "SELECT id,quantity,sort_order,optional,kind FROM recipe_items WHERE recipe_id=?", (rid,)
        ).fetchone()
        revision = collection_revision(con)
    replaced = api(client,"POST",f"/ingredients/{source}/merge",{"target_id":target})
    assert replaced.status_code == 200, replaced.json
    assert replaced.json["data"]["recipe_items_moved"] == 1
    with db.connect() as con:
        after = con.execute(
            "SELECT id,ingredient_id,quantity,sort_order,optional,kind FROM recipe_items WHERE recipe_id=?", (rid,)
        ).fetchone()
        assert (after["id"],after["quantity"],after["sort_order"],after["optional"],after["kind"]) == tuple(before)
        assert after["ingredient_id"] == target
        assert con.execute("SELECT 1 FROM ingredients WHERE id=?",(source,)).fetchone() is None
        assert {r["category_id"] for r in con.execute(
            "SELECT category_id FROM ingredient_categories WHERE ingredient_id=?",(target,)
        )} == {specialty,citrus}
        assert con.execute("SELECT COUNT(*) FROM pantry WHERE ingredient_id=?",(target,)).fetchone()[0] == 2
        assert con.execute("SELECT COUNT(*) FROM pantry WHERE ingredient_id=?",(source,)).fetchone()[0] == 0
        assert collection_revision(con) > revision
        assert not con.execute("PRAGMA foreign_key_check").fetchall()


def test_category_merge_reparents_children_and_migrates_every_users_ownership(client):
    with db.connect() as con:
        parent = cat(con, "Gin")
    old = api(client,"POST","/categories",{"name":"Old Decorative Gin Category","parent_id":parent})
    dest = api(client,"POST","/categories",{"name":"New Decorative Gin Category","parent_id":parent})
    assert old.status_code == 201 and dest.status_code == 201
    source_id, target_id = old.json["data"]["id"], dest.json["data"]["id"]
    nested = api(client,"POST","/categories",{"name":"Old Decorative Subtype","parent_id":source_id})
    child = nested.json["data"]["id"]
    prod = new_ingredient(client,"Test Rare Decoration Gin",source_id)
    assert api(client,"POST","/shelf/items",{"category_id":source_id,"owned":True}).status_code == 200
    with db.connect() as con:
        user = con.execute("SELECT id FROM users WHERE username='guest'").fetchone()["id"]
        con.execute("INSERT INTO pantry(user_id,category_id) VALUES(?,?)",(user,source_id))
    p = api(client,"GET",f"/categories/{source_id}")
    assert p.json["data"]["owned_by_users"] == 2
    result = api(client,"POST",f"/categories/{source_id}/merge",{"target_id":target_id})
    assert result.status_code == 200,result.json
    with db.connect() as con:
        assert con.execute("SELECT parent_id FROM categories WHERE id=?",(child,)).fetchone()["parent_id"] == target_id
        assert con.execute("SELECT 1 FROM categories WHERE id=?",(source_id,)).fetchone() is None
        assert con.execute("SELECT COUNT(*) FROM pantry WHERE category_id=?",(target_id,)).fetchone()[0] == 2
        assert target_id in {x["category_id"] for x in con.execute(
            "SELECT category_id FROM ingredient_categories WHERE ingredient_id=?",(prod,)
        )}
        assert not con.execute("PRAGMA foreign_key_check").fetchall()


def test_atomic_batch_rollback_and_safe_delete_unreferenced_catalog(client):
    with db.connect() as con:
        gin = cat(con,"Gin")
        ingredient = con.execute("SELECT id FROM ingredients WHERE name='Gin'").fetchone()["id"]
    source = new_ingredient(client,"Disposable Demo Gin",gin)
    r = api(client,"POST","/catalog/batch",{"operations":[
        {"action":"update_ingredient","ingredient_id":source,"name":"Renamed Demo Gin"},
        {"action":"delete_ingredient","ingredient_id":ingredient},
    ]})
    assert r.status_code == 409,r.json
    with db.connect() as con:
        assert con.execute("SELECT name FROM ingredients WHERE id=?",(source,)).fetchone()["name"]=="Disposable Demo Gin"
    assert api(client,"DELETE",f"/ingredients/{source}").status_code == 200
    created = api(client,"POST","/categories",{"name":"Completely Unused Category","parent_id":gin})
    cid=created.json["data"]["id"]
    assert api(client,"DELETE",f"/categories/{cid}").status_code == 200
    with db.connect() as con:
        assert not con.execute("PRAGMA foreign_key_check").fetchall()


def test_category_cycle_and_unowned_brand_type_protection(client):
    with db.connect() as con:
        gin = cat(con,"Gin")
        specialty = cat(con,"Specialty Gin")
    assert api(client,"PATCH",f"/categories/{gin}",{"parent_id":specialty}).status_code==409
    source = new_ingredient(client,"Demote Me Test Gin",specialty)
    assert api(client,"POST","/shelf/items",{"ingredient_id":source,"owned":True}).status_code==200
    changed=api(client,"PATCH",f"/ingredients/{source}",{"is_brand":False})
    assert changed.status_code==409
    with db.connect() as con:
        assert con.execute("SELECT is_brand FROM ingredients WHERE id=?",(source,)).fetchone()["is_brand"]==1
    assert api(client,"PATCH",f"/ingredients/{source}",{"categories":[gin,specialty]}).status_code==200


def test_admin_user_invitation_token_and_member_access(client):
    assert api(client,"GET","/invitations",token="ccb_member_write").status_code==403
    result=api(client,"POST","/users",{"username":"third","email":"third@example.com",
                                      "temporary_password":"long temporary password"})
    assert result.status_code==201,result.json
    user_id=result.json["data"]["id"]
    assert api(client,"PATCH",f"/users/{user_id}",{"active":False}).status_code==200
    assert api(client,"PATCH",f"/users/{user_id}",{"role":"admin","active":True,"confirm_admin":True}).status_code==200
    assert api(client,"PATCH","/users/1",{"active":False}).status_code==409
    assert api(client,"POST",f"/users/{user_id}/reset-password",{
        "temporary_password":"a new safe password 123"}).status_code==200
    invite=api(client,"POST","/invitations",{"email":"person@example.com","send_email":False})
    assert invite.status_code==201,invite.json
    invitation_url=invite.json["data"]["invitation_url"]
    assert "/invite/" in invitation_url
    assert "person@example.com" not in invitation_url
    inv_id=invite.json["data"]["id"]
    assert api(client,"DELETE",f"/invitations/{inv_id}").status_code==200
    issued=api(client,"POST","/tokens",{"name":"Automation","scope":"read","expires_days":30})
    assert issued.status_code==201,issued.json
    new_token=issued.json["data"]["token"]
    assert api(client,"GET","/tokens").status_code==200
    assert new_token not in str(api(client,"GET","/tokens").json)
    assert api(client,"GET","/me",token=new_token).status_code==200
    assert api(client,"DELETE",f"/tokens/{issued.json['data']['id']}").status_code==200
    assert api(client,"GET","/me",token=new_token).status_code==401
    assert api(client,"PATCH","/me",{"username":"updated","current_password":"bad"}).status_code==409
    good=api(client,"PATCH","/me",{"username":"changed_owner","current_password":"correct horse battery staple"})
    assert good.status_code==200,good.json
    with db.connect() as con:
        assert not con.execute("PRAGMA foreign_key_check").fetchall()


def test_member_ownership_and_recipe_availability_and_secret_ops(client):
    recipes=api(client,"GET","/recommendations",token="ccb_member_write")
    assert recipes.status_code==200,recipes.json
    assert sum(len(group) for group in recipes.json["data"].values())==22
    response=api(client,"GET","/recipes/1/availability",token="ccb_member_write")
    assert response.status_code==200,response.json
    assert response.json["data"]["recipe_id"]==1
    assert api(client,"POST","/catalog/batch",{"operations":[
        {"action":"delete_category","category_id":99999}
    ]},token="ccb_member_write").status_code==403
    assert api(client,"POST","/invitations",{"email":"x@example.com"},token="ccb_member_write").status_code==403
    assert api(client,"POST","/users",{"username":"hack","email":"h@example.com",
                         "temporary_password":"strong password"},token="ccb_member_write").status_code==403
    assert api(client,"POST","/tokens",{"name":"reader","scope":"read","expires_days":30},
               token="ccb_member_write").status_code==201
    password=api(client,"POST","/me/password",{
        "current_password":"guest strong password",
        "new_password":"guest newer strong password",
    },token="ccb_member_write")
    assert password.status_code==200,password.json
    assert api(client,"GET","/me",token="ccb_member_write").status_code==401


def test_mcp_management_tool_parity_and_permission_failures(client):
    with db.connect() as con:
        gin = cat(con,"Gin")
    tool_list=client.post("/mcp",headers={
        **h(),"Accept":"application/json, text/event-stream",
        "MCP-Protocol-Version":"2025-11-25",
    },json={"jsonrpc":"2.0","id":1,"method":"tools/list"})
    assert tool_list.status_code==200
    names={x["name"] for x in tool_list.json["result"]["tools"]}
    assert {"merge_ingredients","merge_categories","delete_category",
            "batch_catalog_changes","get_recipe_availability","update_user",
            "create_invitation","get_shelf_recommendations"} <= names
    assert mcp(client,"list_users",token="ccb_member_write")["isError"]
    created=mcp(client,"create_category",{"name":"MCP Temporary Category","parent_id":gin})
    assert not created["isError"],created
    cid=created["structuredContent"]["data"]["id"]
    assert mcp(client,"delete_category",{"category_id":cid},token="ccb_member_write")["isError"]
    outcome=mcp(client,"delete_category",{"category_id":cid})
    assert not outcome["isError"],outcome
    assert mcp(client,"get_recipe_availability",{"recipe_id":1},token="ccb_admin_read")["isError"] is False
    result=mcp(client,"get_shelf_recommendations",token="ccb_member_write")
    assert not result["isError"],result


def test_openapi_covers_all_management_resources(client):
    spec=api(client,"GET","/openapi.json")
    assert spec.status_code==200
    paths=spec.json["paths"]
    for route,method in [
        ("/catalog/batch","post"),("/ingredients/{ingredient_id}","delete"),
        ("/ingredients/{ingredient_id}/merge","post"),
        ("/categories/{category_id}/merge","post"),
        ("/users","post"),("/users/{user_id}","patch"),
        ("/invitations","post"),("/tokens","post"),
        ("/recipes/{recipe_id}/availability","get"),("/recommendations","get"),
        ("/me/password","post"),
    ]:
        assert method in paths[route],(route,method)
