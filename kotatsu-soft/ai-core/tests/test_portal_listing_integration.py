import json
from pathlib import Path

from portal_listing_check import (
    check_portal_listing,
    extract_send_play_count_ids,
    parse_portal_cards,
)


PORTAL_CARD = """
<!DOCTYPE html>
<html><body>
<div class="game-grid">
  <article class="game-card">
    <a class="game-card-link" href="./001_demo_game/src/index.html" data-game-id="demo_game">play</a>
    <span class="play-count" data-stat-id="demo_game">0</span>
    <a class="action-btn play" data-game-id="demo_game" href="./001_demo_game/src/index.html">Play</a>
  </article>
</div>
</body></html>
"""


def _write_completed_game(repo_root: Path, game_id: str = "demo_game") -> Path:
    game_rel = f"game-projects/001_{game_id}/src/index.html"
    game_path = repo_root / game_rel
    game_path.parent.mkdir(parents=True, exist_ok=True)
    game_path.write_text(
        f"""<!DOCTYPE html>
<html><body>
<script src="../../common/stats.js"></script>
<script>
  KotatsuStats.sendPlayCount("{game_id}");
</script>
</body></html>
""",
        encoding="utf-8",
    )
    return game_path


def test_parse_portal_cards_extracts_ids() -> None:
    cards = parse_portal_cards(PORTAL_CARD)
    assert "demo_game" in cards
    assert cards["demo_game"]["stat_id"] == "demo_game"
    assert "001_demo_game/src/index.html" in cards["demo_game"]["play_href"].replace("\\", "/")


def test_extract_send_play_count_ids() -> None:
    html = 'sendPlayCount("matatabi_chaos"); sendPlayCount(\'other_id\');'
    assert extract_send_play_count_ids(html) == {"matatabi_chaos", "other_id"}


def test_portal_listing_ok_with_fixture(monkeypatch, tmp_path: Path) -> None:
    repo_root = tmp_path
    monkeypatch.setenv("KOTATSU_REPO_ROOT", str(repo_root))

    _write_completed_game(repo_root)
    portal = repo_root / "game-projects" / "index.html"
    portal.parent.mkdir(parents=True, exist_ok=True)
    portal.write_text(PORTAL_CARD, encoding="utf-8")

    registry = {
        "schema_version": 1,
        "records": [
            {
                "spec_file": "spec_demo.md",
                "linked_games": [
                    {
                        "game_id": "demo_game",
                        "game_path": "game-projects/001_demo_game/src/index.html",
                        "game_title": "Demo",
                    }
                ],
            }
        ],
    }
    registry_path = repo_root / "shared" / "specs" / "spec_game_links.json"
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    registry_path.write_text(json.dumps(registry), encoding="utf-8")
    monkeypatch.setenv("SPEC_LINK_REGISTRY_PATH", str(registry_path))

    report = check_portal_listing(repo_root=repo_root)
    assert report.ok, "\n".join(i.format() for i in report.issues)
    assert report.checked_game_ids == ["demo_game"]


def test_portal_listing_detects_missing_card(monkeypatch, tmp_path: Path) -> None:
    repo_root = tmp_path
    monkeypatch.setenv("KOTATSU_REPO_ROOT", str(repo_root))
    _write_completed_game(repo_root)

    portal = repo_root / "game-projects" / "index.html"
    portal.parent.mkdir(parents=True, exist_ok=True)
    portal.write_text("<!DOCTYPE html><html><body><div class='game-grid'></div></body></html>", encoding="utf-8")

    registry = {
        "schema_version": 1,
        "records": [
            {
                "spec_file": "spec_demo.md",
                "linked_games": [
                    {
                        "game_id": "demo_game",
                        "game_path": "game-projects/001_demo_game/src/index.html",
                        "game_title": "Demo",
                    }
                ],
            }
        ],
    }
    report = check_portal_listing(repo_root=repo_root, registry=registry)
    assert not report.ok
    assert any(i.kind == "listing" for i in report.issues)


def test_portal_listing_integration_against_repo() -> None:
    """実リポジトリ: 完成リンク済みゲームがポータル掲載契約を満たすこと。"""
    report = check_portal_listing()
    assert report.checked_game_ids, "完成リンク済みゲームが1件以上ある想定"
    assert report.ok, "\n".join(i.format() for i in report.issues)
