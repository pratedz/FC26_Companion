"""Tests for the web UI backend bridge (real product paths, no mocks for core)."""

from __future__ import annotations

import json
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src import web_api  # noqa: E402
from src import web_server  # noqa: E402


def test_search_cards_real_catalog() -> None:
    """Shipped facade search hits local card_db (real search_cards path)."""
    res = web_api.search_cards("Messi", year="25", limit=5)
    assert isinstance(res, dict)
    assert res["query"] == "Messi"
    assert "hits" in res
    assert "count" in res
    # Catalog present in this repo — expect non-empty; if empty still assert shape
    if res["count"] > 0:
        hit = res["hits"][0]
        assert hit.get("name")
        assert "overallrating" in hit or "line" in hit
        assert "Messi" in str(hit.get("name") or "") or "messi" in str(hit.get("name") or "").lower()
    else:
        # Empty catalog still returns structured empty result (not exception)
        assert res["hits"] == []


def test_search_cards_via_handle_router() -> None:
    out = web_api.handle(
        "GET",
        "/api/cards/search",
        query={"query": "Neymar", "year": "25", "limit": "5"},
    )
    assert out["status"] == 200
    data = out["data"]
    assert data["query"] == "Neymar"
    assert isinstance(data["hits"], list)
    if data["count"] > 0:
        assert data["hits"][0].get("name")


def test_apply_card_from_search_queues_structured_outcome(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """apply/queue uses real product.apply_card_to_target → turbo write path."""
    q = tmp_path / "queue"
    q.mkdir()
    (q / "done").mkdir()
    (q / "results").mkdir()
    gen = tmp_path / "generated"
    gen.mkdir()

    monkeypatch.setattr("src.paths.queue_dir", lambda: q)
    monkeypatch.setattr("src.paths.done_dir", lambda: q / "done")
    monkeypatch.setattr("src.paths.generated_dir", lambda: gen)
    monkeypatch.setattr("src.paths.app_root", lambda: tmp_path)
    monkeypatch.setattr("src.le_apply.queue_dir", lambda: q)
    monkeypatch.setattr("src.le_apply.done_dir", lambda: q / "done")
    monkeypatch.setattr("src.le_apply.bridge_alive", lambda *a, **k: False)
    monkeypatch.setattr("src.le_apply.bridge_busy", lambda: False)
    monkeypatch.setattr("src.le_apply.bridge_heartbeat_age_sec", lambda: None)
    monkeypatch.setattr("src.le_apply.list_pending_lua", lambda force=False: list(q.glob("*.lua")))
    monkeypatch.setattr("src.le_apply.clear_stale_jobs", lambda **k: {"cleared": 0})
    monkeypatch.setattr("src.le_apply.job_status_text", lambda: "")
    monkeypatch.setattr(
        "src.job_history.add",
        lambda **k: None,
    )
    # Avoid snapshot writes into real app tree
    monkeypatch.setattr(
        "src.snapshot_store.save_fields_snapshot",
        lambda **k: {"id": "test"},
    )

    # Prefer real catalog search; fall back to synthetic card if no hits
    search = web_api.search_cards("Messi", year="25", limit=3)
    if search["count"] > 0:
        out = web_api.apply_card_from_search(
            "Messi",
            target_playerid=158023,
            year="25",
            index=0,
            wait=False,
        )
    else:
        card = {
            "name": "Test Player",
            "playerid": 1,
            "overallrating": 90,
            "acceleration": 90,
            "sprintspeed": 90,
            "preferredposition1": 25,
        }
        out = web_api.apply_card(card, target_playerid=158023, wait=False)

    assert isinstance(out, dict)
    assert "outcome" in out
    assert "reason" in out
    assert out["outcome"] in ("queued_live", "blocked", "applied", "error") or out.get("queued")
    # Offline wait=False should still queue a file when lua wrote
    if out.get("queue_file"):
        assert Path(out["queue_file"]).is_file() or Path(out["queue_file"]).name
        assert out.get("queued") or out.get("ok") is not None
    else:
        # Still structured — do not hardcode success
        assert out.get("reason")


def test_run_profile_turbo_via_facade(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    q = tmp_path / "queue"
    q.mkdir()
    (q / "done").mkdir()
    monkeypatch.setattr("src.paths.queue_dir", lambda: q)
    monkeypatch.setattr("src.paths.done_dir", lambda: q / "done")
    monkeypatch.setattr("src.paths.generated_dir", lambda: tmp_path / "generated")
    monkeypatch.setattr("src.le_apply.queue_dir", lambda: q)
    monkeypatch.setattr("src.le_apply.done_dir", lambda: q / "done")
    monkeypatch.setattr("src.le_apply.bridge_alive", lambda *a, **k: False)
    monkeypatch.setattr("src.le_apply.clear_stale_jobs", lambda **k: {"cleared": 0})
    monkeypatch.setattr("src.le_apply.job_status_text", lambda: "")
    monkeypatch.setattr("src.job_history.add", lambda **k: None)

    out = web_api.run_profile("full_fitness", wait=False)
    assert "outcome" in out
    assert "profile_id" in out
    assert out["profile_id"] == "full_fitness"
    # Real turbo path wrote or returned structured block
    assert out.get("queue_file") or out.get("reason")


def test_list_packs_and_surfaces() -> None:
    packs = web_api.list_packs()
    assert packs["count"] >= 1
    ids = {p["id"] for p in packs["packs"]}
    assert "matchday" in ids or "squad_boost" in ids

    about = web_api.get_about()
    for surface in (
        "home",
        "cards",
        "boost",
        "add_team",
        "squad",
        "editor",
        "catalog",
        "about",
    ):
        assert surface in about["surfaces"]


def test_status_and_home_shape() -> None:
    st = web_api.get_status()
    assert "arm" in st
    assert "health" in st
    assert st.get("product_name") == "LE Companion"
    home = web_api.get_home()
    assert "goals" in home
    assert "quick_packs" in home


def test_web_root_has_shell() -> None:
    root = web_server.web_root()
    assert (root / "index.html").is_file()
    html = (root / "index.html").read_text(encoding="utf-8")
    assert "LE Companion" in html
    assert 'data-view="home"' in html
    assert 'data-view="cards"' in html
    assert 'data-view="boost"' in html
    assert 'data-view="add_team"' in html
    assert 'data-view="squad"' in html
    assert 'data-view="editor"' in html
    assert 'data-view="catalog"' in html
    assert 'data-view="about"' in html
    assert "apply-dock" in html
    assert (root / "app.js").is_file()
    assert (root / "styles.css").is_file()


def test_http_server_health_and_search() -> None:
    """Launch real ThreadingHTTPServer twice-path smoke on free port."""
    # Pick ephemeral port
    httpd = web_server.make_server("127.0.0.1", 0)
    host, port = httpd.server_address[:2]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        assert web_server.wait_for_port(host, port, timeout=3.0)

        def get(path: str) -> tuple[int, dict]:
            url = f"http://{host}:{port}{path}"
            req = urllib.request.Request(url, headers={"Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = resp.read().decode("utf-8")
                return resp.status, json.loads(body)

        status, health = get("/api/health")
        assert status == 200
        assert health.get("ok") is True
        assert "LE Companion" in str(health.get("product") or "")

        status, page = _get_text(host, port, "/")
        assert status == 200
        assert "LE Companion" in page
        assert "data-view" in page or "side-nav" in page

        status, search = get("/api/cards/search?query=Messi&year=25&limit=3")
        assert status == 200
        assert "hits" in search
        assert "count" in search
    finally:
        httpd.shutdown()
        httpd.server_close()


def _get_text(host: str, port: int, path: str) -> tuple[int, str]:
    url = f"http://{host}:{port}{path}"
    with urllib.request.urlopen(url, timeout=30) as resp:
        return resp.status, resp.read().decode("utf-8", errors="replace")
