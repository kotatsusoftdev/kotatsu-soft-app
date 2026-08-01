"""ヂャイアン向け市場調査モジュール。

トレンド取得（上書き型）とゲームメカニクス取得（蓄積型・重複排除）を行う。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Optional

import yaml

DEFAULT_MODEL = "gemini-flash-lite-latest"
DEFAULT_TEMPERATURE = 0.8
YAHOO_REALTIME_URL = "https://search.yahoo.co.jp/realtime"
TOGETTER_HOT_URL = "https://togetter.com/hot"
UNITYROOM_POPULAR_URL = "https://unityroom.com/games?sort=popular"
TRENDS_FILENAME = "latest_trends.json"
MECHANICS_FILENAME = "mechanics_db.json"
MAX_TREND_KEYWORDS = 10
MAX_RAW_TREND_CANDIDATES = 20
MAX_TOGETTER_CANDIDATES = 15
MAX_UNITYROOM_GAMES = 30
MAX_ORIGINAL_KEYWORD_CHARS = 120
TAVILY_MEME_QUERY_SUFFIX = "TikTok X ミーム バズ 元ネタ 流行り"
DATA_SOURCE_LABEL = "Yahoo! Realtime + Togetter + Tavily API"
SNS_PLATFORMS = frozenset({"X", "TikTok", "Hybrid"})
STALE_RESULT_MAX_AGE_DAYS = 8
HTTP_HEADERS = {"User-Agent": "kotatsu-soft-market-research/1.0"}

# 政治・報道・堅い社会ニュース寄りキーワード（部分一致で除外）
HARD_NEWS_BLOCKLIST = (
    "議員",
    "首相",
    "総理",
    "選挙",
    "政党",
    "党首",
    "国会",
    "内閣",
    "官房",
    "与党",
    "野党",
    "報道",
    "記者",
    "会見",
    "裁判",
    "判決",
    "起訴",
    "容疑",
    "逮捕",
    "NHK",
    "ニュース",
    "速報",
    "政局",
    "外交",
    "予算委員会",
    "憲法",
    "法案",
    "デモ行進",
)

# SNSネタ・ミームっぽさを示す語（優先残存）
MEME_SIGNAL_TOKENS = (
    "バズ",
    "あるある",
    "チャレンジ",
    "構文",
    "ミーム",
    "パロ",
    "パロディ",
    "ネタ",
    "ワロタ",
    "草",
    "それな",
    "エモ",
    "沼",
    "沼る",
    "神回",
    "尊い",
    "無理ゲー",
    "クソゲー",
    "バカゲー",
    "シュール",
    "煽り",
    "煽ち",
    "あるある",
    "あるあるネタ",
    "TikTok",
    "tiktok",
    "バズる",
    "流行り",
    "元ネタ",
    "ギャグ",
    "ボケ",
    "ツッコミ",
)

EXCLUDE_MECHANIC_TAGS = frozenset(
    {
        "3d",
        "３ｄ",
        "マルチプレイ",
        "multiplayer",
        "fps",
        "tps",
        "vr",
        "ar",
        "mmo",
        "オンライン対戦",
        "オンライン",
    }
)

EXCLUDE_MECHANIC_KEYWORDS = (
    "3d",
    "マルチプレイ",
    "fps",
    "tps",
    "vr",
    "オンライン対戦",
    "first person",
)

MOCK_TREND_KEYWORDS = [
    "消費税計算バズ",
    "ガチャ演出パロディ",
    "一発撮りチャレンジ",
]

MOCK_SOURCE_CANDIDATES: list[dict[str, str]] = [
    {
        "keyword": "消費税計算バズ",
        "source": "yahoo",
        "snippet": "税率改定や買い物の計算ミスがSNSで急速にバズっている。",
    },
    {
        "keyword": "ガチャ演出パロディ",
        "source": "togetter",
        "snippet": "派手なガチャ演出のパロディがXのまとめ記事で拡散中。",
    },
    {
        "keyword": "一発撮りチャレンジ",
        "source": "tiktok_tavily",
        "snippet": "編集なし一発撮りの失敗・成功がTikTokでエンタメ化している。",
    },
    {
        "keyword": "エッホエッホ構文",
        "source": "togetter",
        "snippet": "虚無の方向へ爆走する構文がXで大喜利化。",
    },
    {
        "keyword": "今日ビジュいいじゃん音源",
        "source": "tiktok_tavily",
        "snippet": "全肯定セルフ褒めの音源・振付チャレンジが今週バズ。",
    },
]

MOCK_TAVILY_CONTEXTS: dict[str, str] = {
    "消費税計算バズ": (
        "税率改定や買い物の計算ミスがSNSで急速にバズり、"
        "理不尽さやツッコミどころとしてパロディ化されている。"
    ),
    "ガチャ演出パロディ": (
        "派手なガチャ演出のパロディ動画が短尺SNSで拡散中。"
        "期待とハズレの落差が感情トリガーになっている。"
    ),
    "一発撮りチャレンジ": (
        "編集なし一発撮りの失敗・成功がエンタメ化し、"
        "緊張感と達成感がシェア動機になっている。"
    ),
    "エッホエッホ構文": (
        "群れを無視して変な方向へ爆走する構文がXで大喜利化している。"
    ),
    "今日ビジュいいじゃん音源": (
        "自分を全肯定する音源チャレンジがTikTokで流行中。"
    ),
}

MOCK_UNITYROOM_GAMES = [
    {
        "title": "崩れる積み木タワー",
        "tags": ["2D", "物理演算", "ワンタップ", "シュール"],
        "description": (
            "シャリとネタがバラバラに降ってくる寿司タワー。"
            "崩さないように積み上げ、一定の高さで巨大な箸が横から邪魔してくる。"
        ),
    },
    {
        "title": "オンラインFPSアリーナ",
        "tags": ["3D", "FPS", "マルチプレイ"],
        "description": "オンライン対戦型の一人称シューティング。",
    },
    {
        "title": "タイミング反射タップ",
        "tags": ["2D", "リズム", "ワンタップ", "猫"],
        "description": (
            "画面を横断する猫の足跡ノートをタップ。"
            "コンボ中に画面が上下反転し、足音のリズムで戻す一捻りギミック付き。"
        ),
    },
]


class MarketResearchError(RuntimeError):
    """市場調査処理の失敗。"""


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def default_research_dir() -> Path:
    return _repo_root() / "shared" / "research"


def default_marketing_config_path() -> Path:
    return Path(__file__).resolve().parent / "agents" / "marketing" / "config.yaml"


def _normalize_token(value: str) -> str:
    text = unicodedata.normalize("NFKC", (value or "").strip().lower())
    text = text.replace("２ｄ", "2d").replace("３ｄ", "3d")
    text = re.sub(r"[\s　\-_/・,，.。:：;；!！?？\[\]【】()（）\"'「」『』]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    return text


def _has_meme_signal(keyword: str) -> bool:
    text = unicodedata.normalize("NFKC", keyword or "")
    if any(token in text for token in MEME_SIGNAL_TOKENS):
        return True
    # カタカナ／英数字／ネットっぽい記号を含む
    if re.search(r"[ァ-ヶーA-Za-z0-9#@!！?？ｗＷ]", text):
        return True
    return False


def _looks_like_hard_person_name(keyword: str) -> bool:
    """姓＋名らしい漢字フルネーム（スペースなし）を粗い推定で判定する。"""
    text = unicodedata.normalize("NFKC", (keyword or "").strip())
    if not text or " " in text or "　" in text:
        return False
    # 漢字のみ 2〜4 文字（例: 伊藤智永）
    if re.fullmatch(r"[\u4e00-\u9fff]{2,4}", text):
        return True
    # 漢字姓 + カタカナ名など報道人名（例: 松井ケムリ）はミーム指標がなければ堅い個人名扱い
    if re.fullmatch(r"[\u4e00-\u9fff]{1,3}[ァ-ヶー]{2,8}", text):
        return True
    return False


def is_hard_news_or_politics_keyword(keyword: str) -> bool:
    """政治・報道・堅い社会ニュース／個人名なら True（除外対象）。"""
    text = unicodedata.normalize("NFKC", (keyword or "").strip())
    if not text:
        return True
    if any(token in text for token in HARD_NEWS_BLOCKLIST):
        return True
    # 漢字のみフルネーム（例: 伊藤智永）
    if re.fullmatch(r"[\u4e00-\u9fff]{2,4}", text):
        return True
    # 漢字姓+カタカナ名の報道人名。ミーム語を含まないものだけ除外
    # （カタカナ単体は _has_meme_signal で優先残存し得るため、ここでは人名形に限定）
    if re.fullmatch(r"[\u4e00-\u9fff]{1,3}[ァ-ヶー]{2,8}", text):
        if not any(token in text for token in MEME_SIGNAL_TOKENS):
            return True
    if _looks_like_hard_person_name(text) and not _has_meme_signal(text):
        return True
    return False


def filter_meme_friendly_keywords(
    keywords: list[str],
    *,
    limit: int = MAX_TREND_KEYWORDS,
) -> list[str]:
    """政治・報道を落とし、ミーム／ネタ寄りキーワードを優先して返す。"""
    preferred: list[str] = []
    neutral: list[str] = []
    seen: set[str] = set()

    for raw in keywords:
        keyword = (raw or "").strip()
        if not keyword:
            continue
        key = keyword.casefold()
        if key in seen:
            continue
        seen.add(key)
        if is_hard_news_or_politics_keyword(keyword):
            continue
        if _has_meme_signal(keyword) or any(
            token in keyword for token in MEME_SIGNAL_TOKENS
        ):
            preferred.append(keyword)
        else:
            neutral.append(keyword)

    ordered = preferred + neutral
    return ordered[:limit]


def build_tiktok_tavily_query(now: datetime | None = None) -> str:
    """Source C 用の動的 TikTok 直近クエリを生成する。"""
    current = now or datetime.now(timezone.utc)
    return (
        f"TikTok 流行り ミーム 音源 元ネタ "
        f"{current.year}年{current.month}月 今週 バズ"
    )


def _parse_published_date(value: Any) -> datetime | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    # ISO っぽい日付を優先
    for candidate in (text, text.replace("Z", "+00:00")):
        try:
            parsed = datetime.fromisoformat(candidate)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
        except ValueError:
            continue
    match = re.search(r"(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})", text)
    if match:
        try:
            return datetime(
                int(match.group(1)),
                int(match.group(2)),
                int(match.group(3)),
                tzinfo=timezone.utc,
            )
        except ValueError:
            return None
    return None


def is_stale_tavily_result(
    item: dict[str, Any],
    *,
    now: datetime | None = None,
) -> bool:
    """数年前や1週間超の古い Tavily 結果なら True。"""
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)

    published = _parse_published_date(item.get("published_date") or item.get("publishedDate"))
    if published is not None:
        age = current - published
        if age > timedelta(days=STALE_RESULT_MAX_AGE_DAYS):
            return True

    blob = " ".join(
        [
            str(item.get("title") or ""),
            str(item.get("content") or item.get("snippet") or ""),
        ]
    )
    # 実行年より2年以上前の「YYYY年」表記があれば古いまとめ扱い
    for year_match in re.finditer(r"(20\d{2})年", blob):
        year = int(year_match.group(1))
        if year <= current.year - 2:
            return True
    return False


def normalize_sns_platform(value: Any) -> str:
    text = str(value or "").strip()
    if text in SNS_PLATFORMS:
        return text
    lowered = text.lower()
    if lowered in {"x", "twitter", "𝕏"}:
        return "X"
    if lowered in {"tiktok", "tt"}:
        return "TikTok"
    if lowered in {"hybrid", "both", "x/tiktok"}:
        return "Hybrid"
    return "Hybrid"


def normalize_game_hook_ideas(hooks: list[Any] | None) -> list[str]:
    """【動画映え】【大喜利映え】の2系統に正規化する。"""
    cleaned = [str(h).strip() for h in (hooks or []) if str(h).strip()]
    video = next((h for h in cleaned if h.startswith("【動画映え】")), None)
    oogiri = next((h for h in cleaned if h.startswith("【大喜利映え】")), None)
    leftovers = [
        h
        for h in cleaned
        if not h.startswith("【動画映え】") and not h.startswith("【大喜利映え】")
    ]
    if video is None:
        base = leftovers.pop(0) if leftovers else "音源のリズムに合わせて連打する1分アクション"
        video = base if base.startswith("【動画映え】") else f"【動画映え】{base}"
    if oogiri is None:
        base = leftovers.pop(0) if leftovers else "一言で押し通す大喜利カードバトル"
        oogiri = base if base.startswith("【大喜利映え】") else f"【大喜利映え】{base}"
    return [video, oogiri]


def normalize_inspiration(
    value: Any,
    *,
    fallback_title: str = "",
    fallback_description: str = "",
) -> dict[str, str]:
    """unityroom 実例由来のモチーフ／ギミック情報を正規化する。"""
    raw = value if isinstance(value, dict) else {}
    source_title = str(raw.get("source_title") or fallback_title or "").strip()
    unique_motif = str(raw.get("unique_motif") or "").strip()
    twist_gimmick = str(raw.get("twist_gimmick") or "").strip()
    if not unique_motif and fallback_description:
        unique_motif = fallback_description[:80]
    if not twist_gimmick:
        twist_gimmick = "実例ならではの一捻りギミックを抽出できず（要再調査）"
    return {
        "source_title": source_title or "unknown",
        "unique_motif": unique_motif or "モチーフ不明",
        "twist_gimmick": twist_gimmick,
    }


def clip_original_keyword(
    text: str,
    *,
    max_chars: int = MAX_ORIGINAL_KEYWORD_CHARS,
) -> str:
    """original / keyword 用。途中切れを避けつつ上限内に収める。"""
    value = (text or "").strip()
    if len(value) <= max_chars:
        return value
    return value[:max_chars].rstrip()


def build_dedupe_key(name: str, core_loop: str, tags: list[str] | None = None) -> str:
    """メカニクス名とコア要素から標準化した dedupe_key を生成する。"""
    parts = [_normalize_token(name), _normalize_token(core_loop)]
    for tag in tags or []:
        token = _normalize_token(tag)
        if token and token not in parts:
            parts.append(token)
    key = "_".join(p for p in parts if p)
    # 長すぎる場合は先頭トークンを優先して短縮
    tokens = [t for t in key.split("_") if t]
    if len(tokens) > 8:
        tokens = tokens[:8]
    return "_".join(tokens) or "unknown_mechanic"


def _empty_mechanics_db() -> dict[str, Any]:
    return {
        "last_updated": _utc_now_iso(),
        "total_count": 0,
        "mechanics": [],
    }


def _load_json(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        return default
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default
    return payload if isinstance(payload, dict) else default


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _extract_json_object(text: str) -> Any:
    raw = (text or "").strip()
    if not raw:
        raise MarketResearchError("LLM response is empty")
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\{[\s\S]*\}|\[[\s\S]*\]", raw)
        if not match:
            raise MarketResearchError("LLM response is not valid JSON") from None
        return json.loads(match.group(0))


class MarketResearcher:
    """Yahoo! Realtime + Tavily + Gemini / unityroom による市場調査。"""

    def __init__(
        self,
        *,
        mock: bool = False,
        research_dir: Path | None = None,
        gemini_api_key: str | None = None,
        tavily_api_key: str | None = None,
        marketing_config_path: Path | None = None,
        llm_callable: Callable[[str], str] | None = None,
    ) -> None:
        self.mock = mock
        self.research_dir = Path(research_dir) if research_dir else default_research_dir()
        self.trends_path = self.research_dir / TRENDS_FILENAME
        self.mechanics_path = self.research_dir / MECHANICS_FILENAME
        self.gemini_api_key = (gemini_api_key or os.getenv("GEMINI_API_KEY") or "").strip()
        self.tavily_api_key = (tavily_api_key or os.getenv("TAVILY_API_KEY") or "").strip()
        self.marketing_config_path = (
            Path(marketing_config_path)
            if marketing_config_path
            else default_marketing_config_path()
        )
        self.llm_callable = llm_callable
        self.model_name, self.temperature = self._load_marketing_llm_config()

    def _load_marketing_llm_config(self) -> tuple[str, float]:
        path = self.marketing_config_path
        if not path.exists():
            return DEFAULT_MODEL, DEFAULT_TEMPERATURE
        try:
            config = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError):
            return DEFAULT_MODEL, DEFAULT_TEMPERATURE
        agent = config.get("agent") or {}
        llm = agent.get("llm") or config.get("llm") or {}
        model = str(llm.get("model") or DEFAULT_MODEL)
        try:
            temperature = float(llm.get("temperature", DEFAULT_TEMPERATURE))
        except (TypeError, ValueError):
            temperature = DEFAULT_TEMPERATURE
        return model, temperature

    def trends_file(self) -> Path:
        return self.trends_path

    def mechanics_file(self) -> Path:
        return self.mechanics_path

    def run_all(self) -> tuple[dict[str, Any], dict[str, Any]]:
        trends = self.get_jaian_trends()
        mechanics = self.get_game_mechanics()
        return trends, mechanics

    # ------------------------------------------------------------------
    # Trends
    # ------------------------------------------------------------------

    def get_jaian_trends(self) -> dict[str, Any]:
        candidates = self._collect_trend_candidates_parallel()
        selected = self._select_trend_candidates(candidates)
        keywords = [str(item["keyword"]) for item in selected]
        sources = {str(item["keyword"]): str(item.get("source") or "") for item in selected}
        contexts: dict[str, str] = {}
        for item in selected:
            keyword = str(item["keyword"])
            snippet = str(item.get("snippet") or "").strip()
            tavily_ctx = self._fetch_tavily_context(keyword)
            contexts[keyword] = "\n".join(
                part for part in (snippet, tavily_ctx) if part
            ).strip()

        trends = self._abstract_trends_with_llm(keywords, contexts, sources=sources)
        payload = {
            "updated_at": _utc_now_iso(),
            "researcher": "ヂャイアン",
            "data_source": DATA_SOURCE_LABEL,
            "trends": trends,
        }
        _write_json(self.trends_path, payload)
        return payload

    def _collect_trend_candidates_parallel(self) -> list[dict[str, str]]:
        if self.mock:
            return [dict(item) for item in MOCK_SOURCE_CANDIDATES]

        collectors = (
            ("yahoo", self._collect_source_yahoo),
            ("togetter", self._collect_source_togetter),
            ("tiktok_tavily", self._collect_source_tiktok_tavily),
        )
        merged: list[dict[str, str]] = []
        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = {
                executor.submit(collector): name for name, collector in collectors
            }
            for future in as_completed(futures):
                name = futures[future]
                try:
                    items = future.result()
                except Exception as exc:  # noqa: BLE001
                    print(f"[market_research] source={name} failed: {exc}")
                    continue
                if items:
                    merged.extend(items)
                    print(f"[market_research] source={name} candidates={len(items)}")
        return merged

    def _select_trend_candidates(
        self,
        candidates: list[dict[str, str]],
        *,
        limit: int = MAX_TREND_KEYWORDS,
    ) -> list[dict[str, str]]:
        preferred: list[dict[str, str]] = []
        neutral: list[dict[str, str]] = []
        seen: set[str] = set()

        for item in candidates:
            keyword = str(item.get("keyword") or "").strip()
            if not keyword:
                continue
            key = keyword.casefold()
            if key in seen:
                continue
            if is_hard_news_or_politics_keyword(keyword):
                continue
            seen.add(key)
            normalized = {
                "keyword": keyword,
                "source": str(item.get("source") or ""),
                "snippet": str(item.get("snippet") or ""),
            }
            if _has_meme_signal(keyword):
                preferred.append(normalized)
            else:
                neutral.append(normalized)

        selected = (preferred + neutral)[:limit]
        if not selected:
            raise MarketResearchError(
                "No meme-friendly trend candidates after multi-source filtering"
            )
        return selected

    def _collect_source_yahoo(self) -> list[dict[str, str]]:
        if self.mock:
            return [
                dict(item)
                for item in MOCK_SOURCE_CANDIDATES
                if item.get("source") == "yahoo"
            ]

        import requests
        from bs4 import BeautifulSoup

        try:
            response = requests.get(
                YAHOO_REALTIME_URL,
                timeout=20,
                headers=HTTP_HEADERS,
            )
            response.raise_for_status()
        except Exception as exc:  # noqa: BLE001
            print(f"[market_research] Yahoo Realtime fetch failed: {exc}")
            return []

        soup = BeautifulSoup(response.text, "html.parser")
        raw_keywords: list[str] = []
        seen: set[str] = set()
        candidates = soup.select(
            "ol li a, ul li a, .Trend_list a, [class*='trend'] a, [class*='Trend'] a"
        )
        if not candidates:
            candidates = soup.find_all("a", href=True)

        for anchor in candidates:
            text = anchor.get_text(" ", strip=True)
            if not text or len(text) < 2 or len(text) > 40:
                continue
            href = str(anchor.get("href") or "")
            looks_trend = any(
                token in href for token in ("realtime", "search", "p=", "query")
            )
            if not looks_trend and "急上昇" not in text:
                parent_text = (
                    anchor.parent.get_text(" ", strip=True) if anchor.parent else ""
                )
                if "急上昇" not in parent_text and "トレンド" not in parent_text:
                    continue
            key = text.casefold()
            if key in seen:
                continue
            seen.add(key)
            raw_keywords.append(text)
            if len(raw_keywords) >= MAX_RAW_TREND_CANDIDATES:
                break

        return [
            {"keyword": kw, "source": "yahoo", "snippet": f"Yahoo急上昇: {kw}"}
            for kw in raw_keywords
            if not is_hard_news_or_politics_keyword(kw)
        ]

    def _collect_source_togetter(self) -> list[dict[str, str]]:
        if self.mock:
            return [
                dict(item)
                for item in MOCK_SOURCE_CANDIDATES
                if item.get("source") == "togetter"
            ]

        import requests
        from bs4 import BeautifulSoup

        try:
            response = requests.get(
                TOGETTER_HOT_URL,
                timeout=20,
                headers=HTTP_HEADERS,
            )
            response.raise_for_status()
        except Exception as exc:  # noqa: BLE001
            print(f"[market_research] Togetter fetch failed: {exc}")
            return []

        soup = BeautifulSoup(response.text, "html.parser")
        items: list[dict[str, str]] = []
        seen: set[str] = set()
        anchors = soup.select(
            "a[href*='/li/'], h2 a, h3 a, .title a, .list_title a, article a"
        )
        if not anchors:
            anchors = soup.find_all("a", href=True)

        for anchor in anchors:
            href = str(anchor.get("href") or "")
            text = anchor.get_text(" ", strip=True)
            if not text or len(text) < 4 or len(text) > MAX_ORIGINAL_KEYWORD_CHARS:
                continue
            if "/li/" not in href and "togetter.com" not in href:
                # hot ページ上の一般リンクは弱く拾う
                if "まとめ" not in text and "バズ" not in text:
                    continue
            key = text.casefold()
            if key in seen:
                continue
            if is_hard_news_or_politics_keyword(text):
                continue
            seen.add(key)
            keyword = clip_original_keyword(text)
            items.append(
                {
                    "keyword": keyword,
                    "source": "togetter",
                    "snippet": f"Togetter急上昇: {text}",
                }
            )
            if len(items) >= MAX_TOGETTER_CANDIDATES:
                break
        return items

    def _collect_source_tiktok_tavily(
        self,
        *,
        now: datetime | None = None,
    ) -> list[dict[str, str]]:
        if self.mock:
            return [
                dict(item)
                for item in MOCK_SOURCE_CANDIDATES
                if item.get("source") == "tiktok_tavily"
            ]

        if not self.tavily_api_key:
            print("[market_research] Source C skipped: TAVILY_API_KEY missing")
            return []

        try:
            from tavily import TavilyClient
        except ImportError:
            print("[market_research] Source C skipped: tavily-python missing")
            return []

        current = now or datetime.now(timezone.utc)
        query = build_tiktok_tavily_query(current)
        try:
            client = TavilyClient(api_key=self.tavily_api_key)
            result = client.search(
                query=query,
                max_results=5,
                search_depth="advanced",
                time_range="w",
                include_answer=True,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"[market_research] Source C Tavily failed: {exc}")
            return []

        items: list[dict[str, str]] = []
        seen: set[str] = set()
        results = result.get("results") if isinstance(result, dict) else None
        if isinstance(results, list):
            for row in results:
                if not isinstance(row, dict):
                    continue
                if is_stale_tavily_result(row, now=current):
                    continue
                title = str(row.get("title") or "").strip()
                content = str(row.get("content") or row.get("snippet") or "").strip()
                if not title and not content:
                    continue
                keyword = clip_original_keyword(title if title else content)
                if not keyword or is_hard_news_or_politics_keyword(keyword):
                    continue
                key = keyword.casefold()
                if key in seen:
                    continue
                seen.add(key)
                snippet_parts = [p for p in (title, content[:240]) if p]
                items.append(
                    {
                        "keyword": keyword,
                        "source": "tiktok_tavily",
                        "snippet": " / ".join(snippet_parts),
                    }
                )

        answer = result.get("answer") if isinstance(result, dict) else None
        if answer and not items:
            text = clip_original_keyword(str(answer))
            if text and not is_hard_news_or_politics_keyword(text):
                items.append(
                    {
                        "keyword": text,
                        "source": "tiktok_tavily",
                        "snippet": str(answer).strip()[:300],
                    }
                )
        return items

    def _fetch_yahoo_trend_keywords(self) -> list[str]:
        """後方互換: Yahoo ソースのみのキーワード一覧。"""
        if self.mock:
            return filter_meme_friendly_keywords(list(MOCK_TREND_KEYWORDS))
        return [
            item["keyword"]
            for item in self._collect_source_yahoo()
            if item.get("keyword")
        ]

    def _fetch_tavily_context(self, keyword: str) -> str:
        if self.mock:
            return MOCK_TAVILY_CONTEXTS.get(
                keyword, f"{keyword} に関するSNS上のバズ・ミーム解説テキスト（モック）"
            )

        if not self.tavily_api_key:
            raise MarketResearchError(
                "TAVILY_API_KEY が設定されていません。環境変数を確認してください。"
            )

        try:
            from tavily import TavilyClient
        except ImportError as exc:
            raise MarketResearchError(
                "tavily-python がインストールされていません。requirements.txt を確認してください。"
            ) from exc

        query = f"{keyword} {TAVILY_MEME_QUERY_SUFFIX}"
        try:
            client = TavilyClient(api_key=self.tavily_api_key)
            result = client.search(
                query=query,
                max_results=3,
                search_depth="basic",
                include_answer=True,
            )
        except Exception as exc:  # noqa: BLE001
            raise MarketResearchError(f"Tavily search failed for '{keyword}': {exc}") from exc

        parts: list[str] = []
        answer = result.get("answer") if isinstance(result, dict) else None
        if answer:
            parts.append(str(answer).strip())
        results = result.get("results") if isinstance(result, dict) else None
        if isinstance(results, list):
            for item in results[:3]:
                if not isinstance(item, dict):
                    continue
                content = str(item.get("content") or item.get("snippet") or "").strip()
                title = str(item.get("title") or "").strip()
                if title and content:
                    parts.append(f"{title}: {content}")
                elif content:
                    parts.append(content)
        context = "\n".join(parts).strip()
        return context or f"{keyword} のバズ文脈・ミーム解説が見つかりませんでした。"

    def _abstract_trends_with_llm(
        self,
        keywords: list[str],
        contexts: dict[str, str],
        *,
        sources: dict[str, str] | None = None,
    ) -> list[dict[str, Any]]:
        date_stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
        source_map = sources or {}
        prompt = self._build_trends_prompt(
            keywords, contexts, date_stamp, sources=source_map
        )

        if self.mock and self.llm_callable is None:
            return self._mock_abstracted_trends(
                keywords, contexts, date_stamp, sources=source_map
            )

        raw = self._call_llm(prompt)
        parsed = _extract_json_object(raw)
        items = parsed.get("trends") if isinstance(parsed, dict) else parsed
        if not isinstance(items, list):
            raise MarketResearchError("LLM trends payload must be a list or {trends: [...]}")

        trends: list[dict[str, Any]] = []
        for index, item in enumerate(items, start=1):
            if not isinstance(item, dict):
                continue
            original = str(
                (
                    (item.get("keyword") or {}).get("original")
                    if isinstance(item.get("keyword"), dict)
                    else None
                )
                or item.get("original")
                or (keywords[index - 1] if index - 1 < len(keywords) else f"trend_{index}")
            )
            keyword_obj = item.get("keyword") if isinstance(item.get("keyword"), dict) else {}
            context_obj = item.get("context") if isinstance(item.get("context"), dict) else {}
            hooks = normalize_game_hook_ideas(
                item.get("game_hook_ideas") if isinstance(item.get("game_hook_ideas"), list) else []
            )
            try:
                viral = int(item.get("viral_score", 70))
            except (TypeError, ValueError):
                viral = 70
            viral = max(0, min(100, viral))
            platform = normalize_sns_platform(item.get("sns_platform"))
            if platform == "Hybrid":
                src = source_map.get(original, "")
                if src == "tiktok_tavily":
                    platform = "TikTok"
                elif src == "togetter":
                    platform = "X"
            trends.append(
                {
                    "trend_id": str(item.get("trend_id") or f"trend_{date_stamp}_{index:03d}"),
                    "sns_platform": platform,
                    "keyword": {
                        "original": original,
                        "abstracted": str(
                            keyword_obj.get("abstracted")
                            or item.get("abstracted")
                            or "シュールな日常ネタ"
                        ),
                        "category": str(
                            keyword_obj.get("category")
                            or item.get("category")
                            or "SNSミーム・ネットネタ"
                        ),
                    },
                    "context": {
                        "summary": str(
                            context_obj.get("summary")
                            or item.get("summary")
                            or contexts.get(original, "")[:200]
                        ),
                        "emotional_trigger": str(
                            context_obj.get("emotional_trigger")
                            or item.get("emotional_trigger")
                            or "爆笑・ツッコミ欲・理不尽あるある"
                        ),
                    },
                    "game_hook_ideas": hooks,
                    "viral_score": viral,
                }
            )
        if not trends:
            raise MarketResearchError("LLM returned no usable trend items")
        return trends

    def _build_trends_prompt(
        self,
        keywords: list[str],
        contexts: dict[str, str],
        date_stamp: str,
        *,
        sources: dict[str, str] | None = None,
    ) -> str:
        source_map = sources or {}
        blocks = []
        for kw in keywords:
            blocks.append(
                f"- keyword: {kw}\n"
                f"  source: {source_map.get(kw, 'unknown')}\n"
                f"  context: {contexts.get(kw, '')}"
            )
        return (
            "あなたはマーケターAI『ヂャイアン』です。\n"
            "コタツ・ソフトはブラウザのバカバカしい・シュールなバカゲーを量産する。\n"
            "以下のバズキーワードと背景テキストを読み、固有名詞（人名/キャラ/企業名など）を"
            "抽象的・汎用的なネタ概念へ変換せよ。\n\n"
            "【絶対ルール】\n"
            "- 社会派・啓発・告発・ドキュメンタリー・真面目な政治／報道テーマは禁止。\n"
            "- abstracted は『理不尽あるある・シュール状況・ネタ構文・パロ演出』など、"
            "ゲーム化しやすいバカバカしい概念にせよ。\n"
            "- sns_platform は必ず X / TikTok / Hybrid のいずれか。"
            "source が togetter なら X 寄り、tiktok_tavily なら TikTok 寄り、両方っぽければ Hybrid。\n"
            "- game_hook_ideas は必ず2件。1件目は『【動画映え】』で始まるTikTok向け、"
            "2件目は『【大喜利映え】』で始まるX向け。どちらも1分で遊べるシュール／バカゲーにせよ。\n"
            "- category は SNSミーム / ネットネタ / あるある / チャレンジ文化 / TikTok音源・動画ミーム 等に寄せよ。\n"
            "- 例: 固有名詞の炎上 → 理不尽ツッコミ判定 / ガチャ演出パロ → ハズレ演出タップ など。\n\n"
            "必ず次の JSON だけを返してください（説明文禁止）:\n"
            "{\n"
            '  "trends": [\n'
            "    {\n"
            f'      "trend_id": "trend_{date_stamp}_001",\n'
            '      "sns_platform": "TikTok",\n'
            '      "keyword": {"original": "...", "abstracted": "...", "category": "..."},\n'
            '      "context": {"summary": "...", "emotional_trigger": "..."},\n'
            '      "game_hook_ideas": ["【動画映え】...", "【大喜利映え】..."],\n'
            '      "viral_score": 0\n'
            "    }\n"
            "  ]\n"
            "}\n\n"
            "viral_score は 0-100 の整数。\n"
            "入力:\n"
            + "\n".join(blocks)
        )

    def _mock_abstracted_trends(
        self,
        keywords: list[str],
        contexts: dict[str, str],
        date_stamp: str,
        *,
        sources: dict[str, str] | None = None,
    ) -> list[dict[str, Any]]:
        source_map = sources or {}
        presets = {
            "消費税計算バズ": {
                "sns_platform": "Hybrid",
                "abstracted": "レジの理不尽計算あるある",
                "category": "あるある",
                "emotional_trigger": "理不尽さ・ツッコミたくなる違和感・爽快感",
                "game_hook_ideas": [
                    "【動画映え】1分で税込を叩き落とすシュール計算アクション",
                    "【大喜利映え】税率が突然変わるレジ打ち失敗を一言で切り返すカードバトル",
                ],
                "viral_score": 88,
            },
            "ガチャ演出パロディ": {
                "sns_platform": "X",
                "abstracted": "ハズレ前提のガチャ演出パロ",
                "category": "SNSミーム",
                "emotional_trigger": "期待・落差・爆笑",
                "game_hook_ideas": [
                    "【動画映え】演出だけ豪華で中身空っぽのガチャ風タップバカゲー",
                    "【大喜利映え】ハズレ演出を一番ダサい煽り文で実況する大喜利",
                ],
                "viral_score": 82,
            },
            "一発撮りチャレンジ": {
                "sns_platform": "TikTok",
                "abstracted": "一発撮り失敗チャレンジ",
                "category": "チャレンジ文化",
                "emotional_trigger": "緊張・失敗の面白さ・共有欲",
                "game_hook_ideas": [
                    "【動画映え】ミスしたら最初からやり直しの一発撮りアクション",
                    "【大喜利映え】崩壊した撮影現場を一番シュールなキャプションで締める大喜利",
                ],
                "viral_score": 79,
            },
            "エッホエッホ構文": {
                "sns_platform": "X",
                "abstracted": "群れ無視で変な方向へ爆走",
                "category": "ネットネタ",
                "emotional_trigger": "シュールな絵面へのツッコミ",
                "game_hook_ideas": [
                    "【動画映え】エッホエッホ爆走を障害物から守る1分誘導アクション",
                    "【大喜利映え】爆走理由を一番哲学的に言い切る大喜利カード",
                ],
                "viral_score": 90,
            },
            "今日ビジュいいじゃん音源": {
                "sns_platform": "TikTok",
                "abstracted": "全肯定自己チューセルフ褒め",
                "category": "TikTok音源・動画ミーム",
                "emotional_trigger": "ナルシストすぎるポジティブさへのツッコミ欲",
                "game_hook_ideas": [
                    "【動画映え】音源のリズムでスタンプ連打して盛れ度カンスト耐久",
                    "【大喜利映え】悲惨な失敗を『今日ビジュいいじゃん！』で押し通すカードバトル",
                ],
                "viral_score": 92,
            },
        }
        trends: list[dict[str, Any]] = []
        for index, keyword in enumerate(keywords, start=1):
            preset = presets.get(keyword, {})
            platform = normalize_sns_platform(
                preset.get("sns_platform") or source_map.get(keyword) or "Hybrid"
            )
            if platform == "Hybrid":
                src = source_map.get(keyword, "")
                if src == "tiktok_tavily":
                    platform = "TikTok"
                elif src == "togetter":
                    platform = "X"
                elif src == "yahoo":
                    platform = "Hybrid"
            trends.append(
                {
                    "trend_id": f"trend_{date_stamp}_{index:03d}",
                    "sns_platform": platform,
                    "keyword": {
                        "original": keyword,
                        "abstracted": str(preset.get("abstracted") or "シュールな日常ネタ"),
                        "category": str(preset.get("category") or "SNSミーム・ネットネタ"),
                    },
                    "context": {
                        "summary": contexts.get(keyword, "")[:200],
                        "emotional_trigger": str(
                            preset.get("emotional_trigger") or "爆笑・ツッコミ欲"
                        ),
                    },
                    "game_hook_ideas": normalize_game_hook_ideas(
                        list(preset.get("game_hook_ideas") or [])
                    ),
                    "viral_score": int(preset.get("viral_score") or 70),
                }
            )
        return trends

    # ------------------------------------------------------------------
    # Mechanics
    # ------------------------------------------------------------------

    def get_game_mechanics(self) -> dict[str, Any]:
        scraped = self._fetch_unityroom_games()
        filtered = [g for g in scraped if self._is_2d_browser_feasible(g)]
        candidates = self._normalize_mechanics_with_llm(filtered)

        db = _load_json(self.mechanics_path, _empty_mechanics_db())
        mechanics = db.get("mechanics")
        if not isinstance(mechanics, list):
            mechanics = []

        existing_keys = {
            str(item.get("dedupe_key"))
            for item in mechanics
            if isinstance(item, dict) and item.get("dedupe_key")
        }

        added = 0
        skipped = 0
        now = _utc_now_iso()
        for candidate in candidates:
            key = str(candidate.get("dedupe_key") or "").strip()
            if not key:
                key = build_dedupe_key(
                    str(candidate.get("name") or ""),
                    str(candidate.get("core_loop") or ""),
                    list(candidate.get("tags") or []),
                )
                candidate["dedupe_key"] = key
            if key in existing_keys:
                skipped += 1
                print(f"[market_research] skip duplicate mechanic dedupe_key={key}")
                continue
            if not candidate.get("mechanic_id"):
                candidate["mechanic_id"] = f"mech_{key}"[:64]
            candidate.setdefault(
                "usage_stats",
                {"times_used": 0, "last_used_at": None},
            )
            candidate["first_added_at"] = now
            mechanics.append(candidate)
            existing_keys.add(key)
            added += 1

        payload = {
            "last_updated": now,
            "total_count": len(mechanics),
            "mechanics": mechanics,
        }
        _write_json(self.mechanics_path, payload)
        print(
            f"[market_research] mechanics added={added} skipped={skipped} total={payload['total_count']}"
        )
        return payload

    def _fetch_unityroom_games(self) -> list[dict[str, Any]]:
        if self.mock:
            return [dict(item) for item in MOCK_UNITYROOM_GAMES]

        import requests
        from bs4 import BeautifulSoup

        try:
            response = requests.get(
                UNITYROOM_POPULAR_URL,
                timeout=20,
                headers={"User-Agent": "kotatsu-soft-market-research/1.0"},
            )
            response.raise_for_status()
        except Exception as exc:  # noqa: BLE001
            raise MarketResearchError(f"unityroom fetch failed: {exc}") from exc

        soup = BeautifulSoup(response.text, "html.parser")
        games: list[dict[str, Any]] = []

        cards = soup.select("article, .game-card, .games-list li, li.game, .card")
        if not cards:
            cards = soup.select("a[href*='/games/']")

        for card in cards:
            title_el = card.select_one("h2, h3, .title, .game-title, a")
            title = title_el.get_text(" ", strip=True) if title_el else ""
            if not title:
                title = card.get_text(" ", strip=True)[:80]
            if not title:
                continue

            tag_els = card.select("a[href*='tag'], .tag, .badge, .label, span")
            tags: list[str] = []
            for tag_el in tag_els:
                tag_text = tag_el.get_text(" ", strip=True)
                if not tag_text:
                    continue
                if tag_text.startswith("#"):
                    tag_text = tag_text[1:]
                if 1 < len(tag_text) <= 20 and tag_text not in tags:
                    tags.append(tag_text)

            desc_el = card.select_one("p, .description, .summary, .game-description")
            description = desc_el.get_text(" ", strip=True) if desc_el else ""
            games.append(
                {
                    "title": title,
                    "tags": tags,
                    "description": description,
                }
            )
            if len(games) >= MAX_UNITYROOM_GAMES:
                break

        if not games:
            raise MarketResearchError("unityroom: no games extracted")
        return games

    def _is_2d_browser_feasible(self, game: dict[str, Any]) -> bool:
        tags = [str(t) for t in (game.get("tags") or [])]
        haystack = " ".join(
            [
                str(game.get("title") or ""),
                str(game.get("description") or ""),
                " ".join(tags),
            ]
        ).lower()
        haystack_nfkc = unicodedata.normalize("NFKC", haystack)

        for tag in tags:
            normalized = unicodedata.normalize("NFKC", tag).strip().lower().lstrip("#")
            if normalized in EXCLUDE_MECHANIC_TAGS:
                return False

        for keyword in EXCLUDE_MECHANIC_KEYWORDS:
            if keyword.lower() in haystack_nfkc:
                return False

        # 明示的に 2D / カジュアル寄りなら採用。タグが空でも除外語がなければ通す
        return True

    def _normalize_mechanics_with_llm(
        self,
        games: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        if not games:
            return []

        if self.mock and self.llm_callable is None:
            return self._mock_normalized_mechanics(games)

        prompt = self._build_mechanics_prompt(games)
        raw = self._call_llm(prompt)
        parsed = _extract_json_object(raw)
        items = parsed.get("mechanics") if isinstance(parsed, dict) else parsed
        if not isinstance(items, list):
            raise MarketResearchError(
                "LLM mechanics payload must be a list or {mechanics: [...]}"
            )

        titles = [str(g.get("title") or "") for g in games]
        descriptions = {
            str(g.get("title") or ""): str(g.get("description") or "") for g in games
        }

        mechanics: list[dict[str, Any]] = []
        for index, item in enumerate(items):
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            core_loop = str(item.get("core_loop") or "").strip()
            if not name or not core_loop:
                continue
            tags = item.get("tags") if isinstance(item.get("tags"), list) else []
            tags = [str(t).lstrip("#") for t in tags if str(t).strip()]
            raw_inspiration = (
                item.get("inspiration") if isinstance(item.get("inspiration"), dict) else {}
            )
            fallback_title = str(raw_inspiration.get("source_title") or "").strip() or (
                titles[index] if index < len(titles) else ""
            )
            inspiration = normalize_inspiration(
                raw_inspiration,
                fallback_title=fallback_title,
                fallback_description=descriptions.get(fallback_title, ""),
            )
            dedupe_key = str(item.get("dedupe_key") or "").strip() or build_dedupe_key(
                name,
                core_loop,
                tags + [inspiration["unique_motif"][:24]],
            )
            suitability = (
                item.get("ai_dev_suitability")
                if isinstance(item.get("ai_dev_suitability"), dict)
                else {}
            )
            appeal = (
                item.get("market_appeal")
                if isinstance(item.get("market_appeal"), dict)
                else {}
            )
            try:
                complexity = int(suitability.get("complexity_score", 2))
            except (TypeError, ValueError):
                complexity = 2
            features = suitability.get("recommended_engine_features")
            if not isinstance(features, list):
                features = ["Canvas2D"]
            mechanics.append(
                {
                    "mechanic_id": str(item.get("mechanic_id") or f"mech_{dedupe_key}")[:64],
                    "dedupe_key": dedupe_key,
                    "name": name,
                    "core_loop": core_loop,
                    "inspiration": inspiration,
                    "tags": tags or ["2D"],
                    "ai_dev_suitability": {
                        "is_1week_feasible": bool(
                            suitability.get("is_1week_feasible", True)
                        ),
                        "complexity_score": max(1, min(5, complexity)),
                        "recommended_engine_features": [str(f) for f in features][:6],
                    },
                    "market_appeal": {
                        "viral_potential": str(
                            appeal.get("viral_potential")
                            or "短尺動画で事故・爽快シーンが切り出しやすい"
                        ),
                        "target_audience": str(
                            appeal.get("target_audience") or "カジュアルゲーマー・配信者"
                        ),
                    },
                    "usage_stats": {"times_used": 0, "last_used_at": None},
                }
            )
        return mechanics

    def _build_mechanics_prompt(self, games: list[dict[str, Any]]) -> str:
        lines = []
        for game in games:
            lines.append(
                f"- title: {game.get('title')}\n"
                f"  tags: {', '.join(game.get('tags') or [])}\n"
                f"  description: {game.get('description')}"
            )
        return (
            "あなたはマーケターAI『ヂャイアン』です。\n"
            "以下の unityroom 作品情報から、1週間・AI単体で開発可能な"
            "2Dブラウザゲーム向けのゲームメカニクスを抽出してください。\n"
            "3D / マルチプレイ / FPS など除外済みの候補だけが渡されます。\n\n"
            "【絶対ルール】\n"
            "- core_loop は遊びの骨格として一般化してよいが、抽象ジャンル名だけで終わらせるな。\n"
            "- inspiration に元作品の具体情報を必ず残せ。"
            "unique_motif（すし・レジ打ち・猫などのモチーフ）と "
            "twist_gimmick（画面逆さ・物理で跳ねる・巨大な箸が邪魔する等の一捻り）を具体的に書け。\n"
            "- 『積み上げパズル』『回避アクション』のような一般語だけに丸めるな。"
            "実例ならではのネタを削ぎ落とすな。\n"
            "- dedupe_key は英小文字とアンダースコア。"
            "モチーフ差が分かる語（例: sushi / cat_flip）を含めてよい。\n"
            "- source_title は入力の title をそのまま使う。\n\n"
            "必ず次の JSON だけを返してください（説明文禁止）:\n"
            "{\n"
            '  "mechanics": [\n'
            "    {\n"
            '      "mechanic_id": "mech_physics_stacking_sushi",\n'
            '      "dedupe_key": "physics_2d_stacking_sushi_chopsticks",\n'
            '      "name": "物理演算積み上げパズル",\n'
            '      "core_loop": "上から落ちてくる物理オブジェクトを崩さないように積み上げる",\n'
            '      "inspiration": {\n'
            '        "source_title": "寿司積み上げバトル（参考例）",\n'
            '        "unique_motif": "シャリとネタがバラバラに降ってくるシュールな世界観",\n'
            '        "twist_gimmick": "一定の高さに達すると巨大な箸が横から邪魔してくる"\n'
            "      },\n"
            '      "tags": ["2D", "物理演算", "シュール", "積み上げ"],\n'
            '      "ai_dev_suitability": {\n'
            '        "is_1week_feasible": true,\n'
            '        "complexity_score": 2,\n'
            '        "recommended_engine_features": ["Rigidbody2D", "Canvas2D"]\n'
            "      },\n"
            '      "market_appeal": {\n'
            '        "viral_potential": "...",\n'
            '        "target_audience": "..."\n'
            "      }\n"
            "    }\n"
            "  ]\n"
            "}\n\n"
            "入力:\n"
            + "\n".join(lines)
        )

    def _mock_normalized_mechanics(
        self,
        games: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        presets = {
            "崩れる積み木タワー": {
                "mechanic_id": "mech_physics_stacking_sushi",
                "dedupe_key": "physics_2d_stacking_sushi_chopsticks",
                "name": "物理演算積み上げパズル",
                "core_loop": "上から落ちてくる物理オブジェクトを崩さないように積み上げる",
                "inspiration": {
                    "source_title": "崩れる積み木タワー",
                    "unique_motif": "シャリとネタがバラバラに降ってくるシュールな寿司世界観",
                    "twist_gimmick": "一定の高さに達すると巨大な箸が横から邪魔してくる",
                },
                "tags": ["2D", "物理演算", "シュール", "積み上げ"],
                "ai_dev_suitability": {
                    "is_1week_feasible": True,
                    "complexity_score": 2,
                    "recommended_engine_features": ["RigidBody2D", "BoxCollider2D"],
                },
                "market_appeal": {
                    "viral_potential": "崩れた瞬間の寿司タワー事故映像がTikTok/Xで映えやすい",
                    "target_audience": "カジュアルゲーマー・配信者",
                },
            },
            "タイミング反射タップ": {
                "mechanic_id": "mech_timing_cat_flip_tap",
                "dedupe_key": "timing_2d_cat_footprint_screen_flip",
                "name": "タイミング反射タップ",
                "core_loop": "流れてくるノートを正確なタイミングでタップしコンボを繋ぐ",
                "inspiration": {
                    "source_title": "タイミング反射タップ",
                    "unique_motif": "画面を横断する猫の足跡ノート",
                    "twist_gimmick": "コンボ中に画面が上下反転し、足音リズムで戻す",
                },
                "tags": ["2D", "リズム", "ワンタップ", "猫"],
                "ai_dev_suitability": {
                    "is_1week_feasible": True,
                    "complexity_score": 2,
                    "recommended_engine_features": ["Canvas2D", "requestAnimationFrame"],
                },
                "market_appeal": {
                    "viral_potential": "画面反転の事故コンボが短尺で映える",
                    "target_audience": "カジュアルゲーマー・リズム好き",
                },
            },
        }
        mechanics: list[dict[str, Any]] = []
        for game in games:
            title = str(game.get("title") or "")
            description = str(game.get("description") or "")
            if title in presets:
                item = dict(presets[title])
                item["inspiration"] = normalize_inspiration(
                    item.get("inspiration"),
                    fallback_title=title,
                    fallback_description=description,
                )
                item["usage_stats"] = {"times_used": 0, "last_used_at": None}
                mechanics.append(item)
                continue
            name = f"2D要素:{title}"[:40]
            core_loop = description[:120] or title
            tags = [str(t).lstrip("#") for t in (game.get("tags") or [])] or ["2D"]
            inspiration = normalize_inspiration(
                None,
                fallback_title=title,
                fallback_description=description,
            )
            dedupe_key = build_dedupe_key(
                name, core_loop, tags + [inspiration["unique_motif"][:24]]
            )
            mechanics.append(
                {
                    "mechanic_id": f"mech_{dedupe_key}"[:64],
                    "dedupe_key": dedupe_key,
                    "name": name,
                    "core_loop": core_loop,
                    "inspiration": inspiration,
                    "tags": tags,
                    "ai_dev_suitability": {
                        "is_1week_feasible": True,
                        "complexity_score": 2,
                        "recommended_engine_features": ["Canvas2D"],
                    },
                    "market_appeal": {
                        "viral_potential": "シンプル操作の失敗・成功が短尺で映えやすい",
                        "target_audience": "カジュアルゲーマー",
                    },
                    "usage_stats": {"times_used": 0, "last_used_at": None},
                }
            )
        return mechanics

    # ------------------------------------------------------------------
    # LLM
    # ------------------------------------------------------------------

    def _call_llm(self, prompt: str) -> str:
        if self.llm_callable is not None:
            return self.llm_callable(prompt)

        if not self.gemini_api_key:
            raise MarketResearchError(
                "GEMINI_API_KEY が設定されていません。環境変数を確認してください。"
            )

        try:
            from google import genai
            from google.genai import types
        except ImportError as exc:
            raise MarketResearchError(
                "google-genai がインストールされていません。"
            ) from exc

        client = genai.Client(api_key=self.gemini_api_key)
        try:
            response = client.models.generate_content(
                model=self.model_name,
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=self.temperature,
                    response_mime_type="application/json",
                ),
            )
        except Exception as exc:  # noqa: BLE001
            raise MarketResearchError(f"Gemini request failed: {exc}") from exc

        text = getattr(response, "text", None)
        if text:
            return str(text)
        # candidates 経由のフォールバック
        candidates = getattr(response, "candidates", None) or []
        for candidate in candidates:
            content = getattr(candidate, "content", None)
            parts = getattr(content, "parts", None) or []
            chunks = [str(getattr(part, "text", "") or "") for part in parts]
            joined = "".join(chunks).strip()
            if joined:
                return joined
        raise MarketResearchError("Gemini response contained no text")


def main(argv: Optional[list[str]] = None) -> int:
    from dotenv import load_dotenv

    # ai-core/.env を読み込む（CWD によらず）
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    load_dotenv()

    parser = argparse.ArgumentParser(description="ヂャイアン市場調査（トレンド＋メカニクス）")
    parser.add_argument(
        "--mock",
        action="store_true",
        help="外部アクセスなしのモックモードで実行する",
    )
    parser.add_argument(
        "--research-dir",
        type=Path,
        default=None,
        help="出力ディレクトリ（default: shared/research）",
    )
    args = parser.parse_args(argv)

    researcher = MarketResearcher(mock=args.mock, research_dir=args.research_dir)
    trends, mechanics = researcher.run_all()
    print(
        f"[market_research] trends={len(trends.get('trends') or [])} "
        f"-> {researcher.trends_file()}"
    )
    print(
        f"[market_research] mechanics_total={mechanics.get('total_count')} "
        f"-> {researcher.mechanics_file()}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
