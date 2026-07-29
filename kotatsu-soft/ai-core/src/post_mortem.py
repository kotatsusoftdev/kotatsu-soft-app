from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
from typing import Any, Callable, Optional

from google import genai
from google.genai import types

from agents.base_agent import BaseAgent
from artifact_naming import meeting_log_path, review_path, spec_path
from lessons_store import (
    LESSON_ITEM_COUNT,
    coerce_lesson_items,
    format_lesson_items,
    lessons_learned_path,
    load_lessons,
    load_yaml_file,
    save_lessons,
)
from spec_link_registry import load_registry

AGENT_ROLES = ("pm", "dev", "marketing")
DEFAULT_MODEL = "gemini-flash-lite-latest"
MAX_MEETING_CHARS = 12_000
MAX_SPEC_CHARS = 8_000
MAX_REVIEW_CHARS = 10_000
MAX_GAME_SOURCE_CHARS = 14_000
MAX_LESSON_ITEM_CHARS = 280

ROLE_FOCUS = {
    "pm": "企画・合意形成・スコープ絞り込み・会議ファシリテーション",
    "dev": "技術実現性・実装落とし所・1日完成の見積もり感・コード品質",
    "marketing": "初見インパクト・拡散性・訴求・制約を武器にする発想",
}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def default_agents_root() -> Path:
    return Path(__file__).resolve().parent / "agents"


def agent_config_path(agents_root: Path, role: str) -> Path:
    return agents_root / role / "config.yaml"


def artifact_stem_from_spec(spec_file: str) -> str:
    name = Path(spec_file).name
    if name.startswith("spec_") and name.endswith(".md"):
        return name[len("spec_") : -len(".md")]
    raise ValueError(f"spec file name must look like spec_<stem>.md: {spec_file}")


def truncate_text(text: str, limit: int) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 20].rstrip() + "\n\n...[truncated]..."


def load_agent_profile(agents_root: Path, role: str) -> dict[str, str]:
    config = load_yaml_file(agent_config_path(agents_root, role))
    agent = config.get("agent") or {}
    llm = agent.get("llm") or config.get("llm") or {}
    criteria = agent.get("evaluation_criteria") or {}
    return {
        "name": str(agent.get("name") or role),
        "title": str(agent.get("title") or role),
        "model": str(llm.get("model") or DEFAULT_MODEL),
        "temperature": str(llm.get("temperature", 0.3)),
        "primary_focus": str(criteria.get("primary_focus") or ROLE_FOCUS.get(role, "")),
    }


def read_text_if_exists(path: Path, *, limit: int, missing_label: str) -> tuple[str, list[str]]:
    warnings: list[str] = []
    if not path.exists():
        warnings.append(f"{missing_label} not found: {path}")
        return "", warnings
    return truncate_text(path.read_text(encoding="utf-8"), limit), warnings


def format_meeting_log(path: Path, *, limit: int = MAX_MEETING_CHARS) -> tuple[str, list[str]]:
    warnings: list[str] = []
    if not path.exists():
        warnings.append(f"meeting log not found: {path}")
        return "", warnings

    lines: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            item = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(item, dict):
            continue
        if item.get("type") not in (None, "text", "system"):
            continue
        role = item.get("display_name") or item.get("role") or "unknown"
        message = str(item.get("message") or "").strip()
        if not message:
            continue
        lines.append(f"{role}: {message}")

    return truncate_text("\n\n".join(lines), limit), warnings


def resolve_game_path(
    *,
    repo: Path,
    artifact_stem: str,
    explicit_game_path: Optional[str] = None,
) -> tuple[Optional[Path], list[str]]:
    warnings: list[str] = []
    if explicit_game_path:
        path = Path(explicit_game_path)
        if not path.is_absolute():
            path = repo / path
        if not path.exists():
            warnings.append(f"game path not found: {path}")
            return None, warnings
        return path, warnings

    registry = load_registry()
    records = registry.get("records") or []
    for record in records:
        if not isinstance(record, dict):
            continue
        if record.get("artifact_stem") != artifact_stem:
            continue
        linked = record.get("linked_games") or []
        if not isinstance(linked, list) or not linked:
            warnings.append(f"no linked_games for artifact_stem={artifact_stem}")
            return None, warnings
        game = linked[0]
        if not isinstance(game, dict):
            warnings.append("linked_games[0] is invalid")
            return None, warnings
        rel = str(game.get("game_path") or "").strip()
        if not rel:
            warnings.append("linked game_path is empty")
            return None, warnings
        path = repo / rel
        if not path.exists():
            warnings.append(f"game path not found: {path}")
            return None, warnings
        return path, warnings

    warnings.append(f"artifact_stem not found in registry: {artifact_stem}")
    return None, warnings


@dataclass(frozen=True)
class PostMortemInputs:
    artifact_stem: str
    spec_text: str
    meeting_text: str
    review_text: str
    game_source: str
    game_path: Optional[Path]
    warnings: list[str]


def collect_inputs(
    *,
    artifact_stem: str,
    repo: Optional[Path] = None,
    game_path: Optional[str] = None,
) -> PostMortemInputs:
    root = repo or repo_root()
    warnings: list[str] = []

    spec_file = spec_path(root, artifact_stem)
    meeting_file = meeting_log_path(root, artifact_stem)
    review_file = review_path(root, artifact_stem)

    spec_text, w1 = read_text_if_exists(spec_file, limit=MAX_SPEC_CHARS, missing_label="spec")
    warnings.extend(w1)

    meeting_text, w2 = format_meeting_log(meeting_file)
    warnings.extend(w2)

    review_text, w3 = read_text_if_exists(
        review_file, limit=MAX_REVIEW_CHARS, missing_label="review"
    )
    warnings.extend(w3)

    resolved_game, w4 = resolve_game_path(
        repo=root,
        artifact_stem=artifact_stem,
        explicit_game_path=game_path,
    )
    warnings.extend(w4)

    game_source = ""
    if resolved_game is not None:
        game_source = truncate_text(
            resolved_game.read_text(encoding="utf-8"),
            MAX_GAME_SOURCE_CHARS,
        )

    return PostMortemInputs(
        artifact_stem=artifact_stem,
        spec_text=spec_text,
        meeting_text=meeting_text,
        review_text=review_text,
        game_source=game_source,
        game_path=resolved_game,
        warnings=warnings,
    )


def build_evolution_prompt(
    *,
    role: str,
    profile: dict[str, str],
    current_lessons: list[str],
    inputs: PostMortemInputs,
) -> str:
    focus = ROLE_FOCUS.get(role, profile.get("primary_focus", ""))
    game_label = str(inputs.game_path) if inputs.game_path else "(未検出)"
    has_review = bool(inputs.review_text.strip())
    primary_source = (
        "【最重要: レビュー指摘・修正】を一次ソースにする。"
        if has_review
        else "レビューが無いので、仕様と会議の『約束』と実装結果のギャップを一次ソースにする。"
    )
    return (
        f"あなたはコタツ・ソフトのAI社員「{profile['name']}（{profile['title']}）」です。\n"
        f"自分の役割（{focus}）だけで防げる／改善できる失敗に限定して教訓を更新してください。\n\n"
        "【目的】\n"
        f"教訓は常にちょうど{LESSON_ITEM_COUNT}個。各項目は1〜2文。スロット数は増やさない。\n"
        "旧教訓を言い換えるだけは禁止。今回の失敗から得た運用ルールへ意味のある差分で進化させる。\n\n"
        "【抽象化手順】\n"
        "1. 一次ソースから具体的な指摘・原因・修正を拾う\n"
        "2. 自分の役割に関係する失敗だけ残す（役割外は無理に取り込まない）\n"
        "3. 固有名詞・スコア式・作品固有ルールは捨て、次の別テーマでも使える再発防止原則へ抽象化する\n"
        "4. 旧教訓3個の枠を保ったまま、各枠の中身を進化させる（新規枠を増やさない。4個目は作らない）\n\n"
        f"【入力の優先順位】\n{primary_source}\n"
        "仕様書と会議ログは『約束とのズレ』確認用。完成コードは補助材料。\n\n"
        "【出力形式】\n"
        "次の3行だけを出力する。前置き・見出し・引用符・JSONは禁止。\n"
        "1. （更新後の教訓1）\n"
        "2. （更新後の教訓2）\n"
        "3. （更新後の教訓3）\n\n"
        f"【現在の教訓】\n{format_lesson_items(current_lessons)}\n\n"
        f"【役割の評価軸（アンカーにしない。今回の失敗と矛盾したら更新する）】\n"
        f"{profile.get('primary_focus') or focus}\n\n"
        f"【最重要: レビュー指摘・修正】\n{inputs.review_text or '（なし）'}\n\n"
        f"【仕様書（ギャップ確認用）】\n{inputs.spec_text or '（なし）'}\n\n"
        f"【会議ログ（主張・約束）】\n{inputs.meeting_text or '（なし）'}\n\n"
        f"【完成ゲームソース（補助）: {game_label}】\n{inputs.game_source or '（なし）'}\n"
    )


def _clean_lesson_item(text: str) -> str:
    cleaned = (text or "").strip()
    cleaned = cleaned.strip("「」『』\"'")
    cleaned = re.sub(r"^[-*・]+\s*", "", cleaned)
    cleaned = re.sub(r"^(?:教訓|更新後|Lesson|lesson)\s*[:：]\s*", "", cleaned)
    cleaned = truncate_text(cleaned, MAX_LESSON_ITEM_CHARS)
    return cleaned


def normalize_lesson_items(text: str) -> list[str]:
    raw = (text or "").strip()
    if not raw:
        raise ValueError("LLM returned empty lesson items")

    # Prefer fenced/JSON array if the model ignores format instructions.
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            items = [_clean_lesson_item(str(item)) for item in parsed if str(item).strip()]
            if len(items) >= LESSON_ITEM_COUNT:
                return items[:LESSON_ITEM_COUNT]
    except json.JSONDecodeError:
        pass

    numbered = re.findall(
        r"(?:^|\n)\s*(?:[-*・]|\d+[.)、．:])\s*(.+?)(?=(?:\n\s*(?:[-*・]|\d+[.)、．:]))|\Z)",
        raw,
        flags=re.DOTALL,
    )
    items = [_clean_lesson_item(item) for item in numbered if _clean_lesson_item(item)]
    if len(items) >= LESSON_ITEM_COUNT:
        return items[:LESSON_ITEM_COUNT]

    # Fallback: non-empty lines
    line_items = [_clean_lesson_item(line) for line in raw.splitlines() if _clean_lesson_item(line)]
    if len(line_items) >= LESSON_ITEM_COUNT:
        return line_items[:LESSON_ITEM_COUNT]

    raise ValueError(
        f"Expected {LESSON_ITEM_COUNT} lesson items, got {len(items) or len(line_items)}: {raw[:200]}"
    )


# Backward-compatible alias used by older tests/call sites.
def normalize_lesson(text: str) -> str:
    items = normalize_lesson_items(text if "\n" in text or re.search(r"^\s*1[.)]", text) else f"1. {text}")
    return items[0]


def evolve_lesson_with_llm(
    *,
    client: Any,
    model: str,
    temperature: float,
    prompt: str,
    request_name: str,
) -> list[str]:
    response = client.models.generate_content(
        model=model,
        contents=prompt,
        config=types.GenerateContentConfig(
            temperature=temperature,
            response_mime_type="text/plain",
        ),
    )
    text = BaseAgent.extract_text_from_response(response)
    return normalize_lesson_items(text)


@dataclass
class LessonUpdate:
    role: str
    name: str
    before: list[str]
    after: list[str]
    path: Path


def run_post_mortem(
    *,
    artifact_stem: str,
    api_key: Optional[str] = None,
    agents_root: Optional[Path] = None,
    repo: Optional[Path] = None,
    game_path: Optional[str] = None,
    model_override: Optional[str] = None,
    dry_run: bool = False,
    llm_callable: Optional[Callable[..., str]] = None,
) -> tuple[list[LessonUpdate], list[str]]:
    root = repo or repo_root()
    agents = agents_root or default_agents_root()
    inputs = collect_inputs(
        artifact_stem=artifact_stem,
        repo=root,
        game_path=game_path,
    )

    key = (api_key or os.getenv("GEMINI_API_KEY") or "").strip()
    client = None
    if llm_callable is None:
        if not key:
            raise RuntimeError("GEMINI_API_KEY is required unless llm_callable is provided")
        client = genai.Client(api_key=key)

    updates: list[LessonUpdate] = []
    for role in AGENT_ROLES:
        profile = load_agent_profile(agents, role)
        lessons = load_lessons(agents, role)
        before = coerce_lesson_items(lessons)
        prompt = build_evolution_prompt(
            role=role,
            profile=profile,
            current_lessons=before,
            inputs=inputs,
        )

        model = model_override or profile["model"] or DEFAULT_MODEL
        try:
            # Slightly warmer than meeting defaults so lessons don't stick to paraphrases.
            temperature = max(float(profile.get("temperature") or 0.3), 0.55)
        except (TypeError, ValueError):
            temperature = 0.55

        if llm_callable is not None:
            after = normalize_lesson_items(
                llm_callable(role=role, prompt=prompt, model=model, temperature=temperature)
            )
        else:
            assert client is not None
            after = evolve_lesson_with_llm(
                client=client,
                model=model,
                temperature=temperature,
                prompt=prompt,
                request_name=f"post_mortem:{role}",
            )

        path = lessons_learned_path(agents, role)
        if not dry_run:
            lessons["lesson_items"] = after
            lessons["updated_at"] = _utc_now_iso()
            lessons["last_artifact_stem"] = artifact_stem
            save_lessons(agents, role, lessons)

        updates.append(
            LessonUpdate(
                role=role,
                name=profile["name"],
                before=before,
                after=after,
                path=path,
            )
        )

    return updates, inputs.warnings
