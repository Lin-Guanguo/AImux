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
    read_tail_messages,
)

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


def jsonl_last_is_assistant(cwd: str, agent_type: str) -> bool:
    """Check if the JSONL session's last meaningful entry is an assistant message.

    This confirms the agent has actually produced a reply, not just that
    the screen shows a prompt (which could mean the task hasn't started yet).
    """
    jsonl_path = None
    if agent_type in ("claude", "unknown-node"):
        jsonl_path = find_claude_session(cwd)
    elif agent_type == "codex":
        jsonl_path = find_codex_session(cwd)

    if not jsonl_path:
        return False

    messages = read_tail_messages(jsonl_path, n=10)
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

    messages = read_tail_messages(jsonl_path, n=200)
    if not messages:
        return None

    if agent_type in ("claude", "unknown-node"):
        return _extract_claude_reply(messages)
    elif agent_type == "codex":
        return _extract_codex_reply(messages)
    return None


def _extract_claude_reply(messages: list[dict]) -> str | None:
    """Extract all assistant text after the last user message in Claude JSONL."""
    last_user_idx = -1
    for i, msg in enumerate(messages):
        if msg.get("type") == "user":
            last_user_idx = i

    if last_user_idx == -1:
        return None

    parts = []
    for msg in messages[last_user_idx + 1:]:
        if msg.get("type") == "assistant":
            content = msg.get("message", {}).get("content", "")
            if isinstance(content, str) and content.strip():
                parts.append(content.strip())
            elif isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        text = block.get("text", "").strip()
                        if text:
                            parts.append(text)

    return "\n".join(parts) if parts else None


def _extract_codex_reply(messages: list[dict]) -> str | None:
    """Extract assistant reply from Codex JSONL."""
    last_user_idx = -1
    for i, msg in enumerate(messages):
        if msg.get("type") == "event_msg":
            if msg.get("payload", {}).get("type") == "user_message":
                last_user_idx = i

    if last_user_idx == -1:
        return None

    parts = []
    for msg in messages[last_user_idx + 1:]:
        if msg.get("type") == "event_msg":
            payload = msg.get("payload", {})
            if payload.get("type") in ("assistant_message", "message"):
                text = payload.get("message", "").strip()
                if text:
                    parts.append(text)

    return "\n".join(parts) if parts else None


def _output_result(pane_id: str, agent_type: str, state: str, elapsed: float, cwd: str) -> None:
    """Print JSON result to stdout."""
    reply = get_last_reply(cwd, agent_type) if state != STATE_UNKNOWN else None
    json.dump({
        "pane": pane_id,
        "agent": agent_type,
        "state": state,
        "elapsed": round(elapsed, 1),
        "reply": reply,
    }, sys.stdout, ensure_ascii=False)
    print()


def wait_for_idle(pane_id: str, timeout: float = 300, interval: float = 2) -> int:
    """Block until pane agent becomes idle or times out.

    Dual-signal detection:
      - Screen shows idle (❯ prompt)  AND
      - JSONL last entry is assistant (not user)
    Both must be true to confirm the agent actually finished.
    This prevents false positives when send-keys was just sent but
    the agent hasn't started processing yet.

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

    while True:
        elapsed = time.monotonic() - start

        screen_state = detect_screen_state(pane_id)

        if screen_state == STATE_WAITING_INPUT:
            _output_result(pane_id, agent_type, STATE_WAITING_INPUT, elapsed, cwd)
            return EXIT_WAITING_INPUT

        if screen_state == STATE_IDLE:
            # Dual-signal: screen says idle, confirm JSONL has assistant reply
            if jsonl_last_is_assistant(cwd, agent_type):
                _output_result(pane_id, agent_type, STATE_IDLE, elapsed, cwd)
                return EXIT_IDLE
            # Screen idle but JSONL last is user → agent hasn't started yet, keep waiting

        if elapsed >= timeout:
            _output_result(pane_id, agent_type, screen_state, elapsed, cwd)
            return EXIT_TIMEOUT

        time.sleep(interval)
