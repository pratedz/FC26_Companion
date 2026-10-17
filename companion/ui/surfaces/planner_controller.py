"""Window-owned planning state; proposal callbacks cannot cross windows or builds."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping
from uuid import uuid4

from ...domain.ai_requests import CLUB_REQUESTS, load_requests, save_request
from ...domain.squad_plan import SquadPlan


REQUEST_PRESETS = CLUB_REQUESTS


@dataclass
class PlannerController:
    original_plan: SquadPlan
    request: str = ""
    last_proposal: SquadPlan | None = None
    generation: int = 0
    closed: bool = False
    window_id: str = field(default_factory=lambda: uuid4().hex)
    tasks: set[str] = field(default_factory=set)

    def task_key(self, purpose: str) -> str:
        key = f"planner.{self.window_id}.{purpose}"
        self.tasks.add(key)
        return key

    def begin(self) -> int:
        self.generation += 1
        return self.generation

    def accepts(self, generation: int) -> bool:
        return not self.closed and self.generation == generation

    def update_request(self, request: str) -> bool:
        request = request.strip()
        if request == self.request:
            return False
        self.request = request
        self.begin()
        return True

    def update_bases(self, rows: Mapping[int, Mapping[str, Any]]) -> None:
        self.original_plan = self.original_plan.with_member_bases(rows)

    def close(self, executor: Any = None) -> None:
        self.closed = True
        self.begin()
        if executor is not None and hasattr(executor, "cancel"):
            for key in self.tasks:
                executor.cancel(key)


__all__ = ["PlannerController", "REQUEST_PRESETS", "load_requests", "save_request"]
