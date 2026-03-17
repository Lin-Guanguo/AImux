"""Map tmux panes to Claude Code / Codex JSONL session files.

Strategy:
1. pane_current_path → ~/.claude/projects/<encoded-path>/*.jsonl  (Claude Code)
2. pane_current_path → ~/.codex/sessions/YYYY/MM/DD/*.jsonl       (Codex)
3. Pick the most recently modified JSONL (= active session)
4. Read tail to get latest user message for cross-validation
"""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .tmux import list_panes

CLAUDE_PROJECTS_DIR = Path.home() / ".claude" / "projects"
CODEX_SESSIONS_DIR = Path.home() / ".codex" / "sessions"

# JSONL lines containing user text are sparse; need enough lines to find one
TAIL_LINES_DEFAULT = 100


# --- Claude Code ---


def cwd_to_claude_project_dir(cwd: str) -> Path:
    """Convert a filesystem path to Claude Code's project directory.

    Claude Code encodes project paths by replacing '/' with '-':
      /Users/x/dev/AImux → -Users-x-dev-AImux
    """
    encoded = cwd.replace("/", "-")
    return CLAUDE_PROJECTS_DIR / encoded


def find_claude_session(cwd: str) -> Path | None:
    """Find the most recently modified Claude Code JSONL for a cwd."""
    project_dir = cwd_to_claude_project_dir(cwd)
    if not project_dir.is_dir():
        return None
    candidates = [
        f for f in project_dir.iterdir()
        if f.suffix == ".jsonl" and f.is_file() and not f.name.startswith("agent-")
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda f: f.stat().st_mtime)


# --- Codex ---


def find_codex_session(cwd: str) -> Path | None:
    """Find the most recently modified Codex JSONL that matches a cwd.

    Codex stores sessions by date: ~/.codex/sessions/YYYY/MM/DD/*.jsonl
    We scan the last 3 days and match by cwd in session_meta.
    """
    if not CODEX_SESSIONS_DIR.is_dir():
        return None

    today = datetime.now(timezone.utc).date()
    candidates: list[Path] = []
    for delta in range(4):  # today + 3 days back
        d = today - timedelta(days=delta)
        day_dir = CODEX_SESSIONS_DIR / str(d.year) / f"{d.month:02d}" / f"{d.day:02d}"
        if day_dir.is_dir():
            candidates.extend(
                f for f in day_dir.iterdir()
                if f.suffix == ".jsonl" and f.is_file()
            )

    if not candidates:
        return None

    # Filter by cwd match in session_meta
    matched = []
    for f in candidates:
        meta_cwd = _codex_session_cwd(f)
        if meta_cwd and meta_cwd == cwd:
            matched.append(f)

    if not matched:
        return None
    return max(matched, key=lambda f: f.stat().st_mtime)


def _codex_session_cwd(jsonl_path: Path) -> str | None:
    """Read the cwd from a Codex JSONL's session_meta entry (first line)."""
    try:
        with open(jsonl_path, "r", encoding="utf-8") as f:
            line = f.readline().strip()
            if not line:
                return None
            entry = json.loads(line)
            if entry.get("type") == "session_meta":
                return entry.get("payload", {}).get("cwd")
    except (OSError, json.JSONDecodeError):
        pass
    return None


# --- Common JSONL reading ---


def read_tail_messages(jsonl_path: Path, n: int = TAIL_LINES_DEFAULT) -> list[dict]:
    """Read the last N JSON lines from a JSONL file.

    Reads from the end of file for efficiency on large files.
    """
    lines: list[str] = []
    buf_size = 8192
    with open(jsonl_path, "rb") as f:
        f.seek(0, 2)
        remaining = f.tell()
        while remaining > 0 and len(lines) < n + 1:
            read_size = min(buf_size, remaining)
            remaining -= read_size
            f.seek(remaining)
            chunk = f.read(read_size).decode("utf-8", errors="replace")
            lines = chunk.splitlines() + lines
        lines = [l for l in lines if l.strip()][-n:]

    messages = []
    for line in lines:
        try:
            messages.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return messages


def get_last_user_text(messages: list[dict], agent_type: str = "claude") -> str | None:
    """Extract the text of the most recent user message."""
    for msg in reversed(messages):
        if agent_type == "claude":
            if msg.get("type") != "user":
                continue
            content = msg.get("message", {}).get("content", "")
            if isinstance(content, str) and content.strip():
                return content.strip()
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        text = block.get("text", "").strip()
                        if text:
                            return text
        elif agent_type == "codex":
            if msg.get("type") != "event_msg":
                continue
            payload = msg.get("payload", {})
            if payload.get("type") != "user_message":
                continue
            text = payload.get("message", "").strip()
            if text:
                return text
    return None


# --- Pane detection ---


def detect_agent_type(pane: dict) -> str:
    """Detect what kind of agent is running in a pane.

    Returns: "claude", "codex", "gemini", "shell", or "other".
    """
    cmd = pane.get("command", "")

    # Claude Code shows version number as process name (e.g. "2.1.76")
    # Also might show as "claude" or "node"
    if cmd.startswith(("claude", "Claude")):
        return "claude"
    if cmd == "node":
        # Could be Claude Code or other node app — need screen check
        return "unknown-node"

    if cmd.startswith("codex"):
        return "codex"

    if cmd.startswith("gemini"):
        return "gemini"

    # Version number pattern → likely Claude Code
    import re
    if re.match(r"^\d+\.\d+\.\d+", cmd):
        return "claude"

    if cmd in ("zsh", "bash", "fish", "sh"):
        return "shell"

    return "other"


# --- Main mapping ---


def map_all_panes() -> list[dict]:
    """Map all tmux panes, detecting agent type and matching sessions.

    Returns list of dicts with pane info, agent type, and session details.
    """
    results = []
    for pane in list_panes():
        agent = detect_agent_type(pane)
        entry: dict = {
            "pane": pane,
            "agent": agent,
            "session": None,
        }

        # Try Claude Code session
        if agent in ("claude", "unknown-node"):
            jsonl = find_claude_session(pane["cwd"])
            if jsonl:
                messages = read_tail_messages(jsonl)
                entry["agent"] = "claude"
                entry["session"] = {
                    "jsonl_path": str(jsonl),
                    "session_id": jsonl.stem,
                    "last_user_text": get_last_user_text(messages, "claude"),
                }

        # Try Codex session
        elif agent == "codex":
            jsonl = find_codex_session(pane["cwd"])
            if jsonl:
                messages = read_tail_messages(jsonl)
                entry["session"] = {
                    "jsonl_path": str(jsonl),
                    "session_id": jsonl.stem,
                    "last_user_text": get_last_user_text(messages, "codex"),
                }

        # Shell panes: check if there's a Claude/Codex session for the cwd anyway
        elif agent == "shell":
            jsonl = find_claude_session(pane["cwd"])
            if jsonl:
                entry["session"] = {
                    "jsonl_path": str(jsonl),
                    "session_id": jsonl.stem,
                    "last_user_text": None,  # don't read — shell isn't running an agent
                }

        results.append(entry)
    return results
