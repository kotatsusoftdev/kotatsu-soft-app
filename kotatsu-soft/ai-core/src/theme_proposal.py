"""ヂャイアンによるトレンド×メカニクスの企画テーマ提案。"""

from __future__ import annotations

import argparse
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

import yaml

DEFAULT_MODEL = "gemini-flash-lite-latest"
DEFAULT_TEMPERATURE = 0.8
TRENDS_FILENAME = "latest_trends.json"
MECHANICS_FILENAME = "mechanics_db.json"
THEME_OPTIONS_FILENAME = "latest_theme_options.json"
MIN_OPTIONS = 3
MAX_OPTIONS = 4
APPROACH_DIRECT = "元ネタ直球"
APPROACH_REMAP = "世界観置換"
VALID_APPROACH_TYPES = frozenset({APPROACH_DIRECT, APPROACH_REMAP})


class ThemeProposalError(RuntimeError):
    """テーマ提案処理の失敗。"""


def normalize_approach_type(value: Any) -> str | None:
    """approach_type を正規化する。不明なら None。"""
    raw = str(value or "").strip()
    if not raw:
        return None
    if raw in VALID_APPROACH_TYPES:
        return raw
    compact = raw.replace(" ", "").replace("　", "")
    if compact in {"元ネタ直球", "直球", "直球案", "元ネタ直球案"}:
        return APPROACH_DIRECT
    if "直球" in compact and "置換" not in compact:
        return APPROACH_DIRECT
    if compact in {"世界観置換", "置換", "世界観置換案", "置換案"}:
        return APPROACH_REMAP
    if "置換" in compact or "抽象" in compact:
        return APPROACH_REMAP
    return None


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def default_research_dir() -> Path:
    return _repo_root() / "shared" / "research"


def default_marketing_config_path() -> Path:
    return Path(__file__).resolve().parent / "agents" / "marketing" / "config.yaml"


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _extract_json_object(text: str) -> Any:
    raw = (text or "").strip()
    if not raw:
        raise ThemeProposalError("LLM response is empty")
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\{[\s\S]*\}|\[[\s\S]*\]", raw)
        if not match:
            raise ThemeProposalError("LLM response is not valid JSON") from None
        return json.loads(match.group(0))


def summarize_trends_for_prompt(trends: dict[str, Any], *, limit: int = 8) -> str:
    items = trends.get("trends") if isinstance(trends, dict) else None
    if not isinstance(items, list) or not items:
        return "（トレンドなし）"
    lines: list[str] = []
    for item in items[:limit]:
        if not isinstance(item, dict):
            continue
        keyword = item.get("keyword") if isinstance(item.get("keyword"), dict) else {}
        context = item.get("context") if isinstance(item.get("context"), dict) else {}
        emotion = str(context.get("emotional_trigger") or "").strip()
        summary = str(context.get("summary") or "").strip()
        lines.append(
            f"- trend_id={item.get('trend_id')} "
            f"platform={item.get('sns_platform')} "
            f"abstracted={keyword.get('abstracted')} "
            f"original={keyword.get('original')} "
            f"emotion={emotion or '（不明）'} "
            f"situation={summary or '（不明）'} "
            f"viral={item.get('viral_score')}"
        )
    return "\n".join(lines) if lines else "（トレンドなし）"


def summarize_mechanics_for_prompt(mechanics: dict[str, Any], *, limit: int = 12) -> str:
    items = mechanics.get("mechanics") if isinstance(mechanics, dict) else None
    if not isinstance(items, list) or not items:
        return "（メカニクスなし）"
    # 新しいものを優先
    ordered = list(reversed(items))[:limit]
    lines: list[str] = []
    for item in ordered:
        if not isinstance(item, dict):
            continue
        insp = item.get("inspiration") if isinstance(item.get("inspiration"), dict) else {}
        core_loop = str(item.get("core_loop") or "").strip()
        twist = str(insp.get("twist_gimmick") or "").strip()
        lines.append(
            f"- mechanic_id={item.get('mechanic_id')} "
            f"name={item.get('name')} "
            f"core_loop={core_loop or '（不明）'} "
            f"core_verb_hint={twist or core_loop or '（不明）'} "
            f"motif={insp.get('unique_motif')} "
            f"twist={twist} "
            f"source_title={insp.get('source_title')}"
        )
    return "\n".join(lines) if lines else "（メカニクスなし）"


def extract_avoid_pairs(
    previous_options: list[dict[str, Any]] | None,
) -> set[tuple[str, str]]:
    """前回までに提示した trend_id × mechanic_id の禁止ペア。"""
    pairs: set[tuple[str, str]] = set()
    for opt in previous_options or []:
        if not isinstance(opt, dict):
            continue
        sources = (
            opt.get("combined_sources")
            if isinstance(opt.get("combined_sources"), dict)
            else {}
        )
        trend_id = str(sources.get("trend_id") or "").strip()
        mechanic_id = str(sources.get("mechanic_id") or "").strip()
        if trend_id and mechanic_id:
            pairs.add((trend_id, mechanic_id))
    return pairs


def option_source_pair(option: dict[str, Any]) -> tuple[str, str] | None:
    sources = (
        option.get("combined_sources")
        if isinstance(option.get("combined_sources"), dict)
        else {}
    )
    trend_id = str(sources.get("trend_id") or "").strip()
    mechanic_id = str(sources.get("mechanic_id") or "").strip()
    if not trend_id or not mechanic_id:
        return None
    return (trend_id, mechanic_id)


@dataclass(frozen=True)
class MeetingThemeParts:
    """会議投入用テーマの分割結果。"""

    title: str
    overview: str
    details: str
    theme_for_agents: str


def _compose_theme_for_agents(overview: str, details: str) -> str:
    overview = (overview or "").strip()
    details = (details or "").strip()
    if not overview and not details:
        return "untitled"
    if details:
        return f"テーマ:\n{overview}\n\n詳細:\n{details}"
    return f"テーマ:\n{overview}"


def build_meeting_theme_parts(option: dict[str, Any]) -> MeetingThemeParts:
    """案オプションを名称・概要・詳細に分割する。"""
    title = str(option.get("title") or "").strip() or "untitled"
    summary = str(option.get("concept_summary") or "").strip()
    approach = normalize_approach_type(option.get("approach_type")) or str(
        option.get("approach_type") or ""
    ).strip()
    design_intent = str(option.get("design_intent") or "").strip()
    synergy = str(option.get("synergy_reason") or "").strip()
    viral = str(option.get("viral_point") or "").strip()
    sources = (
        option.get("combined_sources")
        if isinstance(option.get("combined_sources"), dict)
        else {}
    )
    trend_label = str(sources.get("trend_label") or sources.get("trend_id") or "").strip()
    mech_label = str(
        sources.get("mechanic_label") or sources.get("mechanic_id") or ""
    ).strip()

    overview = "\n\n".join(p for p in (title, summary) if p)
    detail_parts: list[str] = []
    if approach:
        detail_parts.append(f"アプローチ: {approach}")
    if design_intent:
        detail_parts.append(f"アプローチの狙い: {design_intent}")
    if synergy:
        detail_parts.append(f"シナジー理由: {synergy}")
    if viral:
        detail_parts.append(f"バズポイント: {viral}")
    if trend_label or mech_label:
        detail_parts.append(f"参照: トレンド={trend_label} / メカニクス={mech_label}")
    details = "\n\n".join(detail_parts)
    return MeetingThemeParts(
        title=title,
        overview=overview,
        details=details,
        theme_for_agents=_compose_theme_for_agents(overview, details),
    )


def meeting_theme_parts_from_free_text(theme: str) -> MeetingThemeParts:
    """フリー入力テーマを MeetingThemeParts に変換する。"""
    text = (theme or "").strip()
    if not text:
        return MeetingThemeParts(
            title="untitled",
            overview="untitled",
            details="",
            theme_for_agents="untitled",
        )
    first_line = text.splitlines()[0].strip() or "untitled"
    return MeetingThemeParts(
        title=first_line,
        overview=text,
        details="",
        theme_for_agents=text,
    )


def meeting_theme_parts_from_text(theme: str) -> MeetingThemeParts:
    """agents 用テーマ文字列（またはフリー入力）から分割結果を復元する。"""
    text = (theme or "").strip()
    if not text:
        return meeting_theme_parts_from_free_text("")
    if text.startswith("テーマ:"):
        body = text[len("テーマ:") :].lstrip("\n")
        if "\n\n詳細:" in body:
            overview, details = body.split("\n\n詳細:", 1)
            overview = overview.strip()
            details = details.strip()
        else:
            overview = body.strip()
            details = ""
        title = overview.splitlines()[0].strip() if overview else "untitled"
        return MeetingThemeParts(
            title=title or "untitled",
            overview=overview or title or "untitled",
            details=details,
            theme_for_agents=text,
        )
    return meeting_theme_parts_from_free_text(text)


def build_meeting_theme_text(option: dict[str, Any]) -> str:
    """企画会議に渡すテーマ文字列（agents 用。互換 API）。"""
    return build_meeting_theme_parts(option).theme_for_agents


class ThemeProposer:
    """トレンド×メカニクスからバカゲー企画テーマ案を生成する。"""

    def __init__(
        self,
        *,
        mock: bool = False,
        research_dir: Path | None = None,
        gemini_api_key: str | None = None,
        marketing_config_path: Path | None = None,
        llm_callable: Callable[[str], str] | None = None,
    ) -> None:
        self.mock = mock
        self.research_dir = Path(research_dir) if research_dir else default_research_dir()
        self.trends_path = self.research_dir / TRENDS_FILENAME
        self.mechanics_path = self.research_dir / MECHANICS_FILENAME
        self.options_path = self.research_dir / THEME_OPTIONS_FILENAME
        self.gemini_api_key = (gemini_api_key or os.getenv("GEMINI_API_KEY") or "").strip()
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

    def load_research_data(
        self,
        trends: dict[str, Any] | None = None,
        mechanics: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        trends_data = trends if isinstance(trends, dict) else _load_json(self.trends_path)
        mechanics_data = (
            mechanics if isinstance(mechanics, dict) else _load_json(self.mechanics_path)
        )
        trend_items = trends_data.get("trends")
        mech_items = mechanics_data.get("mechanics")
        if not isinstance(trend_items, list) or not trend_items:
            raise ThemeProposalError(
                "latest_trends.json にトレンドがありません。"
                "先に市場調査プロセスを実行してください。"
            )
        if not isinstance(mech_items, list) or not mech_items:
            raise ThemeProposalError(
                "mechanics_db.json にメカニクスがありません。"
                "先に市場調査プロセスを実行してください。"
            )
        return trends_data, mechanics_data

    def generate(
        self,
        trends: dict[str, Any] | None = None,
        mechanics: dict[str, Any] | None = None,
        *,
        previous_options: list[dict[str, Any]] | None = None,
        previous_titles: list[str] | None = None,
    ) -> dict[str, Any]:
        trends_data, mechanics_data = self.load_research_data(trends, mechanics)
        prior = [o for o in (previous_options or []) if isinstance(o, dict)]
        if not prior and previous_titles:
            # 後方互換: タイトルのみ渡された場合
            prior = [{"title": t} for t in previous_titles if t]
        avoid_pairs = extract_avoid_pairs(prior)
        temperature = (
            min(1.0, self.temperature + 0.15) if prior else self.temperature
        )
        prompt = self._build_prompt(
            trends_data,
            mechanics_data,
            previous_options=prior,
        )

        if self.mock and self.llm_callable is None:
            options = self._mock_options(trends_data, mechanics_data, prior)
        else:
            options = self._generate_with_llm(
                prompt,
                trends_data,
                mechanics_data,
                avoid_pairs=avoid_pairs,
                temperature=temperature,
                allow_retry=bool(prior),
            )

        payload = {
            "updated_at": _utc_now_iso(),
            "researcher": "ヂャイアン",
            "options": options,
        }
        _write_json(self.options_path, payload)
        return payload

    def _generate_with_llm(
        self,
        prompt: str,
        trends: dict[str, Any],
        mechanics: dict[str, Any],
        *,
        avoid_pairs: set[tuple[str, str]],
        temperature: float,
        allow_retry: bool,
    ) -> list[dict[str, Any]]:
        raw = self._call_llm(prompt, temperature=temperature)
        try:
            return self._parse_options(
                raw, trends, mechanics, avoid_pairs=avoid_pairs
            )
        except ThemeProposalError:
            if not allow_retry:
                raise
        # 禁止ペア除外で件数不足 → 1回だけ再生成
        raw_retry = self._call_llm(prompt, temperature=temperature)
        return self._parse_options(
            raw_retry, trends, mechanics, avoid_pairs=avoid_pairs
        )

    def _build_prompt(
        self,
        trends: dict[str, Any],
        mechanics: dict[str, Any],
        *,
        previous_options: list[dict[str, Any]],
    ) -> str:
        avoid = ""
        if previous_options:
            titles = [
                str(o.get("title") or "").strip()
                for o in previous_options
                if o.get("title")
            ]
            pair_lines: list[str] = []
            for opt in previous_options:
                sources = (
                    opt.get("combined_sources")
                    if isinstance(opt.get("combined_sources"), dict)
                    else {}
                )
                trend_id = str(sources.get("trend_id") or "").strip()
                mechanic_id = str(sources.get("mechanic_id") or "").strip()
                if not trend_id and not mechanic_id:
                    continue
                trend_label = str(sources.get("trend_label") or trend_id).strip()
                mech_label = str(sources.get("mechanic_label") or mechanic_id).strip()
                pair_lines.append(
                    f"- {trend_id} × {mechanic_id} （{trend_label} × {mech_label}）"
                )
            avoid = (
                "\n【再検討・重複禁止】前回までに出した案の焼き直しは禁止。"
                "別のトレンド×別のメカニクスを優先せよ。\n"
                "- 同じ trend_id × mechanic_id の組み合わせは絶対禁止。\n"
                "- 同じ感情ピーク＋同じコア動詞の言い換えも禁止。\n"
            )
            if titles:
                avoid += "禁止タイトル:\n" + "\n".join(f"- {t}" for t in titles) + "\n"
            if pair_lines:
                avoid += "禁止ペア:\n" + "\n".join(pair_lines) + "\n"
        return (
            "あなたはマーケターAI『ヂャイアン』です。\n"
            "コタツ・ソフトは1週間・AI単体で作れるブラウザのバカバカしい・シュールなバカゲーを量産する。\n"
            "以下の最新トレンドとゲームメカニクスを掛け合わせ、企画テーマ案を3〜4件作れ。\n"
            f"{avoid}\n"
            "【最優先の評価軸】\n"
            "- とにかくゲームとして一番爆笑できて面白いか。形式的な『直球禁止』などのルールに縛るな。\n"
            "- 1分動画にした時に爆発的な笑いとツッコミ（バズ）が生まれるかを最大基準にせよ。\n\n"
            "【アプローチ選択（各案で最適を選べ）】\n"
            f"- 『{APPROACH_DIRECT}』: トレンドのシチュエーション自体が圧倒的に面白く絵面が映えるなら、"
            "元ネタそのままのモチーフで企画化せよ。\n"
            f"- 『{APPROACH_REMAP}』: 元ネタそのままでは地味・パロディ止まりになる場合は、"
            "感情（焦り・不快感・信仰心等）や構造だけを抽出し、"
            "全く別の突拍子もないバカバカしい世界観（異世界、怪しい宮廷、シュールな動物など）に置き換えて昇華せよ。\n"
            "- 直球か置換かは『どちらがより爆笑できるか』だけで決めよ。どちらも禁止ではない。\n\n"
            "【絶対ルール】\n"
            "- 社会派・啓発・真面目なドキュメンタリー路線は禁止。爆笑・失敗絵面・拡散を優先。\n"
            "- 各案は必ず1つの trend_id と1つの mechanic_id を掛け合わせる。\n"
            "- 名詞同士をただくっつけただけの安易なガッチャンコは禁止"
            "（面白さの敵。例: 『青ニンニク』×『鉄球発射』でニンニクを壊すだけ）。\n"
            "- トレンドの感情・シチュエーションのピークを、メカニクスのコアな動詞"
            "（滑る・耐える・押しつぶす・追う等）で体験として増幅せよ。\n"
            "- プレイヤーが『だからこのゲーム性なのかよｗ』とツッコミたくなる納得感を持て。\n"
            "- title はキャッチーに（状況と体験が伝わるタイトル。単なる『A×B』列挙は避ける）。\n"
            "- concept_summary は1分でわかるコア体験。\n"
            f"- approach_type は必須。値は『{APPROACH_DIRECT}』または『{APPROACH_REMAP}』のみ。\n"
            "- design_intent は必須。なぜ直球（または置換）にしたのかの狙いを書け。\n"
            "- synergy_reason は必須。"
            "トレンドの感情・体験とメカニクスのコア動詞がどうシンクロするかを言語化せよ。\n"
            "- viral_point に動画映え（TikTok）と大喜利映え（X）の両方を含めよ。\n"
            "- combined_sources に trend_id / mechanic_id / trend_label / mechanic_label を入れよ。\n\n"
            "必ず次の JSON だけを返せ（説明文禁止）:\n"
            "{\n"
            '  "options": [\n'
            "    {\n"
            '      "option_id": 1,\n'
            '      "title": "...",\n'
            '      "concept_summary": "...",\n'
            '      "combined_sources": {\n'
            '        "trend_id": "trend_...",\n'
            '        "mechanic_id": "mech_...",\n'
            '        "trend_label": "...",\n'
            '        "mechanic_label": "..."\n'
            "      },\n"
            f'      "approach_type": "{APPROACH_DIRECT} または {APPROACH_REMAP}",\n'
            '      "design_intent": "なぜ直球（または置換）にしたのかの狙い",\n'
            '      "synergy_reason": "感情×動詞のシンクロ理由",\n'
            '      "viral_point": "..."\n'
            "    }\n"
            "  ]\n"
            "}\n\n"
            "【トレンド】\n"
            f"{summarize_trends_for_prompt(trends)}\n\n"
            "【メカニクス】\n"
            f"{summarize_mechanics_for_prompt(mechanics)}\n"
        )

    def _parse_options(
        self,
        raw: str,
        trends: dict[str, Any],
        mechanics: dict[str, Any],
        *,
        avoid_pairs: set[tuple[str, str]] | None = None,
    ) -> list[dict[str, Any]]:
        parsed = _extract_json_object(raw)
        items = parsed.get("options") if isinstance(parsed, dict) else parsed
        if not isinstance(items, list):
            raise ThemeProposalError("LLM payload must be {options: [...]} or a list")

        trend_by_id = {
            str(t.get("trend_id")): t
            for t in (trends.get("trends") or [])
            if isinstance(t, dict) and t.get("trend_id")
        }
        mech_by_id = {
            str(m.get("mechanic_id")): m
            for m in (mechanics.get("mechanics") or [])
            if isinstance(m, dict) and m.get("mechanic_id")
        }
        forbidden = avoid_pairs or set()

        options: list[dict[str, Any]] = []
        for index, item in enumerate(items, start=1):
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or "").strip()
            summary = str(item.get("concept_summary") or "").strip()
            approach_type = normalize_approach_type(item.get("approach_type"))
            design_intent = str(item.get("design_intent") or "").strip()
            synergy = str(item.get("synergy_reason") or "").strip()
            viral = str(item.get("viral_point") or "").strip()
            if (
                not title
                or not summary
                or not approach_type
                or not design_intent
                or not synergy
            ):
                continue
            sources = (
                item.get("combined_sources")
                if isinstance(item.get("combined_sources"), dict)
                else {}
            )
            trend_id = str(sources.get("trend_id") or "").strip()
            mechanic_id = str(sources.get("mechanic_id") or "").strip()
            trend_label = str(sources.get("trend_label") or "").strip()
            mechanic_label = str(sources.get("mechanic_label") or "").strip()
            if not trend_label and trend_id in trend_by_id:
                kw = trend_by_id[trend_id].get("keyword") or {}
                if isinstance(kw, dict):
                    trend_label = str(kw.get("abstracted") or kw.get("original") or trend_id)
            if not mechanic_label and mechanic_id in mech_by_id:
                mechanic_label = str(mech_by_id[mechanic_id].get("name") or mechanic_id)
            pair = (
                trend_id or "unknown_trend",
                mechanic_id or "unknown_mechanic",
            )
            if pair in forbidden:
                continue
            try:
                option_id = int(item.get("option_id", index))
            except (TypeError, ValueError):
                option_id = index
            options.append(
                {
                    "option_id": option_id,
                    "title": title,
                    "concept_summary": summary,
                    "combined_sources": {
                        "trend_id": pair[0],
                        "mechanic_id": pair[1],
                        "trend_label": trend_label or trend_id or "unknown",
                        "mechanic_label": mechanic_label or mechanic_id or "unknown",
                    },
                    "approach_type": approach_type,
                    "design_intent": design_intent,
                    "synergy_reason": synergy,
                    "viral_point": viral or "失敗絵面が短尺で映え、一言ツッコミが拡散しやすい",
                }
            )
            if len(options) >= MAX_OPTIONS:
                break

        if len(options) < MIN_OPTIONS:
            raise ThemeProposalError(
                f"テーマ案が不足しています（{len(options)}件）。再生成してください。"
            )
        # option_id を 1..n に正規化
        for i, opt in enumerate(options, start=1):
            opt["option_id"] = i
        return options

    def _mock_options(
        self,
        trends: dict[str, Any],
        mechanics: dict[str, Any],
        previous_options: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        trend_items = [t for t in (trends.get("trends") or []) if isinstance(t, dict)]
        mech_items = [m for m in (mechanics.get("mechanics") or []) if isinstance(m, dict)]
        if not trend_items or not mech_items:
            raise ThemeProposalError("モック生成に必要なトレンド／メカニクスが不足しています。")

        avoid_pairs = extract_avoid_pairs(previous_options)
        previous_titles = {
            str(o.get("title") or "").strip()
            for o in previous_options
            if o.get("title")
        }
        suffix = "改" if previous_options else ""
        options: list[dict[str, Any]] = []
        # 履歴がある場合は開始オフセットをずらし、禁止ペアを避けて組み合わせる
        start_offset = len(avoid_pairs)
        for offset in range(start_offset, start_offset + len(trend_items) * len(mech_items)):
            t_idx = offset % len(trend_items)
            m_idx = (offset // len(trend_items)) % len(mech_items)
            # さらにずらして対角寄りにする
            m_idx = (m_idx + offset) % len(mech_items)
            trend = trend_items[t_idx]
            mech = mech_items[m_idx]
            trend_id = str(trend.get("trend_id") or f"trend_{t_idx+1}")
            mechanic_id = str(mech.get("mechanic_id") or f"mech_{m_idx+1}")
            if (trend_id, mechanic_id) in avoid_pairs:
                continue
            # 同一バッチ内の重複も避ける
            if any(
                option_source_pair(o) == (trend_id, mechanic_id) for o in options
            ):
                continue
            kw = trend.get("keyword") if isinstance(trend.get("keyword"), dict) else {}
            abstracted = str(kw.get("abstracted") or kw.get("original") or "バズネタ")
            mech_name = str(mech.get("name") or "謎メカニクス")
            insp = mech.get("inspiration") if isinstance(mech.get("inspiration"), dict) else {}
            twist = str(insp.get("twist_gimmick") or "一捻りギミック")
            ctx = trend.get("context") if isinstance(trend.get("context"), dict) else {}
            emotion = str(ctx.get("emotional_trigger") or "焦りとシュールさ").strip()
            core_loop = str(mech.get("core_loop") or "操作して笑う").strip()
            title = f"{abstracted[:20]}×{mech_name[:16]}{suffix}"
            if title in previous_titles:
                title = f"{title}・別解{len(options)+1}"
            approach_type = (
                APPROACH_DIRECT if len(options) % 2 == 0 else APPROACH_REMAP
            )
            if approach_type == APPROACH_DIRECT:
                design_intent = (
                    f"『{abstracted}』の絵面そのものが完成度高いので、"
                    "変にひねらず直球でシュールさを前面に押し出した。"
                )
            else:
                design_intent = (
                    f"元ネタ直球だとパロディ止まりなので、『{emotion}』の構造だけ抜き、"
                    "突拍子もない別世界観に置換して爆笑を増幅した。"
                )
            options.append(
                {
                    "option_id": len(options) + 1,
                    "title": title,
                    "concept_summary": (
                        f"{abstracted}を題材に、{mech_name}のコアループで遊ぶ1分バカゲー。"
                        f"決め手は『{twist}』。"
                    ),
                    "combined_sources": {
                        "trend_id": trend_id,
                        "mechanic_id": mechanic_id,
                        "trend_label": abstracted,
                        "mechanic_label": mech_name,
                    },
                    "approach_type": approach_type,
                    "design_intent": design_intent,
                    "synergy_reason": (
                        f"「{emotion}」という感情のピークを、"
                        f"『{core_loop}』という操作感で物理的に増幅する。"
                        f"ギミック『{twist}』が『だからこのゲーム性なのかよｗ』を生む。"
                    ),
                    "viral_point": (
                        "【動画映え】失敗・崩壊の瞬間が短尺で映える。"
                        "【大喜利映え】一言ツッコミがタイムラインで拡散しやすい。"
                    ),
                }
            )
            avoid_pairs.add((trend_id, mechanic_id))
            if len(options) >= MAX_OPTIONS:
                break

        if len(options) < MIN_OPTIONS:
            raise ThemeProposalError(
                f"テーマ案が不足しています（{len(options)}件）。"
                "禁止ペアが多く再生成できません。"
            )
        return options[:MAX_OPTIONS]

    def _call_llm(self, prompt: str, *, temperature: float | None = None) -> str:
        if self.llm_callable is not None:
            return self.llm_callable(prompt)
        if not self.gemini_api_key:
            raise ThemeProposalError(
                "GEMINI_API_KEY が設定されていません。環境変数を確認してください。"
            )
        try:
            from google import genai
            from google.genai import types
        except ImportError as exc:
            raise ThemeProposalError("google-genai がインストールされていません。") from exc

        temp = self.temperature if temperature is None else temperature
        client = genai.Client(api_key=self.gemini_api_key)
        try:
            response = client.models.generate_content(
                model=self.model_name,
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=temp,
                    response_mime_type="application/json",
                ),
            )
        except Exception as exc:  # noqa: BLE001
            raise ThemeProposalError(f"Gemini request failed: {exc}") from exc

        text = getattr(response, "text", None)
        if text:
            return str(text)
        candidates = getattr(response, "candidates", None) or []
        for candidate in candidates:
            content = getattr(candidate, "content", None)
            parts = getattr(content, "parts", None) or []
            chunks = [str(getattr(part, "text", "") or "") for part in parts]
            joined = "".join(chunks).strip()
            if joined:
                return joined
        raise ThemeProposalError("Gemini response contained no text")


def main(argv: Optional[list[str]] = None) -> int:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    load_dotenv()

    parser = argparse.ArgumentParser(description="ヂャイアン企画テーマ提案（トレンド×メカニクス）")
    parser.add_argument("--mock", action="store_true", help="外部APIなしで実行")
    parser.add_argument("--research-dir", type=Path, default=None)
    args = parser.parse_args(argv)

    proposer = ThemeProposer(mock=args.mock, research_dir=args.research_dir)
    payload = proposer.generate()
    print(
        f"[theme_proposal] options={len(payload.get('options') or [])} "
        f"-> {proposer.options_path}"
    )
    for opt in payload.get("options") or []:
        print(f"  {opt.get('option_id')}. {opt.get('title')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
