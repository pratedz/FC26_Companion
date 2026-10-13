"""Window-owned planning state; proposal callbacks cannot cross windows or builds."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

from ...domain.squad_plan import SquadPlan


REQUEST_PRESETS = {
    "Skills and quick step": "Make all players 5 star skill moves and Quick Step+. Arrange jerseys as recommended.",
    "Improve playstyles": "Improve these players by adding role-appropriate PlayStyles and PlayStyle+ with no ceiling. Keep existing playstyles.",
    "Shirts only": "Arrange these players by jersey number as recommended. Do not change ratings, age or playstyles.",
    "Young prospects": "Improve these prospects to an overall range of 75-85 and set age to 19. Improve role-appropriate playstyles.",
}


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


def load_requests(path: Path) -> dict[str, str]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return {}
        return {str(k): str(v) for k, v in raw.items() if isinstance(v, str) and v.strip()}
    except (OSError, ValueError):
        return {}


def save_request(path: Path, name: str, request: str) -> None:
    if not name.strip() or not request.strip():
        raise ValueError("Give the saved request a name and some changes.")
    saved = load_requests(path)
    saved[name.strip()] = request.strip()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(saved, indent=2, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)
