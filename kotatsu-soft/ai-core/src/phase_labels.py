from __future__ import annotations

PHASE_DISPLAY_JA = {
    "DIVERGENCE": "発散",
    "CONFLICT": "衝突",
    "FINAL": "収束",
}


def phase_display_ja(phase: str) -> str:
    return PHASE_DISPLAY_JA.get(phase, phase)
