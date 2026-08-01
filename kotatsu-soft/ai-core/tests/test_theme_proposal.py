from __future__ import annotations

import json
from pathlib import Path

import pytest

from theme_proposal import (
    ThemeProposalError,
    ThemeProposer,
    build_meeting_theme_text,
)


def _sample_trends() -> dict:
    return {
        "updated_at": "2026-08-01T00:00:00Z",
        "researcher": "ヂャイアン",
        "trends": [
            {
                "trend_id": "trend_a",
                "sns_platform": "TikTok",
                "viral_score": 90,
                "keyword": {
                    "original": "今日ビジュいいじゃん",
                    "abstracted": "全肯定セルフ褒め",
                },
            },
            {
                "trend_id": "trend_b",
                "sns_platform": "X",
                "viral_score": 80,
                "keyword": {
                    "original": "謎の法則",
                    "abstracted": "意味不明ルール信仰",
                },
            },
            {
                "trend_id": "trend_c",
                "sns_platform": "Hybrid",
                "viral_score": 70,
                "keyword": {
                    "original": "崩壊チャレンジ",
                    "abstracted": "意図的な失敗自慢",
                },
            },
        ],
    }


def _sample_mechanics() -> dict:
    return {
        "total_count": 3,
        "mechanics": [
            {
                "mechanic_id": "mech_a",
                "name": "自撮りタワー",
                "core_loop": "積む→崩れる→笑う",
                "inspiration": {
                    "source_title": "自撮り積み上げ",
                    "unique_motif": "自撮り棒",
                    "twist_gimmick": "いいね数で高さ変化",
                },
            },
            {
                "mechanic_id": "mech_b",
                "name": "大喜利ルーレット",
                "core_loop": "回す→お題→回答",
                "inspiration": {
                    "source_title": "ルーレット大喜利",
                    "unique_motif": "お題盤",
                    "twist_gimmick": "最悪お題が来る",
                },
            },
            {
                "mechanic_id": "mech_c",
                "name": "崩壊クリッカー",
                "core_loop": "連打→インフレ→崩壊",
                "inspiration": {
                    "source_title": "崩壊連打",
                    "unique_motif": "数値崩壊",
                    "twist_gimmick": "褒め言葉で加速",
                },
            },
        ],
    }


def _write_research(tmp_path: Path) -> None:
    (tmp_path / "latest_trends.json").write_text(
        json.dumps(_sample_trends(), ensure_ascii=False),
        encoding="utf-8",
    )
    (tmp_path / "mechanics_db.json").write_text(
        json.dumps(_sample_mechanics(), ensure_ascii=False),
        encoding="utf-8",
    )


def test_generate_mock_writes_options(tmp_path: Path) -> None:
    _write_research(tmp_path)
    proposer = ThemeProposer(mock=True, research_dir=tmp_path)
    payload = proposer.generate()
    assert payload["researcher"] == "ヂャイアン"
    assert 3 <= len(payload["options"]) <= 4
    assert proposer.options_path.exists()
    for opt in payload["options"]:
        assert opt["title"]
        assert opt["concept_summary"]
        assert opt["viral_point"]
        sources = opt["combined_sources"]
        assert sources["trend_id"]
        assert sources["mechanic_id"]
        assert sources["trend_label"]
        assert sources["mechanic_label"]


def test_generate_raises_without_trends(tmp_path: Path) -> None:
    (tmp_path / "mechanics_db.json").write_text(
        json.dumps(_sample_mechanics(), ensure_ascii=False),
        encoding="utf-8",
    )
    proposer = ThemeProposer(mock=True, research_dir=tmp_path)
    with pytest.raises(ThemeProposalError, match="トレンド"):
        proposer.generate()


def test_generate_raises_without_mechanics(tmp_path: Path) -> None:
    (tmp_path / "latest_trends.json").write_text(
        json.dumps(_sample_trends(), ensure_ascii=False),
        encoding="utf-8",
    )
    proposer = ThemeProposer(mock=True, research_dir=tmp_path)
    with pytest.raises(ThemeProposalError, match="メカニクス"):
        proposer.generate()


def test_generate_with_previous_titles_avoids_same(tmp_path: Path) -> None:
    _write_research(tmp_path)
    proposer = ThemeProposer(mock=True, research_dir=tmp_path)
    first = proposer.generate()
    titles = [o["title"] for o in first["options"]]
    second = proposer.generate(previous_titles=titles)
    second_titles = [o["title"] for o in second["options"]]
    assert second_titles != titles
    assert any("改" in t or "別解" in t for t in second_titles)


def test_parse_options_from_llm(tmp_path: Path) -> None:
    _write_research(tmp_path)

    def fake_llm(_prompt: str) -> str:
        return json.dumps(
            {
                "options": [
                    {
                        "option_id": 1,
                        "title": "案A",
                        "concept_summary": "要約A",
                        "combined_sources": {
                            "trend_id": "trend_a",
                            "mechanic_id": "mech_a",
                            "trend_label": "全肯定セルフ褒め",
                            "mechanic_label": "自撮りタワー",
                        },
                        "viral_point": "映え",
                    },
                    {
                        "option_id": 2,
                        "title": "案B",
                        "concept_summary": "要約B",
                        "combined_sources": {
                            "trend_id": "trend_b",
                            "mechanic_id": "mech_b",
                        },
                        "viral_point": "大喜利",
                    },
                    {
                        "option_id": 3,
                        "title": "案C",
                        "concept_summary": "要約C",
                        "combined_sources": {
                            "trend_id": "trend_c",
                            "mechanic_id": "mech_c",
                        },
                        "viral_point": "崩壊",
                    },
                ]
            },
            ensure_ascii=False,
        )

    proposer = ThemeProposer(
        mock=False, research_dir=tmp_path, llm_callable=fake_llm
    )
    payload = proposer.generate()
    assert len(payload["options"]) == 3
    assert payload["options"][1]["combined_sources"]["trend_label"] == "意味不明ルール信仰"


def test_build_meeting_theme_text() -> None:
    text = build_meeting_theme_text(
        {
            "title": "テスト案",
            "concept_summary": "要約",
            "viral_point": "バズ",
            "combined_sources": {
                "trend_label": "トレンド",
                "mechanic_label": "メカニクス",
            },
        }
    )
    assert "テスト案" in text
    assert "要約" in text
    assert "バズ" in text
    assert "トレンド" in text
    assert "メカニクス" in text
