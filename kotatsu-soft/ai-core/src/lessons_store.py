from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

DEFAULT_SCHEMA_VERSION = 2
LESSON_ITEM_COUNT = 3


def lessons_learned_path(agents_root: Path, role: str) -> Path:
    return agents_root / role / "lessons_learned.yaml"


def load_yaml_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"YAML not found: {path}")
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Invalid YAML structure: {path}")
    return payload


def dump_yaml_file(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(
            payload,
            allow_unicode=True,
            sort_keys=False,
            default_flow_style=False,
        ),
        encoding="utf-8",
    )


def format_lesson_items(items: list[str]) -> str:
    return "\n".join(f"{idx}. {item}" for idx, item in enumerate(items, start=1))


def coerce_lesson_items(payload: dict[str, Any]) -> list[str]:
    raw_items = payload.get("lesson_items")
    items: list[str] = []
    if isinstance(raw_items, list):
        items = [str(item).strip() for item in raw_items if str(item).strip()]
    elif payload.get("lesson"):
        items = [str(payload.get("lesson")).strip()]

    while len(items) < LESSON_ITEM_COUNT:
        items.append("（未設定）")
    return items[:LESSON_ITEM_COUNT]


def active_lesson_items(items: list[str]) -> list[str]:
    """Drop placeholders so meeting prompts only see usable lessons."""
    active: list[str] = []
    for item in items:
        text = str(item).strip()
        if not text or text == "（未設定）":
            continue
        active.append(text)
    return active


def load_lessons(agents_root: Path, role: str) -> dict[str, Any]:
    path = lessons_learned_path(agents_root, role)
    if not path.exists():
        return {
            "schema_version": DEFAULT_SCHEMA_VERSION,
            "updated_at": None,
            "last_artifact_stem": None,
            "lesson_items": ["（未設定）"] * LESSON_ITEM_COUNT,
        }
    try:
        payload = load_yaml_file(path)
    except (OSError, ValueError, yaml.YAMLError):
        return {
            "schema_version": DEFAULT_SCHEMA_VERSION,
            "updated_at": None,
            "last_artifact_stem": None,
            "lesson_items": ["（未設定）"] * LESSON_ITEM_COUNT,
        }
    payload["schema_version"] = DEFAULT_SCHEMA_VERSION
    payload.setdefault("updated_at", None)
    payload.setdefault("last_artifact_stem", None)
    payload["lesson_items"] = coerce_lesson_items(payload)
    payload.pop("lesson", None)
    return payload


def save_lessons(agents_root: Path, role: str, payload: dict[str, Any]) -> Path:
    path = lessons_learned_path(agents_root, role)
    payload["schema_version"] = DEFAULT_SCHEMA_VERSION
    payload["lesson_items"] = coerce_lesson_items(payload)
    payload.pop("lesson", None)
    dump_yaml_file(path, payload)
    return path


def build_lessons_instruction(items: list[str]) -> str:
    active = active_lesson_items(items)
    if not active:
        return ""
    return (
        "【過去開発からの教訓】\n"
        "以下は過去プロジェクトの反省から得た、テーマ非依存の運用ルール（抽象化した原則）です。\n"
        "会議ではこれらを踏まえて提案・比較・削る判断をすること。\n"
        "教訓を番号付きで読み上げたり毎回同じ定型で復唱したりせず、会話の中で自然に反映する。\n"
        "作品固有の細部に縛られず、今回のテーマにも通用する判断として使うこと。\n"
        f"{format_lesson_items(active)}\n"
    )
