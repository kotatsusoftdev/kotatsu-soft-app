from pathlib import Path

from game_id_allocator import (
    allocate_game_identity,
    ensure_game_id_header,
    extract_game_id_from_spec,
    extract_game_title_from_spec,
    normalize_game_id,
    next_dir_number,
)
from spec_link_registry import load_registry, register_generated_spec


def test_normalize_and_extract_game_id() -> None:
    assert normalize_game_id("Matatabi-Chaos") == "matatabi_chaos"
    assert normalize_game_id("pv") is None
    assert normalize_game_id("1bad") is None

    text = "# Spec\n\n- game_id: cool_cats\n\n【ゲームタイトル】にゃんパズル\n"
    assert extract_game_id_from_spec(text) == "cool_cats"
    assert extract_game_title_from_spec(text) == "にゃんパズル"


def test_ensure_game_id_header_replaces_existing() -> None:
    text = "- game_id: old_id\n\n# Title\n"
    updated = ensure_game_id_header(text, "new_id")
    assert updated.startswith("- game_id: new_id\n")
    assert "old_id" not in updated


def test_allocate_uses_filesystem_and_registry(monkeypatch, tmp_path: Path) -> None:
    repo_root = tmp_path / "kotatsu-soft"
    projects = repo_root / "game-projects"
    projects.mkdir(parents=True)
    (projects / "001_matatabi_chaos").mkdir()

    registry_file = tmp_path / "spec_game_links.json"
    monkeypatch.setenv("SPEC_LINK_REGISTRY_PATH", str(registry_file))
    monkeypatch.setenv("KOTATSU_REPO_ROOT", str(repo_root))

    # Provisional reservation without directory yet
    register_generated_spec(
        spec_file=tmp_path / "spec_pending.md",
        selected_plan="plan",
        proposal_summary="summary",
        theme="theme",
        game_id="reserved_slug",
        game_path="game-projects/002_reserved_slug/src/index.html",
        game_title="Reserved",
    )

    assert next_dir_number(repo_root=repo_root) == 3

    identity = allocate_game_identity(
        preferred_game_id="matatabi_chaos",
        game_title="Collision Case",
        repo_root=repo_root,
    )
    assert identity.dir_number == 3
    assert identity.game_id == "matatabi_chaos_2"
    assert identity.game_path == "game-projects/003_matatabi_chaos_2/src/index.html"


def test_register_generated_spec_auto_links(monkeypatch, tmp_path: Path) -> None:
    registry_file = tmp_path / "spec_game_links.json"
    monkeypatch.setenv("SPEC_LINK_REGISTRY_PATH", str(registry_file))

    spec_path = tmp_path / "spec_auto_20260730.md"
    spec_path.write_text("- game_id: cool_cats\n", encoding="utf-8")

    record = register_generated_spec(
        spec_file=spec_path,
        selected_plan="plan",
        proposal_summary="summary",
        theme="theme",
        game_id="cool_cats",
        game_path="game-projects/002_cool_cats/src/index.html",
        game_title="Cool Cats",
    )

    assert record["linked_games"][0]["game_id"] == "cool_cats"
    assert record["linked_games"][0]["game_path"].endswith("002_cool_cats/src/index.html")

    payload = load_registry()
    assert payload["records"][0]["linked_games"][0]["game_id"] == "cool_cats"


def test_allocate_fallback_when_preferred_missing(monkeypatch, tmp_path: Path) -> None:
    repo_root = tmp_path / "kotatsu-soft"
    (repo_root / "game-projects").mkdir(parents=True)
    monkeypatch.setenv("SPEC_LINK_REGISTRY_PATH", str(tmp_path / "empty.json"))
    monkeypatch.setenv("KOTATSU_REPO_ROOT", str(repo_root))

    identity = allocate_game_identity(preferred_game_id=None, repo_root=repo_root)
    assert identity.dir_number == 1
    assert identity.game_id == "game_001"
