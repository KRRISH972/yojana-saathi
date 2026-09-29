"""In-memory sliding-window rate limits that protect the small free Gemini quota.

Once the app is public, anyone could send messages in a loop and use up the whole day's
free quota in minutes. Two limits apply to every chat message: one per visitor (by IP
address) and one global, so rotating addresses cannot get around the global cap either.

The limits live in memory, so they reset when the server restarts; that is fine for a
single small server, and it needs no database.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable
from functools import lru_cache

from backend.app.config import get_settings

WINDOW_SECONDS = 60.0
_MAX_TRACKED_KEYS = 10_000  # forget idle visitors beyond this, so memory stays bounded


class RateLimitExceeded(Exception):
    """Too many messages; the caller should wait before trying again."""


class SlidingWindowRateLimiter:
    """Allows at most ``max_requests`` per key within any ``window_seconds`` window."""

    def __init__(
        self, max_requests: int, window_seconds: float = WINDOW_SECONDS, clock: Callable[[], float] = time.monotonic
    ) -> None:
        """Create an empty limiter. ``clock`` is injectable for tests."""
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._clock = clock
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()  # FastAPI runs sync routes in a thread pool

    def allow(self, key: str) -> bool:
        """Record one request for ``key`` and return whether it is within the limit."""
        now = self._clock()
        with self._lock:
            hits = self._hits.setdefault(key, deque())
            while hits and hits[0] <= now - self.window_seconds:
                hits.popleft()
            if len(hits) >= self.max_requests:
                return False
            hits.append(now)
            if len(self._hits) > _MAX_TRACKED_KEYS:
                self._forget_idle(now)
            return True

    def _forget_idle(self, now: float) -> None:
        """Drop keys with no request inside the current window (caller holds the lock)."""
        cutoff = now - self.window_seconds
        for key in [k for k, hits in self._hits.items() if not hits or hits[-1] <= cutoff]:
            del self._hits[key]


@lru_cache
def _limiters() -> tuple[SlidingWindowRateLimiter, SlidingWindowRateLimiter]:
    """The shared (per-visitor, global) limiters, sized from configuration."""
    settings = get_settings()
    return (
        SlidingWindowRateLimiter(settings.chat_rate_limit_per_minute),
        SlidingWindowRateLimiter(settings.chat_global_rate_limit_per_minute),
    )


def check_chat_rate_limit(visitor_key: str) -> None:
    """Raise RateLimitExceeded if this visitor, or everyone together, is over the limit."""
    per_visitor, global_limit = _limiters()
    if not per_visitor.allow(visitor_key):
        raise RateLimitExceeded("Too many messages. Please wait a minute and try again.")
    if not global_limit.allow("*"):
        raise RateLimitExceeded("Many people are using Yojana Saathi right now. Please try again in a minute.")
