from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
from typing import Any, Optional

DEFAULT_SCHEMA_VERSION = 1


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def registry_path() -> Path:
    override = os.getenv("SPEC_LINK_REGISTRY_PATH", "").strip()
    if override:
        return Path(override)
    return _repo_root() / "shared" / "specs" / "spec_game_links.json"


def _empty_registry() -> dict[str, Any]:
    return {
        "schema_version": DEFAULT_SCHEMA_VERSION,
        "updated_at": _utc_now_iso(),
        "records": [],
    }


def load_registry() -> dict[str, Any]:
    path = registry_path()
    if not path.exists():
        return _empty_registry()

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _empty_registry()

    if not isinstance(payload, dict):
        return _empty_registry()

    records = payload.get("records")
    if not isinstance(records, list):
        payload["records"] = []

    payload.setdefault("schema_version", DEFAULT_SCHEMA_VERSION)
    payload.setdefault("updated_at", _utc_now_iso())
    return payload


def save_registry(payload: dict[str, Any]) -> Path:
    path = registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload["schema_version"] = DEFAULT_SCHEMA_VERSION
    payload["updated_at"] = _utc_now_iso()
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _find_record_index(records: list[dict[str, Any]], spec_file: str) -> Optional[int]:
    for idx, record in enumerate(records):
        if record.get("spec_file") == spec_file:
            return idx
    return None


def register_generated_spec(
    *,
    spec_file: Path,
    selected_plan: str,
    proposal_summary: str,
    theme: Optional[str] = None,
) -> dict[str, Any]:
    payload = load_registry()
    records = [record for record in payload.get("records", []) if isinstance(record, dict)]

    spec_file_name = spec_file.name
    created_at = _utc_now_iso()
    new_record: dict[str, Any] = {
        "spec_file": spec_file_name,
        "spec_path": f"shared/specs/{spec_file_name}",
        "selected_plan": selected_plan,
        "proposal_summary": proposal_summary,
        "theme": theme or "",
        "created_at": created_at,
        "linked_games": [],
    }

    existing_index = _find_record_index(records, spec_file_name)
    if existing_index is None:
        records.append(new_record)
    else:
        preserved_links = records[existing_index].get("linked_games")
        if isinstance(preserved_links, list):
            new_record["linked_games"] = preserved_links
        records[existing_index] = new_record

    payload["records"] = records
    save_registry(payload)
    return new_record


def link_spec_to_game(
    *,
    spec_file: str,
    game_id: str,
    game_path: str,
    game_title: Optional[str] = None,
) -> dict[str, Any]:
    payload = load_registry()
    records = [record for record in payload.get("records", []) if isinstance(record, dict)]

    record_index = _find_record_index(records, spec_file)
    if record_index is None:
        raise ValueError(f"spec record not found: {spec_file}")

    linked_games = records[record_index].get("linked_games")
    if not isinstance(linked_games, list):
        linked_games = []

    # Keep a single mapping per game_id and replace when re-linking.
    linked_games = [
        item
        for item in linked_games
        if not (isinstance(item, dict) and item.get("game_id") == game_id)
    ]

    linked_games.append(
        {
            "game_id": game_id,
            "game_path": game_path,
            "game_title": game_title or "",
            "linked_at": _utc_now_iso(),
        }
    )

    records[record_index]["linked_games"] = linked_games
    payload["records"] = records
    save_registry(payload)
    return records[record_index]


def get_latest_spec_for_game(game_id: str) -> Optional[dict[str, Any]]:
    payload = load_registry()
    records = [record for record in payload.get("records", []) if isinstance(record, dict)]

    matches: list[dict[str, Any]] = []
    for record in records:
        linked_games = record.get("linked_games")
        if not isinstance(linked_games, list):
            continue
        for game in linked_games:
            if isinstance(game, dict) and game.get("game_id") == game_id:
                matches.append(record)
                break

    if not matches:
        return None

    matches.sort(key=lambda record: str(record.get("created_at", "")), reverse=True)
    return matches[0]
