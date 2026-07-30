from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

DEFAULT_SCHEMA_VERSION = 1


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def grand_rules_path(root: Path | None = None) -> Path:
    return (root or repo_root()) / "shared" / "meeting" / "grand_rules.yaml"


def load_yaml_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"YAML not found: {path}")
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Invalid YAML structure: {path}")
    return payload


def coerce_rules(payload: dict[str, Any]) -> list[dict[str, str]]:
    raw_rules = payload.get("rules")
    rules: list[dict[str, str]] = []
    if not isinstance(raw_rules, list):
        return rules
    for item in raw_rules:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        body = str(item.get("body") or "").strip()
        rule_id = str(item.get("id") or "").strip()
        if not title and not body:
            continue
        rules.append(
            {
                "id": rule_id,
                "title": title,
                "body": body,
            }
        )
    return rules


def load_grand_rules(root: Path | None = None) -> dict[str, Any]:
    path = grand_rules_path(root)
    if not path.exists():
        return {
            "schema_version": DEFAULT_SCHEMA_VERSION,
            "updated_at": None,
            "description": "",
            "rules": [],
        }
    try:
        payload = load_yaml_file(path)
    except (OSError, ValueError, yaml.YAMLError):
        return {
            "schema_version": DEFAULT_SCHEMA_VERSION,
            "updated_at": None,
            "description": "",
            "rules": [],
        }
    return {
        "schema_version": int(payload.get("schema_version") or DEFAULT_SCHEMA_VERSION),
        "updated_at": payload.get("updated_at"),
        "description": str(payload.get("description") or ""),
        "rules": coerce_rules(payload),
    }


def format_rule_items(rules: list[dict[str, str]]) -> str:
    lines: list[str] = []
    for idx, rule in enumerate(rules, start=1):
        title = rule.get("title") or ""
        body = rule.get("body") or ""
        if title and body:
            lines.append(f"{idx}. {title}: {body}")
        elif title:
            lines.append(f"{idx}. {title}")
        else:
            lines.append(f"{idx}. {body}")
    return "\n".join(lines)


def build_grand_rules_instruction(root: Path | None = None) -> str:
    payload = load_grand_rules(root)
    rules = payload.get("rules") or []
    if not rules:
        return ""
    return (
        "【企画会議グランドルール】\n"
        "以下は企画会議の全エージェントが共有する制約です。役割固有の評価軸より優先して守ること。\n"
        "ルールを番号付きで読み上げたり毎回同じ定型で復唱したりせず、会話の中で自然に反映する。\n"
        f"{format_rule_items(rules)}\n"
    )
