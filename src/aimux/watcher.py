"""Watch tmux panes for agent state changes."""

import json
import re
import sys
import time

from .tmux import capture_pane, list_panes
from .session_mapper import (
    detect_agent_type,
    find_claude_session,
    find_codex_session,
    parse_tail_jsonl,
)

# Maximum reply characters before truncation (default, overridable via --reply-max)
DEFAULT_REPLY_MAX = 4000

# Exit codes
EXIT_IDLE = 0
EXIT_TIMEOUT = 1
EXIT_WAITING_INPUT = 2
EXIT_NO_PANE = 3

# State constants
STATE_IDLE = "idle"
STATE_GENERATING = "generating"
STATE_WAITING_INPUT = "waiting_input"
STATE_UNKNOWN = "unknown"

# Detection patterns
# Note: capture-pane -J joins wrapped lines and may include trailing whitespace
# and non-breaking spaces (\xa0). Patterns use \s which covers \xa0 in re.
IDLE_PATTERNS = [
    re.compile(r"❯[\s\xa0]*$"),      # Claude Code prompt (❯ followed by nbsp/spaces)
    re.compile(r"\$\s*$"),           # generic shell prompt
    re.compile(r">>>\s*$"),          # python REPL
]

WAITING_PATTERNS = [
    re.compile(r"Allow|Deny", re.IGNORECASE),
    re.compile(r"\(y\)es.*\(n\)o", re.IGNORECASE),
    re.compile(r"Do you want to proceed", re.IGNORECASE),
]

GENERATING_PATTERNS = [
    re.compile(r"⏺"),               # Claude Code activity indicator
]


def find_pane(pane_id: str) -> dict | None:
    """Find a specific pane by ID."""
    for pane in list_panes():
        if pane["pane_id"] == pane_id:
            return pane
    return None


def detect_screen_state(pane_id: str) -> str:
    """Detect agent state from capture-pane content only."""
    try:
        content = capture_pane(pane_id, lines=30)
    except RuntimeError:
        return STATE_UNKNOWN

    lines = [l for l in content.splitlines() if l.strip()]
    if not lines:
        return STATE_UNKNOWN

    tail = "\n".join(lines[-5:])

    # Waiting for input? (check first — takes priority)
    for pat in WAITING_PATTERNS:
        if pat.search(tail):
            return STATE_WAITING_INPUT

    # Generating?
    for pat in GENERATING_PATTERNS:
        if pat.search(tail):
            return STATE_GENERATING

    # Idle? Check last few lines — prompt may not be the very last line
    # (e.g. Claude Code has a status bar below the ❯ prompt)
    for line in lines[-3:]:
        for pat in IDLE_PATTERNS:
            if pat.search(line):
                return STATE_IDLE

    return STATE_UNKNOWN


def _get_jsonl_path(cwd: str, agent_type: str):
    """Resolve JSONL session file path for a given cwd and agent type."""
    if agent_type in ("claude", "unknown-node"):
        return find_claude_session(cwd)
    elif agent_type == "codex":
        return find_codex_session(cwd)
    return None


def _get_jsonl_size(cwd: str, agent_type: str) -> int:
    """Get current JSONL file size in bytes. Returns 0 if not found."""
    path = _get_jsonl_path(cwd, agent_type)
    if path and path.exists():
        return path.stat().st_size
    return 0


def jsonl_last_is_assistant(cwd: str, agent_type: str) -> bool:
    """Check if the JSONL session's last meaningful entry is an assistant message.

    This confirms the agent has actually produced a reply, not just that
    the screen shows a prompt (which could mean the task hasn't started yet).
    """
    jsonl_path = _get_jsonl_path(cwd, agent_type)
    if not jsonl_path:
        return False

    messages = parse_tail_jsonl(jsonl_path, max_bytes=8192)
    if not messages:
        return False

    # Walk backwards to find the last user or assistant entry
    for msg in reversed(messages):
        if agent_type in ("claude", "unknown-node"):
            msg_type = msg.get("type")
            if msg_type == "assistant":
                return True
            if msg_type == "user":
                return False
        elif agent_type == "codex":
            if msg.get("type") == "event_msg":
                payload_type = msg.get("payload", {}).get("type", "")
                if payload_type in ("assistant_message", "message"):
                    return True
                if payload_type == "user_message":
                    return False

    return False


def get_last_reply(cwd: str, agent_type: str) -> str | None:
    """Get the full text of the agent's last reply from JSONL."""
    jsonl_path = None
    if agent_type in ("claude", "unknown-node"):
        jsonl_path = find_claude_session(cwd)
    elif agent_type == "codex":
        jsonl_path = find_codex_session(cwd)

    if not jsonl_path:
        return None

    # Read enough lines to find the last user message even after long tool-call sequences
    messages = parse_tail_jsonl(jsonl_path, max_bytes=512 * 1024)
    if not messages:
        return None

    if agent_type in ("claude", "unknown-node"):
        return _extract_claude_reply(messages)
    elif agent_type == "codex":
        return _extract_codex_reply(messages)
    return None


def _text_from_content(content) -> list[str]:
    """Extract text strings from a Claude assistant message content field."""
    parts = []
    if isinstance(content, str) and content.strip():
        parts.append(content.strip())
    elif isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                text = block.get("text", "").strip()
                if text:
                    parts.append(text)
    return parts


def _extract_claude_reply(messages: list[dict]) -> str | None:
    """Extract all assistant text after the last user message in Claude JSONL."""
    last_user_idx = -1
    for i, msg in enumerate(messages):
        if msg.get("type") == "user":
            last_user_idx = i

    if last_user_idx >= 0:
        # Normal path: collect all assistant text after the last user message
        parts = []
        for msg in messages[last_user_idx + 1:]:
            if msg.get("type") == "assistant":
                parts.extend(_text_from_content(msg.get("message", {}).get("content", "")))
        return "\n".join(parts) if parts else None

    # Fallback: tail didn't reach the last user message.
    # Only extract the very last assistant message's text to avoid dumping history.
    for msg in reversed(messages):
        if msg.get("type") == "assistant":
            parts = _text_from_content(msg.get("message", {}).get("content", ""))
            if parts:
                return "\n".join(parts)
    return None


def _extract_codex_reply(messages: list[dict]) -> str | None:
    """Extract assistant reply from Codex JSONL."""
    last_user_idx = -1
    for i, msg in enumerate(messages):
        if msg.get("type") == "event_msg":
            if msg.get("payload", {}).get("type") == "user_message":
                last_user_idx = i

    if last_user_idx >= 0:
        parts = []
        for msg in messages[last_user_idx + 1:]:
            if msg.get("type") == "event_msg":
                payload = msg.get("payload", {})
                if payload.get("type") in ("assistant_message", "message"):
                    text = payload.get("message", "").strip()
                    if text:
                        parts.append(text)
        return "\n".join(parts) if parts else None

    # Fallback: only the last assistant message
    for msg in reversed(messages):
        if msg.get("type") == "event_msg":
            payload = msg.get("payload", {})
            if payload.get("type") in ("assistant_message", "message"):
                text = payload.get("message", "").strip()
                if text:
                    return text
    return None


def get_reply_with_meta(cwd: str, agent_type: str, reply_max: int = DEFAULT_REPLY_MAX) -> dict:
    """Get agent's last reply with metadata about retrieval success.

    Returns dict with keys:
      reply       - str or None (the reply text, possibly truncated)
      reply_error - str or None: "jsonl_not_found", "no_assistant_reply", "read_error"
      reply_truncated - bool (True if reply was cut to reply_max chars)
    """
    jsonl_path = _get_jsonl_path(cwd, agent_type)
    if not jsonl_path:
        return {"reply": None, "reply_error": "jsonl_not_found", "reply_truncated": False}

    try:
        text = get_last_reply(cwd, agent_type)
    except Exception:
        return {"reply": None, "reply_error": "read_error", "reply_truncated": False}

    if text is None:
        return {"reply": None, "reply_error": "no_assistant_reply", "reply_truncated": False}

    truncated = len(text) > reply_max
    if truncated:
        text = text[:reply_max] + "\n... [truncated]"
    return {"reply": text, "reply_error": None, "reply_truncated": truncated}


def _output_result(pane_id: str, agent_type: str, state: str, elapsed: float, cwd: str,
                   reply_max: int = DEFAULT_REPLY_MAX) -> None:
    """Print JSON result to stdout."""
    if state != STATE_UNKNOWN:
        meta = get_reply_with_meta(cwd, agent_type, reply_max=reply_max)
    else:
        meta = {"reply": None, "reply_error": None, "reply_truncated": False}

    # Capture last 10 screen lines as supplementary context
    try:
        pane_tail = capture_pane(pane_id, lines=10).strip()
    except RuntimeError:
        pane_tail = None

    result = {
        "pane": pane_id,
        "agent": agent_type,
        "state": state,
        "elapsed": round(elapsed, 1),
        "reply": meta["reply"],
        "reply_error": meta["reply_error"],
        "reply_truncated": meta["reply_truncated"],
        "pane_tail": pane_tail,
    }
    json.dump(result, sys.stdout, ensure_ascii=False)
    print()


def wait_for_idle(pane_id: str, timeout: float = 300, interval: float = 2,
                   reply_max: int = DEFAULT_REPLY_MAX) -> int:
    """Block until pane agent becomes idle or times out.

    Quad-signal detection:
      1. Screen shows idle (❯ prompt)
      2. JSONL last entry is assistant (not user)
      3. JSONL file has grown since wait started (new content was written)
      4. JSONL size is stable (unchanged for one full poll cycle)

    Signal 4 prevents the race where Claude Code is between tool calls:
    the screen briefly shows ❯ and JSONL has an assistant entry, but the
    agent is still working.  Requiring size stability ensures writes have
    stopped before declaring idle.

    Returns exit code and prints JSON result to stdout.
    """
    pane = find_pane(pane_id)
    if pane is None:
        json.dump({"pane": pane_id, "error": "pane not found"}, sys.stdout)
        print()
        return EXIT_NO_PANE

    agent_type = detect_agent_type(pane)
    cwd = pane["cwd"]
    start = time.monotonic()

    # Snapshot JSONL size at start — completion requires the file to have grown,
    # proving the agent actually processed something after wait began.
    initial_jsonl_size = _get_jsonl_size(cwd, agent_type)

    # Track previous JSONL size to detect stability (no new writes).
    prev_jsonl_size = initial_jsonl_size

    # Number of consecutive polls where all idle signals are true.
    # Require >= STABLE_IDLE_CHECKS to confirm (prevents flicker between tool calls).
    STABLE_IDLE_CHECKS = 2
    consecutive_idle = 0

    while True:
        elapsed = time.monotonic() - start

        screen_state = detect_screen_state(pane_id)

        if screen_state == STATE_WAITING_INPUT:
            _output_result(pane_id, agent_type, STATE_WAITING_INPUT, elapsed, cwd, reply_max=reply_max)
            return EXIT_WAITING_INPUT

        if screen_state == STATE_IDLE:
            current_size = _get_jsonl_size(cwd, agent_type)
            jsonl_grew = current_size > initial_jsonl_size
            jsonl_stable = current_size == prev_jsonl_size
            prev_jsonl_size = current_size

            if jsonl_grew and jsonl_stable and jsonl_last_is_assistant(cwd, agent_type):
                consecutive_idle += 1
                if consecutive_idle >= STABLE_IDLE_CHECKS:
                    _output_result(pane_id, agent_type, STATE_IDLE, elapsed, cwd, reply_max=reply_max)
                    return EXIT_IDLE
                # Passed once but need more checks to confirm stability
            else:
                consecutive_idle = 0
        else:
            prev_jsonl_size = _get_jsonl_size(cwd, agent_type)
            consecutive_idle = 0

        if elapsed >= timeout:
            _output_result(pane_id, agent_type, screen_state, elapsed, cwd, reply_max=reply_max)
            return EXIT_TIMEOUT

        time.sleep(interval)
