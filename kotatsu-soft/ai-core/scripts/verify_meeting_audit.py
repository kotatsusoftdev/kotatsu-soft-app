from __future__ import annotations

import json
from pathlib import Path
from typing import Any

AUDIT_PATH = Path(__file__).resolve().parents[2] / "shared" / "logs" / "meeting_turn_audit.jsonl"


def expected_phase(turn: int) -> str:
    if turn <= 4:
        return "DIVERGENCE"
    if turn <= 7:
        return "CONFLICT"
    return "FINAL"


def load_latest_record() -> dict[str, Any]:
    if not AUDIT_PATH.exists():
        raise FileNotFoundError(f"Audit log not found: {AUDIT_PATH}")

    lines = [line.strip() for line in AUDIT_PATH.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not lines:
        raise ValueError(f"Audit log is empty: {AUDIT_PATH}")

    return json.loads(lines[-1])


def validate_record(record: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    trace = record.get("trace") or []

    if not trace:
        issues.append("trace is empty")
        return issues

    for item in trace:
        turn = int(item.get("turn", 0))
        exp = expected_phase(turn)
        got = item.get("pm_phase_final")
        if got != exp:
            issues.append(f"turn {turn}: expected phase={exp}, got={got}")

        initial_action = item.get("next_action_initial")
        final_action = item.get("next_action_final")
        if turn <= 5 and final_action == "FINISH_FOR_PRESIDENT":
            issues.append(f"turn {turn}: early finish was not blocked")
        if turn <= 5 and initial_action == "FINISH_FOR_PRESIDENT":
            guards = item.get("guardrails") or []
            if "block_finish_before_turn6" not in guards:
                issues.append(f"turn {turn}: missing guardrail tag block_finish_before_turn6")

    return issues


def main() -> int:
    try:
        record = load_latest_record()
    except Exception as exc:
        print(f"[verify] failed to load audit record: {exc}")
        return 1

    trace = record.get("trace") or []
    issues = validate_record(record)

    print("[verify] latest meeting audit summary")
    print(f"- timestamp: {record.get('timestamp')}")
    print(f"- theme: {record.get('theme')}")
    print(f"- turn_count: {len(trace)}")
    print(f"- has_final_decision: {record.get('has_final_decision')}")

    if issues:
        print("- result: NG")
        for issue in issues:
            print(f"  * {issue}")
        return 2

    print("- result: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
