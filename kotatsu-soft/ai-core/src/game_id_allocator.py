from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
from typing import Iterable, Optional

from spec_link_registry import load_registry

RESERVED_GAME_IDS = frozenset({"pv"})
GAME_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]{1,47}$")
DIR_NAME_PATTERN = re.compile(r"^(\d{3})_(.+)$")
GAME_ID_LINE_PATTERN = re.compile(
    r"(?im)^\s*(?:[-*]\s*)?(?:\*\*)?game_id(?:\*\*)?\s*[:=]\s*`?([a-zA-Z][a-zA-Z0-9_]{0,63})`?\s*$"
)
TITLE_PATTERN = re.compile(
    r"(?im)^\s*(?:#{1,3}\s*)?(?:\*\*)?【ゲームタイトル】(?:\*\*)?\s*[:：]?\s*(.+?)\s*$"
)
GAME_PATH_DIR_PATTERN = re.compile(r"game-projects[/\\](\d{3})_[^/\\]+", re.IGNORECASE)


@dataclass(frozen=True)
class GameIdentity:
    dir_number: int
    game_id: str
    game_path: str
    game_dir: str
    game_title: str = ""

    @property
    def dir_name(self) -> str:
        return f"{self.dir_number:03d}_{self.game_id}"


def _repo_root() -> Path:
    override = os.getenv("KOTATSU_REPO_ROOT", "").strip()
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[2]


def game_projects_root(repo_root: Optional[Path] = None) -> Path:
    return (repo_root or _repo_root()) / "game-projects"


def normalize_game_id(candidate: str) -> Optional[str]:
    raw = (candidate or "").strip().lower().replace("-", "_")
    raw = re.sub(r"_+", "_", raw).strip("_")
    if not raw or raw in RESERVED_GAME_IDS:
        return None
    if not GAME_ID_PATTERN.fullmatch(raw):
        return None
    return raw


def extract_game_id_from_spec(spec_text: str) -> Optional[str]:
    match = GAME_ID_LINE_PATTERN.search(spec_text or "")
    if not match:
        return None
    return normalize_game_id(match.group(1))


def extract_game_title_from_spec(spec_text: str) -> str:
    match = TITLE_PATTERN.search(spec_text or "")
    if not match:
        return ""
    title = match.group(1).strip()
    title = re.sub(r"^[*`]+|[*`]+$", "", title).strip()
    return title


def ensure_game_id_header(spec_text: str, game_id: str) -> str:
    body = GAME_ID_LINE_PATTERN.sub("", spec_text or "")
    body = re.sub(r"\n{3,}", "\n\n", body).lstrip()
    return f"- game_id: {game_id}\n\n{body}"


def _dir_numbers_from_filesystem(projects_root: Path) -> list[int]:
    if not projects_root.exists():
        return []
    numbers: list[int] = []
    for entry in projects_root.iterdir():
        if not entry.is_dir():
            continue
        match = DIR_NAME_PATTERN.match(entry.name)
        if match:
            numbers.append(int(match.group(1)))
    return numbers


def _dir_numbers_from_registry_paths(game_paths: Iterable[str]) -> list[int]:
    numbers: list[int] = []
    for path in game_paths:
        match = GAME_PATH_DIR_PATTERN.search(path or "")
        if match:
            numbers.append(int(match.group(1)))
    return numbers


def _collect_used_game_ids_and_paths(
    *,
    projects_root: Path,
) -> tuple[set[str], list[str]]:
    used_ids: set[str] = set()
    game_paths: list[str] = []

    if projects_root.exists():
        for entry in projects_root.iterdir():
            if not entry.is_dir():
                continue
            match = DIR_NAME_PATTERN.match(entry.name)
            if match:
                used_ids.add(match.group(2))

    payload = load_registry()
    for record in payload.get("records", []):
        if not isinstance(record, dict):
            continue
        linked = record.get("linked_games")
        if not isinstance(linked, list):
            continue
        for game in linked:
            if not isinstance(game, dict):
                continue
            game_id = normalize_game_id(str(game.get("game_id") or ""))
            if game_id:
                used_ids.add(game_id)
            path = str(game.get("game_path") or "").strip()
            if path:
                game_paths.append(path)

    return used_ids, game_paths


def next_dir_number(
    *,
    repo_root: Optional[Path] = None,
    projects_root: Optional[Path] = None,
) -> int:
    root = projects_root or game_projects_root(repo_root)
    _, registry_paths = _collect_used_game_ids_and_paths(projects_root=root)
    numbers = _dir_numbers_from_filesystem(root) + _dir_numbers_from_registry_paths(
        registry_paths
    )
    if not numbers:
        return 1
    return max(numbers) + 1


def resolve_unique_game_id(preferred: Optional[str], used_ids: set[str], dir_number: int) -> str:
    base = normalize_game_id(preferred or "") or f"game_{dir_number:03d}"
    if base not in used_ids and base not in RESERVED_GAME_IDS:
        return base

    suffix = 2
    while True:
        candidate = f"{base}_{suffix}"
        normalized = normalize_game_id(candidate)
        if normalized and normalized not in used_ids and normalized not in RESERVED_GAME_IDS:
            return normalized
        suffix += 1
        if suffix > 999:
            raise RuntimeError(f"unable to allocate unique game_id from base={base}")


def allocate_game_identity(
    *,
    preferred_game_id: Optional[str] = None,
    game_title: str = "",
    repo_root: Optional[Path] = None,
) -> GameIdentity:
    projects_root = game_projects_root(repo_root)
    used_ids, _ = _collect_used_game_ids_and_paths(projects_root=projects_root)
    dir_number = next_dir_number(repo_root=repo_root, projects_root=projects_root)
    game_id = resolve_unique_game_id(preferred_game_id, used_ids, dir_number)
    dir_name = f"{dir_number:03d}_{game_id}"
    game_path = f"game-projects/{dir_name}/src/index.html"
    return GameIdentity(
        dir_number=dir_number,
        game_id=game_id,
        game_path=game_path,
        game_dir=dir_name,
        game_title=game_title or "",
    )
