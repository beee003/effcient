"""
Local embeddings for Astrai Memory
Runs entirely on device - no API calls, no data leaves your machine
"""

import hashlib
from typing import Optional
import numpy as np

# Lazy load to avoid slow startup
_model = None
_model_name = "all-MiniLM-L6-v2"  # Fast, good quality, 384 dims
_use_mock = False  # Set to True if sentence-transformers not available

def get_model():
    """Lazy load the embedding model"""
    global _model, _use_mock
    if _model is None:
        try:
            from sentence_transformers import SentenceTransformer
            print(f"Loading embedding model: {_model_name}...")
            _model = SentenceTransformer(_model_name)
            print("Model loaded.")
        except ImportError:
            print("sentence-transformers not installed. Using simple hash-based embeddings.")
            print("For better results: pip install sentence-transformers")
            _use_mock = True
            _model = "mock"
    return _model

def _mock_embed(text: str) -> np.ndarray:
    """
    Simple hash-based embedding for testing.
    Not semantically meaningful, but allows testing the pipeline.
    """
    # Create deterministic pseudo-random embedding from text hash
    h = hashlib.sha256(text.encode()).digest()
    # Expand to 384 dimensions
    embedding = np.zeros(384, dtype=np.float32)
    for i in range(384):
        embedding[i] = (h[i % 32] / 255.0) * 2 - 1
    # Normalize
    embedding = embedding / np.linalg.norm(embedding)
    return embedding

def embed(text: str) -> np.ndarray:
    """Generate embedding for text (runs locally)"""
    model = get_model()
    if _use_mock or model == "mock":
        return _mock_embed(text)
    embedding = model.encode(text, convert_to_numpy=True)
    return embedding.astype(np.float32)

def embed_batch(texts: list[str]) -> np.ndarray:
    """Generate embeddings for multiple texts"""
    model = get_model()
    if _use_mock or model == "mock":
        return np.array([_mock_embed(t) for t in texts])
    embeddings = model.encode(texts, convert_to_numpy=True)
    return embeddings.astype(np.float32)

def content_hash(text: str) -> str:
    """Hash content for cache lookup"""
    return hashlib.sha256(text.encode()).hexdigest()[:16]

def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """Calculate cosine similarity between two vectors"""
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))

def embedding_to_bytes(embedding: np.ndarray) -> bytes:
    """Convert numpy array to bytes for SQLite storage"""
    return embedding.astype(np.float32).tobytes()

def bytes_to_embedding(data: bytes) -> np.ndarray:
    """Convert bytes back to numpy array"""
    return np.frombuffer(data, dtype=np.float32)

def get_embedding_dims() -> int:
    """Get the dimensionality of embeddings"""
    return 384  # all-MiniLM-L6-v2 produces 384-dim vectors
