"""Additive API-token storage and collection revision tracking.

These tables and triggers are deliberately kept separate from the original
schema. Existing Synology Docker volumes are migrated in place on startup.
"""
from __future__ import annotations

API_SCHEMA = '''
CREATE TABLE IF NOT EXISTS api_tokens (
 id INTEGER PRIMARY KEY,
 user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 name TEXT NOT NULL,
 token_prefix TEXT NOT NULL,
 token_hash TEXT NOT NULL UNIQUE,
 scope TEXT NOT NULL CHECK(scope IN ('read','write')),
 expires_at TEXT,
 revoked_at TEXT,
 last_used_at TEXT,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_api_tokens_user ON api_tokens(user_id);
CREATE INDEX IF NOT EXISTS idx_api_tokens_hash ON api_tokens(token_hash);
INSERT OR IGNORE INTO settings(key,value) VALUES ('collection_revision','1');
'''

# Keep a monotonically increasing revision for changes made through *either*
# the web interface or API, including photo changes and deletions.
TRACKED_TABLES = ('recipes', 'recipe_items', 'ingredients', 'ingredient_categories', 'categories')
TRACKED_EVENTS = ('INSERT', 'UPDATE', 'DELETE')


def initialize_api_schema(con):
    con.executescript(API_SCHEMA)
    for table in TRACKED_TABLES:
        for event in TRACKED_EVENTS:
            sql = f'''CREATE TRIGGER IF NOT EXISTS revision_{table}_{event.lower()}
            AFTER {event} ON {table}
            BEGIN
              UPDATE settings SET value=CAST(value AS INTEGER)+1
               WHERE key='collection_revision';
            END;'''
            con.execute(sql)


def collection_revision(con):
    row = con.execute("SELECT value FROM settings WHERE key='collection_revision'").fetchone()
    return int(row['value']) if row else 0
