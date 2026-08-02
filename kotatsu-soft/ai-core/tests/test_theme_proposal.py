from __future__ import annotations

import json
from pathlib import Path

import pytest

from theme_proposal import (
    APPROACH_DIRECT,
    APPROACH_REMAP,
    ThemeProposalError,
    ThemeProposer,
    build_meeting_theme_parts,
    build_meeting_theme_text,
    extract_avoid_pairs,
    meeting_theme_parts_from_free_text,
    meeting_theme_parts_from_text,
    normalize_approach_type,
    option_source_pair,
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
                "context": {
                    "summary": "自撮りで自分を全肯定する短尺",
                    "emotional_trigger": "自己肯定と誇張",
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
                "context": {
                    "summary": "根拠薄弱な法則を信じ込む",
                    "emotional_trigger": "理不尽な納得",
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
                "context": {
                    "summary": "わざと崩して笑いを取る",
                    "emotional_trigger": "失敗の高揚",
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
        assert opt["approach_type"] in {APPROACH_DIRECT, APPROACH_REMAP}
        assert opt["design_intent"]
        assert opt["synergy_reason"]
        assert opt["viral_point"]
        sources = opt["combined_sources"]
        assert sources["trend_id"]
        assert sources["mechanic_id"]
        assert sources["trend_label"]
        assert sources["mechanic_label"]
    assert {o["approach_type"] for o in payload["options"]} == {
        APPROACH_DIRECT,
        APPROACH_REMAP,
    }


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


def test_generate_with_previous_options_avoids_same_pairs(tmp_path: Path) -> None:
    _write_research(tmp_path)
    proposer = ThemeProposer(mock=True, research_dir=tmp_path)
    first = proposer.generate()
    first_pairs = extract_avoid_pairs(first["options"])
    second = proposer.generate(previous_options=first["options"])
    second_pairs = {option_source_pair(o) for o in second["options"]}
    assert second_pairs.isdisjoint(first_pairs)
    assert any("改" in str(o.get("title") or "") or "別解" in str(o.get("title") or "") for o in second["options"])


def test_extract_avoid_pairs() -> None:
    pairs = extract_avoid_pairs(
        [
            {
                "combined_sources": {
                    "trend_id": "trend_a",
                    "mechanic_id": "mech_a",
                }
            },
            {"title": "no sources"},
        ]
    )
    assert pairs == {("trend_a", "mech_a")}


def test_normalize_approach_type() -> None:
    assert normalize_approach_type("元ネタ直球") == APPROACH_DIRECT
    assert normalize_approach_type("直球") == APPROACH_DIRECT
    assert normalize_approach_type("世界観置換") == APPROACH_REMAP
    assert normalize_approach_type("置換案") == APPROACH_REMAP
    assert normalize_approach_type("不明") is None


def _full_option(
    *,
    option_id: int,
    title: str,
    trend_id: str,
    mechanic_id: str,
    approach_type: str = APPROACH_DIRECT,
    **extra: object,
) -> dict:
    base = {
        "option_id": option_id,
        "title": title,
        "concept_summary": f"{title}の要約",
        "combined_sources": {
            "trend_id": trend_id,
            "mechanic_id": mechanic_id,
        },
        "approach_type": approach_type,
        "design_intent": f"{title}の狙い",
        "synergy_reason": f"{title}のシナジー",
        "viral_point": f"{title}のバズ",
    }
    base.update(extra)
    return base


def test_parse_filters_forbidden_pairs_and_retries(tmp_path: Path) -> None:
    _write_research(tmp_path)
    calls = {"n": 0}

    def fake_llm(prompt: str) -> str:
        calls["n"] += 1
        assert "禁止ペア" in prompt
        assert "trend_a × mech_a" in prompt
        if calls["n"] == 1:
            # 禁止ペアばかり → フィルタ後不足 → 再試行
            return json.dumps(
                {
                    "options": [
                        _full_option(
                            option_id=1,
                            title="重複A",
                            trend_id="trend_a",
                            mechanic_id="mech_a",
                        ),
                        _full_option(
                            option_id=2,
                            title="重複B",
                            trend_id="trend_a",
                            mechanic_id="mech_a",
                        ),
                        _full_option(
                            option_id=3,
                            title="重複C",
                            trend_id="trend_a",
                            mechanic_id="mech_a",
                        ),
                    ]
                },
                ensure_ascii=False,
            )
        return json.dumps(
            {
                "options": [
                    _full_option(
                        option_id=1,
                        title="新案1",
                        trend_id="trend_b",
                        mechanic_id="mech_b",
                        approach_type=APPROACH_REMAP,
                    ),
                    _full_option(
                        option_id=2,
                        title="新案2",
                        trend_id="trend_c",
                        mechanic_id="mech_c",
                    ),
                    _full_option(
                        option_id=3,
                        title="新案3",
                        trend_id="trend_b",
                        mechanic_id="mech_c",
                        approach_type=APPROACH_REMAP,
                    ),
                ]
            },
            ensure_ascii=False,
        )

    proposer = ThemeProposer(
        mock=False, research_dir=tmp_path, llm_callable=fake_llm
    )
    previous = [
        {
            "title": "前回",
            "combined_sources": {
                "trend_id": "trend_a",
                "mechanic_id": "mech_a",
                "trend_label": "全肯定セルフ褒め",
                "mechanic_label": "自撮りタワー",
            },
        }
    ]
    payload = proposer.generate(previous_options=previous)
    assert calls["n"] == 2
    pairs = {option_source_pair(o) for o in payload["options"]}
    assert ("trend_a", "mech_a") not in pairs
    assert len(payload["options"]) >= 3


def test_parse_options_from_llm(tmp_path: Path) -> None:
    _write_research(tmp_path)

    def fake_llm(_prompt: str) -> str:
        assert "synergy_reason" in _prompt
        assert "approach_type" in _prompt
        assert "design_intent" in _prompt
        assert "安易なガッチャンコ" in _prompt
        assert "世界観置換" in _prompt
        assert "爆笑" in _prompt
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
                        "approach_type": "直球",
                        "design_intent": "絵面が完成しているので直球",
                        "synergy_reason": "自己肯定を積む操作で増幅",
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
                        "approach_type": APPROACH_REMAP,
                        "design_intent": "置換の方がカオス",
                        "synergy_reason": "理不尽ルールを回す操作で再現",
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
                        "approach_type": APPROACH_DIRECT,
                        "design_intent": "失敗自慢を直球で",
                        "synergy_reason": "失敗の高揚を連打で加速",
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
    assert payload["options"][0]["synergy_reason"] == "自己肯定を積む操作で増幅"
    assert payload["options"][0]["approach_type"] == APPROACH_DIRECT
    assert payload["options"][1]["approach_type"] == APPROACH_REMAP


def test_parse_skips_options_without_required_fields(tmp_path: Path) -> None:
    _write_research(tmp_path)

    def fake_llm(_prompt: str) -> str:
        return json.dumps(
            {
                "options": [
                    {
                        "option_id": 1,
                        "title": "欠落案",
                        "concept_summary": "要約",
                        "combined_sources": {
                            "trend_id": "trend_a",
                            "mechanic_id": "mech_a",
                        },
                        "viral_point": "映え",
                    },
                    _full_option(
                        option_id=2,
                        title="案B",
                        trend_id="trend_b",
                        mechanic_id="mech_b",
                    ),
                    _full_option(
                        option_id=3,
                        title="案C",
                        trend_id="trend_c",
                        mechanic_id="mech_c",
                        approach_type=APPROACH_REMAP,
                    ),
                    _full_option(
                        option_id=4,
                        title="案D",
                        trend_id="trend_a",
                        mechanic_id="mech_c",
                    ),
                ]
            },
            ensure_ascii=False,
        )

    proposer = ThemeProposer(
        mock=False, research_dir=tmp_path, llm_callable=fake_llm
    )
    payload = proposer.generate()
    titles = [o["title"] for o in payload["options"]]
    assert "欠落案" not in titles
    assert len(payload["options"]) == 3


def test_build_meeting_theme_parts_splits_overview_and_details() -> None:
    option = {
        "title": "テスト案",
        "concept_summary": "要約",
        "approach_type": APPROACH_REMAP,
        "design_intent": "宮廷料理人に置換した方がカオス",
        "synergy_reason": "焦りを滑る操作で再現",
        "viral_point": "バズ",
        "combined_sources": {
            "trend_label": "トレンド",
            "mechanic_label": "メカニクス",
        },
    }
    parts = build_meeting_theme_parts(option)
    assert parts.title == "テスト案"
    assert parts.overview == "テスト案\n\n要約"
    assert "アプローチ: 世界観置換" in parts.details
    assert "アプローチの狙い" in parts.details
    assert "宮廷料理人に置換した方がカオス" in parts.details
    assert "シナジー理由" in parts.details
    assert "焦りを滑る操作で再現" in parts.details
    assert "バズ" in parts.details
    assert "トレンド" in parts.details
    assert "メカニクス" in parts.details
    assert "テスト案" not in parts.details
    assert parts.details.startswith("アプローチ:")
    assert parts.theme_for_agents.startswith("テーマ:\n")
    assert "詳細:\n" in parts.theme_for_agents
    assert "要約" in parts.theme_for_agents
    assert "世界観置換" in parts.theme_for_agents

    text = build_meeting_theme_text(option)
    assert text == parts.theme_for_agents


def test_meeting_theme_parts_from_free_text_uses_first_line_as_title() -> None:
    parts = meeting_theme_parts_from_free_text("短いタイトル\n\n長い本文")
    assert parts.title == "短いタイトル"
    assert parts.overview == "短いタイトル\n\n長い本文"
    assert parts.details == ""
    assert parts.theme_for_agents == "短いタイトル\n\n長い本文"


def test_meeting_theme_parts_from_text_roundtrip() -> None:
    original = build_meeting_theme_parts(
        {
            "title": "復元テスト",
            "concept_summary": "概要文",
            "approach_type": APPROACH_DIRECT,
            "design_intent": "狙い",
            "synergy_reason": "",
            "viral_point": "",
            "combined_sources": {},
        }
    )
    restored = meeting_theme_parts_from_text(original.theme_for_agents)
    assert restored.title == "復元テスト"
    assert restored.overview == original.overview
    assert "アプローチ: 元ネタ直球" in restored.details
    assert "狙い" in restored.details
