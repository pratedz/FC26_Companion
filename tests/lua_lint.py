"""Minimal structural validator for generated Lua.

The project builds Lua by f-string templating across six modules and ships it
into the game, where a syntax error is invisible: the bridge `load()`s the
chunk, the load fails, and the user sees a generic failure with no line number.
There is no Lua interpreter in this environment, so this module does the one
check that catches the realistic template bug — unbalanced block structure —
plus a few LE-specific safety rules.

This is deliberately NOT a Lua parser. It tokenizes well enough to ignore
strings and comments, then balances block keywords. It will not catch every
syntax error, but it does catch the class that template editing actually
produces: a stray or missing `end`.
"""

from __future__ import annotations

import re
from typing import List, NamedTuple


class LuaIssue(NamedTuple):
    line: int
    kind: str
    detail: str


# +1 depth. Note `then` counts, not `if`, so `elseif ... then` does not
# double-open; and `do` counts, not `for`/`while`, for the same reason.
_OPENERS = {"function", "do", "then", "repeat"}
_CLOSERS = {"end", "until"}

_TOKEN_RE = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]*\b")


def strip_comments_and_strings(src: str) -> str:
    """Blank out string literals and comments, preserving line structure."""
    out: List[str] = []
    i = 0
    n = len(src)
    while i < n:
        ch = src[i]

        # Long bracket [[ ... ]] or [=[ ... ]=] (string or, after --, comment)
        if ch == "[":
            m = re.match(r"\[(=*)\[", src[i:])
            if m:
                level = m.group(1)
                close = "]" + level + "]"
                end = src.find(close, i + len(m.group(0)))
                chunk = src[i:] if end == -1 else src[i : end + len(close)]
                out.append(re.sub(r"[^\n]", " ", chunk))
                i += len(chunk)
                continue

        # Comment
        if src.startswith("--", i):
            m = re.match(r"--\[(=*)\[", src[i:])
            if m:
                level = m.group(1)
                close = "]" + level + "]"
                end = src.find(close, i + len(m.group(0)))
                chunk = src[i:] if end == -1 else src[i : end + len(close)]
                out.append(re.sub(r"[^\n]", " ", chunk))
                i += len(chunk)
                continue
            end = src.find("\n", i)
            if end == -1:
                end = n
            out.append(" " * (end - i))
            i = end
            continue

        # Quoted string
        if ch in ("'", '"'):
            j = i + 1
            while j < n:
                if src[j] == "\\":
                    j += 2
                    continue
                if src[j] == ch or src[j] == "\n":
                    break
                j += 1
            chunk = src[i : min(j + 1, n)]
            out.append(re.sub(r"[^\n]", " ", chunk))
            i += len(chunk)
            continue

        out.append(ch)
        i += 1
    return "".join(out)


def check_blocks(src: str) -> List[LuaIssue]:
    """Balance Lua block keywords. Returns [] when the structure is sound."""
    clean = strip_comments_and_strings(src)
    issues: List[LuaIssue] = []
    depth = 0
    # `elseif <cond> then` continues the open `if` block rather than starting a
    # new one, and the condition sits between the two keywords — so the flag has
    # to survive until the matching `then`, not just one token.
    pending_elseif = False
    for lineno, line in enumerate(clean.splitlines(), start=1):
        for tok in _TOKEN_RE.findall(line):
            if tok == "elseif":
                pending_elseif = True
                continue
            if tok == "then" and pending_elseif:
                pending_elseif = False
                continue
            if tok in _OPENERS:
                depth += 1
            elif tok in _CLOSERS:
                depth -= 1
                if depth < 0:
                    issues.append(
                        LuaIssue(lineno, "unbalanced", f"unexpected '{tok}' (depth < 0)")
                    )
                    depth = 0
    if depth > 0:
        issues.append(LuaIssue(0, "unbalanced", f"{depth} unclosed block(s) — missing 'end'"))
    return issues


def check_le_safety(src: str) -> List[LuaIssue]:
    """LE-specific rules learned from real freezes and crashes."""
    issues: List[LuaIssue] = []
    for lineno, line in enumerate(src.splitlines(), start=1):
        stripped = line.strip()
        if stripped.startswith("--"):
            continue
        # Freezes many FC26 Career sessions.
        if "ReloadPlayersManager" in stripped:
            issues.append(LuaIssue(lineno, "forbidden_api", "ReloadPlayersManager freezes Career"))
        # Shelling out blocks the game thread and pops a console window.
        if "os.execute" in stripped or "io.popen" in stripped:
            issues.append(LuaIssue(lineno, "forbidden_api", "shell call blocks the game thread"))
    return issues


def lint(src: str, *, safety: bool = True) -> List[LuaIssue]:
    issues = check_blocks(src)
    if safety:
        issues.extend(check_le_safety(src))
    return issues


def format_issues(issues: List[LuaIssue]) -> str:
    return "\n".join(f"  line {i.line}: [{i.kind}] {i.detail}" for i in issues)
