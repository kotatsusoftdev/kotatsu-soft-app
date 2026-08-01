from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType

import pytest

from market_research import (
    DATA_SOURCE_LABEL,
    MAX_ORIGINAL_KEYWORD_CHARS,
    MarketResearchError,
    MarketResearcher,
    TAVILY_MEME_QUERY_SUFFIX,
    build_dedupe_key,
    build_tiktok_tavily_query,
    clip_original_keyword,
    filter_meme_friendly_keywords,
    is_hard_news_or_politics_keyword,
    is_stale_tavily_result,
    normalize_game_hook_ideas,
    normalize_inspiration,
)


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_build_dedupe_key_normalizes_tokens() -> None:
    key = build_dedupe_key(
        "2D物理タワー積み上げ",
        "上から落ちてくる物理オブジェクトを崩さないように高く積み上げる",
        ["2D", "物理演算", "バランス"],
    )
    assert "2d" in key
    assert " " not in key
    assert key == key.lower()


def test_get_jaian_trends_mock_overwrites_and_schema(tmp_path: Path) -> None:
    researcher = MarketResearcher(mock=True, research_dir=tmp_path)
    first = researcher.get_jaian_trends()
    path = researcher.trends_file()
    assert path.exists()
    assert first["researcher"] == "ヂャイアン"
    assert first["data_source"] == DATA_SOURCE_LABEL
    assert isinstance(first["updated_at"], str)
    assert len(first["trends"]) >= 1

    sample = first["trends"][0]
    assert "trend_id" in sample
    assert sample["sns_platform"] in {"X", "TikTok", "Hybrid"}
    assert "original" in sample["keyword"]
    assert "abstracted" in sample["keyword"]
    assert "category" in sample["keyword"]
    assert "summary" in sample["context"]
    assert "emotional_trigger" in sample["context"]
    hooks = sample["game_hook_ideas"]
    assert isinstance(hooks, list)
    assert len(hooks) == 2
    assert hooks[0].startswith("【動画映え】")
    assert hooks[1].startswith("【大喜利映え】")
    assert isinstance(sample["viral_score"], int)

    second = researcher.get_jaian_trends()
    assert len(second["trends"]) == len(first["trends"])
    on_disk = _read_json(path)
    assert len(on_disk["trends"]) == len(first["trends"])
    assert on_disk["researcher"] == "ヂャイアン"


def test_mock_multi_source_merges_candidates(tmp_path: Path) -> None:
    researcher = MarketResearcher(mock=True, research_dir=tmp_path)
    candidates = researcher._collect_trend_candidates_parallel()
    sources = {c["source"] for c in candidates}
    assert "yahoo" in sources
    assert "togetter" in sources
    assert "tiktok_tavily" in sources
    selected = researcher._select_trend_candidates(candidates)
    assert len(selected) >= 3
    payload = researcher.get_jaian_trends()
    assert len(payload["trends"]) >= 3


def test_one_source_failure_still_continues(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    researcher = MarketResearcher(mock=False, research_dir=tmp_path, tavily_api_key="k")

    def boom() -> list[dict[str, str]]:
        raise RuntimeError("yahoo down")

    monkeypatch.setattr(researcher, "_collect_source_yahoo", boom)
    monkeypatch.setattr(
        researcher,
        "_collect_source_togetter",
        lambda: [
            {
                "keyword": "エッホエッホ構文",
                "source": "togetter",
                "snippet": "Xで盛り上がり",
            }
        ],
    )
    monkeypatch.setattr(
        researcher,
        "_collect_source_tiktok_tavily",
        lambda: [
            {
                "keyword": "今日ビジュいいじゃん音源",
                "source": "tiktok_tavily",
                "snippet": "TikTokでバズ",
            }
        ],
    )
    candidates = researcher._collect_trend_candidates_parallel()
    assert len(candidates) == 2
    assert all(c["source"] != "yahoo" for c in candidates)


def test_get_game_mechanics_dedupe_and_accumulate(tmp_path: Path) -> None:
    researcher = MarketResearcher(mock=True, research_dir=tmp_path)
    first = researcher.get_game_mechanics()
    assert first["total_count"] >= 1
    assert researcher.mechanics_file().exists()

    keys = {m["dedupe_key"] for m in first["mechanics"]}
    assert "physics_2d_stacking_sushi_chopsticks" in keys
    for item in first["mechanics"]:
        tags = {str(t).lower() for t in item.get("tags") or []}
        assert "fps" not in tags
        assert "マルチプレイ" not in tags
        insp = item.get("inspiration") or {}
        assert insp.get("source_title")
        assert insp.get("unique_motif")
        assert insp.get("twist_gimmick")

    sample = first["mechanics"][0]
    assert "mechanic_id" in sample
    assert "dedupe_key" in sample
    assert "name" in sample
    assert "core_loop" in sample
    assert "inspiration" in sample
    assert "ai_dev_suitability" in sample
    assert "market_appeal" in sample
    assert sample["usage_stats"]["times_used"] == 0
    assert sample["usage_stats"]["last_used_at"] is None
    assert "first_added_at" in sample
    # 実例モチーフが残っていること
    sushi = next(
        m for m in first["mechanics"] if m["dedupe_key"] == "physics_2d_stacking_sushi_chopsticks"
    )
    assert "寿司" in sushi["inspiration"]["unique_motif"] or "シャリ" in sushi["inspiration"]["unique_motif"]
    assert "箸" in sushi["inspiration"]["twist_gimmick"]

    second = researcher.get_game_mechanics()
    assert second["total_count"] == first["total_count"]
    assert len(second["mechanics"]) == len(first["mechanics"])


def test_get_game_mechanics_adds_new_dedupe_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    researcher = MarketResearcher(mock=True, research_dir=tmp_path)
    first = researcher.get_game_mechanics()
    baseline = first["total_count"]

    def fake_fetch() -> list[dict]:
        return [
            {
                "title": "崩れる積み木タワー",
                "tags": ["2D", "物理演算", "ワンタップ"],
                "description": "上から落ちるブロックを積み上げて高さを競うカジュアル物理ゲーム。",
            },
            {
                "title": "色合わせスライドパズル",
                "tags": ["2D", "パズル"],
                "description": "同じ色を揃えて消すスライドパズル。",
            },
        ]

    monkeypatch.setattr(researcher, "_fetch_unityroom_games", fake_fetch)
    second = researcher.get_game_mechanics()
    assert second["total_count"] == baseline + 1
    keys = {m["dedupe_key"] for m in second["mechanics"]}
    assert "physics_2d_stacking_sushi_chopsticks" in keys
    assert any(
        "puzzle" in key or "色合わせ" in m["name"]
        for m, key in ((m, m["dedupe_key"]) for m in second["mechanics"])
    )
    new_item = next(m for m in second["mechanics"] if "色合わせ" in m.get("name", ""))
    assert "inspiration" in new_item
    assert new_item["inspiration"]["source_title"]


def test_run_all_mock_writes_both_files(tmp_path: Path) -> None:
    researcher = MarketResearcher(mock=True, research_dir=tmp_path)
    trends, mechanics = researcher.run_all()
    assert researcher.trends_file().exists()
    assert researcher.mechanics_file().exists()
    assert len(trends["trends"]) >= 1
    assert mechanics["total_count"] >= 1


def test_missing_tavily_key_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    researcher = MarketResearcher(
        mock=False,
        research_dir=tmp_path,
        gemini_api_key="dummy-gemini",
        tavily_api_key="",
    )
    monkeypatch.setattr(
        researcher,
        "_collect_trend_candidates_parallel",
        lambda: [
            {
                "keyword": "テストキーワード",
                "source": "yahoo",
                "snippet": "snippet",
            }
        ],
    )
    with pytest.raises(MarketResearchError, match="TAVILY_API_KEY"):
        researcher.get_jaian_trends()


def test_missing_gemini_key_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    researcher = MarketResearcher(
        mock=False,
        research_dir=tmp_path,
        gemini_api_key="",
        tavily_api_key="dummy-tavily",
    )
    monkeypatch.setattr(
        researcher,
        "_collect_trend_candidates_parallel",
        lambda: [
            {
                "keyword": "テストキーワード",
                "source": "yahoo",
                "snippet": "snippet",
            }
        ],
    )
    monkeypatch.setattr(
        researcher,
        "_fetch_tavily_context",
        lambda _keyword: "背景テキスト",
    )
    with pytest.raises(MarketResearchError, match="GEMINI_API_KEY"):
        researcher.get_jaian_trends()


def test_llm_callable_injection_for_trends(tmp_path: Path) -> None:
    def fake_llm(_prompt: str) -> str:
        return json.dumps(
            {
                "trends": [
                    {
                        "trend_id": "trend_test_001",
                        "sns_platform": "TikTok",
                        "keyword": {
                            "original": "消費税計算バズ",
                            "abstracted": "レジ理不尽計算",
                            "category": "あるある",
                        },
                        "context": {
                            "summary": "テスト要約",
                            "emotional_trigger": "理不尽さ",
                        },
                        "game_hook_ideas": [
                            "【動画映え】税込連打",
                            "【大喜利映え】税率煽り",
                        ],
                        "viral_score": 90,
                    }
                ]
            },
            ensure_ascii=False,
        )

    researcher = MarketResearcher(
        mock=True,
        research_dir=tmp_path,
        llm_callable=fake_llm,
    )
    payload = researcher.get_jaian_trends()
    assert payload["trends"][0]["trend_id"] == "trend_test_001"
    assert payload["trends"][0]["viral_score"] == 90
    assert payload["trends"][0]["sns_platform"] == "TikTok"


def test_marketing_config_model_loaded() -> None:
    researcher = MarketResearcher(mock=True)
    assert researcher.model_name == "gemini-flash-lite-latest"
    assert researcher.temperature == 0.8


def test_hard_news_and_person_names_are_filtered() -> None:
    assert is_hard_news_or_politics_keyword("伊藤智永")
    assert is_hard_news_or_politics_keyword("松井ケムリ")
    assert is_hard_news_or_politics_keyword("選挙速報")
    assert is_hard_news_or_politics_keyword("首相会見")
    assert is_hard_news_or_politics_keyword("NHKニュース")
    assert not is_hard_news_or_politics_keyword("ガチャ演出パロディ")
    assert not is_hard_news_or_politics_keyword("消費税計算バズ")
    assert not is_hard_news_or_politics_keyword("一発撮りチャレンジ")

    filtered = filter_meme_friendly_keywords(
        [
            "伊藤智永",
            "松井ケムリ",
            "選挙速報",
            "ガチャ演出パロディ",
            "あるある構文",
            "レジあるある",
            "普通の短い流行語",
        ]
    )
    assert "伊藤智永" not in filtered
    assert "松井ケムリ" not in filtered
    assert "選挙速報" not in filtered
    assert "ガチャ演出パロディ" in filtered
    assert "あるある構文" in filtered
    assert filtered[0] in {"ガチャ演出パロディ", "あるある構文", "レジあるある"}


def test_tavily_query_includes_meme_terms(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, object] = {}

    class FakeClient:
        def __init__(self, api_key: str | None = None) -> None:
            pass

        def search(self, query: str, **kwargs: object) -> dict:
            captured["query"] = query
            captured["kwargs"] = kwargs
            return {"answer": "バズ文脈", "results": []}

    fake_mod = ModuleType("tavily")
    fake_mod.TavilyClient = FakeClient  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "tavily", fake_mod)

    researcher = MarketResearcher(
        mock=False,
        research_dir=tmp_path,
        gemini_api_key="dummy-gemini",
        tavily_api_key="dummy-tavily",
    )
    context = researcher._fetch_tavily_context("ガチャ演出")
    assert "バズ文脈" in context
    query = str(captured["query"])
    assert query.startswith("ガチャ演出")
    assert "TikTok" in query
    assert "ミーム" in query
    assert "バズ" in query
    assert TAVILY_MEME_QUERY_SUFFIX in query
    kwargs = captured["kwargs"]
    assert isinstance(kwargs, dict)
    assert kwargs.get("search_depth") == "basic"
    assert "time_range" not in kwargs


def test_trends_prompt_pushes_silly_surreal_games() -> None:
    researcher = MarketResearcher(mock=True)
    prompt = researcher._build_trends_prompt(
        ["ガチャ演出パロディ"],
        {"ガチャ演出パロディ": "短尺でパロが拡散"},
        "20260801",
        sources={"ガチャ演出パロディ": "togetter"},
    )
    assert "バカバカしい" in prompt or "バカゲー" in prompt
    assert "シュール" in prompt
    assert "1分" in prompt
    assert "社会派" in prompt
    assert "sns_platform" in prompt
    assert "【動画映え】" in prompt
    assert "【大喜利映え】" in prompt


def test_build_tiktok_tavily_query_embeds_current_month() -> None:
    now = datetime(2026, 8, 1, tzinfo=timezone.utc)
    query = build_tiktok_tavily_query(now)
    assert "2026年8月" in query
    assert "今週" in query
    assert "TikTok" in query
    assert "バズ" in query


def test_source_c_tavily_uses_week_advanced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, object] = {}

    class FakeClient:
        def __init__(self, api_key: str | None = None) -> None:
            pass

        def search(self, query: str, **kwargs: object) -> dict:
            captured["query"] = query
            captured["kwargs"] = kwargs
            return {
                "answer": "",
                "results": [
                    {
                        "title": "今週のTikTok音源バズ",
                        "content": "振付チャレンジが流行中",
                        "published_date": "2026-07-30",
                    }
                ],
            }

    fake_mod = ModuleType("tavily")
    fake_mod.TavilyClient = FakeClient  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "tavily", fake_mod)

    researcher = MarketResearcher(
        mock=False,
        research_dir=tmp_path,
        tavily_api_key="dummy-tavily",
    )
    now = datetime(2026, 8, 1, tzinfo=timezone.utc)
    items = researcher._collect_source_tiktok_tavily(now=now)
    assert len(items) >= 1
    assert "2026年8月" in str(captured["query"])
    kwargs = captured["kwargs"]
    assert isinstance(kwargs, dict)
    assert kwargs.get("time_range") == "w"
    assert kwargs.get("search_depth") == "advanced"


def test_stale_tavily_results_are_filtered() -> None:
    now = datetime(2026, 8, 1, tzinfo=timezone.utc)
    assert is_stale_tavily_result(
        {
            "title": "古いまとめ",
            "content": "2023年の流行りミーム解説",
            "published_date": "2023-01-01",
        },
        now=now,
    )
    assert is_stale_tavily_result(
        {
            "title": "先月の記事",
            "content": "まだ新しいが日付は古い",
            "published_date": "2026-06-01",
        },
        now=now,
    )
    assert not is_stale_tavily_result(
        {
            "title": "今週のバズ",
            "content": "直近のTikTok音源",
            "published_date": "2026-07-28",
        },
        now=now,
    )


def test_normalize_game_hook_ideas_prefixes() -> None:
    hooks = normalize_game_hook_ideas(["連打アクション", "一言大喜利"])
    assert hooks[0].startswith("【動画映え】")
    assert hooks[1].startswith("【大喜利映え】")


def test_mechanics_prompt_keeps_unique_motif() -> None:
    researcher = MarketResearcher(mock=True)
    prompt = researcher._build_mechanics_prompt(
        [
            {
                "title": "崩れる積み木タワー",
                "tags": ["2D"],
                "description": "寿司が降ってくる",
            }
        ]
    )
    assert "inspiration" in prompt
    assert "unique_motif" in prompt
    assert "twist_gimmick" in prompt
    assert "抽象ジャンル名だけで終わらせるな" in prompt or "丸めるな" in prompt


def test_normalize_inspiration_fallback() -> None:
    insp = normalize_inspiration(
        None,
        fallback_title="テスト作品",
        fallback_description="猫がレジを打つシュールゲー",
    )
    assert insp["source_title"] == "テスト作品"
    assert "猫" in insp["unique_motif"]
    assert insp["twist_gimmick"]


def test_clip_original_keyword_keeps_long_title() -> None:
    title = "パックに水道水を入れて、冷蔵庫で色がついたら飲むという家に出会ってから他人の家の冷蔵庫を見る癖がついた"
    assert len(title) > 40
    clipped = clip_original_keyword(title)
    assert clipped.startswith("パックに水道水を入れて")
    assert len(clipped) > 40
    assert len(clipped) <= MAX_ORIGINAL_KEYWORD_CHARS
    assert clip_original_keyword("短い") == "短い"


def test_togetter_keeps_long_original_keyword(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    long_title = (
        "パックに水道水を入れて、冷蔵庫で色がついたら飲むという家に出会ってから"
        "他人の家の冷蔵庫を見る癖がついた話まとめ"
    )
    assert len(long_title) > 40
    assert len(long_title) <= MAX_ORIGINAL_KEYWORD_CHARS

    html = f"""
    <html><body>
      <a href="https://togetter.com/li/123456">{long_title}</a>
    </body></html>
    """

    class FakeResponse:
        text = html

        def raise_for_status(self) -> None:
            return None

    class FakeRequests:
        @staticmethod
        def get(*_args: object, **_kwargs: object) -> FakeResponse:
            return FakeResponse()

    monkeypatch.setitem(sys.modules, "requests", FakeRequests)

    researcher = MarketResearcher(
        mock=False,
        research_dir=tmp_path,
        tavily_api_key="dummy",
    )
    items = researcher._collect_source_togetter()
    assert len(items) >= 1
    keyword = items[0]["keyword"]
    assert keyword == long_title
    assert len(keyword) > 40
    assert not keyword.endswith("他人の家の")
