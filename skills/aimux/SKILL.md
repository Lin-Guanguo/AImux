---
name: AImux
description: This skill should be used when the user asks to "manage tmux agents", "check what's running", "dispatch work to agents", "monitor agent progress", "send a task to a pane", or needs to coordinate multiple AI coding agents running in tmux panes. Covers the full boss-pattern workflow: scan panes, dispatch work, monitor progress, collect results.
---

# AImux Skill Reference

Manage multiple AI coding agents running in tmux panes. This reference covers monitoring, controlling, and coordinating them.

## 0. Workspace Conventions

### Session layout

| tmux session | Owner | Purpose |
|-------------|-------|---------|
| `0` | Human | User's primary workspace. Windows `0:0`, `0:1`, `0:2`, `0:3` etc. |
| `9` | AI | AI-created workspace. Create if not exists, reuse if exists. |

### Rules

- **Session `0` is the user's space.** Scan it to understand what the user is working on. Don't create or destroy windows here unless explicitly asked.
- **Session `9` is AI's workspace.** When you need to spawn new agents, create windows here. One window per pane (no splits).
- **Creating a pane in session `9`:**

```bash
# Create session 9 if it doesn't exist
tmux has-session -t 9 2>/dev/null || tmux new-session -d -s 9

# Create a new window with a meaningful name
tmux new-window -t 9 -n "task-name"
```

- **Finding existing panes:** Always check session `0` first (user's work), then session `9` (AI's work).

```bash
# List user's panes
tmux list-panes -t 0 -a -F "#{pane_id} | #{window_name} | #{pane_current_command} | #{pane_current_path}"

# List AI's panes
tmux list-panes -t 9 -a -F "#{pane_id} | #{window_name} | #{pane_current_command} | #{pane_current_path}"
```

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

### Send an image to a pane

Claude Code 可以通过文件路径读取图片，效果等同于 Ctrl+V 粘贴：

```bash
tmux send-keys -t <pane_id> "分析这张图片 /path/to/image.jpg" Enter
```

典型场景：用户通过 Discord 发图片 → OpenClaw 自动保存到 `~/.openclaw/media/inbound/<uuid>.jpg` → 直接 send-keys 引用该路径即可。

### Special operations

| Agent | Exit | Cancel plan mode |
|-------|------|-----------------|
| Claude Code | `C-c C-c` (two Ctrl-C) | `Escape` |
| Codex | `C-c` | — |

## 4. Workflow: Dispatch + Async Wait (推荐)

**核心原则：你是调度器，不是执行者。** 下发任务后立即返回，用 `aimux wait` 后台监听完成。这样你能快速响应用户、同时跟进多条任务线。

### 标准流程

```
1. aimux               → 扫描所有 pane，找到空闲的
2. send-keys 下发任务   → 秒完成
3. aimux wait 后台监听  → exec background:true，不阻塞你
4. 立即回复用户         → "已下发，完成后汇报"
5. wait 进程退出        → OpenClaw notifyOnExit 自动唤醒你
6. 读取结果 + 汇报      → capture-pane 或 JSONL
```

### Dispatch（下发任务）

```bash
# 1. 扫描
aimux

# 2. 下发
tmux send-keys -t <pane_id> "your instruction here" Enter

# 3. 后台监听 + 完成通知（关键！）
#    用 --output-file 把 JSON 写到文件，避免 shell 管道/变量展开破坏 JSON
exec background:true command:"cd /path/to/AImux && uv run aimux wait <pane_id> --timeout 300 --output-file /tmp/aimux_wait_<pane_id>.json; python3 -c \"import json; d=json.load(open('/tmp/aimux_wait_<pane_id>.json')); reply=(d.get('reply') or 'no reply')[:500]; exit_state=d.get('state','?')\" && openclaw agent --agent main --channel discord --to 'discord:861231414467756062' --message 'pane <pane_id> 任务完成' --deliver --timeout 60"

# 4. 立即回复用户，不要等
```

> **⚠ 不要** 用 `$(uv run aimux wait ...)` 捕获输出再 `json.loads("$result")`——`reply` / `pane_tail` 里的 `$`、反引号等字符会被 shell 展开导致 JSON 解析失败。始终用 `--output-file` 写文件再 `json.load(open(...))` 读取。

当 `aimux wait` 退出后，`openclaw agent --deliver` 会注入消息触发你的 agent turn，你会被唤醒并可以在当前对话中主动汇报结果。

### 退出码

| Code | 含义 | 你该做什么 |
|------|------|-----------|
| 0 | agent 完成（idle） | capture-pane 读结果，汇报给用户 |
| 1 | 超时 | capture-pane 看状态，决定是否继续等或中断 |
| 2 | agent 在等审批 | 告知用户或自动审批 |
| 3 | pane 不存在 | 报错 |

### 多任务并行

```bash
# 同时下发多个任务
tmux send-keys -t %3 "task A" Enter
tmux send-keys -t %6 "task B" Enter

# 分别监听
exec background:true command:"uv run aimux wait %3 --timeout 300"
exec background:true command:"uv run aimux wait %6 --timeout 300"

# 哪个先完成，哪个先唤醒你
```

### ⚠️ 不要做的事

- **不要** 在 send-keys 后反复 capture-pane 轮询 — 用 aimux wait
- **不要** 在等 agent 完成的过程中阻塞主对话 — 用 background
- **不要** 自己去写代码 — 你是 boss，下指令就好

## 4b. Workflow: 手动监控（备用）

仅在 `aimux wait` 不可用、或需要即时查看时使用：

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

## Reference

For detailed tmux operations (all special keys, process detection, window/pane management, error handling), consult:

- **[`references/tmux-reference.md`](references/tmux-reference.md)** — Complete tmux command reference
