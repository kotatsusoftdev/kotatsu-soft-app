from __future__ import annotations

import json
from pathlib import Path

import yaml

from artifact_naming import review_filename, review_path
from post_mortem import (
    artifact_stem_from_spec,
    build_evolution_prompt,
    collect_inputs,
    format_lesson_items,
    load_lessons,
    normalize_lesson_items,
    run_post_mortem,
    save_lessons,
)


def _write_agent_fixture(
    agents_root: Path,
    role: str,
    *,
    lesson_items: list[str],
    name: str,
) -> None:
    role_dir = agents_root / role
    role_dir.mkdir(parents=True, exist_ok=True)
    (role_dir / "config.yaml").write_text(
        "\n".join(
            [
                "agent:",
                f'  name: "{name}"',
                f'  role: "{role}"',
                f'  title: "{role} title"',
                "  llm:",
                '    model: "gemini-flash-lite-latest"',
                "    temperature: 0.2",
                "  evaluation_criteria:",
                f'    primary_focus: "{role} focus"',
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    save_lessons(
        agents_root,
        role,
        {
            "schema_version": 2,
            "updated_at": None,
            "last_artifact_stem": None,
            "lesson_items": lesson_items,
        },
    )


def test_artifact_stem_from_spec() -> None:
    assert (
        artifact_stem_from_spec("spec_テトリスと猫_20260725_124622.md")
        == "テトリスと猫_20260725_124622"
    )


def test_review_naming(tmp_path: Path) -> None:
    stem = "demo_20260725_120000"
    assert review_filename(stem) == f"review_{stem}.md"
    assert review_path(tmp_path, stem) == tmp_path / "shared" / "review" / f"review_{stem}.md"


def test_normalize_lesson_items_parses_numbered_list() -> None:
    text = "1. 一つ目の教訓です。\n2. 二つ目の教訓です。\n3. 三つ目の教訓です。"
    assert normalize_lesson_items(text) == [
        "一つ目の教訓です。",
        "二つ目の教訓です。",
        "三つ目の教訓です。",
    ]


def test_parse_evolution_response_splits_speech_and_lessons() -> None:
    from post_mortem import parse_evolution_response

    raw = (
        "【発表文】\n"
        "着地判定がずれていたから、次の教訓を得たよ。\n"
        "1. 判定ははっきり書く。\n\n"
        "【教訓】\n"
        "1. 判定ルールはわかりやすく決める。\n"
        "2. 仕様とコードをそろえる。\n"
        "3. テストで確認する。"
    )
    speech, items = parse_evolution_response(raw)
    assert "着地判定がずれていた" in speech
    assert items == [
        "判定ルールはわかりやすく決める。",
        "仕様とコードをそろえる。",
        "テストで確認する。",
    ]


def test_parse_evolution_response_legacy_lessons_only() -> None:
    from post_mortem import parse_evolution_response

    speech, items = parse_evolution_response(
        "1. 一つ目。\n2. 二つ目。\n3. 三つ目。"
    )
    assert items == ["一つ目。", "二つ目。", "三つ目。"]
    assert "次の教訓を得たよ" in speech
    assert "一つ目。" in speech


def test_prompt_prioritizes_review() -> None:
    from post_mortem import PostMortemInputs

    inputs = PostMortemInputs(
        artifact_stem="demo",
        spec_text="仕様本文",
        meeting_text="会議本文",
        review_text="レビュー本文",
        game_source="code",
        game_path=None,
        warnings=[],
    )
    prompt = build_evolution_prompt(
        role="dev",
        profile={
            "name": "スゴ杉",
            "title": "エンジニア",
            "primary_focus": "実現性",
            "tone": "落ち着いた優等生の口調",
            "mindset": "あわてず順番に整理する",
        },
        current_lessons=["旧1", "旧2", "旧3"],
        inputs=inputs,
    )
    assert prompt.index("【最重要: レビュー指摘・修正】") < prompt.index("【仕様書")
    assert "言い換えるだけは禁止" in prompt
    assert "中学生でもわかる" in prompt
    assert "【発表文】" in prompt
    assert "【教訓】" in prompt
    assert "落ち着いた優等生の口調" in prompt
    assert "旧1" in prompt
    assert format_lesson_items(["旧1", "旧2", "旧3"]) in prompt


def _fake_evolution_response(role: str) -> str:
    return (
        f"【発表文】\n"
        f"{role}として、判定のズレがあったから次の教訓を得たよ。\n"
        f"1. {role}の進化教訓1。\n"
        f"2. {role}の進化教訓2。\n"
        f"3. {role}の進化教訓3。\n\n"
        f"【教訓】\n"
        f"1. {role}の進化教訓1。\n"
        f"2. {role}の進化教訓2。\n"
        f"3. {role}の進化教訓3。"
    )


def test_collect_inputs_and_run_post_mortem(tmp_path: Path, monkeypatch) -> None:
    stem = "demo_theme_20260725_120000"
    shared = tmp_path / "shared"
    (shared / "specs").mkdir(parents=True)
    (shared / "meeting").mkdir(parents=True)
    (shared / "review").mkdir(parents=True)
    game = tmp_path / "game-projects" / "001_demo" / "src"
    game.mkdir(parents=True)
    game_file = game / "index.html"
    game_file.write_text("<html>demo game</html>", encoding="utf-8")

    (shared / "specs" / f"spec_{stem}.md").write_text("# 仕様\nデモ仕様", encoding="utf-8")
    meeting_line = {
        "role": "pm",
        "display_name": "すずかちゃん(PM)",
        "message": "1案に絞ろう",
        "type": "text",
    }
    (shared / "meeting" / f"meeting_{stem}.jsonl").write_text(
        json.dumps(meeting_line, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    (shared / "review" / f"review_{stem}.md").write_text(
        "# レビュー\n- 着地判定を直した\n",
        encoding="utf-8",
    )

    registry = {
        "schema_version": 1,
        "records": [
            {
                "artifact_stem": stem,
                "spec_file": f"spec_{stem}.md",
                "linked_games": [
                    {
                        "game_id": "demo",
                        "game_path": "game-projects/001_demo/src/index.html",
                        "game_title": "Demo",
                    }
                ],
            }
        ],
    }
    registry_path = shared / "specs" / "spec_game_links.json"
    registry_path.write_text(json.dumps(registry, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("SPEC_LINK_REGISTRY_PATH", str(registry_path))

    agents_root = tmp_path / "agents"
    _write_agent_fixture(
        agents_root, "pm", lesson_items=["旧PM1", "旧PM2", "旧PM3"], name="すずかちゃん(PM)"
    )
    _write_agent_fixture(
        agents_root, "dev", lesson_items=["旧Dev1", "旧Dev2", "旧Dev3"], name="スゴ杉くん(エンジニア)"
    )
    _write_agent_fixture(
        agents_root, "marketing", lesson_items=["旧Mkt1", "旧Mkt2", "旧Mkt3"], name="ヂャイアン(マーケ)"
    )

    inputs = collect_inputs(artifact_stem=stem, repo=tmp_path)
    assert "デモ仕様" in inputs.spec_text
    assert "1案に絞ろう" in inputs.meeting_text
    assert "着地判定" in inputs.review_text
    assert inputs.game_path == game_file
    assert "demo game" in inputs.game_source

    def fake_llm(*, role: str, prompt: str, model: str, temperature: float) -> str:
        return _fake_evolution_response(role)

    updates, warnings = run_post_mortem(
        artifact_stem=stem,
        agents_root=agents_root,
        repo=tmp_path,
        dry_run=False,
        llm_callable=fake_llm,
    )
    assert not any("not found" in w for w in warnings)
    assert len(updates) == 3
    assert updates[0].after == ["pmの進化教訓1。", "pmの進化教訓2。", "pmの進化教訓3。"]
    assert "判定のズレがあった" in updates[0].speech
    assert "pmの進化教訓1。" in updates[0].speech

    saved = load_lessons(agents_root, "pm")
    assert saved["lesson_items"][0] == "pmの進化教訓1。"
    assert saved["last_artifact_stem"] == stem
    assert saved["updated_at"]
    assert "lesson" not in saved

    raw = yaml.safe_load((agents_root / "pm" / "lessons_learned.yaml").read_text(encoding="utf-8"))
    assert raw["lesson_items"][2] == "pmの進化教訓3。"


def test_dry_run_does_not_write(tmp_path: Path, monkeypatch) -> None:
    stem = "dry_20260725_120000"
    shared = tmp_path / "shared"
    (shared / "specs").mkdir(parents=True)
    (shared / "meeting").mkdir(parents=True)
    (shared / "review").mkdir(parents=True)
    (shared / "specs" / f"spec_{stem}.md").write_text("# spec", encoding="utf-8")
    (shared / "meeting" / f"meeting_{stem}.jsonl").write_text("", encoding="utf-8")
    (shared / "review" / f"review_{stem}.md").write_text("# review", encoding="utf-8")

    registry_path = shared / "specs" / "spec_game_links.json"
    registry_path.write_text(
        json.dumps({"schema_version": 1, "records": [{"artifact_stem": stem, "linked_games": []}]}),
        encoding="utf-8",
    )
    monkeypatch.setenv("SPEC_LINK_REGISTRY_PATH", str(registry_path))

    agents_root = tmp_path / "agents"
    before = ["変更前1", "変更前2", "変更前3"]
    _write_agent_fixture(agents_root, "pm", lesson_items=before, name="PM")
    _write_agent_fixture(agents_root, "dev", lesson_items=before, name="Dev")
    _write_agent_fixture(agents_root, "marketing", lesson_items=before, name="Mkt")

    updates, _ = run_post_mortem(
        artifact_stem=stem,
        agents_root=agents_root,
        repo=tmp_path,
        dry_run=True,
        llm_callable=lambda **kwargs: (
            "【発表文】\n判定がずれていたから、次の教訓を得た。\n"
            "【教訓】\n1. dry1\n2. dry2\n3. dry3"
        ),
    )
    assert updates[0].after == ["dry1", "dry2", "dry3"]
    assert "判定がずれていた" in updates[0].speech
    assert load_lessons(agents_root, "pm")["lesson_items"] == before


def test_load_lessons_migrates_legacy_lesson_field(tmp_path: Path) -> None:
    role_dir = tmp_path / "pm"
    role_dir.mkdir(parents=True)
    (role_dir / "lessons_learned.yaml").write_text(
        "schema_version: 1\nlesson: 旧単一文\n",
        encoding="utf-8",
    )
    loaded = load_lessons(tmp_path, "pm")
    assert loaded["lesson_items"][0] == "旧単一文"
    assert loaded["lesson_items"][1] == "（未設定）"
    assert "lesson" not in loaded
