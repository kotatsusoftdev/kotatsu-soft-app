from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import re
from typing import Any, Optional

from artifact_naming import meeting_log_path
from phase_labels import phase_display_ja


ROLE_META: dict[str, dict[str, str]] = {
    "president": {
        "display_name": "のぶ太社長",
        "avatar": "nobita",
        "side": "right",
    },
    "pm": {
        "display_name": "すずかちゃん",
        "avatar": "shizuka",
        "side": "left",
    },
    "dev": {
        "display_name": "スゴ杉くん",
        "avatar": "suge",
        "side": "left",
    },
    "marketing": {
        "display_name": "ヂャイアン",
        "avatar": "gian",
        "side": "left",
    },
    "system": {
        "display_name": "SYSTEM",
        "avatar": "system",
        "side": "left",
    },
}

AGENT_NAME_TO_ROLE = {
    "すずかちゃん(PM)": "pm",
    "スゴ杉くん(エンジニア)": "dev",
    "ヂャイアン(マーケ)": "marketing",
}


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def normalize_plain_text(text: str) -> str:
    normalized = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    normalized = re.sub(r"\*\*(.+?)\*\*", r"\1", normalized)
    normalized = re.sub(r"__(.+?)__", r"\1", normalized)
    normalized = re.sub(r"`([^`]+)`", r"\1", normalized)
    normalized = re.sub(r"^#{1,6}\s+", "", normalized, flags=re.MULTILINE)
    normalized = re.sub(r"^\s*[-*]\s+", "・", normalized, flags=re.MULTILINE)
    normalized = re.sub(r"\n{3,}", "\n\n", normalized)
    return normalized.strip()


class MeetingChatLogWriter:
    def __init__(self, *, artifact_stem: str, repo_root_path: Path | None = None):
        self.artifact_stem = artifact_stem
        root = repo_root_path or repo_root()
        self.path = meeting_log_path(root, artifact_stem)
        self._seq = 0
        self._last_timestamp: Optional[datetime] = None
        self._last_logged_phase: Optional[str] = None
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.path.touch()

    @classmethod
    def open_existing(cls, path: Path) -> MeetingChatLogWriter:
        filename = path.name
        if not filename.startswith("meeting_") or not filename.endswith(".jsonl"):
            raise ValueError(f"invalid meeting log filename: {filename}")
        artifact_stem = filename[len("meeting_") : -len(".jsonl")]
        writer = cls(artifact_stem=artifact_stem, repo_root_path=path.parents[2])
        writer.path = path
        writer._seq = writer._count_existing_messages()
        writer._last_timestamp = writer._read_last_timestamp()
        writer._last_logged_phase = writer._read_last_phase()
        return writer

    def _count_existing_messages(self) -> int:
        count = 0
        if not self.path.exists():
            return 0
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                count += 1
        return count

    def _read_last_timestamp(self) -> Optional[datetime]:
        if not self.path.exists():
            return None
        last_line = ""
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                last_line = line
        if not last_line:
            return None
        payload = json.loads(last_line)
        raw = payload.get("timestamp")
        if not isinstance(raw, str):
            return None
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))

    def _read_last_phase(self) -> Optional[str]:
        if not self.path.exists():
            return None
        last_phase: Optional[str] = None
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            payload = json.loads(line)
            phase = payload.get("phase")
            if isinstance(phase, str):
                last_phase = phase
        return last_phase

    def _next_timestamp(self) -> str:
        now = datetime.now(timezone.utc)
        if self._last_timestamp is not None and now <= self._last_timestamp:
            now = self._last_timestamp.replace(microsecond=self._last_timestamp.microsecond + 1)
        self._last_timestamp = now
        return now.isoformat().replace("+00:00", "Z")

    def _next_id(self) -> str:
        message_id = f"msg_{self._seq:03d}"
        self._seq += 1
        return message_id

    def append(
        self,
        *,
        role: str,
        message: str,
        msg_type: str = "text",
        phase: str,
        turn: int,
        reply_to: Optional[str] = None,
        display_name: Optional[str] = None,
        avatar: Optional[str] = None,
        side: Optional[str] = None,
    ) -> str:
        meta = ROLE_META.get(role, ROLE_META["system"])
        record: dict[str, Any] = {
            "timestamp": self._next_timestamp(),
            "role": role,
            "display_name": display_name or meta["display_name"],
            "avatar": avatar or meta["avatar"],
            "side": side or meta["side"],
            "message": normalize_plain_text(message),
            "type": msg_type,
            "phase": phase,
            "turn": turn,
            "id": self._next_id(),
        }
        if reply_to:
            record["reply_to"] = reply_to
        with self.path.open("a", encoding="utf-8") as fp:
            fp.write(json.dumps(record, ensure_ascii=False) + "\n")
        return record["id"]

    def log_meeting_start(self, theme: str) -> None:
        self.append(
            role="system",
            message="会議を開始します",
            msg_type="system",
            phase="DIVERGENCE",
            turn=0,
        )
        self.append(
            role="system",
            message=f"テーマ：{normalize_plain_text(theme)}",
            msg_type="system",
            phase="DIVERGENCE",
            turn=0,
        )
        self.log_phase_if_changed("DIVERGENCE", turn=0)

    def log_phase_if_changed(self, phase: str, *, turn: int) -> None:
        if self._last_logged_phase == phase:
            return
        self._last_logged_phase = phase
        self.append(
            role="system",
            message=f"フェーズ：{phase_display_ja(phase)}",
            msg_type="system",
            phase=phase,
            turn=turn,
        )

    def log_agent_message(
        self,
        *,
        agent_name: str,
        message: str,
        phase: str,
        turn: int,
        msg_type: str = "text",
        reply_to: Optional[str] = None,
    ) -> str:
        role = AGENT_NAME_TO_ROLE.get(agent_name)
        if role is None:
            lowered = agent_name.lower()
            if "pm" in lowered or "すずか" in agent_name:
                role = "pm"
            elif "dev" in lowered or "スゴ杉" in agent_name:
                role = "dev"
            elif "marketing" in lowered or "ヂャイアン" in agent_name or "ジャイアン" in agent_name:
                role = "marketing"
            else:
                role = "pm"
        return self.append(
            role=role,
            message=message,
            msg_type=msg_type,
            phase=phase,
            turn=turn,
            reply_to=reply_to,
            display_name=agent_name,
        )

    def log_president_message(self, message: str, *, phase: str = "DIVERGENCE", turn: int = 1) -> str:
        return self.append(
            role="president",
            message=message,
            msg_type="text",
            phase=phase,
            turn=turn,
        )

    def log_proposal(
        self,
        *,
        message: str,
        phase: str = "FINAL",
        turn: int,
        agent_name: str | None = None,
    ) -> str:
        return self.log_agent_message(
            agent_name=agent_name or "すずかちゃん(PM)",
            message=message,
            phase=phase,
            turn=turn,
            msg_type="proposal",
        )

    def log_decision(self, *, decision: str, phase: str = "FINAL", turn: int, reply_to: Optional[str] = None) -> str:
        label = "Go ✅" if decision.lower() == "go" else "NoGo ❌"
        message = f"{label}\nこの案で進めて！" if decision.lower() == "go" else f"{label}\n修正方針を反映して再検討します。"
        self.append(
            role="system",
            message="Go / NoGo 判定",
            msg_type="system",
            phase=phase,
            turn=turn,
        )
        return self.append(
            role="president",
            message=message,
            msg_type="decision",
            phase=phase,
            turn=turn,
            reply_to=reply_to,
        )
