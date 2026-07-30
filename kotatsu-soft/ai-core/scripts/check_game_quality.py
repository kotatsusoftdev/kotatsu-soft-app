#!/usr/bin/env python3
"""完成ゲームの品質ゲート（構文 + ポータル掲載連携）。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from game_syntax_check import check_completed_games  # noqa: E402
from portal_listing_check import check_portal_listing  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Check completed game HTML/JS syntax and portal listing.")
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=None,
        help="kotatsu-soft リポジトリルート（省略時は ai-core から推定）",
    )
    parser.add_argument(
        "--skip-syntax",
        action="store_true",
        help="HTML/JS 構文チェックをスキップ",
    )
    parser.add_argument(
        "--skip-listing",
        action="store_true",
        help="ポータル掲載連携チェックをスキップ",
    )
    args = parser.parse_args()

    repo_root = args.repo_root.resolve() if args.repo_root else None
    exit_code = 0

    if not args.skip_syntax:
        print("[quality] HTML/JS syntax check")
        syntax = check_completed_games(repo_root)
        if syntax.ok:
            print("- result: OK")
        else:
            exit_code = 2
            print("- result: NG")
            for issue in syntax.issues:
                print(f"  * {issue.format()}")

    if not args.skip_listing:
        print("[quality] portal listing integration check")
        listing = check_portal_listing(repo_root=repo_root)
        checked = ", ".join(listing.checked_game_ids) or "(none)"
        print(f"- completed linked games: {checked}")
        if listing.ok:
            print("- result: OK")
        else:
            exit_code = 2
            print("- result: NG")
            for issue in listing.issues:
                print(f"  * {issue.format()}")

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
