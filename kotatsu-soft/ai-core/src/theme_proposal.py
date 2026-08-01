"""ヂャイアンによるトレンド×メカニクスの企画テーマ提案。"""

from __future__ import annotations

import argparse
import json
import os
import re
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


class ThemeProposalError(RuntimeError):
    """テーマ提案処理の失敗。"""


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
        lines.append(
            f"- trend_id={item.get('trend_id')} "
            f"platform={item.get('sns_platform')} "
            f"abstracted={keyword.get('abstracted')} "
            f"original={keyword.get('original')} "
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
        lines.append(
            f"- mechanic_id={item.get('mechanic_id')} "
            f"name={item.get('name')} "
            f"core_loop={item.get('core_loop')} "
            f"motif={insp.get('unique_motif')} "
            f"twist={insp.get('twist_gimmick')} "
            f"source_title={insp.get('source_title')}"
        )
    return "\n".join(lines) if lines else "（メカニクスなし）"


def build_meeting_theme_text(option: dict[str, Any]) -> str:
    """企画会議に渡すテーマ文字列。"""
    title = str(option.get("title") or "").strip()
    summary = str(option.get("concept_summary") or "").strip()
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
    parts = [title]
    if summary:
        parts.append(summary)
    if viral:
        parts.append(f"バズポイント: {viral}")
    if trend_label or mech_label:
        parts.append(f"参照: トレンド={trend_label} / メカニクス={mech_label}")
    return "\n\n".join(p for p in parts if p)


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
        previous_titles: list[str] | None = None,
    ) -> dict[str, Any]:
        trends_data, mechanics_data = self.load_research_data(trends, mechanics)
        prompt = self._build_prompt(
            trends_data,
            mechanics_data,
            previous_titles=previous_titles or [],
        )

        if self.mock and self.llm_callable is None:
            options = self._mock_options(trends_data, mechanics_data, previous_titles or [])
        else:
            raw = self._call_llm(prompt)
            options = self._parse_options(raw, trends_data, mechanics_data)

        payload = {
            "updated_at": _utc_now_iso(),
            "researcher": "ヂャイアン",
            "options": options,
        }
        _write_json(self.options_path, payload)
        return payload

    def _build_prompt(
        self,
        trends: dict[str, Any],
        mechanics: dict[str, Any],
        *,
        previous_titles: list[str],
    ) -> str:
        avoid = ""
        if previous_titles:
            avoid = (
                "\n【再検討】以下のタイトルと似た案は禁止。別の掛け合わせを出せ:\n"
                + "\n".join(f"- {t}" for t in previous_titles)
                + "\n"
            )
        return (
            "あなたはマーケターAI『ヂャイアン』です。\n"
            "コタツ・ソフトは1週間・AI単体で作れるブラウザのバカバカしい・シュールなバカゲーを量産する。\n"
            "以下の最新トレンドとゲームメカニクスを掛け合わせ、企画テーマ案を3〜4件作れ。\n"
            f"{avoid}\n"
            "【絶対ルール】\n"
            "- 社会派・啓発・真面目なドキュメンタリー路線は禁止。爆笑・失敗絵面・拡散を優先。\n"
            "- 各案は必ず1つの trend_id と1つの mechanic_id を掛け合わせる。\n"
            "- title はキャッチーに（例: 『今日ビジュいいじゃん×自撮りタワー』）。\n"
            "- concept_summary は1分でわかるコア体験。\n"
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

        options: list[dict[str, Any]] = []
        for index, item in enumerate(items, start=1):
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or "").strip()
            summary = str(item.get("concept_summary") or "").strip()
            viral = str(item.get("viral_point") or "").strip()
            if not title or not summary:
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
                        "trend_id": trend_id or "unknown_trend",
                        "mechanic_id": mechanic_id or "unknown_mechanic",
                        "trend_label": trend_label or trend_id or "unknown",
                        "mechanic_label": mechanic_label or mechanic_id or "unknown",
                    },
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
        previous_titles: list[str],
    ) -> list[dict[str, Any]]:
        trend_items = [t for t in (trends.get("trends") or []) if isinstance(t, dict)]
        mech_items = [m for m in (mechanics.get("mechanics") or []) if isinstance(m, dict)]
        pairs = min(MAX_OPTIONS, len(trend_items), len(mech_items), 4)
        if pairs < MIN_OPTIONS:
            # 足りない場合は循環して埋める
            pairs = MAX_OPTIONS
        options: list[dict[str, Any]] = []
        suffix = "改" if previous_titles else ""
        for i in range(pairs):
            trend = trend_items[i % len(trend_items)]
            mech = mech_items[i % len(mech_items)]
            kw = trend.get("keyword") if isinstance(trend.get("keyword"), dict) else {}
            abstracted = str(kw.get("abstracted") or kw.get("original") or "バズネタ")
            mech_name = str(mech.get("name") or "謎メカニクス")
            insp = mech.get("inspiration") if isinstance(mech.get("inspiration"), dict) else {}
            twist = str(insp.get("twist_gimmick") or "一捻りギミック")
            title = f"{abstracted[:20]}×{mech_name[:16]}{suffix}"
            if title in previous_titles:
                title = f"{title}・別解{i+1}"
            options.append(
                {
                    "option_id": i + 1,
                    "title": title,
                    "concept_summary": (
                        f"{abstracted}を題材に、{mech_name}のコアループで遊ぶ1分バカゲー。"
                        f"決め手は『{twist}』。"
                    ),
                    "combined_sources": {
                        "trend_id": str(trend.get("trend_id") or f"trend_{i+1}"),
                        "mechanic_id": str(mech.get("mechanic_id") or f"mech_{i+1}"),
                        "trend_label": abstracted,
                        "mechanic_label": mech_name,
                    },
                    "viral_point": (
                        "【動画映え】失敗・崩壊の瞬間が短尺で映える。"
                        "【大喜利映え】一言ツッコミがタイムラインで拡散しやすい。"
                    ),
                }
            )
            if len(options) >= MAX_OPTIONS:
                break
        return options[:MAX_OPTIONS]

    def _call_llm(self, prompt: str) -> str:
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
