from pathlib import Path

from agents.dev.agent import DevAgent
from lessons_store import build_lessons_instruction, save_lessons


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


def test_build_system_instruction_includes_lessons(tmp_path: Path) -> None:
    agents_root = tmp_path / "agents"
    role_dir = agents_root / "dev"
    role_dir.mkdir(parents=True)
    config_path = role_dir / "config.yaml"
    _write_dev_config(config_path)
    save_lessons(
        agents_root,
        "dev",
        {
            "lesson_items": [
                "スコアは体験と一致する定義を先に固定する",
                "見た目より操作と勝敗条件を先に検証する",
                "演出名だけで終わらせずフィードバックまで入れる",
            ]
        },
    )

    agent = DevAgent(
        api_key="dummy",
        config_path=str(config_path),
        mention_id="@スゴ杉くん(エンジニア)",
    )
    instruction = agent.build_system_instruction()
    assert "【過去開発からの教訓】" in instruction
    assert "スコアは体験と一致する定義を先に固定する" in instruction
    assert "テーマ非依存" in instruction
    assert "読み上げ" in instruction


def test_build_system_instruction_without_lessons_file(tmp_path: Path) -> None:
    agents_root = tmp_path / "agents"
    role_dir = agents_root / "dev"
    role_dir.mkdir(parents=True)
    config_path = role_dir / "config.yaml"
    _write_dev_config(config_path)

    agent = DevAgent(
        api_key="dummy",
        config_path=str(config_path),
        mention_id="@スゴ杉くん(エンジニア)",
    )
    instruction = agent.build_system_instruction()
    assert "【過去開発からの教訓】" not in instruction
    assert "最優先評価軸" in instruction


def test_build_lessons_instruction_skips_placeholders() -> None:
    text = build_lessons_instruction(["使える教訓", "（未設定）", ""])
    assert "使える教訓" in text
    assert "（未設定）" not in text
