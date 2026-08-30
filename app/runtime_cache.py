from __future__ import annotations

import asyncio
import copy
import time
from collections.abc import Awaitable, Callable
from typing import Any


class StaleSnapshot:
    """Small in-process stale-while-revalidate cache for expensive UI snapshots.

    Normal navigation should never wait for a full filesystem/API sweep when a
    usable recent snapshot already exists.  Once stale, the previous snapshot
    is returned immediately and one background refresh is started.  `force=True`
    is reserved for explicit refresh actions/tests.
    """

    def __init__(self, ttl: float = 30.0):
        self.ttl = float(ttl)
        self._value: Any = None
        self._updated = 0.0
        self._lock = asyncio.Lock()
        self._task: asyncio.Task | None = None

    @staticmethod
    def _clone(value: Any) -> Any:
        try:
            return copy.deepcopy(value)
        except Exception:
            return value

    @property
    def age(self) -> float:
        if not self._updated:
            return 0.0
        return max(0.0, time.monotonic() - self._updated)

    def clear(self) -> None:
        self._value = None
        self._updated = 0.0
        task = self._task
        if task and not task.done():
            task.cancel()
        self._task = None

    async def _refresh(self, loader: Callable[[], Awaitable[Any]]) -> Any:
        async with self._lock:
            value = await loader()
            self._value = value
            self._updated = time.monotonic()
            return self._clone(value)

    def _start_background_refresh(self, loader: Callable[[], Awaitable[Any]]) -> None:
        if self._task and not self._task.done():
            return

        async def runner():
            try:
                await self._refresh(loader)
            except asyncio.CancelledError:
                raise
            except Exception:
                # Keep serving the last known-good snapshot. The calling route
                # owns user-visible/logged error handling for cold loads.
                return

        self._task = asyncio.create_task(runner())

    async def get(self, loader: Callable[[], Awaitable[Any]], *, force: bool = False) -> tuple[Any, int, bool]:
        """Return `(value, age_seconds, refreshing)`.

        A stale value is returned immediately while a refresh runs in the
        background.  With no previous value (or force=True) the caller waits for
        a fresh snapshot.
        """
        if self._value is not None and not force:
            age = self.age
            if age < self.ttl:
                return self._clone(self._value), int(age), False
            self._start_background_refresh(loader)
            return self._clone(self._value), int(age), True

        value = await self._refresh(loader)
        return value, 0, False
