from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys

ROOT_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT_DIR / "src"


def _reexec_with_venv_if_needed() -> None:
    """Bare `python` often misses project deps; prefer ai-core/venv when present."""
    if os.name == "nt":
        venv_python = ROOT_DIR / "venv" / "Scripts" / "python.exe"
    else:
        venv_python = ROOT_DIR / "venv" / "bin" / "python"
    if not venv_python.exists():
        return
    try:
        if Path(sys.executable).resolve() == venv_python.resolve():
            return
    except OSError:
        return
    os.execv(str(venv_python), [str(venv_python), *sys.argv])


_reexec_with_venv_if_needed()

if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))


def _load_dotenv(path: Path) -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        if not path.exists():
            return
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value
        return
    load_dotenv(path)


from post_mortem import (  # noqa: E402
    artifact_stem_from_spec,
    format_lesson_items,
    run_post_mortem,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run an automatic post-mortem and evolve each agent's lessons_learned.yaml."
        ),
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--artifact-stem",
        help="Artifact stem shared by spec/meeting/review (without prefixes).",
    )
    group.add_argument(
        "--spec",
        help="Spec file name under shared/specs (e.g. spec_xxx.md).",
    )
    parser.add_argument(
        "--game-path",
        default="",
        help="Optional workspace-relative game source path override.",
    )
    parser.add_argument(
        "--agents-root",
        default="",
        help="Optional override for ai-core/src/agents (tests / custom layouts).",
    )
    parser.add_argument(
        "--model",
        default="",
        help="Optional model override for all agents.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show updates without writing YAML files.",
    )
    return parser


def main() -> int:
    _load_dotenv(ROOT_DIR / ".env")
    parser = build_parser()
    args = parser.parse_args()

    if args.artifact_stem:
        stem = str(args.artifact_stem).strip()
    else:
        stem = artifact_stem_from_spec(str(args.spec).strip())

    agents_root = Path(args.agents_root).resolve() if args.agents_root else None
    game_path = str(args.game_path).strip() or None
    model = str(args.model).strip() or None

    try:
        updates, warnings = run_post_mortem(
            artifact_stem=stem,
            agents_root=agents_root,
            game_path=game_path,
            model_override=model,
            dry_run=bool(args.dry_run),
        )
    except Exception as exc:
        print(f"[post_mortem] failed: {exc}")
        return 1

    print(f"[post_mortem] artifact_stem={stem}")
    if args.dry_run:
        print("[post_mortem] dry-run: files were not written")
    for warning in warnings:
        print(f"[warn] {warning}")

    for item in updates:
        print(f"\n=== {item.role} / {item.name} ===")
        print(f"path: {item.path}")
        print("speech:")
        print(item.speech or "(empty)")
        print("before:")
        print(format_lesson_items(item.before))
        print("after:")
        print(format_lesson_items(item.after))

    print("\n[post_mortem] done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
