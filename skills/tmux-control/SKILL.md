---
name: tmux-control
description: This skill should be used when the user asks to "send keys to a pane", "capture pane output", "list tmux panes", "detect agent state", "create tmux window", or needs low-level tmux operations like send-keys, capture-pane, and process detection for Claude Code, Codex, or shell panes.
---

# tmux Control

Operate tmux sessions, windows, and panes programmatically for managing, monitoring, or interacting with running processes in tmux panes.

## Listing Panes

List all panes across all sessions with metadata:

```bash
tmux list-panes -a -F "#{pane_id} | #{session_name}:#{window_index}.#{pane_index} | #{pane_current_command} | #{pane_width}x#{pane_height} | #{pane_current_path} | #{pane_title}"
```

Output columns: pane ID, location, running process, size, working directory, title.

## Detecting Process Type

`pane_current_command` reveals what's running:

| Value | Process |
|-------|---------|
| `zsh` / `bash` | Idle shell |
| `python` / `python3` | Python process |
| `node` | Node.js process |
| `vim` / `nvim` | Editor |
| Version number (e.g. `2.1.76`) | Likely Claude Code, but not reliable for detection |

**Note:** `pane_current_command` is unreliable for identifying Claude Code — it displays its version number as the process name, which could collide with other tools. To accurately identify what's running in a pane, use `capture-pane` and read the screen content (look for `Ctx:` status bar, `❯` prompt, etc.).

## Reading Screen Content

Capture pane content with metadata header:

```bash
# Print metadata first, then screen content
echo "[pane:$(tmux display -t <pane_id> -p '#{pane_id}') | $(tmux display -t <pane_id> -p '#{pane_width}x#{pane_height}') | process:$(tmux display -t <pane_id> -p '#{pane_current_command}') | path:$(tmux display -t <pane_id> -p '#{pane_current_path}') | title:$(tmux display -t <pane_id> -p '#{pane_title}')]" && tmux capture-pane -t <pane_id> -p -S -30
```

Adjust `-S -30` (last 30 lines) as needed. Use `-S -` for full scrollback.

## Sending Input

### Send text (without Enter)

```bash
tmux send-keys -t <pane_id> "your text here"
```

### Send text + Enter (submit)

```bash
tmux send-keys -t <pane_id> "your text here" Enter
```

### Send special keys

```bash
tmux send-keys -t <pane_id> Enter      # Return/Enter
tmux send-keys -t <pane_id> Escape     # Escape
tmux send-keys -t <pane_id> C-c        # Ctrl-C
tmux send-keys -t <pane_id> C-d        # Ctrl-D
tmux send-keys -t <pane_id> Tab        # Tab
tmux send-keys -t <pane_id> Up         # Arrow up
tmux send-keys -t <pane_id> Down       # Arrow down
```

## Tool-Specific Operations

### Claude Code

| Action | Command |
|--------|---------|
| Send message | `send-keys -t <pane> "message" Enter` |
| Interrupt response | `send-keys -t <pane> C-c` |
| Exit Claude Code | `send-keys -t <pane> C-c C-c` (two Ctrl-C) |
| Approve tool use | `send-keys -t <pane> "y" Enter` |
| Deny tool use | `send-keys -t <pane> "n" Enter` |
| Escape plan mode | `send-keys -t <pane> Escape` |

Detecting Claude Code state from `capture-pane` output:

| Screen content | State |
|----------------|-------|
| `❯ ` at last line (cursor waiting) | Idle — ready for input |
| `⏺` followed by text being generated | Running — AI is responding |
| `Allow \| Deny` visible | Waiting approval — needs permission decision |
| `Interrupted` visible | Interrupted — was stopped mid-response |
| `Resume this session with:` | Exited — Claude Code has quit, pane is back to shell |
| `Ctx:` in bottom status bar | Active — Claude Code process is running |

### Codex

| Action | Command |
|--------|---------|
| Send message | `send-keys -t <pane> "message" Enter` |
| Interrupt | `send-keys -t <pane> C-c` |
| Exit | `send-keys -t <pane> C-c` or `send-keys -t <pane> "exit" Enter` |

### Generic Shell (zsh/bash)

| Action | Command |
|--------|---------|
| Run command | `send-keys -t <pane> "command" Enter` |
| Interrupt running process | `send-keys -t <pane> C-c` |
| Clear screen | `send-keys -t <pane> C-l` |
| EOF / exit | `send-keys -t <pane> C-d` |

## Window & Pane Management

### Create

```bash
# New window (optionally named, at specific index)
tmux new-window [-n "name"] [-t <index>]

# Split current pane
tmux split-window -t <pane_id> [-h]   # -h for horizontal split
```

### Rename

```bash
# Rename window
tmux rename-window -t <session>:<window> "new-name"

# Rename pane (set title)
tmux select-pane -t <pane_id> -T "new-title"
```

### Move

```bash
# Move pane to another window
tmux move-pane -s <src_pane> -t <dst_window>

# Join pane from another window
tmux join-pane -s <src_pane> -t <dst_pane>
```

### Close

```bash
tmux kill-pane -t <pane_id>
```

## Error Handling

Always check if a pane exists before operating:

```bash
tmux has-session -t <pane_id> 2>/dev/null && echo "exists" || echo "[ERROR] pane <pane_id> not found"
```

Common errors:
- `can't find pane` — pane ID does not exist
- `session not found` — session does not exist
- `no server running` — tmux is not running at all
