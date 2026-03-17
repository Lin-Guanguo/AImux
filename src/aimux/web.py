"""AImux Web UI — lightweight tmux control panel."""

import asyncio
import json
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

from .tmux import list_panes, capture_pane, capture_pane_ansi, get_pane_size, send_keys
from .session_mapper import detect_agent_type
from .watcher import detect_screen_state

TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def _do_capture(pane_id: str, lines: int, ansi: bool) -> str:
    if ansi:
        return capture_pane_ansi(pane_id)
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

    return app
