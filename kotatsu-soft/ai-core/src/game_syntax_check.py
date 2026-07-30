"""完成ゲーム HTML / インライン JS の構文チェック。"""

from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path
import re
import subprocess
import tempfile
from typing import Iterable, Optional

INLINE_SCRIPT_RE = re.compile(
    r"<script\b(?![^>]*\bsrc\s*=)[^>]*>(?P<body>.*?)</script>",
    re.IGNORECASE | re.DOTALL,
)
EXTERNAL_SCRIPT_RE = re.compile(
    r"""<script\b[^>]*\bsrc\s*=\s*["'](?P<src>[^"']+)["'][^>]*>\s*</script>""",
    re.IGNORECASE,
)
SCRIPT_OPEN_RE = re.compile(r"<script\b", re.IGNORECASE)
SCRIPT_CLOSE_RE = re.compile(r"</script\s*>", re.IGNORECASE)
STYLE_OPEN_RE = re.compile(r"<style\b", re.IGNORECASE)
STYLE_CLOSE_RE = re.compile(r"</style\s*>", re.IGNORECASE)


@dataclass(frozen=True)
class SyntaxIssue:
    path: str
    kind: str
    message: str
    line: Optional[int] = None

    def format(self) -> str:
        loc = f"{self.path}:{self.line}" if self.line is not None else self.path
        return f"[{self.kind}] {loc}: {self.message}"


@dataclass
class SyntaxReport:
    issues: list[SyntaxIssue] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.issues

    def extend(self, issues: Iterable[SyntaxIssue]) -> None:
        self.issues.extend(issues)


def _repo_root() -> Path:
    override = os.getenv("KOTATSU_REPO_ROOT", "").strip()
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[2]


def game_projects_root(repo_root: Optional[Path] = None) -> Path:
    return (repo_root or _repo_root()) / "game-projects"


def iter_completed_game_html(repo_root: Optional[Path] = None) -> list[Path]:
    root = game_projects_root(repo_root)
    if not root.exists():
        return []
    paths: list[Path] = []
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        if child.name in {"common", "assets"}:
            continue
        index = child / "src" / "index.html"
        if index.is_file():
            paths.append(index)
    return paths


def iter_shared_js(repo_root: Optional[Path] = None) -> list[Path]:
    common = game_projects_root(repo_root) / "common"
    if not common.exists():
        return []
    return sorted(p for p in common.rglob("*.js") if p.is_file())


def _line_number_at(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _count_tag_balance(
    text: str,
    *,
    open_re: re.Pattern[str],
    close_re: re.Pattern[str],
    tag: str,
    rel_path: str,
) -> list[SyntaxIssue]:
    opens = list(open_re.finditer(text))
    closes = list(close_re.finditer(text))
    if len(opens) == len(closes):
        return []
    return [
        SyntaxIssue(
            path=rel_path,
            kind="html",
            message=f"<{tag}> の開始 ({len(opens)}) と終了 ({len(closes)}) の数が一致しません",
            line=_line_number_at(text, opens[-1].start()) if opens else 1,
        )
    ]


def check_html_structure(text: str, *, rel_path: str) -> list[SyntaxIssue]:
    issues: list[SyntaxIssue] = []

    if not re.search(r"<!DOCTYPE\s+html\b", text, re.IGNORECASE):
        issues.append(
            SyntaxIssue(path=rel_path, kind="html", message="<!DOCTYPE html> がありません", line=1)
        )

    issues.extend(
        _count_tag_balance(
            text,
            open_re=SCRIPT_OPEN_RE,
            close_re=SCRIPT_CLOSE_RE,
            tag="script",
            rel_path=rel_path,
        )
    )
    issues.extend(
        _count_tag_balance(
            text,
            open_re=STYLE_OPEN_RE,
            close_re=STYLE_CLOSE_RE,
            tag="style",
            rel_path=rel_path,
        )
    )
    return issues


def extract_inline_scripts(html: str) -> list[tuple[int, str]]:
    scripts: list[tuple[int, str]] = []
    for match in INLINE_SCRIPT_RE.finditer(html):
        body = match.group("body")
        if body.strip():
            scripts.append((_line_number_at(html, match.start("body")), body))
    return scripts


def extract_external_script_srcs(html: str) -> list[str]:
    return [match.group("src").strip() for match in EXTERNAL_SCRIPT_RE.finditer(html)]


def check_js_syntax(source: str, *, path_label: str, line_offset: int = 1) -> list[SyntaxIssue]:
    """Node のパーサで JS 構文を検証する（node --check 相当）。"""
    with tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".js",
        encoding="utf-8",
        delete=False,
    ) as tmp:
        tmp.write(source)
        tmp_path = Path(tmp.name)

    try:
        completed = subprocess.run(
            ["node", "--check", str(tmp_path)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except FileNotFoundError:
        return [
            SyntaxIssue(
                path=path_label,
                kind="js",
                message="node コマンドが見つかりません（JS 構文チェックに Node.js が必要です）",
                line=line_offset,
            )
        ]
    finally:
        tmp_path.unlink(missing_ok=True)

    if completed.returncode == 0:
        return []

    detail = (completed.stderr or completed.stdout or "syntax error").strip()
    message = detail.splitlines()[-1] if detail else "JavaScript 構文エラー"
    return [
        SyntaxIssue(
            path=path_label,
            kind="js",
            message=message,
            line=line_offset,
        )
    ]


def check_js_file(path: Path, *, rel_path: str) -> list[SyntaxIssue]:
    source = path.read_text(encoding="utf-8")
    return check_js_syntax(source, path_label=rel_path, line_offset=1)


def check_html_file(path: Path, *, repo_root: Path) -> list[SyntaxIssue]:
    rel_path = _rel(path, repo_root)
    text = path.read_text(encoding="utf-8")
    issues = check_html_structure(text, rel_path=rel_path)

    for line, body in extract_inline_scripts(text):
        issues.extend(
            check_js_syntax(
                body,
                path_label=f"{rel_path} (inline script)",
                line_offset=line,
            )
        )

    for src in extract_external_script_srcs(text):
        if src.startswith(("http://", "https://", "//")):
            continue
        resolved = (path.parent / src).resolve()
        if not resolved.is_file():
            issues.append(
                SyntaxIssue(
                    path=rel_path,
                    kind="html",
                    message=f"外部スクリプトが見つかりません: {src}",
                )
            )
    return issues


def _rel(path: Path, repo_root: Path) -> str:
    try:
        return path.resolve().relative_to(repo_root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def check_completed_games(repo_root: Optional[Path] = None) -> SyntaxReport:
    root = repo_root or _repo_root()
    report = SyntaxReport()

    for html_path in iter_completed_game_html(root):
        report.extend(check_html_file(html_path, repo_root=root))

    portal = game_projects_root(root) / "index.html"
    if portal.is_file():
        report.extend(check_html_file(portal, repo_root=root))

    for js_path in iter_shared_js(root):
        report.extend(check_js_file(js_path, rel_path=_rel(js_path, root)))

    return report
