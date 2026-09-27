"""Resolve a profile id (or Profile) to a Lua source string."""

from __future__ import annotations

from pathlib import Path
from typing import Optional, Union

from . import paths
from . import profiles as profiles_mod
from . import snippets
from .profiles import Profile, ProfileError


def resolve_profile_lua(
    profile: Union[str, Profile],
    profiles_path: Optional[Union[str, Path]] = None,
    le_root: Optional[Union[str, Path]] = None,
) -> str:
    """
    Return Lua source for the given profile id or Profile object.

    - kind == snippet: return snippet template body
    - kind == stock_script: read file content from LE root
    """
    if isinstance(profile, str):
        prof = profiles_mod.get_profile(profile, path=profiles_path)
    else:
        prof = profile

    if prof.kind == "snippet":
        if not prof.snippet_key:
            raise ProfileError(f"profile {prof.id!r} missing snippet_key")
        return snippets.get_snippet(prof.snippet_key)

    if prof.kind == "stock_script":
        if not prof.script_path:
            raise ProfileError(f"profile {prof.id!r} missing script_path")
        if le_root is not None:
            script = Path(le_root) / Path(
                prof.script_path.replace("\\", "/").lstrip("/")
            )
        else:
            script = paths.stock_script_path(prof.script_path)
        if not script.is_file():
            raise FileNotFoundError(
                f"Stock script not found for profile {prof.id!r}: {script}"
            )
        return script.read_text(encoding="utf-8", errors="replace")

    if prof.kind == "app_script":
        if not prof.script_path:
            raise ProfileError(f"profile {prof.id!r} missing script_path")
        rel = prof.script_path.replace("\\", "/").lstrip("/")
        # Prefer writable app folder; fall back to bundled resource root (frozen).
        script = paths.app_root() / Path(rel)
        if not script.is_file():
            script = paths.resource_root() / Path(rel)
        if not script.is_file():
            raise FileNotFoundError(
                f"App script not found for profile {prof.id!r}: {rel}"
            )
        return script.read_text(encoding="utf-8", errors="replace")

    raise ProfileError(f"profile {prof.id!r}: unsupported kind {prof.kind!r}")


def dump_profile(
    profile_id: str,
    profiles_path: Optional[Union[str, Path]] = None,
) -> str:
    """Convenience: resolve profile id to Lua source string."""
    return resolve_profile_lua(profile_id, profiles_path=profiles_path)
