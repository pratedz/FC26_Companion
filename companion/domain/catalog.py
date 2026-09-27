"""Canonical local card library.

The Library reads the normalized multi-year ``universe.sqlite`` first and
falls back to v1's ``catalog.sqlite`` payload index.  It deliberately owns the
normalization, matching, comparison, favorite and cross-year import rules so
the UI never needs to import ``src.*``.
"""

from __future__ import annotations

import json
import re
import sqlite3
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence


class CatalogError(RuntimeError):
    """Catalog missing, unreadable, or schema-unexpected."""


@dataclass(frozen=True, slots=True)
class CatalogQuery:
    text: str = ""
    year: str = ""
    ovr_min: int | None = None
    ovr_max: int | None = None
    position: int | None = None
    source: str = ""
    limit: int = 50


@dataclass(frozen=True, slots=True)
class ImportPlan:
    """A reviewable card-to-current-player import, never a write operation."""

    target_playerid: int
    fields: Mapping[str, int]
    source: Mapping[str, Any]
    warnings: tuple[str, ...] = ()


KIT_IMPORT_FIELDS = frozenset(
    {
        "jerseyfit", "jerseysleevelengthcode", "socklengthcode",
        "shoetypecode", "shoedesigncode", "shoecolorcode1",
    }
)
_BODY_IMPORT_FIELDS = frozenset(
    {
        "height", "weight", "bodytypecode", "muscularitycode",
        "runstylecode", "runningcode1", "runningcode2",
    }
)
_DIRECT_IMPORT_FIELDS = frozenset(
    {
        "overallrating", "potential", "skillmoves", "weakfootabilitytypecode",
        "preferredposition1", "preferredposition2", "preferredposition3",
        "preferredposition4", "preferredfoot",
        "trait1", "trait2", "icontrait1", "icontrait2",
    }
) | _BODY_IMPORT_FIELDS | KIT_IMPORT_FIELDS
# Body, movement and kit codes are not 0–99 attributes. Clamping them would
# turn an 08/09 CR7 body type into 99 and silently wreck the look.
_UNCLAMPED_IMPORT_FIELDS = (
    _BODY_IMPORT_FIELDS
    | KIT_IMPORT_FIELDS
    | frozenset({"trait1", "trait2", "icontrait1", "icontrait2"})
)
_ATTR_RE = re.compile(
    r"^(acceleration|aggression|agility|balance|ballcontrol|composure|crossing|"
    r"curve|defensiveawareness|dribbling|finishing|freekickaccuracy|gkdiving|"
    r"gkhandling|gkkicking|gkpositioning|gkreflexes|headingaccuracy|"
    r"interceptions|jumping|longpassing|longshots|penalties|positioning|"
    r"reactions|shortpassing|shotpower|slidingtackle|sprintspeed|stamina|"
    r"standingtackle|strength|vision|volleys)$"
)

# The Career/Live Editor table stores skill moves as 0..4, while the external
# card sources in the local universe publish the display value (1..5 stars).
# ``le_base`` is the only in-game/raw source in that database.  Keeping this
# provenance here prevents a card source from leaking its display encoding into
# a validated FC 26 write patch.
_DISPLAY_STAR_SOURCES = frozenset({"fut", "career", "ea_official"})
_RAW_SKILLMOVE_SOURCES = frozenset({"le_base"})


def _norm(value: str) -> str:
    value = unicodedata.normalize("NFKD", str(value or "").casefold())
    return " ".join(
        "".join(ch for ch in value if not unicodedata.combining(ch)).split()
    )


def _integer(value: Any) -> int | None:
    if value in (None, "") or isinstance(value, bool):
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _year_number(value: str) -> int | None:
    token = str(value or "").strip().casefold()
    match = re.search(r"(\d{2,4})", token)
    if not match:
        return None
    number = int(match.group(1))
    return number % 100 if number >= 2000 else number


def _card_skillmoves_to_raw(
    value: int,
    *,
    source_kind: str,
    raw_name: str,
) -> tuple[int | None, str]:
    """Translate recognized catalog encoding to FC 26's raw 0..4 value."""
    kind = (source_kind or "").strip().casefold()
    # ``skillmoves`` is the catalog's canonical/raw spelling. The camel-case
    # and underscore spellings are source API display-star fields.
    external_alias = raw_name in {"skillMoves", "skill_moves", "skills", "Skills"}
    if kind in _DISPLAY_STAR_SOURCES or (external_alias and kind not in _RAW_SKILLMOVE_SOURCES):
        if 1 <= value <= 5:
            return value - 1, "Card skill moves were converted from display stars to FC raw storage."
        return None, "Card skill moves were skipped because this external card has no valid 1-5 star value."
    if kind in _RAW_SKILLMOVE_SOURCES:
        if 0 <= value <= 4:
            return value, ""
        return None, "Card skill moves were skipped because the FC raw value is outside 0-4."
    if value == 5:
        return 4, "Card skill moves were inferred as 5 display stars and converted to FC raw storage."
    if 0 <= value <= 4:
        return value, ""
    return None, "Card skill moves were skipped because the value is outside FC's 0-4 raw range."


def card_key(card: Mapping[str, Any]) -> str:
    """Stable identity for a particular card variant."""
    obs = card.get("obs_id")
    if obs not in (None, ""):
        return f"obs:{obs}"
    return "|".join(
        str(card.get(k) or "")
        for k in ("year", "playerid", "revision", "overallrating", "source")
    )


class LocalCatalog:
    """Read-only multi-year search plus state-database-backed favorites."""

    def __init__(
        self,
        catalog_path: Path | None = None,
        universe_path: Path | None = None,
        *,
        state_db: Any = None,
    ) -> None:
        self.catalog_path = Path(catalog_path) if catalog_path else None
        self.universe_path = Path(universe_path) if universe_path else None
        self.state_db = state_db

    @property
    def available(self) -> bool:
        return any(
            path is not None and path.is_file()
            for path in (self.universe_path, self.catalog_path)
        )

    def search(
        self,
        query: str | CatalogQuery = "",
        *,
        year: str = "",
        ovr_min: int | None = None,
        ovr_max: int | None = None,
        position: int | None = None,
        source: str = "",
        limit: int = 50,
    ) -> Sequence[Mapping[str, Any]]:
        spec = query if isinstance(query, CatalogQuery) else CatalogQuery(
            text=str(query or ""), year=year, ovr_min=ovr_min,
            ovr_max=ovr_max, position=position, source=source, limit=limit,
        )
        spec = CatalogQuery(
            text=spec.text.strip(), year=spec.year.strip(),
            ovr_min=_integer(spec.ovr_min), ovr_max=_integer(spec.ovr_max),
            position=_integer(spec.position), source=spec.source.strip(),
            limit=max(1, min(int(spec.limit), 200)),
        )
        if not spec.text and not any(
            (spec.year, spec.ovr_min is not None, spec.ovr_max is not None,
             spec.position is not None, spec.source)
        ):
            return ()

        errors: list[str] = []
        if self.universe_path is not None and self.universe_path.is_file():
            try:
                return self._search_universe(self.universe_path, spec)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"universe: {exc}")
        if self.catalog_path is not None and self.catalog_path.is_file():
            try:
                return self._search_catalog(self.catalog_path, spec)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"catalog: {exc}")
        if not self.available:
            raise CatalogError(
                "Card catalog missing - expected card_db/universe.sqlite or "
                "card_db/catalog.sqlite next to the app."
            )
        raise CatalogError(
            "Catalog present but unreadable: " + ("; ".join(errors) or "unknown error")
        )

    def variants(
        self, card_or_person: Mapping[str, Any] | int, *, limit: int = 200
    ) -> Sequence[Mapping[str, Any]]:
        person_id = (
            _integer(card_or_person.get("person_id"))
            if isinstance(card_or_person, Mapping)
            else _integer(card_or_person)
        )
        if person_id is not None and self.universe_path and self.universe_path.is_file():
            return self._universe_person_variants(self.universe_path, person_id, limit)
        if isinstance(card_or_person, Mapping):
            return self.search(str(card_or_person.get("name") or ""), limit=limit)
        return ()

    def best_matches(
        self,
        target: Mapping[str, Any],
        *,
        year: str = "",
        limit: int = 8,
    ) -> Sequence[Mapping[str, Any]]:
        name = str(target.get("name") or target.get("playername") or "")
        candidates = self.search(name, year=year, limit=max(40, limit * 8))
        ranked = [
            (self.match_score(target, card), dict(card)) for card in candidates
        ]
        ranked = [item for item in ranked if item[0] >= 0]
        ranked.sort(
            key=lambda item: (
                -item[0],
                -(_integer(item[1].get("overallrating")) or 0),
                card_key(item[1]),
            )
        )
        return tuple(
            dict(card, _match_score=round(score, 2))
            for score, card in ranked[: max(1, limit)]
        )

    @staticmethod
    def match_score(target: Mapping[str, Any], card: Mapping[str, Any]) -> float:
        wanted = _norm(str(target.get("name") or target.get("playername") or ""))
        candidate = _norm(str(card.get("name") or ""))
        if not wanted or not candidate:
            return -1.0
        if wanted == candidate:
            score = 100.0
        elif wanted in candidate or candidate in wanted:
            score = 70.0
        else:
            a, b = set(wanted.split()), set(candidate.split())
            overlap = len(a & b) / max(len(a), 1)
            if overlap == 0:
                return -1.0
            score = 40.0 * overlap
        target_pid = _integer(target.get("playerid"))
        card_pid = _integer(card.get("person_id") or card.get("playerid"))
        if target_pid is not None and target_pid == card_pid:
            score += 50.0
        target_ovr = _integer(
            target.get("overallrating") or target.get("ovr") or target.get("rating")
        )
        card_ovr = _integer(card.get("overallrating") or card.get("ovr"))
        if target_ovr is not None and card_ovr is not None:
            score += max(0.0, 25.0 - abs(target_ovr - card_ovr) * 1.5)
        target_pos = _integer(target.get("preferredposition1") or target.get("position"))
        card_pos = _integer(card.get("preferredposition1") or card.get("position"))
        if target_pos is not None and target_pos == card_pos:
            score += 15.0
        return score + (card_ovr or 0) * 0.01

    @staticmethod
    def compare(
        before: Mapping[str, Any] | None, after: Mapping[str, Any]
    ) -> tuple[Mapping[str, Any], ...]:
        before = before or {}
        fields = sorted(
            {
                key for key in set(before) | set(after)
                if key in _DIRECT_IMPORT_FIELDS or _ATTR_RE.fullmatch(str(key))
            }
        )
        rows = []
        for field in fields:
            old, new = _integer(before.get(field)), _integer(after.get(field))
            if old is None and new is None:
                continue
            rows.append(
                {
                    "field": field, "before": old, "after": new,
                    "delta": new - old if old is not None and new is not None else None,
                    "changed": old != new,
                }
            )
        rows.sort(
            key=lambda row: (
                not row["changed"], -abs(row["delta"] or 0), row["field"]
            )
        )
        return tuple(rows)

    @staticmethod
    def prepare_cross_year_import(
        card: Mapping[str, Any], *, target_playerid: int
    ) -> ImportPlan:
        """Convert a historical/FUT card into reviewable FC26 edit fields.

        Identity, team, contract and source IDs are never copied.  The target
        remains the currently selected FC26 player.
        """
        target = _integer(target_playerid)
        if target is None or target <= 0:
            raise ValueError("A valid current-save target player id is required.")
        fields: dict[str, int] = {}
        warnings: list[str] = []
        expanded = dict(card)
        attrs = card.get("attrs")
        if isinstance(attrs, str):
            try:
                attrs = json.loads(attrs)
            except json.JSONDecodeError:
                attrs = {}
        if isinstance(attrs, Mapping):
            expanded.update(attrs)
        aliases = {
            "overall": "overallrating", "ovr": "overallrating",
            "weakfoot": "weakfootabilitytypecode",
            # These aliases are conventionally external, display-star names.
            # Source provenance below still decides whether a numeric value is
            # display stars or FC's raw 0..4 storage value.
            "skill_moves": "skillmoves", "skillmoves": "skillmoves",
            "skills": "skillmoves",
        }
        for raw_key, raw_value in expanded.items():
            raw_name = str(raw_key).strip()
            key = aliases.get(raw_name.casefold(), raw_name.casefold())
            if key not in _DIRECT_IMPORT_FIELDS and not _ATTR_RE.fullmatch(key):
                continue
            value = _integer(raw_value)
            if value is None:
                continue
            if key == "skillmoves":
                value, warning = _card_skillmoves_to_raw(
                    value,
                    source_kind=str(
                        expanded.get("source_kind")
                        or card.get("source_kind")
                        or card.get("source")
                        or ""
                    ),
                    raw_name=raw_name,
                )
                if warning:
                    warnings.append(warning)
                if value is None:
                    continue
            if key not in _UNCLAMPED_IMPORT_FIELDS:
                value = max(0, min(99, value))
            fields[key] = value
        year = str(card.get("year") or "")
        if year and year not in {"26", "2026", "local", "current"}:
            warnings.append(
                f"Source is FC/FIFA {year}; review positions, traits and body values."
            )
        if not fields:
            warnings.append("This card has no supported editable fields.")
        return ImportPlan(
            target_playerid=target,
            fields=fields,
            source={
                "card_key": card_key(card),
                "name": str(card.get("name") or ""),
                "year": year,
                "variant": str(card.get("revision") or card.get("variant") or ""),
                "source": str(card.get("source") or ""),
            },
            warnings=tuple(warnings),
        )


    def is_favorite(self, card: Mapping[str, Any]) -> bool:
        if self.state_db is None:
            return False
        year, playerid, revision = self._favorite_identity(card)
        return self.state_db.is_favorite(year, playerid, revision)

    def toggle_favorite(self, card: Mapping[str, Any]) -> bool:
        if self.state_db is None:
            raise CatalogError("Favorites are unavailable because state.sqlite is not open.")
        year, playerid, revision = self._favorite_identity(card)
        if self.state_db.is_favorite(year, playerid, revision):
            self.state_db.remove_favorite(year, playerid, revision)
            return False
        self.state_db.add_favorite(
            year=year, playerid=playerid, revision=revision,
            name=str(card.get("name") or ""),
            ovr=_integer(card.get("overallrating") or card.get("ovr")),
            card=dict(card),
        )
        return True

    def favorites(self, *, limit: int = 500) -> Sequence[Mapping[str, Any]]:
        if self.state_db is None:
            return ()
        return tuple(
            dict(row.card or {}, name=row.name, year=row.year,
                 playerid=row.playerid, revision=row.revision, ovr=row.ovr)
            for row in self.state_db.favorites(limit=limit)
        )

    @staticmethod
    def _favorite_identity(card: Mapping[str, Any]) -> tuple[str, str, str]:
        year = str(card.get("year") or "")
        playerid = str(
            card.get("variant_id") or card.get("playerid") or card.get("person_id") or ""
        )
        revision = str(card.get("revision") or card.get("variant") or "")
        if not year or not playerid:
            raise CatalogError("This result has no stable year/player identity.")
        return year, playerid, revision

    def _search_universe(
        self, path: Path, spec: CatalogQuery
    ) -> tuple[dict[str, Any], ...]:
        where, args = [], []
        if spec.text:
            folded = _norm(spec.text)
            if len(folded) < 3:
                raise CatalogError("Type at least 3 letters of the player name.")
            like = f"{folded}%" if len(folded) < 5 else f"%{folded}%"
            where.append("(p.name_norm LIKE ? OR lower(p.display_name) LIKE ?)")
            args.extend((like, like))
        if spec.year:
            year = _year_number(spec.year)
            if year is None:
                return ()
            where.append("(o.year=? OR o.year=?)")
            args.extend((year, year + 2000))
        if spec.ovr_min is not None:
            where.append("o.overall>=?")
            args.append(spec.ovr_min)
        if spec.ovr_max is not None:
            where.append("o.overall<=?")
            args.append(spec.ovr_max)
        if spec.position is not None:
            where.append("? IN (o.preferredposition1,o.preferredposition2,"
                         "o.preferredposition3,o.preferredposition4)")
            args.append(spec.position)
        if spec.source:
            where.append("lower(o.source_kind)=?")
            args.append(spec.source.casefold())
        args.append(spec.limit)
        sql = (
            "SELECT p.display_name AS name, p.exists_in_fc26, o.* "
            "FROM observation o JOIN person p ON p.person_id=o.person_id "
            f"WHERE {' AND '.join(where) if where else '1=1'} "
            "ORDER BY o.overall DESC, o.year DESC, p.display_name COLLATE NOCASE "
            "LIMIT ?"
        )
        with self._connect(path) as con:
            return tuple(self._normalize_universe(dict(row)) for row in con.execute(sql, args))

    def _universe_person_variants(
        self, path: Path, person_id: int, limit: int
    ) -> tuple[dict[str, Any], ...]:
        spec_limit = max(1, min(int(limit), 500))
        with self._connect(path) as con:
            rows = con.execute(
                "SELECT p.display_name AS name,p.exists_in_fc26,o.* "
                "FROM observation o JOIN person p ON p.person_id=o.person_id "
                "WHERE o.person_id=? ORDER BY o.year DESC,o.overall DESC LIMIT ?",
                (person_id, spec_limit),
            )
            return tuple(self._normalize_universe(dict(row)) for row in rows)

    @staticmethod
    def _normalize_universe(row: dict[str, Any]) -> dict[str, Any]:
        # ``variant_id`` is a FUT/card-definition id and cannot target a career
        # player. ``person_id`` is the stable real-player identity.
        row["playerid"] = row.get("person_id")
        row["overallrating"] = row.get("overall")
        row["revision"] = row.get("variant") or ""
        row["club"] = row.get("club_name")
        row["nation"] = row.get("nationality_name")
        row["weakfootabilitytypecode"] = row.get("weakfoot")
        attrs = row.get("attrs")
        if isinstance(attrs, str):
            try:
                decoded = json.loads(attrs)
                if isinstance(decoded, dict):
                    row.update(decoded)
            except json.JSONDecodeError:
                pass
        row["_card_key"] = card_key(row)
        return row

    def _search_catalog(
        self, path: Path, spec: CatalogQuery
    ) -> tuple[dict[str, Any], ...]:
        where, args = [], []
        if spec.text:
            folded = _norm(spec.text)
            if len(folded) < 3:
                raise CatalogError("Type at least 3 letters of the player name.")
            like = f"{folded}%" if len(folded) < 5 else f"%{folded}%"
            where.append("(name_norm LIKE ? OR revision_norm LIKE ?)")
            args.extend((like, like))
        if spec.year:
            where.append("year IN (?,?)")
            number = _year_number(spec.year)
            if number is None:
                return ()
            short = str(number)
            args.extend((short, f"20{number:02d}"))
        if spec.ovr_min is not None:
            where.append("ovr>=?")
            args.append(spec.ovr_min)
        if spec.ovr_max is not None:
            where.append("ovr<=?")
            args.append(spec.ovr_max)
        args.append(spec.limit)
        with self._connect(path) as con:
            rows = con.execute(
                f"SELECT * FROM cards WHERE {' AND '.join(where) if where else '1=1'} "
                "ORDER BY ovr DESC,name_norm LIMIT ?", args,
            )
            out = []
            for row in rows:
                raw = dict(row)
                try:
                    payload = json.loads(raw.pop("payload", "{}"))
                except json.JSONDecodeError:
                    payload = {}
                card = dict(raw)
                if isinstance(payload, dict):
                    card.update(payload)
                card.setdefault("overallrating", raw.get("ovr"))
                card.setdefault("revision", raw.get("revision") or "")
                if spec.position is not None and spec.position not in {
                    _integer(card.get(f"preferredposition{i}")) for i in range(1, 5)
                }:
                    continue
                if spec.source and spec.source.casefold() not in str(
                    card.get("source") or ""
                ).casefold():
                    continue
                card["_card_key"] = card_key(card)
                out.append(card)
            return tuple(out)

    @staticmethod
    def _connect(path: Path) -> sqlite3.Connection:
        con = sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        return con
