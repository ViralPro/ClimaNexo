"""Caché de doble nivel: memoria LRU+TTL + SQLite persistente con fallback stale."""

from __future__ import annotations

import hashlib
import json
import threading
import time
from collections import OrderedDict
from typing import Any, Optional

from app.db.repository import WeatherRepository


class CacheService:
    def __init__(self, repo: WeatherRepository | None = None, max_memory_items: int = 256) -> None:
        self.repo = repo or WeatherRepository()
        self.max_memory_items = max_memory_items
        self._mem: OrderedDict[str, tuple[float, Any]] = OrderedDict()
        self._lock = threading.RLock()
        self._mem_hits = 0
        self._db_hits = 0
        self._misses = 0

    @staticmethod
    def build_key(namespace: str, **params: Any) -> str:
        canonical = json.dumps(params, sort_keys=True, ensure_ascii=False, default=str)
        digest = hashlib.sha256(f"{namespace}:{canonical}".encode("utf-8")).hexdigest()[:24]
        return f"{namespace}:{digest}"

    def get(self, key: str, allow_stale: bool = False) -> Optional[Any]:
        now = time.time()
        with self._lock:
            item = self._mem.get(key)
            if item is not None:
                exp, val = item
                if exp >= now or allow_stale:
                    self._mem.move_to_end(key)
                    self._mem_hits += 1
                    return val
                self._mem.pop(key, None)

        db_entry = self.repo.get_cache_entry(key, allow_stale=allow_stale)
        if db_entry is not None:
            db_val, db_exp = db_entry
            self._db_hits += 1
            if db_exp >= now:
                with self._lock:
                    self._mem[key] = (min(db_exp, now + 60), db_val)
                    self._evict_if_needed()
            return db_val

        self._misses += 1
        return None

    def set(self, key: str, namespace: str, payload: Any, ttl_seconds: int) -> None:
        now = time.time()
        with self._lock:
            self._mem[key] = (now + ttl_seconds, payload)
            self._mem.move_to_end(key)
            self._evict_if_needed()
        self.repo.set_cache(key, namespace, payload, ttl_seconds)

    def clear_memory(self) -> None:
        with self._lock:
            self._mem.clear()

    def stats(self) -> dict[str, Any]:
        db_stats = self.repo.get_cache_stats()
        total_req = self._mem_hits + self._db_hits + self._misses
        hit_rate = round(((self._mem_hits + self._db_hits) / total_req) * 100, 1) if total_req else 0.0
        return {
            "memory_items": len(self._mem),
            "memory_hits": self._mem_hits,
            "db_hits": self._db_hits,
            "misses": self._misses,
            "hit_rate_pct": hit_rate,
            **db_stats,
        }

    def _evict_if_needed(self) -> None:
        while len(self._mem) > self.max_memory_items:
            self._mem.popitem(last=False)
