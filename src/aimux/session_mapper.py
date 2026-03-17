"""Map tmux panes to Claude Code / Codex JSONL session files.

Strategy:
1. pane_current_path → walk up to git root → ~/.claude/projects/<encoded>/*.jsonl
2. pane_current_path → ~/.codex/sessions/YYYY/MM/DD/*.jsonl (match by cwd)
3. Pick most recently modified JSONL (= active session)
4. Read tail efficiently to find latest user message
"""

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .tmux import list_panes

CLAUDE_PROJECTS_DIR = Path.home() / ".claude" / "projects"
CODEX_SESSIONS_DIR = Path.home() / ".codex" / "sessions"


# --- Efficient tail reading ---


def read_tail_bytes(filepath: Path, max_bytes: int = 64 * 1024) -> bytes:
    """Read the last max_bytes from a file."""
    size = filepath.stat().st_size
    read_size = min(max_bytes, size)
    with open(filepath, "rb") as f:
        f.seek(size - read_size)
        return f.read(read_size)


def parse_tail_jsonl(filepath: Path, max_bytes: int = 64 * 1024) -> list[dict]:
    """Parse JSONL lines from the tail of a file.

    Reads last max_bytes, discards the first (potentially partial) line,
    then parses complete JSON lines.
    """
    raw = read_tail_bytes(filepath, max_bytes)
    text = raw.decode("utf-8", errors="replace")

    # Split into lines, discard first (may be partial from chunk boundary)
    lines = text.split("\n")
    if len(lines) > 1:
        lines = lines[1:]  # drop potentially truncated first line

    messages = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            messages.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return messages


def find_last_user_text(filepath: Path, agent_type: str = "claude") -> str | None:
    """Find the most recent user text message from a JSONL file.

    Reads progressively larger chunks from the tail until a user message is found.
    Starts with 64KB, doubles up to 1MB.
    """
    for max_bytes in (64 * 1024, 256 * 1024, 1024 * 1024):
        messages = parse_tail_jsonl(filepath, max_bytes)
        text = _extract_last_user_text(messages, agent_type)
        if text:
            return text
    return None


def _extract_last_user_text(messages: list[dict], agent_type: str) -> str | None:
    """Extract the text of the most recent user message from parsed messages."""
    for msg in reversed(messages):
        if agent_type == "claude":
            if msg.get("type") != "user":
                continue
            if msg.get("isCompactSummary"):
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


# --- Claude Code session discovery ---


def _find_git_root(cwd: str) -> str | None:
    """Walk up from cwd to find the nearest .git directory."""
    p = Path(cwd).resolve()
    for d in [p, *p.parents]:
        if (d / ".git").exists():
            return str(d)
        if d == d.parent:
            break
    return None


def cwd_to_claude_project_dirs(cwd: str) -> list[Path]:
    """Convert a cwd to possible Claude Code project directories.

    Returns candidates in priority order:
    1. Exact cwd match
    2. Git root match (Claude Code typically uses git root as project dir)
    3. Parent directories up to 3 levels
    """
    candidates = []
    seen = set()

    for path_str in [cwd, _find_git_root(cwd)]:
        if not path_str:
            continue
        encoded = path_str.replace("/", "-")
        project_dir = CLAUDE_PROJECTS_DIR / encoded
        if project_dir not in seen and project_dir.is_dir():
            seen.add(project_dir)
            candidates.append(project_dir)

    # Walk up a few levels as fallback
    p = Path(cwd).resolve()
    for parent in list(p.parents)[:3]:
        encoded = str(parent).replace("/", "-")
        project_dir = CLAUDE_PROJECTS_DIR / encoded
        if project_dir not in seen and project_dir.is_dir():
            seen.add(project_dir)
            candidates.append(project_dir)

    return candidates


def _list_session_jsonls(project_dir: Path) -> list[Path]:
    """List JSONL session files in a project dir, excluding subagent files."""
    return [
        f for f in project_dir.iterdir()
        if f.suffix == ".jsonl" and f.is_file() and not f.name.startswith("agent-")
    ]


def find_claude_session(cwd: str) -> Path | None:
    """Find the most recently modified Claude Code JSONL for a cwd.

    Tries exact cwd, git root, and parent directories.
    """
    for project_dir in cwd_to_claude_project_dirs(cwd):
        candidates = _list_session_jsonls(project_dir)
        if candidates:
            return max(candidates, key=lambda f: f.stat().st_mtime)
    return None


# --- Codex session discovery ---


def find_codex_session(cwd: str) -> Path | None:
    """Find the most recently modified Codex JSONL that matches a cwd.

    Codex stores sessions by date: ~/.codex/sessions/YYYY/MM/DD/*.jsonl
    Scans the last 3 days and matches by cwd in session_meta (first line).
    """
    if not CODEX_SESSIONS_DIR.is_dir():
        return None

    today = datetime.now(timezone.utc).date()
    candidates: list[Path] = []
    for delta in range(4):
        d = today - timedelta(days=delta)
        day_dir = CODEX_SESSIONS_DIR / str(d.year) / f"{d.month:02d}" / f"{d.day:02d}"
        if day_dir.is_dir():
            candidates.extend(
                f for f in day_dir.iterdir()
                if f.suffix == ".jsonl" and f.is_file()
            )

    if not candidates:
        return None

    # Match by cwd (and git root as fallback)
    git_root = _find_git_root(cwd)
    match_cwds = {cwd}
    if git_root:
        match_cwds.add(git_root)

    matched = []
    for f in candidates:
        meta_cwd = _codex_session_cwd(f)
        if meta_cwd and meta_cwd in match_cwds:
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


# --- Pane detection ---


_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+")


def detect_agent_type(pane: dict) -> str:
    """Detect what kind of agent is running in a pane.

    Returns: "claude", "codex", "gemini", "shell", or "other".
    """
    cmd = pane.get("command", "")

    if cmd.startswith(("claude", "Claude")):
        return "claude"

    if cmd.startswith("codex"):
        return "codex"

    if cmd.startswith("gemini"):
        return "gemini"

    # Claude Code shows version number as process name (e.g. "2.1.76")
    if _VERSION_RE.match(cmd):
        return "claude"

    # node could be Claude Code — caller should verify via session or screen
    if cmd == "node":
        return "unknown-node"

    if cmd in ("zsh", "bash", "fish", "sh"):
        return "shell"

    return "other"


# --- Main mapping ---


def map_all_panes() -> list[dict]:
    """Map all tmux panes, detecting agent type and matching sessions.

    Returns list of dicts with pane info, agent type, and session details.
    Deduplicates session reads: if multiple panes share the same JSONL,
    reads it only once.
    """
    panes = list_panes()

    # Phase 1: detect agent type and find JSONL path for each pane
    pane_entries = []
    for pane in panes:
        agent = detect_agent_type(pane)
        jsonl: Path | None = None

        if agent in ("claude", "unknown-node"):
            jsonl = find_claude_session(pane["cwd"])
            if jsonl:
                agent = "claude"
        elif agent == "codex":
            jsonl = find_codex_session(pane["cwd"])
        elif agent == "shell":
            jsonl = find_claude_session(pane["cwd"])

        pane_entries.append((pane, agent, jsonl))

    # Phase 2: deduplicate JSONL reads
    jsonl_cache: dict[str, str | None] = {}  # path → last_user_text
    for _, agent, jsonl in pane_entries:
        if jsonl and str(jsonl) not in jsonl_cache:
            effective_agent = "codex" if agent == "codex" else "claude"
            jsonl_cache[str(jsonl)] = find_last_user_text(jsonl, effective_agent)

    # Phase 3: build results
    results = []
    for pane, agent, jsonl in pane_entries:
        session = None
        if jsonl:
            session = {
                "jsonl_path": str(jsonl),
                "session_id": jsonl.stem,
                "last_user_text": jsonl_cache.get(str(jsonl)),
                "mtime": datetime.fromtimestamp(
                    jsonl.stat().st_mtime, tz=timezone.utc
                ).isoformat(),
            }
        results.append({
            "pane": pane,
            "agent": agent,
            "session": session,
        })

    return results
