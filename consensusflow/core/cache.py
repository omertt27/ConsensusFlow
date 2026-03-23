"""
cache.py — In-memory and persistent LRU cache for ConsensusFlow.

Caches (model, system_prompt_hash, user_prompt) → LLM response dict
so identical requests during a session skip the LLM entirely.

Two backends:
  • MemoryCache   — fast per-process TTL dict (default)
  • NullCache     — no-op, used when caching is disabled

Usage::

    from consensusflow.core.cache import MemoryCache
    cache = MemoryCache(maxsize=512, ttl_seconds=3600)

    key = cache.make_key("gpt-4o", system, user)
    hit = await cache.get(key)
    if hit is None:
        hit = await llm_call(...)
        await cache.set(key, hit)
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from collections import OrderedDict
from typing import Any, Optional

log = logging.getLogger("consensusflow.cache")


def _hash(text: str) -> str:
    """SHA-256 hex of a string (first 16 chars for readability)."""
    return hashlib.sha256(text.encode()).hexdigest()[:16]


class NullCache:
    """No-op cache — always returns None (cache disabled)."""

    def make_key(self, model: str, system: str, user: str) -> str:  # noqa: D401
        return f"{model}:{_hash(system)}:{_hash(user)}"

    async def get(self, key: str) -> Optional[dict]:
        return None

    async def set(self, key: str, value: dict) -> None:  # noqa: D401
        pass

    async def clear(self) -> None:
        pass

    @property
    def size(self) -> int:
        return 0


class MemoryCache:
    """
    Thread-safe asyncio-compatible LRU cache with per-entry TTL.

    Parameters
    ----------
    maxsize : int
        Maximum number of cached entries before LRU eviction.
    ttl_seconds : float
        Seconds until a cached entry is considered stale.
    """

    def __init__(self, maxsize: int = 256, ttl_seconds: float = 3600.0) -> None:
        self._maxsize     = maxsize
        self._ttl         = ttl_seconds
        self._store: OrderedDict[str, tuple[dict, float]] = OrderedDict()
        self._lock        = asyncio.Lock()
        self._hits        = 0
        self._misses      = 0

    # ── Public API ───────────────────────────────────────────────────────────

    def make_key(self, model: str, system: str, user: str) -> str:
        """Build a deterministic cache key from model name + prompt content."""
        combined = f"{model}\x00{system}\x00{user}"
        return _hash(combined) + ":" + model

    async def get(self, key: str) -> Optional[dict]:
        """Return cached value if present and not expired, else None."""
        async with self._lock:
            if key not in self._store:
                self._misses += 1
                return None
            value, inserted_at = self._store[key]
            if time.monotonic() - inserted_at > self._ttl:
                del self._store[key]
                self._misses += 1
                return None
            # Move to end (most recently used)
            self._store.move_to_end(key)
            self._hits += 1
            log.debug("Cache HIT  key=%s  hits=%d  misses=%d", key[:20], self._hits, self._misses)
            return dict(value)  # return a copy

    async def set(self, key: str, value: dict) -> None:
        """Store a value; evict LRU entry when at capacity."""
        async with self._lock:
            if key in self._store:
                self._store.move_to_end(key)
            self._store[key] = (dict(value), time.monotonic())
            while len(self._store) > self._maxsize:
                evicted_key, _ = self._store.popitem(last=False)
                log.debug("Cache EVICT key=%s", evicted_key[:20])

    async def clear(self) -> None:
        """Remove all cached entries."""
        async with self._lock:
            self._store.clear()
            self._hits   = 0
            self._misses = 0

    @property
    def size(self) -> int:
        return len(self._store)

    @property
    def stats(self) -> dict[str, Any]:
        total = self._hits + self._misses
        rate  = self._hits / total if total else 0.0
        return {
            "size":       self.size,
            "maxsize":    self._maxsize,
            "ttl":        self._ttl,
            "hits":       self._hits,
            "misses":     self._misses,
            "hit_rate":   round(rate, 4),
        }

    def export_snapshot(self) -> list[dict]:
        """
        Export non-expired entries as a list of dicts for inspection
        or warm-start seeding.  Not async — call only from a sync context
        or protect externally.
        """
        now = time.monotonic()
        return [
            {
                "key":        k,
                "value":      v,
                "age_seconds": round(now - ts, 1),
            }
            for k, (v, ts) in self._store.items()
            if now - ts <= self._ttl
        ]
