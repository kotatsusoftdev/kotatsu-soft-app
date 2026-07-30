from pathlib import Path

from agents.dev.agent import DevAgent
from grand_rules_store import (
    build_grand_rules_instruction,
    load_grand_rules,
)


def _write_dev_config(path: Path) -> None:
    path.write_text(
        "\n".join(
            [
                "agent:",
                '  name: "スゴ杉くん(エンジニア)"',
                '  role: "dev"',
                '  title: "開発エンジニアAI"',
                "  persona:",
                '    tone: "落ち着いた優等生"',
                '    mindset: "無理のないやり方を選ぶ"',
                "  llm:",
                '    model: "gemini-flash-lite-latest"',
                "    temperature: 0.1",
                "  evaluation_criteria:",
                '    primary_focus: "技術的な実現性を見極める"',
                "    decision_rules:",
                '      - "代替案を出す"',
                '  output_format: "分かりやすく"',
            ]
        )
        + "\n",
        encoding="utf-8",
    )


def _make_agent_layout(tmp_path: Path) -> Path:
    """Create kotatsu-soft-like layout: <root>/ai-core/src/agents/dev/config.yaml."""
    role_dir = tmp_path / "ai-core" / "src" / "agents" / "dev"
    role_dir.mkdir(parents=True)
    config_path = role_dir / "config.yaml"
    _write_dev_config(config_path)
    return config_path


def _write_grand_rules(root: Path) -> None:
    meeting_dir = root / "shared" / "meeting"
    meeting_dir.mkdir(parents=True)
    (meeting_dir / "grand_rules.yaml").write_text(
        "\n".join(
            [
                "schema_version: 1",
                'updated_at: "2026-07-30"',
                'description: "test rules"',
                "rules:",
                "  - id: one_day_scope",
                '    title: "実装スコープは1日完了を上限とする"',
                '    body: "仕様は1日で実装完了できる範囲に収める。"',
                "  - id: vanilla_stack",
                '    title: "外部エンジン禁止"',
                '    body: "単一 HTML で完結させる。"',
            ]
        )
        + "\n",
        encoding="utf-8",
    )


def test_load_grand_rules_reads_yaml(tmp_path: Path) -> None:
    _write_grand_rules(tmp_path)
    payload = load_grand_rules(tmp_path)
    assert payload["schema_version"] == 1
    assert len(payload["rules"]) == 2
    assert payload["rules"][0]["id"] == "one_day_scope"


def test_load_grand_rules_missing_file_returns_empty(tmp_path: Path) -> None:
    payload = load_grand_rules(tmp_path)
    assert payload["rules"] == []


def test_build_grand_rules_instruction_formats_rules(tmp_path: Path) -> None:
    _write_grand_rules(tmp_path)
    text = build_grand_rules_instruction(tmp_path)
    assert "【企画会議グランドルール】" in text
    assert "実装スコープは1日完了を上限とする" in text
    assert "外部エンジン禁止" in text
    assert "読み上げ" in text


def test_build_grand_rules_instruction_empty_when_missing(tmp_path: Path) -> None:
    assert build_grand_rules_instruction(tmp_path) == ""


def test_build_system_instruction_includes_grand_rules_and_persona(
    tmp_path: Path,
) -> None:
    config_path = _make_agent_layout(tmp_path)
    _write_grand_rules(tmp_path)

    agent = DevAgent(
        api_key="dummy",
        config_path=str(config_path),
        mention_id="@スゴ杉くん(エンジニア)",
    )
    instruction = agent.build_system_instruction()
    assert "【企画会議グランドルール】" in instruction
    assert "実装スコープは1日完了を上限とする" in instruction
    assert "【口調・スタンス】" in instruction
    assert "落ち着いた優等生" in instruction
    assert "論理的かつ客観的" not in instruction
    assert "工数見積もりは行わず" not in instruction


def test_build_system_instruction_without_grand_rules_file(tmp_path: Path) -> None:
    config_path = _make_agent_layout(tmp_path)

    agent = DevAgent(
        api_key="dummy",
        config_path=str(config_path),
        mention_id="@スゴ杉くん(エンジニア)",
    )
    instruction = agent.build_system_instruction()
    assert "【企画会議グランドルール】" not in instruction
    assert "最優先評価軸" in instruction
