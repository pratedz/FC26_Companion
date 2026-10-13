"""Bounded, per-planner caches. No credentials or live game data on disk."""
from __future__ import annotations

from collections import OrderedDict
from copy import deepcopy
from dataclasses import asdict
import hashlib
import json
from threading import Lock
from typing import Any


class SquadProposalCache:
    def __init__(self, capacity: int = 256, *, scope: str = "") -> None:
        self.capacity = max(1, capacity)
        self.scope = scope
        self._values: OrderedDict[str, Any] = OrderedDict()
        self._owners: OrderedDict[str, int] = OrderedDict()
        self._lock = Lock()

    def key(self, kind: str, *parts: Any) -> str:
        payload = json.dumps((self.scope, *parts), sort_keys=True, default=str, separators=(",", ":"))
        return kind + ":" + hashlib.sha256(payload.encode()).hexdigest()

    def proposal_key(self, row: Any, recipe: Any, request: str, model: str) -> str:
        key = self.key("proposal", row, asdict(recipe), request, model)
        with self._lock:
            self._owners[key] = int(row["playerid"])
            self._owners.move_to_end(key)
            while len(self._owners) > self.capacity * 2:
                self._owners.popitem(last=False)
        return key

    def invalidate_players(self, playerids) -> None:
        """Retry affected players while retaining successful teammate proposals."""
        wanted = {int(pid) for pid in playerids}
        with self._lock:
            for key, pid in list(self._owners.items()):
                if pid in wanted:
                    self._values.pop(key, None)
                    self._owners.pop(key, None)

    def get(self, key: str) -> Any:
        with self._lock:
            if key not in self._values:
                return None
            value = self._values.pop(key)
            self._values[key] = value
            return deepcopy(value)

    def put(self, key: str, value: Any) -> None:
        with self._lock:
            self._values.pop(key, None)
            self._values[key] = deepcopy(value)
            while len(self._values) > self.capacity:
                evicted, _ = self._values.popitem(last=False)
                self._owners.pop(evicted, None)

    def clear(self) -> None:
        with self._lock:
            self._values.clear()
            self._owners.clear()
