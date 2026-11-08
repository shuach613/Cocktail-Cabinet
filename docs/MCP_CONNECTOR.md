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
