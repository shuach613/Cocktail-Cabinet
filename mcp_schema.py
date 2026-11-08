"""Small, additive OAuth 2.1 storage for the private MCP bridge.

No new service, secrets or Docker volumes. All entries survive image updates in
Cocktail Cabinet's existing SQLite database; no raw OAuth tokens are stored.
"""

MCP_SCHEMA = '''
CREATE TABLE IF NOT EXISTS mcp_oauth_clients (
 client_id TEXT PRIMARY KEY,
 client_name TEXT NOT NULL,
 redirect_uris_json TEXT NOT NULL,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS mcp_oauth_codes (
 code_hash TEXT PRIMARY KEY,
 client_id TEXT NOT NULL REFERENCES mcp_oauth_clients(client_id) ON DELETE CASCADE,
 user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 redirect_uri TEXT NOT NULL,
 code_challenge TEXT NOT NULL,
 scopes TEXT NOT NULL,
 resource TEXT NOT NULL,
 expires_at TEXT NOT NULL,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS mcp_oauth_refresh (
 refresh_hash TEXT PRIMARY KEY,
 client_id TEXT NOT NULL REFERENCES mcp_oauth_clients(client_id) ON DELETE CASCADE,
 user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 access_id INTEGER REFERENCES api_tokens(id) ON DELETE SET NULL,
 scopes TEXT NOT NULL,
 resource TEXT NOT NULL,
 expires_at TEXT NOT NULL,
 revoked_at TEXT,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_mcp_oauth_codes_expiry ON mcp_oauth_codes(expires_at);
CREATE INDEX IF NOT EXISTS idx_mcp_oauth_refresh_user ON mcp_oauth_refresh(user_id);
'''


def initialize_mcp_schema(con):
    con.executescript(MCP_SCHEMA)
