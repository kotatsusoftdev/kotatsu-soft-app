from datetime import datetime
from pathlib import Path

import pytest

from artifact_naming import (
    MAX_ARTIFACT_SLUG_CHARS,
    build_artifact_stem,
    meeting_log_filename,
    meeting_log_path,
    sanitize_artifact_slug,
    spec_filename,
    spec_path,
)
from meeting_chat_log import MeetingChatLogWriter, normalize_plain_text
from theme_proposal import build_meeting_theme_parts


def test_sanitize_artifact_slug() -> None:
    assert sanitize_artifact_slug("テトリスみたいなゲームを作って") == "テトリスみたいなゲームを作って"
    assert sanitize_artifact_slug("hello world!!") == "hello_world"
    assert sanitize_artifact_slug("   ") == "untitled"


def test_sanitize_artifact_slug_truncates_long_input() -> None:
    long_source = "あ" * 300
    slug = sanitize_artifact_slug(long_source)
    assert len(slug) <= MAX_ARTIFACT_SLUG_CHARS
    stem = build_artifact_stem(long_source, datetime(2026, 8, 2, 11, 0, 0))
    assert len(meeting_log_filename(stem)) <= 255


def test_build_artifact_stem_uses_title_not_full_theme_dump() -> None:
    parts = build_meeting_theme_parts(
        {
            "title": "トマホーク・エッグスタンド：物理法則完全無視の卵投げ",
            "concept_summary": "どう見ても卵を置く気がない凶悪なトマホーク型の武器をフリック操作でぶん投げ、"
            "移動するゴールに卵を破壊せずに着地させる物理演算バカゲー。"
            + ("詳細詰め込み" * 40),
            "approach_type": "元ネタ直球",
            "design_intent": "長い狙い" * 30,
            "synergy_reason": "長いシナジー" * 30,
            "viral_point": "長いバズ" * 30,
            "combined_sources": {
                "trend_label": "長いトレンド" * 20,
                "mechanic_label": "長いメカニクス" * 20,
            },
        }
    )
    ts = datetime(2026, 8, 2, 11, 22, 58)
    stem = build_artifact_stem(parts.title, ts)
    filename = meeting_log_filename(stem)
    assert len(filename) <= 255
    assert "トマホーク" in stem
    assert "シナジー" not in stem
    assert "バズ" not in stem


def test_build_artifact_stem_uses_shared_timestamp() -> None:
    ts = datetime(2026, 7, 25, 10, 12, 31)
    stem = build_artifact_stem("テトリスみたいなゲームを作って", ts)
    assert stem == "テトリスみたいなゲームを作って_20260725_101231"
    assert meeting_log_filename(stem) == f"meeting_{stem}.jsonl"
    assert spec_filename(stem) == f"spec_{stem}.md"


def test_meeting_and_spec_paths_share_stem(tmp_path: Path) -> None:
    stem = "sample_theme_20260725_101231"
    assert meeting_log_path(tmp_path, stem).as_posix().endswith(
        "shared/meeting/meeting_sample_theme_20260725_101231.jsonl"
    )
    assert spec_path(tmp_path, stem).as_posix().endswith(
        "shared/specs/spec_sample_theme_20260725_101231.md"
    )


def test_normalize_plain_text_strips_markdown() -> None:
    text = "**太字** と `- item`"
    assert normalize_plain_text(text) == "太字 と ・item"


def test_meeting_chat_log_writer_writes_jsonl(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("meeting_chat_log.repo_root", lambda: tmp_path)
    writer = MeetingChatLogWriter(artifact_stem="theme_20260725_101231")
    writer.log_meeting_start("テトリスみたいなゲームを作って")
    writer.log_president_message("テトリスみたいなゲームを作って")
    writer.log_agent_message(
        agent_name="すずかちゃん(PM)",
        message="方向性を整理します",
        phase="DIVERGENCE",
        turn=1,
    )

    lines = writer.path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 5
    first = __import__("json").loads(lines[0])
    assert first["type"] == "system"
    assert first["role"] == "system"
    assert first["id"] == "msg_000"

    phase_line = __import__("json").loads(lines[2])
    assert phase_line["message"] == "フェーズ：発散"
    assert phase_line["phase"] == "DIVERGENCE"

    reopened = MeetingChatLogWriter.open_existing(writer.path)
    message_id = reopened.log_decision(decision="go", phase="FINAL", turn=10, reply_to="msg_004")
    payload = __import__("json").loads(reopened.path.read_text(encoding="utf-8").splitlines()[-1])
    assert payload["type"] == "decision"
    assert payload["reply_to"] == "msg_004"
    assert message_id.startswith("msg_")
    assert "Go ✅" in payload["message"]


def test_log_decision_abort_message(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("meeting_chat_log.repo_root", lambda: tmp_path)
    writer = MeetingChatLogWriter(artifact_stem="theme_abort_20260730_120000")
    writer.log_meeting_start("中止テスト")

    message_id = writer.log_decision(decision="abort", phase="FINAL", turn=10, reply_to="msg_001")
    lines = writer.path.read_text(encoding="utf-8").splitlines()
    system_payload = __import__("json").loads(lines[-2])
    decision_payload = __import__("json").loads(lines[-1])

    assert system_payload["message"] == "Go / NoGo / 中止 判定"
    assert decision_payload["type"] == "decision"
    assert decision_payload["role"] == "president"
    assert decision_payload["reply_to"] == "msg_001"
    assert "中止 ⏹" in decision_payload["message"]
    assert "この企画会議を終了します。" in decision_payload["message"]
    assert message_id.startswith("msg_")
