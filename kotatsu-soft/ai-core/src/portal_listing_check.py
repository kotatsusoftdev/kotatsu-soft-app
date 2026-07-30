"""完成ゲームのポータル自動掲載連携チェック。

レジストリに紐づき実ファイルがあるゲームが、ポータルカード・
data-game-id / data-stat-id / sendPlayCount と一貫していることを検証する。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from html.parser import HTMLParser
import os
from pathlib import Path
import re
from typing import Iterable, Optional

from game_id_allocator import DIR_NAME_PATTERN
from spec_link_registry import load_registry

SEND_PLAY_COUNT_RE = re.compile(
    r"""sendPlayCount\s*\(\s*["'](?P<gid>[a-z][a-z0-9_]*)["']\s*\)""",
)
GAME_PATH_RE = re.compile(
    r"^game-projects/(?P<dir>\d{3}_[a-z0-9_]+)/src/index\.html$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ListingIssue:
    game_id: str
    kind: str
    message: str

    def format(self) -> str:
        return f"[{self.kind}] {self.game_id}: {self.message}"


@dataclass
class ListingReport:
    issues: list[ListingIssue] = field(default_factory=list)
    checked_game_ids: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.issues

    def extend(self, issues: Iterable[ListingIssue]) -> None:
        self.issues.extend(issues)


def _repo_root() -> Path:
    override = os.getenv("KOTATSU_REPO_ROOT", "").strip()
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[2]


def portal_index_path(repo_root: Optional[Path] = None) -> Path:
    return (repo_root or _repo_root()) / "game-projects" / "index.html"


class _PortalCardParser(HTMLParser):
    """ポータル上の game-card から game_id / play href / stat-id を収集する。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.cards: dict[str, dict[str, str]] = {}
        self._current: Optional[dict[str, str]] = None
        self._in_card = False
        self._card_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        attr_map = {k: (v or "") for k, v in attrs}
        classes = set(attr_map.get("class", "").split())

        if tag == "article" and "game-card" in classes:
            self._in_card = True
            self._card_depth = 1
            self._current = {}
            return

        if not self._in_card or self._current is None:
            return

        if tag == "article":
            self._card_depth += 1

        game_id = attr_map.get("data-game-id", "").strip()
        stat_id = attr_map.get("data-stat-id", "").strip()
        href = attr_map.get("href", "").strip()

        if game_id and "game_id" not in self._current:
            self._current["game_id"] = game_id
        if game_id and "play" in classes and href:
            self._current["play_href"] = href
            self._current["play_game_id"] = game_id
        if "game-card-link" in classes and href and "card_href" not in self._current:
            self._current["card_href"] = href
            if game_id:
                self._current["game_id"] = game_id
        if stat_id:
            self._current["stat_id"] = stat_id

    def handle_endtag(self, tag: str) -> None:
        if not self._in_card or tag != "article":
            return
        self._card_depth -= 1
        if self._card_depth > 0:
            return
        self._in_card = False
        if self._current and self._current.get("game_id"):
            gid = self._current["game_id"]
            self.cards[gid] = dict(self._current)
        self._current = None


def parse_portal_cards(portal_html: str) -> dict[str, dict[str, str]]:
    parser = _PortalCardParser()
    parser.feed(portal_html)
    parser.close()
    return parser.cards


def iter_completed_linked_games(
    *,
    repo_root: Optional[Path] = None,
    registry: Optional[dict] = None,
) -> list[dict[str, str]]:
    """レジストリ上リンク済みで、game_path の実ファイルが存在するゲームを返す。"""
    root = repo_root or _repo_root()
    payload = registry if registry is not None else load_registry()
    completed: list[dict[str, str]] = []
    seen: set[str] = set()

    for record in payload.get("records") or []:
        for link in record.get("linked_games") or []:
            game_id = str(link.get("game_id") or "").strip()
            game_path = str(link.get("game_path") or "").strip().replace("\\", "/")
            game_title = str(link.get("game_title") or "").strip()
            if not game_id or not game_path or game_id in seen:
                continue
            abs_path = root / game_path
            if not abs_path.is_file():
                continue
            seen.add(game_id)
            completed.append(
                {
                    "game_id": game_id,
                    "game_path": game_path,
                    "game_title": game_title,
                    "spec_file": str(record.get("spec_file") or ""),
                }
            )
    return completed


def _normalize_play_href(href: str) -> str:
    href = href.strip().replace("\\", "/")
    if href.startswith("./"):
        href = href[2:]
    return href


def _expected_play_href(game_path: str) -> str:
    # game-projects/001_x/src/index.html -> 001_x/src/index.html
    prefix = "game-projects/"
    normalized = game_path.replace("\\", "/")
    if normalized.startswith(prefix):
        return normalized[len(prefix) :]
    return normalized


def extract_send_play_count_ids(game_html: str) -> set[str]:
    return {m.group("gid") for m in SEND_PLAY_COUNT_RE.finditer(game_html)}


def check_portal_listing(
    *,
    repo_root: Optional[Path] = None,
    registry: Optional[dict] = None,
    portal_html: Optional[str] = None,
) -> ListingReport:
    root = repo_root or _repo_root()
    report = ListingReport()

    portal_path = portal_index_path(root)
    if portal_html is None:
        if not portal_path.is_file():
            report.issues.append(
                ListingIssue(
                    game_id="*",
                    kind="portal",
                    message=f"ポータルが見つかりません: {portal_path}",
                )
            )
            return report
        portal_html = portal_path.read_text(encoding="utf-8")

    cards = parse_portal_cards(portal_html)
    completed = iter_completed_linked_games(repo_root=root, registry=registry)
    report.checked_game_ids = [item["game_id"] for item in completed]

    for item in completed:
        game_id = item["game_id"]
        game_path = item["game_path"]
        issues: list[ListingIssue] = []

        path_match = GAME_PATH_RE.fullmatch(game_path)
        if not path_match:
            issues.append(
                ListingIssue(
                    game_id=game_id,
                    kind="path",
                    message=f"game_path が規約外です: {game_path}",
                )
            )
        else:
            dir_name = path_match.group("dir")
            dir_match = DIR_NAME_PATTERN.fullmatch(dir_name)
            if not dir_match or dir_match.group(2) != game_id:
                issues.append(
                    ListingIssue(
                        game_id=game_id,
                        kind="path",
                        message=f"ディレクトリ名と game_id が一致しません: {dir_name}",
                    )
                )

        card = cards.get(game_id)
        if card is None:
            issues.append(
                ListingIssue(
                    game_id=game_id,
                    kind="listing",
                    message="ポータルに data-game-id 付きカードがありません（自動掲載未反映）",
                )
            )
        else:
            expected_href = _expected_play_href(game_path)
            play_href = _normalize_play_href(card.get("play_href") or card.get("card_href") or "")
            if not play_href:
                issues.append(
                    ListingIssue(
                        game_id=game_id,
                        kind="listing",
                        message="プレイリンク (href) がありません",
                    )
                )
            elif play_href != expected_href:
                issues.append(
                    ListingIssue(
                        game_id=game_id,
                        kind="listing",
                        message=f"プレイリンクが不一致です: got={play_href} expected={expected_href}",
                    )
                )

            stat_id = card.get("stat_id", "").strip()
            if stat_id and stat_id != game_id:
                issues.append(
                    ListingIssue(
                        game_id=game_id,
                        kind="stats",
                        message=f"data-stat-id ({stat_id}) が game_id と一致しません",
                    )
                )

        game_file = root / game_path
        game_source = game_file.read_text(encoding="utf-8")
        play_ids = extract_send_play_count_ids(game_source)
        if game_id not in play_ids:
            issues.append(
                ListingIssue(
                    game_id=game_id,
                    kind="stats",
                    message=f'sendPlayCount("{game_id}") がゲーム本体にありません',
                )
            )
        unexpected = play_ids - {game_id}
        for other in sorted(unexpected):
            issues.append(
                ListingIssue(
                    game_id=game_id,
                    kind="stats",
                    message=f'sendPlayCount("{other}") は別 ID です（短名別名は禁止）',
                )
            )

        report.extend(issues)

    return report
