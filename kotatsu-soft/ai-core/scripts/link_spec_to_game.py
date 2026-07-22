from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from spec_link_registry import (  # noqa: E402
    link_spec_to_game,
    load_registry,
    registry_path,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Link a generated spec file to a game-project game id.",
    )
    parser.add_argument(
        "--spec",
        required=True,
        help="Spec file name under shared/specs (e.g. spec_xxx.md)",
    )
    parser.add_argument(
        "--game-id",
        required=True,
        help="Game identifier used in portal data-game-id (e.g. mikan_buster)",
    )
    parser.add_argument(
        "--game-path",
        required=True,
        help="Workspace relative game path (e.g. game-projects/001_mikan_buster/src/index.html)",
    )
    parser.add_argument(
        "--game-title",
        default="",
        help="Optional game title for display metadata.",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    payload = load_registry()
    records = payload.get("records")
    if not isinstance(records, list):
        print("Invalid registry format.")
        return 2

    spec_names = {record.get("spec_file") for record in records if isinstance(record, dict)}
    if args.spec not in spec_names:
        print(f"Spec not found in registry: {args.spec}")
        print(f"Registry path: {registry_path()}")
        return 1

    updated = link_spec_to_game(
        spec_file=args.spec,
        game_id=args.game_id,
        game_path=args.game_path,
        game_title=args.game_title,
    )

    print("Linked successfully.")
    print(f"Registry: {registry_path()}")
    print(f"Spec: {updated.get('spec_file')}")
    print(f"Game: {args.game_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
