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


# A per-user revision counter avoids refreshing one member's PWA because
# another member changed their private shelf. Web and API writes share triggers.
SHELF_REVISION_SCHEMA = '''
CREATE TABLE IF NOT EXISTS shelf_revisions (
 user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
 revision INTEGER NOT NULL DEFAULT 0
);
CREATE TRIGGER IF NOT EXISTS shelf_revision_insert
 AFTER INSERT ON pantry
 BEGIN
 INSERT INTO shelf_revisions(user_id,revision) VALUES(NEW.user_id,1)
 ON CONFLICT(user_id) DO UPDATE SET revision=revision+1;
 END;
CREATE TRIGGER IF NOT EXISTS shelf_revision_delete
 AFTER DELETE ON pantry
 BEGIN
 INSERT INTO shelf_revisions(user_id,revision) VALUES(OLD.user_id,1)
 ON CONFLICT(user_id) DO UPDATE SET revision=revision+1;
 END;
CREATE TRIGGER IF NOT EXISTS shelf_revision_update
 AFTER UPDATE ON pantry
 BEGIN
 INSERT INTO shelf_revisions(user_id,revision) VALUES(OLD.user_id,1)
 ON CONFLICT(user_id) DO UPDATE SET revision=revision+1;
 INSERT INTO shelf_revisions(user_id,revision) VALUES(NEW.user_id,1)
 ON CONFLICT(user_id) DO UPDATE SET revision=revision+1;
 END;
'''

# Keep a monotonically increasing revision for changes made through *either*
# the web interface or API, including photo changes and deletions.
TRACKED_TABLES = ('recipes', 'recipe_items', 'ingredients', 'ingredient_categories', 'categories')
TRACKED_EVENTS = ('INSERT', 'UPDATE', 'DELETE')


def initialize_api_schema(con):
    con.executescript(API_SCHEMA)
    con.executescript(SHELF_REVISION_SCHEMA)
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


def shelf_revision(con,user_id):
    """Monotonic revision for the connected user's shelf (zero if untouched)."""
    row=con.execute('SELECT revision FROM shelf_revisions WHERE user_id=?',(user_id,)).fetchone()
    return int(row['revision']) if row else 0
