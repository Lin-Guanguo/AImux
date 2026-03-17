import sys


def main() -> None:
    """CLI entry point: dispatch to subcommands."""
    args = sys.argv[1:]

    if args and args[0] == "wait":
        _cmd_wait(args[1:])
    else:
        _cmd_scan()


def _cmd_scan() -> None:
    """Show all pane → session mappings."""
    from .session_mapper import map_all_panes

    results = map_all_panes()
    if not results:
        print("No tmux panes found.")
        return

    agent_labels = {
        "claude": "Claude Code",
        "codex": "Codex",
        "gemini": "Gemini",
        "shell": "shell",
        "other": "other",
        "unknown-node": "node(?)",
    }

    for r in results:
        pane = r["pane"]
        agent = r["agent"]
        session = r["session"]
        project = pane["cwd"].rsplit("/", 1)[-1]
        label = agent_labels.get(agent, pane.get("command", agent))
        loc = f"{pane['session']}:{pane['window']}"

        print(f"{pane['pane_id']}  [{label}]  {loc}  {project}  ({pane['cwd']})")

        if session:
            sid = session["session_id"][:8]
            last = session.get("last_user_text")
            if last:
                preview = last.replace("\n", " ")
                if len(preview) > 80:
                    preview = preview[:77] + "..."
                print(f"  session {sid}  last: {preview}")
            else:
                print(f"  session {sid}")
        else:
            print(f"  (no session)")
        print()


def _cmd_wait(args: list[str]) -> None:
    """aimux wait <pane_id> [--timeout N] [--interval N]"""
    from .watcher import wait_for_idle

    if not args or args[0].startswith("-"):
        print("Usage: aimux wait <pane_id> [--timeout 300] [--interval 2]", file=sys.stderr)
        sys.exit(1)

    pane_id = args[0]
    timeout = 300.0
    interval = 2.0

    # Simple arg parsing
    i = 1
    while i < len(args):
        if args[i] == "--timeout" and i + 1 < len(args):
            timeout = float(args[i + 1])
            i += 2
        elif args[i] == "--interval" and i + 1 < len(args):
            interval = float(args[i + 1])
            i += 2
        else:
            print(f"Unknown argument: {args[i]}", file=sys.stderr)
            sys.exit(1)

    exit_code = wait_for_idle(pane_id, timeout=timeout, interval=interval)
    sys.exit(exit_code)
