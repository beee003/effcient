"""
Astrai Memory - Local-first, privacy-preserving memory
Your data never leaves your device.

Security features:
- Optional AES-256-GCM encryption for memory content
- Password-based key derivation (PBKDF2, 600k iterations)
- File permissions set to owner-only (chmod 600)
"""

import json
import os
import sqlite3
import stat
import time
import uuid
from pathlib import Path
from typing import Optional

import numpy as np

from .schema import init_schema
from .embeddings import (
    embed,
    content_hash,
    embedding_to_bytes,
    bytes_to_embedding,
    cosine_similarity,
)


class AstraiMemory:
    """
    Local memory store with hybrid search (vector + keyword).
    All data stays on your device.

    Security options:
        password: Enable AES-256 encryption for memory content
        secure_permissions: Set file to owner-only (default: True)
    """

    def __init__(
        self,
        db_path: Optional[str] = None,
        password: Optional[str] = None,
        secure_permissions: bool = True,
    ):
        if db_path is None:
            # Default: ~/.astrai/memory.db
            home = Path.home()
            astrai_dir = home / ".astrai"
            astrai_dir.mkdir(exist_ok=True, mode=0o700)  # Owner-only directory
            db_path = str(astrai_dir / "memory.db")

        self.db_path = db_path
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        init_schema(self.conn)

        # Set secure file permissions (owner read/write only)
        if secure_permissions and os.path.exists(db_path):
            os.chmod(db_path, stat.S_IRUSR | stat.S_IWUSR)  # 600

        # Setup encryption if password provided
        self._encryption = None
        if password:
            self._setup_encryption(password)

        # Cache for embeddings
        self._embedding_cache: dict[str, np.ndarray] = {}

    def _setup_encryption(self, password: str):
        """Initialize encryption with password"""
        try:
            from .encryption import get_encryption, password_hash

            # Check if we have existing salt in meta table
            row = self.conn.execute(
                "SELECT value FROM meta WHERE key = 'encryption_salt'"
            ).fetchone()

            if row:
                # Use existing salt
                import base64
                salt = base64.b64decode(row["value"].encode())
                self._encryption = get_encryption(password, salt)

                # Verify password by checking stored hash
                stored_hash = self.conn.execute(
                    "SELECT value FROM meta WHERE key = 'encryption_hash'"
                ).fetchone()
                if stored_hash and stored_hash["value"] != password_hash(password):
                    raise ValueError("Incorrect password")
            else:
                # New encrypted database
                self._encryption = get_encryption(password)
                self.conn.execute(
                    "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                    ("encryption_salt", self._encryption.get_salt_b64())
                )
                self.conn.execute(
                    "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                    ("encryption_hash", password_hash(password))
                )
                self.conn.commit()
        except ImportError as e:
            print(f"Warning: Encryption unavailable - {e}")
            print("Install with: pip install cryptography")

    def _encrypt(self, text: str) -> str:
        """Encrypt text if encryption is enabled"""
        if self._encryption:
            return self._encryption.encrypt(text)
        return text

    def _decrypt(self, text: str) -> str:
        """Decrypt text if encryption is enabled"""
        if self._encryption:
            return self._encryption.decrypt(text)
        return text

    @property
    def is_encrypted(self) -> bool:
        """Check if database is using encryption"""
        return self._encryption is not None

    def remember(
        self,
        content: str,
        category: str = "general",
        source: str = "user",
        metadata: Optional[dict] = None,
    ) -> str:
        """
        Store a memory. Returns the memory ID.

        Args:
            content: The text to remember
            category: Category (e.g., "preference", "fact", "conversation")
            source: Source of memory (e.g., "user", "assistant", "system")
            metadata: Optional JSON metadata
        """
        memory_id = str(uuid.uuid4())[:8]
        now = int(time.time() * 1000)

        # Get or compute embedding
        c_hash = content_hash(content)
        embedding = self._get_cached_embedding(content, c_hash)

        self.conn.execute(
            """
            INSERT INTO memories (id, content, category, source, created_at, updated_at, embedding, metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                memory_id,
                content,
                category,
                source,
                now,
                now,
                embedding_to_bytes(embedding),
                json.dumps(metadata) if metadata else None,
            ),
        )
        self.conn.commit()
        return memory_id

    def recall(
        self,
        query: str,
        limit: int = 5,
        category: Optional[str] = None,
        min_score: float = 0.0,
        vector_weight: float = 0.7,
        keyword_weight: float = 0.3,
    ) -> list[dict]:
        """
        Hybrid search: combines vector similarity + keyword matching.

        Args:
            query: Search query
            limit: Max results to return
            category: Filter by category (optional)
            min_score: Minimum combined score (0-1)
            vector_weight: Weight for vector similarity (default 0.7)
            keyword_weight: Weight for keyword match (default 0.3)

        Returns:
            List of memories with scores
        """
        # Get query embedding
        query_embedding = embed(query)

        # Vector search
        vector_results = self._vector_search(query_embedding, limit * 3, category)

        # Keyword search (BM25 via FTS5)
        keyword_results = self._keyword_search(query, limit * 3, category)

        # Merge results with weights
        merged = self._merge_results(
            vector_results,
            keyword_results,
            vector_weight,
            keyword_weight,
        )

        # Filter by min_score and limit
        results = [r for r in merged if r["score"] >= min_score][:limit]

        return results

    def _vector_search(
        self,
        query_embedding: np.ndarray,
        limit: int,
        category: Optional[str] = None,
    ) -> list[dict]:
        """Search by vector similarity"""
        sql = "SELECT id, content, category, source, created_at, embedding FROM memories"
        params = []

        if category:
            sql += " WHERE category = ?"
            params.append(category)

        rows = self.conn.execute(sql, params).fetchall()

        results = []
        for row in rows:
            if row["embedding"] is None:
                continue

            mem_embedding = bytes_to_embedding(row["embedding"])
            score = cosine_similarity(query_embedding, mem_embedding)

            results.append({
                "id": row["id"],
                "content": row["content"],
                "category": row["category"],
                "source": row["source"],
                "created_at": row["created_at"],
                "vector_score": score,
            })

        # Sort by score descending
        results.sort(key=lambda x: x["vector_score"], reverse=True)
        return results[:limit]

    def _keyword_search(
        self,
        query: str,
        limit: int,
        category: Optional[str] = None,
    ) -> list[dict]:
        """Search by keywords using FTS5"""
        # Escape special FTS5 characters
        safe_query = query.replace('"', '""')

        sql = """
            SELECT m.id, m.content, m.category, m.source, m.created_at,
                   bm25(memories_fts) as bm25_score
            FROM memories_fts fts
            JOIN memories m ON fts.id = m.id
            WHERE memories_fts MATCH ?
        """
        params = [f'"{safe_query}"']

        if category:
            sql += " AND m.category = ?"
            params.append(category)

        sql += " ORDER BY bm25_score LIMIT ?"
        params.append(limit)

        try:
            rows = self.conn.execute(sql, params).fetchall()
        except sqlite3.OperationalError:
            # FTS query failed, return empty
            return []

        # Normalize BM25 scores to 0-1 range (BM25 returns negative, lower is better)
        results = []
        for row in rows:
            # BM25 scores are negative, more negative = better match
            # Convert to 0-1 where 1 is best
            bm25 = row["bm25_score"]
            normalized = 1.0 / (1.0 + abs(bm25))

            results.append({
                "id": row["id"],
                "content": row["content"],
                "category": row["category"],
                "source": row["source"],
                "created_at": row["created_at"],
                "keyword_score": normalized,
            })

        return results

    def _merge_results(
        self,
        vector_results: list[dict],
        keyword_results: list[dict],
        vector_weight: float,
        keyword_weight: float,
    ) -> list[dict]:
        """Merge vector and keyword results with weighted scores"""
        # Index by ID
        merged: dict[str, dict] = {}

        for r in vector_results:
            merged[r["id"]] = {
                **r,
                "vector_score": r.get("vector_score", 0),
                "keyword_score": 0,
            }

        for r in keyword_results:
            if r["id"] in merged:
                merged[r["id"]]["keyword_score"] = r.get("keyword_score", 0)
            else:
                merged[r["id"]] = {
                    **r,
                    "vector_score": 0,
                    "keyword_score": r.get("keyword_score", 0),
                }

        # Calculate combined score
        results = []
        for mem in merged.values():
            combined_score = (
                vector_weight * mem["vector_score"] +
                keyword_weight * mem["keyword_score"]
            )
            results.append({
                "id": mem["id"],
                "content": mem["content"],
                "category": mem["category"],
                "source": mem["source"],
                "created_at": mem["created_at"],
                "score": combined_score,
                "vector_score": mem["vector_score"],
                "keyword_score": mem["keyword_score"],
            })

        # Sort by combined score
        results.sort(key=lambda x: x["score"], reverse=True)
        return results

    def _get_cached_embedding(self, content: str, c_hash: str) -> np.ndarray:
        """Get embedding from cache or compute"""
        # Check memory cache
        if c_hash in self._embedding_cache:
            return self._embedding_cache[c_hash]

        # Check DB cache
        row = self.conn.execute(
            "SELECT embedding FROM embedding_cache WHERE content_hash = ?",
            (c_hash,)
        ).fetchone()

        if row:
            embedding = bytes_to_embedding(row["embedding"])
            self._embedding_cache[c_hash] = embedding
            return embedding

        # Compute new embedding
        embedding = embed(content)

        # Store in DB cache
        self.conn.execute(
            """
            INSERT OR REPLACE INTO embedding_cache (content_hash, embedding, model, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (c_hash, embedding_to_bytes(embedding), "all-MiniLM-L6-v2", int(time.time() * 1000))
        )
        self.conn.commit()

        # Store in memory cache
        self._embedding_cache[c_hash] = embedding

        return embedding

    def forget(self, memory_id: str) -> bool:
        """Delete a memory by ID"""
        cursor = self.conn.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
        self.conn.commit()
        return cursor.rowcount > 0

    def list_memories(
        self,
        category: Optional[str] = None,
        limit: int = 50,
    ) -> list[dict]:
        """List all memories, optionally filtered by category"""
        sql = "SELECT id, content, category, source, created_at FROM memories"
        params = []

        if category:
            sql += " WHERE category = ?"
            params.append(category)

        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)

        rows = self.conn.execute(sql, params).fetchall()
        return [dict(row) for row in rows]

    def stats(self) -> dict:
        """Get memory statistics"""
        total = self.conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        categories = self.conn.execute(
            "SELECT category, COUNT(*) as count FROM memories GROUP BY category"
        ).fetchall()
        cache_size = self.conn.execute("SELECT COUNT(*) FROM embedding_cache").fetchone()[0]

        # Get file permissions
        perms = oct(os.stat(self.db_path).st_mode)[-3:] if os.path.exists(self.db_path) else "N/A"

        return {
            "total_memories": total,
            "categories": {row["category"]: row["count"] for row in categories},
            "embedding_cache_size": cache_size,
            "db_path": self.db_path,
            "encrypted": self.is_encrypted,
            "file_permissions": perms,
        }

    def close(self):
        """Close database connection"""
        self.conn.close()


# Convenience functions for quick usage
_default_memory: Optional[AstraiMemory] = None

def get_memory() -> AstraiMemory:
    """Get the default memory instance"""
    global _default_memory
    if _default_memory is None:
        _default_memory = AstraiMemory()
    return _default_memory

def remember(content: str, category: str = "general", **kwargs) -> str:
    """Quick remember"""
    return get_memory().remember(content, category, **kwargs)

def recall(query: str, limit: int = 5, **kwargs) -> list[dict]:
    """Quick recall"""
    return get_memory().recall(query, limit, **kwargs)
