"""Codex account and OpenAI API transport for validated edit proposals.

Codex Account reuses the ``codex login`` cache at ``~/.codex/auth.json``.
The API key, when used, is resolved by ``ai_provider`` and is never stored here.
The model never returns a replacement player card and this module never submits a game job.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import uuid
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from ..domain.builds import BuildProposal, proposal, revision
from ..domain.add_player import CardRecommendation
from ..domain.player import FIELD_SPECS
from ..domain.playstyles import OUTFIELD, GOALKEEPER, encode_names, validate_ai_styles
from ..domain.squad_session import (
    PROPOSE_CHUNK,
    RosterPlayer,
    SquadRecipe,
    SquadSession,
    build_session,
    club_identity_note,
    compact_current,
    merge_recipe,
    parse_grok_targets,
    parse_recipe,
    roster_from_rows,
    lock_python_scope,
)

CODEX_RESPONSES_URL = "https://chatgpt.com/backend-api/codex/responses"
DEFAULT_MODEL = "gpt-6-luna"
DEFAULT_REASONING_EFFORT = "high"
# Same prompt and reasoning as a serial run. Overlap the chunk waits only.
_PARALLEL_CHUNKS = 3
_JSON_OBJECT_HINT = "Respond with JSON only."


def _with_json_word(text: str) -> str:
    """Codex json_object format 400s unless some input message contains 'json'."""
    if "json" in (text or "").lower():
        return text
    return f"{text.rstrip()}\n\n{_JSON_OBJECT_HINT}".strip()


@dataclass(frozen=True, slots=True)
class SessionStatus:
    connected: bool
    message: str
    path: Path
    state: str = "needs_login"
    login_hint: str = ""


def auth_path() -> Path:
    home = (os.environ.get("CODEX_HOME") or "").strip()
    if home:
        return Path(home) / "auth.json"
    return Path.home() / ".codex" / "auth.json"


def _codex_tokens(raw: Mapping[str, Any]) -> tuple[str, str]:
    """Return (access_token, account_id) from a Codex CLI auth.json object."""
    tokens = raw.get("tokens")
    if not isinstance(tokens, Mapping):
        return "", ""
    access = str(tokens.get("access_token") or "").strip()
    account = str(tokens.get("account_id") or raw.get("account_id") or "").strip()
    return access, account


def _build_entry() -> Mapping[str, Any] | None:
    path = auth_path()
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(raw, Mapping):
        return None
    access, account = _codex_tokens(raw)
    if not access:
        return None
    return {"key": access, "account_id": account}


def _auth_file_state() -> str:
    """Return a non-sensitive description of the local session file state."""
    path = auth_path()
    if not path.exists():
        return "missing"
    if not path.is_file():
        return "unreadable"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "unreadable"
    if not isinstance(raw, Mapping):
        return "unusable"
    return "unusable" if _build_entry() is None else "available"


def _login_hint() -> str:
    """Give an actionable instruction without launching an opaque terminal."""
    if shutil.which("codex"):
        return "Codex is installed. Run `codex login` if needed, then check again."
    return "Open Codex CLI, run `codex login`, then return here and choose Check Codex login."


def status() -> SessionStatus:
    entry = _build_entry()
    if entry:
        return SessionStatus(
            True, "Codex CLI login found and ready to propose.", auth_path(), "connected"
        )
    file_state = _auth_file_state()
    hint = _login_hint()
    if file_state in {"unreadable", "unusable"}:
        return SessionStatus(
            False,
            "A Codex CLI login was found but is not usable.",
            auth_path(),
            "needs_login",
            hint,
        )
    return SessionStatus(
        False,
        "Codex CLI is not signed in on this Windows account.",
        auth_path(),
        "needs_login",
        hint,
    )


def use_build_session() -> SessionStatus:
    """Explicit button action: re-check the active user login without copying it."""
    result = status()
    if not result.connected:
        instruction = f" {result.login_hint}" if result.login_hint else ""
        raise RuntimeError(f"{result.message}{instruction}")
    return result


def _session() -> Mapping[str, str]:
    entry = _build_entry()
    token = str((entry or {}).get("key") or "").strip()
    account = str((entry or {}).get("account_id") or "").strip()
    if not token:
        raise RuntimeError(
            "Codex is not connected. Run `codex login` in Codex CLI, then choose "
            "“Check Codex login” here."
        )
    return {"token": token, "account_id": account}


def _codex_headers(session: Mapping[str, str]) -> dict[str, str]:
    headers = {
        "Authorization": f"Bearer {session['token']}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
        "OpenAI-Beta": "responses=experimental",
        "originator": "FC26-Companion",
        "session_id": str(uuid.uuid4()),
        "User-Agent": "FC26-Companion/2",
    }
    account = session.get("account_id") or ""
    if account:
        headers["chatgpt-account-id"] = account
    return headers


def _responses_body(
    messages: list[dict[str, str]],
    *,
    model: str,
    json_object: bool = True,
    reasoning: bool = True,
) -> dict[str, Any]:
    instructions = ""
    items: list[dict[str, str]] = []
    for message in messages:
        role = str(message.get("role") or "user")
        content = str(message.get("content") or "")
        if role == "system" and not instructions:
            instructions = content
            continue
        items.append({"role": role if role in {"user", "assistant"} else "user", "content": content})
    if not items:
        fallback = "Respond with JSON only." if json_object else "Respond with plain text."
        items = [{"role": "user", "content": instructions or fallback}]
        instructions = ""
    if json_object:
        items = [
            {
                "role": item["role"],
                "content": _with_json_word(item["content"]),
            }
            for item in items
        ]
        if instructions and "json" not in instructions.lower():
            instructions = _with_json_word(instructions)
    body: dict[str, Any] = {
        "model": model,
        "instructions": instructions,
        "input": items,
        "store": False,
        "stream": True,
    }
    if reasoning:
        body["reasoning"] = {"effort": DEFAULT_REASONING_EFFORT}
    if json_object:
        body["text"] = {"format": {"type": "json_object"}}
    return body


def _sse_payloads(raw: str) -> list[Mapping[str, Any]]:
    payloads: list[Mapping[str, Any]] = []
    for block in raw.replace("\r\n", "\n").split("\n\n"):
        data_lines = [
            line[5:].lstrip()
            for line in block.splitlines()
            if line.startswith("data:")
        ]
        if not data_lines:
            continue
        blob = "\n".join(data_lines).strip()
        if not blob or blob == "[DONE]":
            continue
        try:
            parsed = json.loads(blob)
        except ValueError:
            continue
        if isinstance(parsed, Mapping):
            payloads.append(parsed)
    return payloads


def _piece_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, Mapping):
        for key in ("text", "output_text", "delta"):
            piece = value.get(key)
            if isinstance(piece, str) and piece:
                return piece
    return ""


def _walk_output_text(value: Any, into: list[str]) -> None:
    if isinstance(value, str):
        if value:
            into.append(value)
        return
    if isinstance(value, Mapping):
        kind = str(value.get("type") or "")
        if kind.startswith("reasoning"):
            return
        piece = _piece_text(value)
        if piece and kind in {"", "output_text", "text", "response.output_text.delta", "response.output_text.done"}:
            into.append(piece)
            return
        if piece and "delta" in kind:
            into.append(piece)
            return
        for key in ("content", "output", "item", "message"):
            if key in value:
                _walk_output_text(value[key], into)
        return
    if isinstance(value, list):
        for item in value:
            _walk_output_text(item, into)


def _collect_stream_text(events: Sequence[Mapping[str, Any]]) -> str:
    deltas: list[str] = []
    done: list[str] = []
    for event in events:
        kind = str(event.get("type") or "")
        if kind in {"response.failed", "error"}:
            message = event.get("error") or event.get("message") or event
            raise RuntimeError(f"Codex stream failed: {message}")
        if "output_text.delta" in kind or kind.endswith("text.delta"):
            piece = _piece_text(event.get("delta") if "delta" in event else event)
            if not piece:
                piece = _piece_text(event)
            if piece:
                deltas.append(piece)
        elif kind.endswith("output_text.done") or kind == "response.output_text.done":
            piece = _piece_text(event.get("text") or event.get("output_text") or event)
            if piece:
                done.append(piece)
        elif kind == "response.output_item.done":
            chunks: list[str] = []
            _walk_output_text(event.get("item"), chunks)
            if chunks:
                done.append("".join(chunks))
    if deltas:
        return "".join(deltas)
    if done:
        return done[-1]
    for event in reversed(events):
        if str(event.get("type") or "") not in {"response.completed", "response.done"}:
            continue
        chunks = []
        _walk_output_text(event.get("response"), chunks)
        if chunks:
            return "".join(chunks)
    return ""


def _assemble_codex_stream(raw: str) -> Mapping[str, Any]:
    events = _sse_payloads(raw)
    if not events:
        stripped = raw.lstrip()
        if stripped.startswith("{") or stripped.startswith("["):
            try:
                parsed = json.loads(raw)
            except ValueError:
                parsed = None
            if isinstance(parsed, Mapping):
                try:
                    text = _response_text(parsed)
                except RuntimeError:
                    text = ""
                if text:
                    return {"output_text": text}
                if parsed.get("output_text") or parsed.get("output") or parsed.get("choices"):
                    return parsed
        raise RuntimeError("Codex stream was empty.")
    text = _collect_stream_text(events)
    if text.strip():
        return {"output_text": text}
    kinds = [str(event.get("type") or "?") for event in events]
    raise RuntimeError(
        "Codex stream ended without a proposal. events=" + ",".join(kinds[-16:])
    )


def _request(messages: list[dict[str, str]], *, model: str) -> Mapping[str, Any]:
    from .ai_provider import dispatch_request

    return dispatch_request(messages, model=model)


def _codex_request(
    messages: list[dict[str, str]],
    *,
    model: str,
    json_object: bool = True,
    reasoning: bool = True,
) -> Mapping[str, Any]:
    payload = json.dumps(
        _responses_body(
            messages, model=model, json_object=json_object, reasoning=reasoning,
        )
    ).encode("utf-8")
    req = urllib.request.Request(
        CODEX_RESPONSES_URL,
        data=payload,
        method="POST",
        headers=_codex_headers(_session()),
    )
    try:
        with urllib.request.urlopen(req, timeout=180.0) as response:
            raw = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        if exc.code == 401:
            raise RuntimeError(
                "Codex login expired. Run `codex login` again, then choose "
                "“Check Codex login”."
            ) from exc
        if exc.code == 429 or "usage_limit" in detail:
            raise RuntimeError(
                "Codex login credit is used up. Switch the provider to "
                "OpenAI API Key. Nothing was written to the career."
            ) from exc
        raise RuntimeError(f"Codex request failed ({exc.code}): {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Cannot reach Codex: {exc.reason}") from exc
    body = _assemble_codex_stream(raw)
    if not isinstance(body, Mapping):
        raise RuntimeError("Codex returned an invalid response.")
    return body


def _response_text(body: Mapping[str, Any]) -> str:
    direct = body.get("output_text")
    if isinstance(direct, str) and direct.strip():
        return direct
    chunks: list[str] = []
    output = body.get("output")
    if isinstance(output, list):
        for item in output:
            if not isinstance(item, Mapping):
                continue
            content = item.get("content")
            if isinstance(content, list):
                for part in content:
                    if not isinstance(part, Mapping):
                        continue
                    if part.get("type") in {"output_text", "text"}:
                        text = part.get("text") or part.get("output_text") or ""
                        if text:
                            chunks.append(str(text))
            elif isinstance(item.get("text"), str):
                chunks.append(str(item["text"]))
    if chunks:
        return "\n".join(chunks)
    try:
        content = body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        content = None
    if isinstance(content, list):
        content = "".join(
            str(item.get("text") or "") for item in content
            if isinstance(item, Mapping)
        )
    if isinstance(content, str) and content.strip():
        return content
    raise RuntimeError("GPT response did not contain a proposal.")


def _parse_json_object(text: str) -> Mapping[str, Any]:
    blob = (text or "").strip()
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", blob)
    if fence:
        blob = fence.group(1).strip()
    try:
        parsed = json.loads(blob)
    except ValueError:
        start = blob.find("{")
        end = blob.rfind("}")
        if start < 0 or end <= start:
            raise RuntimeError("GPT proposal was not valid JSON.")
        parsed = json.loads(blob[start : end + 1])
    if not isinstance(parsed, Mapping):
        raise RuntimeError("GPT proposal must be a JSON object.")
    return parsed


def _content(body: Mapping[str, Any]) -> Mapping[str, Any]:
    return _parse_json_object(_response_text(body))


def propose_changes(
    *,
    playerid: int,
    player_name: str,
    current: Mapping[str, Any],
    request: str,
    model: str = DEFAULT_MODEL,
) -> BuildProposal:
    """Ask GPT for changed fields only and reject stale/malformed answers."""
    prompt = (request or "").strip()
    if not prompt:
        raise ValueError("Describe the build or changes you want.")
    rev = revision(playerid, current)
    field_contract = {
        name: {"min": spec.minimum, "max": spec.maximum, "label": spec.label}
        for name, spec in FIELD_SPECS.items()
    }
    system = (
        "You design safe EA FC 26 Career Mode player edits. Return JSON only. "
        "Do not invent a different playerid. Return exactly: "
        '{"target_playerid":number,"base_revision":"string","summary":"string",'
        '"changes":[{"field":"canonical_name","value":integer,"reason":"short"}]}. '
        "Only propose fields from the supplied contract. "
        "CRITICAL — overall vs in-stats: Career Mode stores overallrating "
        "separately from Pace/Shooting/Passing/Dribbling/Defending/Physical. "
        "If the user asks for a target overall, a rebuild, a card-style "
        "rating (e.g. \"Neymar 92\", \"make him 90 overall\"), or any full "
        "build, you MUST include EVERY outfield attribute field in those six "
        "groups plus overallrating and potential. Returning overallrating "
        "alone leaves the old in-stats and looks broken in-game. "
        "Shape attributes for the player's position and style; keep the "
        "relative profile (e.g. Neymar stays high dribbling / low defending). "
        "Prefer 25–40 attribute/rating fields for overall builds. "
        "For tiny requests (\"+2 acceleration only\") change only the asked "
        "attributes and leave body unchanged. "
        "If the user asks for an era, year, physique, or to feel like a "
        "specific season (for example CR7 08/09), you MUST include height and "
        "weight, and include bodytypecode and runstylecode when those keys "
        "exist in current_values or the user named them. "
        "Never change identity, names, team, contract, or face unless those "
        "topics were asked."
    )
    user = json.dumps(
        {
            "target_playerid": int(playerid),
            "target_name": player_name,
            "base_revision": rev,
            "current_values": dict(current),
            "editable_field_contract": field_contract,
            "user_request": prompt,
            "requirements": {
                "overall_builds_need_full_attributes": True,
                "never_overall_only": True,
                "tiny_requests_attributes_only": True,
                "era_physique_requires_height_weight": True,
            },
        },
        ensure_ascii=False,
    )
    parsed = _content(
        _request(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            model=model,
        )
    )
    try:
        returned_pid = int(parsed.get("target_playerid"))
    except (TypeError, ValueError) as exc:
        raise RuntimeError("GPT proposal omitted the target player.") from exc
    if returned_pid != int(playerid) or str(parsed.get("base_revision") or "") != rev:
        raise RuntimeError(
            "GPT replied for an older or different player. Nothing was staged."
        )
    raw_changes = parsed.get("changes")
    if not isinstance(raw_changes, list) or not raw_changes:
        raise RuntimeError("GPT proposed no editable changes.")
    patch: dict[str, Any] = {}
    unknown: list[str] = []
    for item in raw_changes:
        if not isinstance(item, Mapping):
            raise RuntimeError("GPT returned a malformed change.")
        field = str(item.get("field") or "").strip().lower()
        if field not in FIELD_SPECS:
            unknown.append(field or "(blank)")
            continue
        patch[field] = item.get("value")
    if unknown:
        raise RuntimeError(
            "GPT proposed unsupported fields: " + ", ".join(sorted(set(unknown)))
        )
    return proposal(
        patch,
        source="grok",
        label="GPT proposal",
        summary=str(parsed.get("summary") or ""),
        warnings=("AI suggestions can be wrong. Review every before/after value.",),
        current=current,
        expand_thin_overall=True,
    )


def propose_squad_template(
    *,
    selection: Sequence[Mapping[str, Any]],
    selection_revision: str,
    request: str,
    model: str = DEFAULT_MODEL,
) -> BuildProposal:
    """Ask GPT for one shared template patch for a multi-player selection.

    Returns a BuildProposal (fields only). Caller clones it onto the selection.
    Never submits a game job.
    """
    prompt = (request or "").strip()
    if not prompt:
        raise ValueError("Describe the squad build you want for the selection.")
    if not selection:
        raise ValueError("No players selected.")
    if not selection_revision:
        raise ValueError("Missing selection revision.")
    field_contract = {
        name: {"min": spec.minimum, "max": spec.maximum, "label": spec.label}
        for name, spec in FIELD_SPECS.items()
    }
    system = (
        "You design safe EA FC 26 Career Mode squad templates. Return JSON only. "
        "Return exactly one shared patch for ALL selected players (not per-player): "
        '{"selection_revision":"string","summary":"string",'
        '"changes":[{"field":"canonical_name","value":integer,"reason":"short"}]}. '
        "Only fields from the contract. Do not invent playerids. Prefer attributes "
        "and ratings the user asked for. Never change identity, names, team, "
        "contract, or face unless explicitly requested. Keep the change set small."
    )
    user = json.dumps(
        {
            "selection_revision": selection_revision,
            "selected_players": list(selection),
            "player_count": len(selection),
            "editable_field_contract": field_contract,
            "user_request": prompt,
            "plan_kind": "template",
        },
        ensure_ascii=False,
    )
    parsed = _content(
        _request(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            model=model,
        )
    )
    if str(parsed.get("selection_revision") or "") != selection_revision:
        raise RuntimeError(
            "GPT replied for an older selection. Nothing was staged."
        )
    raw_changes = parsed.get("changes")
    if not isinstance(raw_changes, list) or not raw_changes:
        raise RuntimeError("GPT proposed no editable template fields.")
    patch: dict[str, Any] = {}
    unknown: list[str] = []
    for item in raw_changes:
        if not isinstance(item, Mapping):
            raise RuntimeError("GPT returned a malformed change.")
        field = str(item.get("field") or "").strip().lower()
        if field not in FIELD_SPECS:
            unknown.append(field or "(blank)")
            continue
        patch[field] = item.get("value")
    if unknown:
        raise RuntimeError(
            "GPT proposed unsupported fields: " + ", ".join(sorted(set(unknown)))
        )
    if len(patch) > 20:
        raise RuntimeError("GPT template is too large (max 20 fields).")
    return proposal(
        patch,
        source="grok",
        label="GPT squad template",
        summary=str(parsed.get("summary") or ""),
        warnings=(
            "AI template is applied to every selected player. "
            "Review the matrix before Apply.",
        ),
    )


def recommend_library_players(
    *,
    request: str,
    max_results: int = 5,
    model: str = DEFAULT_MODEL,
) -> tuple[str, tuple[CardRecommendation, ...]]:
    """Turn natural language into names/hints; the local catalog resolves them.

    This function never returns a card payload and never submits a game job.
    The caller must resolve every name against ``LocalCatalog`` and show those
    concrete rows for user selection before adding anything.
    """
    prompt = (request or "").strip()
    if not prompt:
        raise ValueError("Describe the players you want GPT to find.")
    count = max(1, min(int(max_results), 11))
    system = (
        "You recommend footballers for an EA FC 26 local card-library search. "
        "Return JSON only with this exact shape: "
        '{"summary":"short","recommendations":['
        '{"name":"full player name","reason":"short",'
        '"variant_hint":"e.g. Icon","club_hint":"e.g. AC Milan",'
        '"position_hint":"e.g. midfielder"}]}. '
        f"Return at most {count} distinct players. Names must be real footballers. "
        "Do not invent card ids, ratings, attributes, or claim that a card is "
        "present; the app will verify every recommendation in its local library."
    )
    parsed = _content(
        _request(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            model=model,
        )
    )
    raw = parsed.get("recommendations")
    if not isinstance(raw, list) or not raw:
        raise RuntimeError("GPT returned no player recommendations.")
    recommendations: list[CardRecommendation] = []
    seen: set[str] = set()
    for item in raw[:count]:
        if not isinstance(item, Mapping):
            continue
        name = str(item.get("name") or "").strip()
        key = name.casefold()
        if not name or key in seen:
            continue
        seen.add(key)
        recommendations.append(
            CardRecommendation(
                name=name,
                reason=str(item.get("reason") or "").strip(),
                variant_hint=str(item.get("variant_hint") or "").strip(),
                club_hint=str(item.get("club_hint") or "").strip(),
                position_hint=str(item.get("position_hint") or "").strip(),
            )
        )
    if not recommendations:
        raise RuntimeError("GPT returned no usable player names.")
    return str(parsed.get("summary") or "").strip(), tuple(recommendations)


def interpret_squad_edit(
    *,
    roster: Sequence[Mapping[str, Any]] | Sequence[RosterPlayer],
    request: str,
    club_name: str = "",
    model: str = DEFAULT_MODEL,
) -> SquadSession:
    """Parse a Career ask into a ranked, filter-clamped session. Never submits a job."""
    prompt = (request or "").strip()
    if not prompt:
        raise ValueError("Describe what you want to do with this squad.")
    players: tuple[RosterPlayer, ...]
    if roster and isinstance(roster[0], RosterPlayer):
        players = tuple(roster)  # type: ignore[arg-type]
    else:
        players = roster_from_rows(roster)  # type: ignore[arg-type]
    if not players:
        raise ValueError("No squad players to scan.")
    python_recipe = parse_recipe(prompt, club_name)
    thin = [
        {
            "playerid": p.playerid,
            "name": p.name,
            "pos": p.position,
            "ovr": p.ovr,
            "pot": p.pot,
        }
        for p in players
    ]
    system = (
        "You plan EA FC 26 Career Mode squad edits. Return JSON only: "
        '{"recipe":{"intent":"lift_band|prime|overstat|relative|custom",'
        '"ovr_min":null,"ovr_max":null,"target_ovr":null,"band_lo":null,"band_hi":null,'
        '"soft_band":false,"positions":[],"include_gk":false,'
        '"families":["attrs"],"protect_top11":false,"allow_icon_traits":false},'
        '"targets":[{"playerid":number,"target_ovr":number,'
        '"rung":"face|star|rotation|prospect","why":"short Career reason"}]}. '
        "Rank realistically: the club face at the top of any overall band, "
        "wonderkids/prospects at the floor. Do not invent playerids. "
        "If the user names a different overall for specific players, return those "
        "exact target_ovr values on those playerids. Do not collapse different "
        "named overalls into one shared target_ovr or band. "
        "The roster is the user's selection: include its goalkeepers unless explicitly excluded. Never change identity/face/names "
        "unless asked. Python will re-apply numeric filters and named overalls."
    )
    user = json.dumps(
        {
            "club": club_name,
            "user_request": prompt,
            "roster": thin,
            "python_recipe_hint": {
                "intent": python_recipe.intent,
                "ovr_min": python_recipe.ovr_min,
                "ovr_max": python_recipe.ovr_max,
                "target_ovr": python_recipe.target_ovr,
                "band_lo": python_recipe.band_lo,
                "band_hi": python_recipe.band_hi,
                "soft_band": python_recipe.soft_band,
                "positions": list(python_recipe.positions),
                "include_gk": python_recipe.include_gk,
                "families": list(python_recipe.families),
                "named_overalls": [
                    {"name": name, "ovr": ovr}
                    for name, ovr in python_recipe.named_overalls
                ],
            },
        },
        ensure_ascii=False,
    )
    grok_note = ""
    parsed: Mapping[str, Any] = {}
    try:
        parsed = _content(
            _request(
                [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                model=model,
            )
        )
    except Exception as exc:  # noqa: BLE001
        grok_note = str(exc).strip().splitlines()[0][:160]
        parsed = {}
    grok_recipe = parsed.get("recipe") if isinstance(parsed.get("recipe"), Mapping) else None
    recipe = merge_recipe(python_recipe, grok_recipe)
    recipe = lock_python_scope(python_recipe, recipe)
    session = build_session(
        players,
        recipe,
        grok_targets=parse_grok_targets(parsed.get("targets")),
    )
    if grok_note:
        session = replace(
            session,
            warnings=session.warnings + (f"GPT ranking skipped: {grok_note}",),
        )
    return session


def propose_squad_players(
    *,
    players: Sequence[Mapping[str, Any]],
    selection_revision: str,
    request: str,
    recipe: SquadRecipe,
    model: str = DEFAULT_MODEL,
    cancel_check: Callable[[], bool] | None = None,
    on_chunk: Callable[[int, int], None] | None = None,
    problems: list[str] | None = None,
) -> tuple[dict[int, dict[str, Any]], dict[int, str], dict[int, str]]:
    """Per-player patches in chunks. Never submits a game job."""
    prompt = (request or "").strip()
    if not prompt:
        raise ValueError("Describe the squad build you want.")
    if not players:
        raise ValueError("No players selected.")
    if not selection_revision:
        raise ValueError("Missing selection revision.")
    patches: dict[int, dict[str, Any]] = {}
    reasons: dict[int, str] = {}
    hints: dict[int, str] = {}
    rows = list(players)
    chunks = [rows[index:index + PROPOSE_CHUNK] for index in range(0, len(rows), PROPOSE_CHUNK)]
    total = len(chunks)

    def run_chunk(chunk: list[Mapping[str, Any]]):
        if cancel_check is not None and cancel_check():
            return None
        return _propose_squad_chunk(
            chunk,
            selection_revision=selection_revision,
            request=prompt,
            recipe=recipe,
            model=model,
        )

    parts: list[tuple[dict, dict, dict] | None] = [None] * total
    if on_chunk is not None:
        on_chunk(0, total)
    if total == 1:
        parts[0] = run_chunk(chunks[0])
        if on_chunk is not None:
            on_chunk(1, 1)
    else:
        done = 0
        first_error: BaseException | None = None
        with ThreadPoolExecutor(max_workers=min(_PARALLEL_CHUNKS, total)) as pool:
            pending = {pool.submit(run_chunk, chunk): index for index, chunk in enumerate(chunks)}
            for future in as_completed(pending):
                index = pending[future]
                try:
                    parts[index] = future.result()
                except Exception as exc:
                    if first_error is None:
                        first_error = exc
                    if problems is not None:
                        problems.append(
                            f"Codex group {index + 1} of {total} failed: {exc}"
                        )
                    continue
                done += 1
                if on_chunk is not None:
                    on_chunk(done, total)
                if cancel_check is not None and cancel_check():
                    break
        if first_error is not None and not any(part for part in parts):
            if not (cancel_check is not None and cancel_check()):
                raise first_error
    if cancel_check is not None and cancel_check():
        return patches, reasons, hints
    for part in parts:
        if not part:
            continue
        part_patches, part_reasons, part_hints = part
        patches.update(part_patches)
        reasons.update(part_reasons)
        hints.update(part_hints)
    if not patches:
        raise RuntimeError("GPT proposed no per-player changes.")
    return patches, reasons, hints


def library_hint_for(
    name: str,
    position: str,
    target_ovr: int,
    catalog: Any | None,
) -> str:
    if catalog is None or not name:
        return ""
    try:
        hits = catalog.search(
            name,
            ovr_min=max(1, int(target_ovr) - 1),
            ovr_max=min(99, int(target_ovr) + 1),
            limit=3,
        )
    except Exception:
        return ""
    if not hits:
        return ""
    card = hits[0]
    ovr = card.get("overallrating") or card.get("ovr") or target_ovr
    pos = card.get("positions_text") or card.get("position") or position or ""
    label = str(card.get("name") or name)
    return f"Inspired by: {ovr} {pos} Library card ({label})"


def _field_contract(recipe: SquadRecipe) -> dict[str, dict[str, Any]]:
    """Bounds for fields this ask may edit. Face, kit, and tattoos stay out."""
    families = set(recipe.families or ())
    body_names = {"height", "weight", "bodytypecode", "muscularitycode", "runstylecode"}
    playstyle_names = {"trait1", "trait2", "icontrait1", "icontrait2"}
    out: dict[str, dict[str, Any]] = {}
    for name, spec in FIELD_SPECS.items():
        keep = spec.category in {
            "Ratings", "Pace", "Shooting", "Passing", "Dribbling",
            "Defending", "Physical", "Goalkeeping",
        }
        if "playstyles" in families and name in playstyle_names:
            keep = True
        if "skills" in families and spec.category == "Skills & foot":
            keep = True
        if "body" in families and name in body_names:
            keep = True
        if "age" in families and name == "birthdate":
            keep = True
        if "identity" in families and name in {
            "nationality", "headassetid", "hairtypecode", "skintonecode",
        }:
            keep = True
        if not keep:
            continue
        out[name] = {"min": spec.minimum, "max": spec.maximum, "label": spec.label}
    return out


def _propose_squad_chunk(
    chunk: Sequence[Mapping[str, Any]],
    *,
    selection_revision: str,
    request: str,
    recipe: SquadRecipe,
    model: str,
) -> tuple[dict[int, dict[str, Any]], dict[int, str], dict[int, str]]:
    """Repair missing/invalid players once, preserving every valid proposal."""
    patches, reasons, hints = _propose_squad_chunk_once(
        chunk, selection_revision=selection_revision, request=request, recipe=recipe, model=model,
    )
    missing = [row for row in chunk if int(row["playerid"]) not in patches]
    if missing:
        details = "; ".join(f"{row.get('name', row['playerid'])}: "
                            f"{reasons.get(int(row['playerid']), 'missing proposal')}" for row in missing)
        try:
            repaired, why, extra = _propose_squad_chunk_once(
                missing, selection_revision=selection_revision,
                request=request + "\nRepair these rejected proposals: " + details,
                recipe=recipe, model=model,
            )
            patches.update(repaired)
            reasons.update(why)
            hints.update(extra)
        except Exception as exc:
            for row in missing:
                reasons[int(row["playerid"])] = f"Proposal repair failed: {exc}"
    if not patches:
        raise RuntimeError("No valid player proposals. " + "; ".join(reasons.values()))
    return patches, reasons, hints


def _propose_squad_chunk_once(
    chunk: Sequence[Mapping[str, Any]],
    *,
    selection_revision: str,
    request: str,
    recipe: SquadRecipe,
    model: str,
) -> tuple[dict[int, dict[str, Any]], dict[int, str], dict[int, str]]:
    field_contract = _field_contract(recipe)
    allowed = list(recipe.families)
    identity_note = club_identity_note(recipe.club_name)
    system = (
        "You design safe EA FC 26 Career Mode per-player edits. Return JSON only: "
        '{"selection_revision":"string","players":['
        '{"playerid":number,"summary":"one-line Career reason",'
        '"playstyles":["exact name"],"playstyles_plus":["exact name"],'
        '"changes":[{"field":"canonical_name","value":integer,"reason":"short"}]}]}. '
        "Each player gets a DIFFERENT patch from their live current_values. "
        "Keep relative shape (a creator stays high passing/dribble, a CB stays a CB). "
        "Hit the supplied target_ovr. When pinned is true, overallrating must equal "
        "that target_ovr exactly. Include full outfield attributes for overall "
        "rebuilds — never overallrating alone. "
        f"Allowed families: {allowed}. "
        "When playstyles are allowed, choose names from playstyle_catalog for "
        "playstyles and playstyles_plus. Python encodes the masks. Never guess "
        "trait1/trait2/icontrait1/icontrait2 numbers in changes. "
        "Include every selected player, including goalkeepers. For a PlayStyle+ "
        "request, every player needs a nonempty playstyles_plus list. "
        "Use goalkeeper PlayStyle+ for keepers and outfield PlayStyle+ for others. "
        "CPU/Career traits such as Injury Prone are not PlayStyle+. "
        "Honor each player's playstyle_plus_max for icontrait1/icontrait2. "
        "A null playstyle_plus_max means the user typed no limit: give each "
        "player as many PlayStyle+ as suit his position and live attributes, "
        "and do not copy one set onto the whole squad. "
        "A higher target_ovr gets more regular playstyles and more PlayStyle+ "
        "than a lower teammate: 88+ should look like a star, not the same two "
        "bits as a 79. When playstyle_plus_max is a number, never exceed it. "
        "Each player gets their own attribute shape and their own playstyle bits. "
        "Do not copy one trait mask onto every player. "
        "If the user asks to arrange or recommend jersey numbers, also set "
        "jerseynumber to that player's well-known squad shirt, usually 1-30. "
        "Never copy overallrating into jerseynumber. "
        "Never change identity, names, team, contract, or face unless identity is allowed. "
        "Do not invent playerids. Do not nerf unless the user asked."
    )
    user = json.dumps(
        {
            "selection_revision": selection_revision,
            "user_request": request,
            "club_identity": identity_note,
            "recipe": {
                "intent": recipe.intent,
                "families": list(recipe.families),
                "allow_icon_traits": recipe.allow_icon_traits,
                "unlimited_playstyles": recipe.unlimited_playstyles,
                "allow_nerf": recipe.allow_nerf,
                "soft_band": recipe.soft_band,
                "named_overalls": [
                    {"name": name, "ovr": ovr}
                    for name, ovr in recipe.named_overalls
                ],
            },
            "players": [
                {
                    "playerid": row.get("playerid"),
                    "name": row.get("name"),
                    "position": row.get("position"),
                    "rung": row.get("rung"),
                    "target_ovr": row.get("target_ovr"),
                    "pinned": bool(row.get("pinned")),
                    "playstyle_plus_max": row.get("playstyle_plus_max"),
                    "current_values": compact_current(row.get("current_values") or row),
                    "library_hint": row.get("library_hint") or "",
                }
                for row in chunk
            ],
            "editable_field_contract": field_contract,
            "playstyle_catalog": {"outfield": OUTFIELD, "goalkeeper": GOALKEEPER},
        },
        ensure_ascii=False,
    )
    parsed = _content(
        _request(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            model=model,
        )
    )
    if str(parsed.get("selection_revision") or "") != selection_revision:
        raise RuntimeError("GPT replied for an older selection. Nothing was staged.")
    raw_players = parsed.get("players")
    if not isinstance(raw_players, list) or not raw_players:
        raise RuntimeError("GPT proposed no per-player changes.")
    wanted = {int(row.get("playerid")) for row in chunk if row.get("playerid") not in (None, "")}
    rows_by_id = {int(row["playerid"]): row for row in chunk}
    patches: dict[int, dict[str, Any]] = {}
    reasons: dict[int, str] = {}
    hints: dict[int, str] = {}
    for item in raw_players:
        if not isinstance(item, Mapping):
            continue
        try:
            pid = int(item.get("playerid"))
        except (TypeError, ValueError):
            continue
        if pid not in wanted:
            continue
        changes = item.get("changes")
        patch: dict[str, Any] = {}
        if isinstance(changes, list):
            for change in changes:
                if not isinstance(change, Mapping):
                    continue
                field = str(change.get("field") or "").strip().lower()
                if field in {"jerseynumber", "jersey", "shirt", "shirtnumber"}:
                    patch["jerseynumber"] = change.get("value")
                    continue
                if field not in FIELD_SPECS:
                    continue
                patch[field] = change.get("value")
        try:
            if recipe.wants_playstyles:
                for key, plus in (("playstyles", False), ("playstyles_plus", True)):
                    if key in item:
                        patch.update(encode_names(item[key], plus=plus))
                validate_ai_styles(patch, require_plus=recipe.wants_playstyle_plus,
                                   position=str(rows_by_id[pid].get("position") or ""))
            # Reject invented fields' values before any player reaches Apply.
            for key, value in patch.items():
                if key in FIELD_SPECS:
                    FIELD_SPECS[key].coerce(value)
        except (ValueError, TypeError) as exc:
            reasons[pid] = str(exc)
            continue
        if patch:
            patches[pid] = patch
            reasons[pid] = str(item.get("summary") or "").strip()
            hints[pid] = str(item.get("library_hint") or "").strip()
    return patches, reasons, hints

