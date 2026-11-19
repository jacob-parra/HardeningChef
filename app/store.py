"""Short-lived, in-memory storage for generated scripts served at /s/<token>.

Tokens are 144-bit random values, so links cannot be guessed or enumerated.
Entries expire after a TTL and the store is capped, oldest evicted first.
Everything lives in process memory: restarting the container clears it, and the
app must run as a single process (one uvicorn worker) for links to resolve.
"""

from __future__ import annotations

import secrets
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass


@dataclass
class Entry:
    script: str
    sha256: str
    filename: str
    expires: float


class ScriptStore:
    def __init__(self, ttl: int, max_entries: int):
        self.ttl = ttl
        self.max_entries = max_entries
        self._items: OrderedDict[str, Entry] = OrderedDict()
        self._lock = threading.Lock()

    def _purge(self, now: float) -> None:
        for token in [t for t, e in self._items.items() if e.expires <= now]:
            del self._items[token]
        while len(self._items) > self.max_entries:
            self._items.popitem(last=False)

    def put(self, script: str, sha256: str, filename: str) -> tuple[str, float]:
        token = secrets.token_urlsafe(18)
        now = time.time()
        expires = now + self.ttl
        with self._lock:
            self._items[token] = Entry(script, sha256, filename, expires)
            self._purge(now)
        return token, expires

    def get(self, token: str) -> Entry | None:
        now = time.time()
        with self._lock:
            self._purge(now)
            return self._items.get(token)

    def __len__(self) -> int:
        with self._lock:
            self._purge(time.time())
            return len(self._items)
