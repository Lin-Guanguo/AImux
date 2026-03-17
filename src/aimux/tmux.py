"""Thin wrapper around tmux CLI."""

import subprocess


def run_tmux(*args: str) -> str:
    """Run a tmux command and return stdout. Raises on failure."""
    result = subprocess.run(
        ["tmux", *args],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"tmux {' '.join(args)}: {result.stderr.strip()}")
    return result.stdout


def list_panes() -> list[dict]:
    """List all panes across all sessions with metadata."""
    fmt = "#{pane_id}\t#{session_name}\t#{window_index}\t#{pane_index}\t#{pane_current_command}\t#{pane_current_path}\t#{pane_title}\t#{pane_width}\t#{pane_height}"
    out = run_tmux("list-panes", "-a", "-F", fmt)
    panes = []
    for line in out.strip().splitlines():
        parts = line.split("\t")
        if len(parts) < 9:
            continue
        panes.append({
            "pane_id": parts[0],
            "session": parts[1],
            "window": int(parts[2]),
            "pane_index": int(parts[3]),
            "command": parts[4],
            "cwd": parts[5],
            "title": parts[6],
            "width": int(parts[7]),
            "height": int(parts[8]),
        })
    return panes


def capture_pane(pane_id: str, lines: int = 30) -> str:
    """Capture last N lines from a pane."""
    return run_tmux("capture-pane", "-t", pane_id, "-p", "-S", f"-{lines}", "-J")


def send_keys(pane_id: str, *keys: str) -> None:
    """Send keys to a pane."""
    run_tmux("send-keys", "-t", pane_id, *keys)
