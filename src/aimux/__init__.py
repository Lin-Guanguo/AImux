import sys


def main() -> None:
    """CLI entry point: dispatch to subcommands."""
    args = sys.argv[1:]

    if args and args[0] == "wait":
        _cmd_wait(args[1:])
    elif args and args[0] == "web":
        _cmd_web(args[1:])
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
    """aimux wait <pane_id> [--timeout N] [--interval N] [--reply-max N] [--output-file PATH]"""
    from .watcher import DEFAULT_REPLY_MAX, wait_for_idle

    if not args or args[0].startswith("-"):
        print("Usage: aimux wait <pane_id> [--timeout 300] [--interval 2] [--reply-max 4000] [--output-file /tmp/result.json]", file=sys.stderr)
        sys.exit(1)

    pane_id = args[0]
    timeout = 300.0
    interval = 2.0
    reply_max = DEFAULT_REPLY_MAX
    output_file = None

    # Simple arg parsing
    i = 1
    while i < len(args):
        if args[i] == "--timeout" and i + 1 < len(args):
            timeout = float(args[i + 1])
            i += 2
        elif args[i] == "--interval" and i + 1 < len(args):
            interval = float(args[i + 1])
            i += 2
        elif args[i] == "--reply-max" and i + 1 < len(args):
            reply_max = int(args[i + 1])
            i += 2
        elif args[i] == "--output-file" and i + 1 < len(args):
            output_file = args[i + 1]
            i += 2
        else:
            print(f"Unknown argument: {args[i]}", file=sys.stderr)
            sys.exit(1)

    # Redirect stdout to file to avoid shell quoting/expansion issues
    # when the JSON output is consumed by downstream notification chains.
    if output_file:
        sys.stdout = open(output_file, "w")

    exit_code = wait_for_idle(pane_id, timeout=timeout, interval=interval, reply_max=reply_max)

    if output_file:
        sys.stdout.close()
        sys.stdout = sys.__stdout__

    sys.exit(exit_code)


def _cmd_web(args: list[str]) -> None:
    """aimux web [--host HOST] [--port PORT]"""
    host = "0.0.0.0"
    port = 21840

    i = 0
    while i < len(args):
        if args[i] == "--host" and i + 1 < len(args):
            host = args[i + 1]
            i += 2
        elif args[i] == "--port" and i + 1 < len(args):
            port = int(args[i + 1])
            i += 2
        else:
            print(f"Unknown argument: {args[i]}", file=sys.stderr)
            print("Usage: aimux web [--host 0.0.0.0] [--port 8080]", file=sys.stderr)
            sys.exit(1)

    from .web import create_app

    import uvicorn
    uvicorn.run(create_app(), host=host, port=port)
