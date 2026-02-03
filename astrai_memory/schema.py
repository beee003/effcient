"""
SQLite schema for Astrai Memory
Inspired by moltbot's memory architecture
"""

SCHEMA = """
-- Metadata table
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- Memory entries (the actual memories)
CREATE TABLE IF NOT EXISTS memories (
    id TEXT PRIMARY KEY,
    content TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT 'general',
    source TEXT NOT NULL DEFAULT 'user',
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    embedding BLOB,
    metadata TEXT
);

-- FTS5 virtual table for keyword search
CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
    content,
    category UNINDEXED,
    id UNINDEXED,
    tokenize='porter'
);

-- Triggers to keep FTS in sync
CREATE TRIGGER IF NOT EXISTS memories_insert AFTER INSERT ON memories BEGIN
    INSERT INTO memories_fts(id, content, category)
    VALUES (new.id, new.content, new.category);
END;

CREATE TRIGGER IF NOT EXISTS memories_delete AFTER DELETE ON memories BEGIN
    DELETE FROM memories_fts WHERE id = old.id;
END;

CREATE TRIGGER IF NOT EXISTS memories_update AFTER UPDATE ON memories BEGIN
    DELETE FROM memories_fts WHERE id = old.id;
    INSERT INTO memories_fts(id, content, category)
    VALUES (new.id, new.content, new.category);
END;

-- Indexes
CREATE INDEX IF NOT EXISTS idx_memories_category ON memories(category);
CREATE INDEX IF NOT EXISTS idx_memories_created ON memories(created_at);
CREATE INDEX IF NOT EXISTS idx_memories_source ON memories(source);

-- Embedding cache (avoid re-embedding same content)
CREATE TABLE IF NOT EXISTS embedding_cache (
    content_hash TEXT PRIMARY KEY,
    embedding BLOB NOT NULL,
    model TEXT NOT NULL,
    created_at INTEGER NOT NULL
);
"""

def init_schema(conn):
    """Initialize the database schema"""
    conn.executescript(SCHEMA)
    conn.commit()
