"""A saved, one-shot match preparation recipe; no recurring game edits."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping

from .job import Job, JobValidationError, op_career_set, op_injury_cure

ROUTINE_KEY = "matchday.routine"
ORIGIN = "ui.matchday.prepare"
TARGETS = (("fitness", "Fitness", 100), ("sharpness", "Sharpness", 100),
           ("form", "Form", 100), ("morale", "Morale", 120))


@dataclass(frozen=True)
class Routine:
    fitness: bool = True
    sharpness: bool = True
    form: bool = True
    morale: bool = True
    injuries: bool = False

    @classmethod
    def from_pref(cls, value: Any) -> "Routine":
        raw = value if isinstance(value, Mapping) else {}
        defaults = asdict(cls())
        return cls(**{key: raw[key] if isinstance(raw.get(key), bool) else default
                      for key, default in defaults.items()})

    def to_pref(self) -> dict[str, bool]:
        return asdict(self)

    @property
    def empty(self) -> bool:
        return not any(asdict(self).values())


def build_preparation(routine: Routine, *, save_uid: str = "", teamid: int | None = None) -> Job:
    if routine.empty:
        raise JobValidationError("Choose at least one step in your routine.")
    fields = {key: value for key, _label, value in TARGETS if getattr(routine, key)}
    ops = []
    if fields:
        ops.append(op_career_set("prepare", teamid=teamid, **fields))
    if routine.injuries:
        ops.append(op_injury_cure("recover"))
    requires = {"core_version": "2.6.31" if routine.injuries else "2.6.4"}
    if save_uid:
        requires["save_uid"] = save_uid
    job = Job(ops=tuple(ops), label="Prepare squad", origin=ORIGIN,
              require_cm=True, requires=requires, budget_ms=5000)
    job.validate()
    return job
