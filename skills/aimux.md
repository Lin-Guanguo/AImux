# AImux Skill Reference

You are managing multiple AI coding agents running in tmux panes. Use this reference to monitor, control, and coordinate them.

## 1. Scan: See what's running

```bash
aimux
```

Output shows every tmux pane with its agent type, project, session ID, and last user message:

```
%3  [Claude Code]  myproject  (/Users/x/dev/myproject)
  session 16a77abb  last: fix the failing test

%6  [Codex]  backend  (/Users/x/dev/backend)
  session a1b2c3d4  last: add pagination

%4  [shell]  AImux  (/Users/x/dev/AImux)
  (no session)
```

## 2. Read: Check what a pane is doing

### Quick state check (capture-pane)

```bash
tmux capture-pane -t <pane_id> -p -S -30 -J
```

Interpret the output:

| What you see | State | Action |
|-------------|-------|--------|
| `❯ ` at last line | Idle | Ready — send it work |
| `⏺` + text streaming | Generating | Wait — it's working |
| `Allow \| Deny` | Waiting approval | Send `y` or `n` + Enter |
| `Interrupted` | Stopped | Send new prompt or investigate |
| Error messages / stack traces | Error | Read and decide how to fix |
| `Ctx:` in status bar | Process alive | Claude Code is running |

### Detailed history (JSONL)

`aimux` output shows the session ID and JSONL path. To read more context, parse the JSONL file directly:

```python
import json
with open("<jsonl_path>") as f:
    for line in f:
        msg = json.loads(line)
        if msg.get("type") == "assistant":
            # Read what the agent said/did
            ...
```

## 3. Command: Send work to a pane

### Send a prompt

```bash
tmux send-keys -t <pane_id> "your instruction here" Enter
```

### Approve/deny tool use

```bash
tmux send-keys -t <pane_id> "y" Enter    # approve
tmux send-keys -t <pane_id> "n" Enter    # deny
```

### Interrupt

```bash
tmux send-keys -t <pane_id> C-c          # interrupt current response
```

### Special operations

| Agent | Exit | Cancel plan mode |
|-------|------|-----------------|
| Claude Code | `C-c C-c` (two Ctrl-C) | `Escape` |
| Codex | `C-c` | — |

## 4. Workflow: Boss pattern

The standard workflow for managing multiple agents:

### Dispatch work

```
1. Run `aimux` to see all panes
2. Pick an idle pane (❯ prompt visible)
3. Send it a task via send-keys
4. Move to the next idle pane
```

### Monitor

```
1. Run `aimux` to see latest messages per session
2. For panes that seem stuck, capture-pane to check state
3. If waiting approval → approve or deny
4. If errored → read the error, send correction or escalate
5. If idle → check if output looks correct, send follow-up or new task
```

### Collect results

```
1. capture-pane to read the final output on screen
2. Or read the JSONL for full structured conversation history
```

## 5. Pane → Session mapping

AImux maps panes to session files automatically:

- **Claude Code**: `pane cwd` → `~/.claude/projects/<cwd with / replaced by ->/*.jsonl` → most recently modified file
- **Codex**: `pane cwd` → `~/.codex/sessions/YYYY/MM/DD/*.jsonl` → matched by `session_meta.cwd`

When multiple sessions exist for the same directory, the most recent JSONL (by mtime) is selected. Cross-validate by checking if the JSONL's latest user message matches what you sent.

## 6. Tips

- **Don't read too much screen**: capture-pane with `-S -30` (30 lines) is usually enough. Reading thousands of lines wastes tokens.
- **JSONL is lossless**: If you need full history (tool calls, token counts, exact responses), read the JSONL — capture-pane only shows what's visible on screen.
- **Human can take over anytime**: `tmux attach` gives the human full control. Your send-keys and their keyboard input are indistinguishable.
- **One thing at a time per pane**: Don't send a new prompt while the agent is still generating. Wait for idle state first.
- **Label panes**: `tmux select-pane -t <pane_id> -T "task-name"` helps you track what each pane is working on.
