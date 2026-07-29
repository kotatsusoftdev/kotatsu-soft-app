import json
from pathlib import Path

from spec_link_registry import (
    get_latest_linked_game,
    get_latest_spec_for_game,
    link_spec_to_game,
    load_registry,
    register_generated_spec,
)


def test_register_generated_spec_and_link(monkeypatch, tmp_path: Path) -> None:
    registry_file = tmp_path / "spec_game_links.json"
    monkeypatch.setenv("SPEC_LINK_REGISTRY_PATH", str(registry_file))

    spec_path = tmp_path / "spec_example_20260722.md"
    spec_path.write_text("# sample", encoding="utf-8")

    record = register_generated_spec(
        spec_file=spec_path,
        selected_plan="sample plan",
        proposal_summary="summary",
        theme="sample theme",
    )

    assert record["spec_file"] == "spec_example_20260722.md"

    linked = link_spec_to_game(
        spec_file="spec_example_20260722.md",
        game_id="matatabi_chaos",
        game_path="game-projects/001_matatabi_chaos/src/index.html",
        game_title="マタタビ大合唱 ～モフモフ・カオス・タワー～",
    )

    assert linked["linked_games"][0]["game_id"] == "matatabi_chaos"

    latest = get_latest_spec_for_game("matatabi_chaos")
    assert latest is not None
    assert latest["spec_file"] == "spec_example_20260722.md"

    payload = load_registry()
    assert len(payload["records"]) == 1


def test_get_latest_linked_game_picks_newest_link(monkeypatch, tmp_path: Path) -> None:
    registry_file = tmp_path / "spec_game_links.json"
    monkeypatch.setenv("SPEC_LINK_REGISTRY_PATH", str(registry_file))
    registry_file.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "records": [
                    {
                        "spec_file": "spec_old.md",
                        "artifact_stem": "old_stem",
                        "created_at": "2026-07-01T00:00:00+00:00",
                        "linked_games": [
                            {
                                "game_id": "old_game",
                                "game_title": "Old",
                                "game_path": "game-projects/old/src/index.html",
                                "linked_at": "2026-07-01T01:00:00+00:00",
                            }
                        ],
                    },
                    {
                        "spec_file": "spec_new.md",
                        "artifact_stem": "new_stem",
                        "created_at": "2026-07-02T00:00:00+00:00",
                        "linked_games": [
                            {
                                "game_id": "new_game",
                                "game_title": "New Title",
                                "game_path": "game-projects/new/src/index.html",
                                "linked_at": "2026-07-03T01:00:00+00:00",
                            }
                        ],
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    latest = get_latest_linked_game()
    assert latest is not None
    assert latest["game_id"] == "new_game"
    assert latest["artifact_stem"] == "new_stem"
    assert latest["game_title"] == "New Title"


def test_get_latest_linked_game_returns_none_when_unlinked(monkeypatch, tmp_path: Path) -> None:
    registry_file = tmp_path / "spec_game_links.json"
    monkeypatch.setenv("SPEC_LINK_REGISTRY_PATH", str(registry_file))
    registry_file.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "records": [
                    {
                        "spec_file": "spec_only.md",
                        "artifact_stem": "only_stem",
                        "created_at": "2026-07-01T00:00:00+00:00",
                        "linked_games": [],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    assert get_latest_linked_game() is None
