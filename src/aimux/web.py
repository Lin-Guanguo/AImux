"""AImux Web UI — lightweight tmux control panel."""

import asyncio
import json
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

from .tmux import list_panes, capture_pane, capture_pane_ansi, get_pane_size, send_keys
from .session_mapper import (
    detect_agent_type,
    find_claude_session,
    find_codex_session,
    parse_tail_jsonl,
)
from .watcher import detect_screen_state

TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def _do_capture(pane_id: str, lines: int, ansi: bool) -> str:
    if ansi:
        # xterm.js needs \r\n; capture-pane outputs \n only
        content = capture_pane_ansi(pane_id)
        return content.replace("\n", "\r\n")
    return capture_pane(pane_id, lines)


def create_app() -> FastAPI:
    app = FastAPI(title="AImux", docs_url=None, redoc_url=None)

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request):
        return templates.TemplateResponse(request, "index.html")

    @app.get("/api/tree")
    async def tree():
        panes = await asyncio.to_thread(list_panes)
        sessions: dict[str, dict] = {}
        for pane in panes:
            sess_name = pane["session"]
            if sess_name not in sessions:
                sessions[sess_name] = {"session": sess_name, "windows": {}}
            win_key = pane["window"]
            if win_key not in sessions[sess_name]["windows"]:
                sessions[sess_name]["windows"][win_key] = {
                    "index": win_key,
                    "panes": [],
                }
            agent = detect_agent_type(pane)
            state = detect_screen_state(pane["pane_id"])
            sessions[sess_name]["windows"][win_key]["panes"].append({
                "id": pane["pane_id"],
                "index": pane["pane_index"],
                "command": pane["command"],
                "cwd": pane["cwd"],
                "agent": agent,
                "state": state,
                "width": pane["width"],
                "height": pane["height"],
            })
        result = []
        for sess in sorted(sessions.values(), key=lambda s: s["session"]):
            windows = sorted(sess["windows"].values(), key=lambda w: w["index"])
            for win in windows:
                win["panes"].sort(key=lambda p: p["index"])
            result.append({
                "session": sess["session"],
                "windows": windows,
            })
        return result

    @app.get("/api/pane/{pane_id}/capture")
    async def pane_capture(pane_id: str, lines: int = 50, ansi: int = 0):
        pane_id_fmt = f"%{pane_id}"
        content = await asyncio.to_thread(_do_capture, pane_id_fmt, lines, bool(ansi))
        state = await asyncio.to_thread(detect_screen_state, pane_id_fmt)
        resp = {"content": content, "state": state}
        if ansi:
            w, h = await asyncio.to_thread(get_pane_size, pane_id_fmt)
            resp["cols"] = w
            resp["rows"] = h
        return resp

    @app.get("/api/pane/{pane_id}/stream")
    async def pane_stream(pane_id: str, lines: int = 50, ansi: int = 0):
        pane_id_fmt = f"%{pane_id}"
        use_ansi = bool(ansi)

        async def event_generator():
            prev = ""
            while True:
                try:
                    content = await asyncio.to_thread(_do_capture, pane_id_fmt, lines, use_ansi)
                    state = await asyncio.to_thread(detect_screen_state, pane_id_fmt)
                except RuntimeError:
                    data = json.dumps({"error": "pane not found"})
                    yield f"data: {data}\n\n"
                    return
                if content != prev:
                    prev = content
                    payload = {"content": content, "state": state}
                    if use_ansi:
                        try:
                            w, h = get_pane_size(pane_id_fmt)
                            payload["cols"] = w
                            payload["rows"] = h
                        except RuntimeError:
                            pass
                    data = json.dumps(payload)
                    yield f"data: {data}\n\n"
                await asyncio.sleep(0.5)

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    @app.post("/api/pane/{pane_id}/keys")
    async def pane_send_keys(pane_id: str, request: Request):
        body = await request.json()
        keys = body.get("keys", [])
        if not keys:
            return {"error": "no keys provided"}
        pane_id_fmt = f"%{pane_id}"
        await asyncio.to_thread(send_keys, pane_id_fmt, *keys)
        return {"ok": True}

    @app.get("/api/pane/{pane_id}/jsonl")
    async def pane_jsonl(pane_id: str, limit: int = 30):
        """Read recent JSONL entries for a pane, return conversation items."""
        pane_id_fmt = f"%{pane_id}"
        panes = await asyncio.to_thread(list_panes)
        pane_info = next((p for p in panes if p["pane_id"] == pane_id_fmt), None)
        if not pane_info:
            return {"error": "pane not found", "messages": []}

        agent = detect_agent_type(pane_info)
        cwd = pane_info["cwd"]

        def _read_jsonl():
            if agent in ("claude", "unknown-node", "shell"):
                jsonl_path = find_claude_session(cwd)
                return jsonl_path, "claude"
            elif agent == "codex":
                jsonl_path = find_codex_session(cwd)
                return jsonl_path, "codex"
            return None, agent

        jsonl_path, effective_agent = await asyncio.to_thread(_read_jsonl)
        if not jsonl_path:
            return {"error": "no session found", "messages": [], "agent": agent}

        raw = await asyncio.to_thread(parse_tail_jsonl, jsonl_path, 256 * 1024)
        messages = _extract_conversation(raw, effective_agent, limit)
        return {
            "agent": effective_agent,
            "session_file": jsonl_path.name,
            "messages": messages,
        }

    return app


def _extract_conversation(entries: list[dict], agent: str, limit: int) -> list[dict]:
    """Extract human-readable conversation items from JSONL entries."""
    items: list[dict] = []

    for entry in entries:
        if agent == "claude":
            item = _extract_claude_entry(entry)
        elif agent == "codex":
            item = _extract_codex_entry(entry)
        else:
            continue
        if item:
            items.append(item)

    # Return last N items
    return items[-limit:]


def _extract_claude_entry(entry: dict) -> dict | None:
    """Extract a displayable item from a Claude Code JSONL entry."""
    entry_type = entry.get("type")

    if entry_type == "user" and not entry.get("isCompactSummary"):
        content = entry.get("message", {}).get("content", "")
        text = _claude_content_to_text(content)
        if text:
            return {"role": "user", "content": text}

    elif entry_type == "assistant":
        message = entry.get("message", {})
        content = message.get("content", [])
        if isinstance(content, str):
            return {"role": "assistant", "content": content} if content.strip() else None

        texts: list[str] = []
        tool_uses: list[dict] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text":
                t = block.get("text", "").strip()
                if t:
                    texts.append(t)
            elif block.get("type") == "tool_use":
                tool_uses.append({
                    "name": block.get("name", "unknown"),
                    "input_preview": _truncate(json.dumps(block.get("input", {}), ensure_ascii=False), 200),
                })
            elif block.get("type") == "tool_result":
                # skip tool results in display
                pass

        if not texts and not tool_uses:
            return None
        result: dict = {"role": "assistant"}
        if texts:
            result["content"] = "\n\n".join(texts)
        if tool_uses:
            result["tools"] = tool_uses
        return result

    elif entry_type == "result":
        # Final result with cost info
        cost = entry.get("costUSD")
        duration = entry.get("durationMs")
        if cost is not None or duration is not None:
            return {
                "role": "system",
                "content": f"Cost: ${cost:.4f}" + (f"  Duration: {duration/1000:.1f}s" if duration else ""),
            }

    return None


def _extract_codex_entry(entry: dict) -> dict | None:
    """Extract a displayable item from a Codex JSONL entry."""
    if entry.get("type") != "event_msg":
        return None
    payload = entry.get("payload", {})
    ptype = payload.get("type")

    if ptype == "user_message":
        text = payload.get("message", "").strip()
        if text:
            return {"role": "user", "content": text}
    elif ptype == "assistant_message":
        text = payload.get("message", "").strip()
        if text:
            return {"role": "assistant", "content": text}
    return None


def _claude_content_to_text(content) -> str:
    """Convert Claude content (string or list of blocks) to plain text."""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                t = block.get("text", "").strip()
                if t:
                    parts.append(t)
        return "\n".join(parts)
    return ""


def _truncate(s: str, max_len: int) -> str:
    return s if len(s) <= max_len else s[:max_len - 3] + "..."
