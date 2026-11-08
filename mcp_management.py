"""Additional MCP tool schemas and deterministic routes for management API.

All tools call authenticated REST endpoints, so role/owner/scope checks live
in one place. Do not expose password- or secret-token-producing operations in
an LLM tool; those remain available over private REST or through the website.
"""
from __future__ import annotations


def management_tool_definitions():
    def integer(label):
        return {"type": "integer", "minimum": 1, "description": label}

    def string(label):
        return {"type": "string", "description": label}

    def boolean(label):
        return {"type": "boolean", "description": label}

    def entry(name, description, properties=None, required=(), write=False, destructive=False):
        return {
            "name": name, "description": description,
            "inputSchema": {
                "type": "object", "properties": properties or {},
                "required": list(required), "additionalProperties": False,
            },
            "annotations": {
                "readOnlyHint": not write,
                "destructiveHint": destructive,
                "idempotentHint": not write, "openWorldHint": False,
            },
            "_meta": {"securitySchemes": [{
                "type": "oauth2",
                "scopes": ["recipes:read", "recipes:write"] if write else ["recipes:read"],
            }]},
        }

    category_ids = {"type":"array","items":integer("Existing category ID"),"minItems":1}
    operations = {"type":"array","minItems":1,"maxItems":50,"items":{"type":"object",
        "description":"Catalog operation: action update_ingredient/delete_ingredient/merge_ingredient/update_category/delete_category/merge_category. Supply IDs and edit fields as applicable.",
        "properties":{
            "action":{"type":"string","enum":[
                "update_ingredient","delete_ingredient","merge_ingredient",
                "update_category","delete_category","merge_category"
            ]},
            "ingredient_id":integer("Ingredient ID"),"category_id":integer("Category ID"),
            "source_id":integer("Source to merge"),"target_id":integer("Retained destination ID"),
            "name":string("New name"),"categories":category_ids,
            "is_brand":boolean("Brand flag"),"parent_id":{"type":["integer","null"]}
        },"required":["action"],"additionalProperties":False}}
    return [
        entry("get_ingredient_details","Administrator-only: inspect an ingredient's recipe references and shelf owners before editing.",
              {"ingredient_id":integer("Ingredient ID")},("ingredient_id",)),
        entry("get_category_details","Administrator-only: inspect child categories, classified ingredients and shelf owners before deleting or merging.",
              {"category_id":integer("Category ID")},("category_id",)),
        entry("update_ingredient","Administrator-only: rename, change brand/generic flag, or replace an existing ingredient's category IDs without changing recipe lines.",
              {"ingredient_id":integer("Existing ingredient ID"),"name":string("Updated name"),
               "is_brand":boolean("Whether a named bottle"),"categories":category_ids},
              ("ingredient_id",),write=True),
        entry("delete_ingredient","Administrator-only: delete an UNUSED ingredient. Rejects if used by any recipe or owned by any user; inspect references first and confirm.",
              {"ingredient_id":integer("Unused ingredient ID")},("ingredient_id",),write=True,destructive=True),
        entry("merge_ingredients","Administrator-only: merge a redundant source ingredient into a target, retaining recipe quantities and transferring shelf ownership. Confirm first.",
              {"ingredient_id":integer("Source ingredient ID"),"target_id":integer("Retained ingredient ID")},
              ("ingredient_id","target_id"),write=True,destructive=True),
        entry("update_category","Administrator-only: rename or move a category in the hierarchy, preventing cycles.",
              {"category_id":integer("Existing category ID"),"name":string("Updated name"),
               "parent_id":{"type":["integer","null"],"description":"New parent; null means root"}},
              ("category_id",),write=True),
        entry("delete_category","Administrator-only: delete an empty, unowned category; rejects dependencies. Confirm first.",
              {"category_id":integer("Empty category ID")},("category_id",),write=True,destructive=True),
        entry("merge_categories","Administrator-only: merge a category into another, preserving ingredient classifications, child categories and user shelves. Confirm first.",
              {"category_id":integer("Source category ID"),"target_id":integer("Retained destination category ID")},
              ("category_id","target_id"),write=True,destructive=True),
        entry("batch_catalog_changes","Administrator-only: validate and apply up to 50 catalogue operations as one atomic transaction. All operations roll back if one fails. Confirm destructive operations.",
              {"operations":operations},("operations",),write=True,destructive=True),
        entry("get_shelf_recommendations","Find cocktails ready to make, missing one ingredient, or missing more based on the connected user's shelf."),
        entry("get_recipe_availability","Read matched/missing ingredients for one recipe using the connected user's personal shelf.",
              {"recipe_id":integer("Recipe ID")},("recipe_id",)),
        entry("list_users","Administrator-only: list members, roles and status without exposing passwords."),
        entry("update_user","Administrator-only: change a member's status, role, username or email. Revokes affected tokens on role/activation changes. Confirm sensitive changes.",
              {"user_id":integer("User ID"),"active":boolean("Enabled account"),
               "role":{"type":"string","enum":["member","admin"]},
               "confirm_admin":boolean("Explicitly confirm a promotion to administrator"),
               "username":string("Account name"),"email":string("Account email")},
              ("user_id",),write=True),
        entry("list_invitations","Administrator-only: inspect issued invitations, expiry and whether used/revoked. No raw invitation tokens."),
        entry("create_invitation","Administrator-only: create a single-use seven-day invitation URL. Optionally email it via configured SMTP; the secret link is returned once.",
              {"email":string("Invitee email"),"send_email":boolean("Send through configured SMTP if available")},
              ("email",),write=True),
        entry("revoke_invitation","Administrator-only: revoke an unused invitation. Confirm first.",
              {"invite_id":integer("Invitation ID")},("invite_id",),write=True,destructive=True),
        entry("list_api_tokens","List the connected user's token metadata and prefixes; never return token secrets."),
        entry("revoke_api_token","Revoke one of the connected user's tokens, invalidating linked refresh tokens too. Confirm first.",
              {"token_id":integer("Personal token ID")},("token_id",),write=True,destructive=True),
    ]


def management_route(name, args):
    """Return method, exact path and IDs removed from JSON payload, or None."""
    specs = {
        "get_ingredient_details": ("GET", "/api/v1/ingredients/{ingredient_id}", ("ingredient_id",)),
        "get_category_details": ("GET", "/api/v1/categories/{category_id}", ("category_id",)),
        "update_ingredient": ("PATCH", "/api/v1/ingredients/{ingredient_id}", ("ingredient_id",)),
        "delete_ingredient": ("DELETE", "/api/v1/ingredients/{ingredient_id}", ("ingredient_id",)),
        "merge_ingredients": ("POST", "/api/v1/ingredients/{ingredient_id}/merge", ("ingredient_id",)),
        "update_category": ("PATCH", "/api/v1/categories/{category_id}", ("category_id",)),
        "delete_category": ("DELETE", "/api/v1/categories/{category_id}", ("category_id",)),
        "merge_categories": ("POST", "/api/v1/categories/{category_id}/merge", ("category_id",)),
        "batch_catalog_changes": ("POST", "/api/v1/catalog/batch", ()),
        "get_shelf_recommendations": ("GET", "/api/v1/recommendations", ()),
        "get_recipe_availability": ("GET", "/api/v1/recipes/{recipe_id}/availability", ("recipe_id",)),
        "list_users": ("GET", "/api/v1/users", ()),
        "update_user": ("PATCH", "/api/v1/users/{user_id}", ("user_id",)),
        "list_invitations": ("GET", "/api/v1/invitations", ()),
        "create_invitation": ("POST", "/api/v1/invitations", ()),
        "revoke_invitation": ("DELETE", "/api/v1/invitations/{invite_id}", ("invite_id",)),
        "list_api_tokens": ("GET", "/api/v1/tokens", ()),
        "revoke_api_token": ("DELETE", "/api/v1/tokens/{token_id}", ("token_id",)),
    }
    if name not in specs:
        return None
    method, template, keys = specs[name]
    for key in keys:
        value = args.get(key)
        if type(value) is not int or value <= 0:
            raise ValueError(f"{key} must be a positive integer.")
        template = template.replace("{" + key + "}", str(value))
    return method, template, keys
