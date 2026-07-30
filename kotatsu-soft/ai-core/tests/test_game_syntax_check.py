from pathlib import Path

from game_syntax_check import (
    check_completed_games,
    check_html_file,
    check_js_syntax,
    extract_inline_scripts,
)


def test_extract_inline_scripts_skips_external() -> None:
    html = """
    <script src="./a.js"></script>
    <script>
      const x = 1;
    </script>
    """
    scripts = extract_inline_scripts(html)
    assert len(scripts) == 1
    assert "const x = 1" in scripts[0][1]


def test_check_js_syntax_detects_error() -> None:
    issues = check_js_syntax("const x = ;", path_label="broken.js", line_offset=3)
    assert issues
    assert issues[0].kind == "js"
    assert issues[0].line == 3


def test_check_js_syntax_accepts_valid() -> None:
    issues = check_js_syntax("const x = 1;\nfunction f() { return x; }\n", path_label="ok.js")
    assert issues == []


def test_check_html_file_detects_unbalanced_script(tmp_path: Path) -> None:
    html_path = tmp_path / "index.html"
    html_path.write_text(
        "<!DOCTYPE html><html><body><script>const a = 1;</body></html>",
        encoding="utf-8",
    )
    issues = check_html_file(html_path, repo_root=tmp_path)
    assert any(i.kind == "html" and "script" in i.message for i in issues)


def test_check_html_file_detects_js_error(tmp_path: Path) -> None:
    html_path = tmp_path / "index.html"
    html_path.write_text(
        "<!DOCTYPE html><html><body><script>const a = ;</script></body></html>",
        encoding="utf-8",
    )
    issues = check_html_file(html_path, repo_root=tmp_path)
    assert any(i.kind == "js" for i in issues)


def test_check_completed_games_against_repo() -> None:
    """実リポジトリの完成ゲームが構文 OK であること。"""
    report = check_completed_games()
    assert report.ok, "\n".join(i.format() for i in report.issues)
