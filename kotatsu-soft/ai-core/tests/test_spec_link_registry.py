from pathlib import Path

from spec_link_registry import get_latest_spec_for_game, link_spec_to_game, load_registry, register_generated_spec


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
        game_id="mikan_buster",
        game_path="game-projects/001_mikan_buster/src/index.html",
        game_title="10秒コタツミカンバスター",
    )

    assert linked["linked_games"][0]["game_id"] == "mikan_buster"

    latest = get_latest_spec_for_game("mikan_buster")
    assert latest is not None
    assert latest["spec_file"] == "spec_example_20260722.md"

    payload = load_registry()
    assert len(payload["records"]) == 1
