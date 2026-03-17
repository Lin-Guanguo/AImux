# AImux Skills

Skills for AI-driven tmux session management. These files teach AI agents how to monitor and control coding agents running in tmux.

| Skill | Description |
|-------|-------------|
| [aimux.md](skills/aimux.md) | Main workflow: scan panes, dispatch work, monitor progress, collect results |
| [tmux-control.md](skills/tmux-control.md) | Low-level tmux operations: send-keys, capture-pane, agent state detection |

## Usage

Skills are symlinked to `.claude/skills/` and load automatically in Claude Code sessions within this repo.

For other AI tools, point them at `skills/aimux.md` as the entry point.
