def main() -> None:
    """CLI entry point: show all pane → session mappings."""
    from .session_mapper import map_all_panes

    results = map_all_panes()
    if not results:
        print("No tmux panes found.")
        return

    # Group by session/window for readability
    for r in results:
        pane = r["pane"]
        agent = r["agent"]
        session = r["session"]
        project = pane["cwd"].rsplit("/", 1)[-1] if "/" in pane["cwd"] else pane["cwd"]

        # Pane header
        agent_label = {
            "claude": "Claude Code",
            "codex": "Codex",
            "gemini": "Gemini",
            "shell": "shell",
            "other": pane["command"],
            "unknown-node": "node(?)",
        }.get(agent, agent)

        print(f"{pane['pane_id']}  [{agent_label}]  {project}  ({pane['cwd']})")

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
