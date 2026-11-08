# Cocktail Cabinet remote MCP connector

The connector is part of the same Flask/Gunicorn container as Cocktail Cabinet.
It uses the existing SQLite database and API authorization rules. **No extra
Docker Compose service, database, environment file, API-key setting, or port is
required.** Your current Synology reverse proxy must expose the app over HTTPS.

## Connect ChatGPT (OAuth account linking)

**Important authentication distinction:** ChatGPT's custom MCP connection flow
currently expects OAuth for a private user account and does **not** offer a
fixed-bearer-API-key input in its standard connection form. Cocktail Cabinet
therefore provides an OAuth 2.1 authorization-code/PKCE flow so you link your
existing Cocktail Cabinet account rather than paste a secret into ChatGPT.

1. Deploy the updated Cocktail Cabinet image using the unchanged `compose.yaml`.
2. Confirm your public HTTPS hostname reaches Cocktail Cabinet. The endpoint is
   `https://YOUR-COCKTAIL-DOMAIN/mcp`.
3. Open **ChatGPT → Plugins → + → Add custom MCP server** (if offered by your
   ChatGPT plan/workspace). Give it a name such as *My Cocktail Cabinet*.
4. Enter the `/mcp` HTTPS URL. Choose OAuth/automatic authentication. The
   server supplies metadata at `/.well-known/oauth-protected-resource` and
   `/.well-known/oauth-authorization-server`, plus dynamic client registration.
5. When the connection asks to link an account, sign in to Cocktail Cabinet and
   explicitly approve recipe read access and, if requested, write access. The
   OAuth code flow uses PKCE S256 and rotating refresh tokens. There is no need
   to give ChatGPT your password or personal API key.
6. Review the tools, install/enable the connection, and request a read-only
   operation before attempting changes. ChatGPT may request confirmation for
   recipe deletions or modifications.

**Account availability:** OpenAI currently documents full MCP write actions for
eligible Business/Enterprise/Edu workspaces. Other plans may not have the same
custom MCP features. If the custom-MCP creation option is absent in your UI,
this is a ChatGPT account-feature limitation, not a server defect.

## Use a personal API token in other MCP clients

The exact same `/mcp` URL also accepts the **personal API token** you generate
in **Cocktail Cabinet → Settings → API access**. Use a read-only or read/write
scope according to what your integration should do. Keep the token in the
client's secret store, never in GitHub, the Docker YAML or the URL.

Example generic Streamable HTTP MCP client configuration:

```json
{
  "mcpServers": {
    "cocktail-cabinet": {
      "type": "streamable-http",
      "url": "https://YOUR-COCKTAIL-DOMAIN/mcp",
      "headers": {
        "Authorization": "Bearer REPLACE_WITH_PERSONAL_API_TOKEN"
      }
    }
  }
}
```

Different clients have different authentication setup screens; prefer their
secure credential fields over writing the token into a plaintext config file.
This fixed bearer-token method **is not the ChatGPT custom-MCP OAuth setup**.

### Verify with curl (replace placeholders locally)

```bash
# Set your own values in your local shell, not in chat.
export CABINET_MCP_URL='https://YOUR-COCKTAIL-DOMAIN/mcp'
read -rsp 'Cocktail Cabinet personal API token: ' CABINET_TOKEN; echo

curl -fsS "$CABINET_MCP_URL" \
  -H "Authorization: Bearer $CABINET_TOKEN" \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"curl","version":"1"}}}'

curl -fsS "$CABINET_MCP_URL" \
  -H "Authorization: Bearer $CABINET_TOKEN" \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"list_recipes","arguments":{"per_page":20}}}'

unset CABINET_TOKEN
```

A valid response lists your actual recipes. The token is never transmitted in
query parameters or returned in MCP tool outputs.

## Available MCP tools

| Tool | Token scope | Action |
| --- | --- | --- |
| `get_account` | Read | Show account identity and permissions |
| `list_recipes`, `get_recipe` | Read | Search and retrieve complete recipes |
| `list_ingredients`, `list_categories` | Read | Inspect taxonomy |
| `download_recipe_image` | Read | Fetch private WebP photo as MCP image content |
| `create_recipe`, `update_recipe` | Write | Create/edit recipes using existing validation |
| `delete_recipe` | Write + owner/admin | Delete recipe; destructive |
| `create_ingredient` | Write | Add classified ingredient, optionally with new subcategory |
| `create_category` | Write + admin | Create a generic category |
| `upload_recipe_image` | Write + owner/admin | Add/replace JPEG, PNG or WebP photo (base64, max 4 MB) |
| `remove_recipe_image` | Write + owner/admin | Remove a photo; destructive |

All MCP operations are run through the already-tested **`/api/v1` REST API**.
This means disabling a user, downgrading/revoking their token, or restricting
ownership has immediate effect on MCP too. The app's existing live change
counter makes API/MCP edits visible in open PWA pages within approximately five
seconds. Uploaded files and recipes remain in the existing persistent volume.

## Security and operational notes

- Only **public HTTPS through your Synology reverse proxy** is suitable for
  ChatGPT's remote MCP connection. Don't forward bare HTTP to the Internet.
- The app accepts ChatGPT's known HTTPS OAuth redirect paths for dynamic client
  registration. Arbitrary callback hosts are rejected to prevent open redirects.
- OAuth authorization codes are single-use, expire after five minutes, and
  require **S256 PKCE**. Access tokens last one hour; refresh tokens rotate on
  each use. All bearer tokens and refresh tokens are stored as SHA-256 hashes.
- OAuth is per account, not a shared administrator key. Each linked client only
  receives the rights of the consenting account. Approval of write actions is
  performed in the web consent page, then subject to the normal app permissions.
- API-key clients bypass browser CSRF **only at `/mcp`**, where every call must
  present a valid bearer token. Browser forms still require CSRF tokens.
- Disconnect/revoke from the ChatGPT connection or **Settings → API access**.
  Revocation invalidates refresh credentials as well as the current access token.
- The MCP server is stateless and returns JSON responses instead of SSE for
  regular tool calls. It requires no WebSocket/SSE reverse-proxy settings or
  extra dependencies.
- If you replace the Docker image, the server will briefly disconnect while the
  container restarts. API keys and OAuth tokens are stored in the existing
  SQLite volume and remain valid after normal updates; no new admin setup.

### OpenAI Responses API alternative

If the ChatGPT custom plugin feature is not available on your plan, clients
using the OpenAI Responses API can call an MCP server with `server_url` and a
bearer credential. This requires separate OpenAI API access and is not the
same as an installed ChatGPT plugin.

## Troubleshoot: browser stays on Cocktail Cabinet after linking

When ChatGPT launches OAuth, **log in** if prompted, then look for the
**Connect Cocktail Cabinet** consent screen. Click **Allow connection** there:
only that button issues the authorization code and redirects to ChatGPT.
Signing in or opening the normal collection homepage is not enough.

If the login page returns to the ordinary homepage instead of the consent
screen, retry from ChatGPT after updating to a release containing the OAuth
login-continuation fix. The server now preserves the pending authorization
request through login and advertises RFC 9207 issuer identification, allowing
ChatGPT's stable `https://chatgpt.com/connector_platform_oauth_redirect` callback.
After upgrading, disconnect and recreate the ChatGPT connection to fetch the
updated OAuth metadata and exact callback URI. Test with a read-only tool first.

Never share the callback URL containing `code` or `state`, nor any access
or refresh tokens. For support, share only the **path** of the page where the
flow stops (e.g. `/login`, `/`, `/oauth/authorize`), not its query string.

## Chrome consent form does not return to ChatGPT

The site uses a strict Content Security Policy (`form-action 'self'`) for all
normal pages. In Chromium, this can block the **cross-origin 302 redirect**
after submitting the OAuth consent form, even if the original POST targets
Cocktail Cabinet. The result can look exactly like an inert **Allow connection**
button, while ChatGPT stays disconnected.

The `/oauth/authorize` **consent HTML** now uses a narrowly scoped CSP:
`form-action 'self' https://chatgpt.com`. It permits return to the registered
ChatGPT OAuth callback but still prevents all other cross-origin form targets.
The OAuth callback remains validated against the client-specific registration;
we do **not** allow arbitrary redirect hosts or weaken CSP elsewhere.

After merging and deploying, open a **new** OAuth connection attempt and click
**Allow connection**. The browser should return to ChatGPT. If it does not,
inspect Chrome DevTools → Console and Network for a CSP violation or a failed
`POST /oauth/authorize`, sharing only the status codes and safe error text.
Do not share a callback URL containing an OAuth `code`, `state` or tokens.

## Read and update the connected user's shelf

Use `get_my_shelf` to find a bottle or generic subcategory ID and its
current ownership flag. Use `set_shelf_item_owned` with
`{"ingredient_id":12,"owned":true}` for a brand or
`{"category_id":7,"owned":false}` for a generic category; never both IDs.

For a batch, `update_my_shelf` accepts `{"changes":[...]}` with at most
100 entries, validated and committed together. No other owned selections,
catalogue entries or recipes are modified. Permissions follow the connected
user and token read/write scope. The installed PWA detects edits through a
per-user revision counter.


## Full management API and ChatGPT tools

A read/write personal bearer token can now perform the website's data operations
through the REST API, with the **same user and administrator permission rules**.
The connected ChatGPT account also receives dedicated management tools after
reloading its MCP tool list.

For administrators, the connector includes:
`get_ingredient_details`, `get_category_details`, `update_ingredient`,
`delete_ingredient`, `merge_ingredients`, `update_category`,
`delete_category`, `merge_categories`, `batch_catalog_changes`,
`list_users`, `update_user`, `list_invitations`, `create_invitation`,
and `revoke_invitation`.

For members and administrators: `get_shelf_recommendations`,
`get_recipe_availability`, `list_api_tokens`, `revoke_api_token`, plus
the existing own-shelf, recipe and photo operations.

**Deletion is dependency-aware.** Deleting an ingredient referenced by a recipe
or owned on any user's shelf returns HTTP 409. Deleting a category with child
categories, classified ingredients or shelf owners also returns HTTP 409.
Review those references first, then use an explicit merge where appropriate.
Ingredient merging keeps every recipe row, measurement, optional flag, garnish
and preparation step, and transfers ownership to the retained product.
Category merging preserves classifications, descendants and per-user shelf
selections. Batch changes run as a single database transaction and fully roll
back if any step fails. No admin action can silently delete recipes.

**Private credentials remain REST-/website-only, not MCP tools.** The full API
also supports account password changes, personal API-token issuance/revocation,
member creation, temporary password resets and invitation administration.
Passwords, newly issued API secrets and temporary credentials should never be
entered into ChatGPT prompts. Administrative writes require both an active admin
account and a write-scoped token. Individual members can still update their own
shelves and own recipes but never another user's shelf or the shared taxonomy.

New REST routes are described in the authenticated [OpenAPI specification](../docs/openapi.json).
Refer to the [repository README](../README.md) for deployment. After publishing
this branch and updating the container, **refresh tools** for the installed
ChatGPT connection so it discovers the new MCP actions.
