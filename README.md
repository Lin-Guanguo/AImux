# AImux

Python utilities + AI skill for managing tmux-based coding agents.

**Not a server or framework** — just lightweight CLI tools and a [skills/aimux/SKILL.md](skills/aimux/SKILL.md) that teaches AI how to monitor and control your running sessions.

## What is this?

AImux maps your tmux panes to the AI coding agents running inside them (Claude Code, Codex, Gemini, etc.), giving you a unified view and programmatic control. It uses two orthogonal channels:

- **Input** — `tmux send-keys` to inject commands into running sessions
- **Output** — Agent JSONL session files for structured, lossless conversation data

The core deliverable is **[skills/aimux/SKILL.md](skills/aimux/SKILL.md)** — a reference that any AI agent can read to learn how to manage multiple coding agents via tmux. The Python utilities (`aimux` CLI) support the skill by providing pane enumeration, agent detection, and pane→session mapping.

This combination is unique: it can attach to sessions that are *already running* with full context intact, while still getting structured output — something no other tool can do.

## Why?

Every existing approach has a gap:

| Approach | Attach existing session? | Structured output? | Self-hosted? |
|----------|------------------------|--------------------|-------------|
| Claude Code Remote Control | Yes | Yes | No (Anthropic relay) |
| Agent SDK / `resume` | No (spawns new process) | Yes | Yes |
| acpx / ACP protocol | No (spawns new process) | Yes | Yes |
| Community Web UIs | No (new sessions only) | Partial | Yes |
| SSH + tmux attach | Yes | No (raw terminal) | Yes |
| **AImux** | **Yes** | **Yes (JSONL)** | **Yes** |

AImux is the only self-hosted solution that attaches to running sessions *and* reads structured output.

## How it works

```
tmux send-keys ──→ Running Claude Code / Codex / Gemini
                        │
                        ├── Screen output ←── tmux capture-pane (real-time state)
                        └── JSONL files   ←── ~/.claude/projects/  (structured history)
```

### Dual-channel design

| Channel | Source | Use case |
|---------|--------|----------|
| `capture-pane` | tmux screen buffer | "What is it doing right now?" (idle / generating / waiting approval / error) |
| JSONL session files | `~/.claude/projects/` or `~/.codex/sessions/` | "What did it do?" (full conversation, tool calls, token usage) |

### Pane → Session mapping

AImux automatically maps tmux panes to their agent session files:

```
tmux pane %3 → cwd: /Users/x/dev/myproject
                 ↓
~/.claude/projects/-Users-x-dev-myproject/*.jsonl
                 ↓
session 16a77abb — last: "fix the failing test"
```

The mapping is deterministic (Claude Code encodes paths by replacing `/` with `-`). For multiple sessions in the same directory, the latest user message content is used for cross-validation.

## Quick start

```bash
# Install
pip install -e .
# or
uv pip install -e .

# See all your tmux panes and their agent sessions
aimux
```

Example output:

```
%3  [Claude Code]  myproject  (/Users/x/dev/myproject)
  session 16a77abb  last: fix the failing test

%6  [Codex]  backend  (/Users/x/dev/backend)
  session a1b2c3d4  last: add pagination to the API

%4  [shell]  AImux  (/Users/x/dev/AImux)
  (no session)
```

## Project structure

```
skills/aimux/
├── SKILL.md             # Main skill — AI reads this to manage agents
└── references/
    └── tmux-reference.md  # Detailed tmux command reference
.claude/skills → ../skills   # Symlink (auto-discovered by Claude Code)
src/aimux/
├── tmux.py              # Thin wrapper: list_panes, capture_pane, send_keys
├── session_mapper.py    # Pane → JSONL mapping (Claude Code + Codex)
└── __init__.py          # CLI entry point (aimux command)
docs/                    # Research & design decisions
```

### Design decisions

1. **Utilities, not a server.** AImux is a bag of CLI tools and skill files. No daemon, no framework, no state to manage.

2. **tmux only.** No terminal abstraction layer. A previous attempt (TermSupervisor) tried supporting iTerm2 + tmux — the adapter complexity exploded.

3. **No state machines.** AI reads the screen via `capture-pane` and decides what's happening. Zero code changes to support new tools.

4. **AI is the orchestrator.** The orchestration logic lives in [skills/aimux/SKILL.md](skills/aimux/SKILL.md), not in application code. The skill describes tools and rules — the AI does the rest.

5. **Human can always intervene.** `tmux attach` and you're in control. AImux operates alongside you, not instead of you.

## Supported agents

| Agent | Detection | Session source |
|-------|-----------|---------------|
| Claude Code | Process name (version number pattern) | `~/.claude/projects/<encoded-cwd>/*.jsonl` |
| Codex | Process name `codex` | `~/.codex/sessions/YYYY/MM/DD/*.jsonl` |
| Gemini | Process name `gemini` | — |
| Shell | `zsh` / `bash` / `fish` | Checks for nearby Claude/Codex sessions |

## Tech stack

- **Python** (3.12+)
- **uv** for package management
- **tmux** subprocess calls — no dependencies beyond the standard library

## Status

Core working: pane enumeration, agent detection, pane→session mapping with JSONL reading. The [skills/aimux/SKILL.md](skills/aimux/SKILL.md) covers the full boss-pattern workflow (scan → dispatch → monitor → collect).

## Research

Design decisions are documented in depth:

- [Research 1: tmux validation & architecture](docs/research-1-tmux-validation.md) — tmux send-keys feasibility, comparison with Remote Control / Web UIs / Agent SDK
- [Research 2: Agora & acpx/ACP analysis](docs/research-2-agora-acpx-analysis.md) — Why ACP can't attach to running sessions, Agent SDK resume mechanics, Agora framework evaluation

## License

MIT
