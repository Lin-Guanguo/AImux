# AImux Skills

Skills for AI-driven tmux session management. These files teach AI agents how to monitor and control coding agents running in tmux.

| Skill | Description |
|-------|-------------|
| [aimux](skills/aimux/SKILL.md) | Main workflow: scan panes, dispatch work, monitor progress, collect results |
| [tmux-control](skills/tmux-control/SKILL.md) | Low-level tmux operations: send-keys, capture-pane, agent state detection |

## Usage

Skills are symlinked to `.claude/skills/` via `../skills` and auto-discovered by Claude Code in this repo.

For other AI tools, point them at `skills/aimux/SKILL.md` as the entry point.
