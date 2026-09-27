"""Load and validate action profiles from profiles.json."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from . import paths
from . import snippets


ALLOWED_KINDS = frozenset({"snippet", "stock_script", "app_script"})


class ProfileError(ValueError):
    """Invalid profile definition or profiles.json."""


@dataclass(frozen=True)
class Profile:
    id: str
    label: str
    category: str
    description: str
    kind: str
    snippet_key: Optional[str] = None
    script_path: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "id": self.id,
            "label": self.label,
            "category": self.category,
            "description": self.description,
            "kind": self.kind,
        }
        if self.snippet_key is not None:
            d["snippet_key"] = self.snippet_key
        if self.script_path is not None:
            d["script_path"] = self.script_path
        return d


def _validate_profile(raw: Dict[str, Any], index: int) -> Profile:
    if not isinstance(raw, dict):
        raise ProfileError(f"profiles[{index}] must be an object")

    pid = raw.get("id")
    if not pid or not isinstance(pid, str):
        raise ProfileError(f"profiles[{index}] missing string 'id'")

    kind = raw.get("kind")
    if kind not in ALLOWED_KINDS:
        raise ProfileError(
            f"profile {pid!r}: kind must be one of {sorted(ALLOWED_KINDS)}, got {kind!r}"
        )

    label = raw.get("label") or pid
    category = raw.get("category") or "uncategorized"
    description = raw.get("description") or ""

    snippet_key = raw.get("snippet_key")
    script_path = raw.get("script_path")

    if kind == "snippet":
        if not snippet_key or not isinstance(snippet_key, str):
            raise ProfileError(f"profile {pid!r}: snippet kind requires 'snippet_key'")
        if snippet_key not in snippets.SNIPPETS:
            raise ProfileError(
                f"profile {pid!r}: unknown snippet_key {snippet_key!r}"
            )
        script_path = None
    elif kind in ("stock_script", "app_script"):
        if not script_path or not isinstance(script_path, str):
            raise ProfileError(
                f"profile {pid!r}: {kind} kind requires 'script_path'"
            )
        snippet_key = None
    else:
        raise ProfileError(f"profile {pid!r}: unsupported kind {kind!r}")

    return Profile(
        id=pid,
        label=str(label),
        category=str(category),
        description=str(description),
        kind=str(kind),
        snippet_key=snippet_key,
        script_path=script_path,
    )


# mtime-keyed cache — avoid re-read/validate on every Boost paint / Run
_profiles_cache: Dict[str, Any] = {"path": None, "mtime": None, "items": None}


def clear_profiles_cache() -> None:
    _profiles_cache["path"] = None
    _profiles_cache["mtime"] = None
    _profiles_cache["items"] = None


def load_profiles(path: Optional[Union[str, Path]] = None) -> List[Profile]:
    """Load and validate profiles.json. Returns list of Profile (cached by mtime)."""
    p = Path(path) if path is not None else paths.profiles_path()
    if not p.is_file():
        raise ProfileError(f"profiles file not found: {p}")

    try:
        mtime = p.stat().st_mtime_ns
    except OSError:
        mtime = None
    try:
        key = str(p.resolve())
    except OSError:
        key = str(p)
    if (
        _profiles_cache["items"] is not None
        and _profiles_cache["path"] == key
        and _profiles_cache["mtime"] == mtime
    ):
        return list(_profiles_cache["items"])  # type: ignore[arg-type]

    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ProfileError(f"invalid JSON in {p}: {e}") from e

    if not isinstance(data, dict):
        raise ProfileError("profiles.json root must be an object")

    raw_list = data.get("profiles")
    if not isinstance(raw_list, list):
        raise ProfileError("profiles.json must contain a 'profiles' array")

    profiles: List[Profile] = []
    seen: set[str] = set()
    for i, raw in enumerate(raw_list):
        prof = _validate_profile(raw, i)
        if prof.id in seen:
            raise ProfileError(f"duplicate profile id {prof.id!r}")
        seen.add(prof.id)
        profiles.append(prof)

    _profiles_cache["path"] = key
    _profiles_cache["mtime"] = mtime
    _profiles_cache["items"] = profiles
    return list(profiles)


def get_profile(
    profile_id: str, path: Optional[Union[str, Path]] = None
) -> Profile:
    """Return profile by id or raise ProfileError / KeyError."""
    for prof in load_profiles(path):
        if prof.id == profile_id:
            return prof
    raise KeyError(f"Unknown profile id: {profile_id!r}")


def list_profile_summaries(
    path: Optional[Union[str, Path]] = None,
) -> List[Dict[str, str]]:
    return [
        {
            "id": p.id,
            "label": p.label,
            "category": p.category,
            "kind": p.kind,
            "description": p.description,
        }
        for p in load_profiles(path)
    ]
