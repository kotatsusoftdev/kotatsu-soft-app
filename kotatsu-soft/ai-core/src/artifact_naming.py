from __future__ import annotations

from datetime import datetime
from pathlib import Path
import re


def sanitize_artifact_slug(source: str) -> str:
    slug = "".join(
        c if c.isalnum() or c in ("_", "-") else "_"
        for c in (source or "").strip()
    )
    slug = re.sub(r"_+", "_", slug).strip("_")
    return slug or "untitled"


def build_artifact_stem(slug_source: str, timestamp: datetime | None = None) -> str:
    slug = sanitize_artifact_slug(slug_source)
    ts = (timestamp or datetime.now()).strftime("%Y%m%d_%H%M%S")
    return f"{slug}_{ts}"


def meeting_log_filename(artifact_stem: str) -> str:
    return f"meeting_{artifact_stem}.jsonl"


def spec_filename(artifact_stem: str) -> str:
    return f"spec_{artifact_stem}.md"


def meeting_log_path(repo_root: Path, artifact_stem: str) -> Path:
    return repo_root / "shared" / "meeting" / meeting_log_filename(artifact_stem)


def spec_path(repo_root: Path, artifact_stem: str) -> Path:
    return repo_root / "shared" / "specs" / spec_filename(artifact_stem)
