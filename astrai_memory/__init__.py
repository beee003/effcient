"""
Astrai Memory - Local-first, privacy-preserving AI memory

Your data stays on YOUR device. We can't see it. But your AI remembers everything.

Usage:
    from astrai_memory import remember, recall

    # Store a memory
    remember("User prefers dark mode", category="preference")

    # Recall relevant memories
    results = recall("What are user's UI preferences?")
"""

from .memory import (
    AstraiMemory,
    get_memory,
    remember,
    recall,
)

__all__ = [
    "AstraiMemory",
    "get_memory",
    "remember",
    "recall",
]

__version__ = "0.1.0"
