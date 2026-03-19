#!/usr/bin/env python3
"""AImux service manager.

Usage:
    ./service.py start                Start web service (background)
    ./service.py start --fg           Start in foreground
    ./service.py stop                 Stop web service
    ./service.py restart              Restart web service
    ./service.py status               Show service status

    ./service.py log                  View last 50 lines
    ./service.py log -n 100           View last 100 lines
    ./service.py log -f               Follow log output (tail -f)
    ./service.py log -f -g error      Follow + filter by pattern
    ./service.py log -g WARNING       Search log by keyword
    ./service.py log --clear          Clear log file
"""

import argparse
import os
import signal
import socket
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent
PID_FILE = Path("/tmp/aimux_web.pid")
LOG_FILE = Path("/tmp/aimux_web.log")
DEFAULT_PORT = 21840
_PROC_NAMES = ("uvicorn", "uv", "aimux")


def _get_lan_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "unknown"


def _write_pid(pid: int, port: int):
    entry = (
        f"{pid}"
        f"\tstarted={datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        f"\tport={port}"
        f"\tcwd={PROJECT_DIR}\n"
    )
    PID_FILE.write_text(entry)


def _read_pid() -> int | None:
    if not PID_FILE.exists():
        return None
    first = PID_FILE.read_text().strip().split("\t")[0].strip()
    return int(first) if first.isdigit() else None


def _is_pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    result = subprocess.run(
        ["ps", "-p", str(pid), "-o", "command="],
        capture_output=True, text=True,
    )
    return any(name in result.stdout for name in _PROC_NAMES)


def _is_running() -> int | None:
    pid = _read_pid()
    if pid is None:
        return None
    if _is_pid_alive(pid):
        return pid
    PID_FILE.unlink(missing_ok=True)
    return None


def _wait_for_port(pid: int, port: int, timeout: int = 15) -> bool:
    for _ in range(timeout):
        time.sleep(1)
        if not _is_pid_alive(pid):
            return False
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return True
    return False


def _kill_port_holders(port: int):
    result = subprocess.run(
        ["lsof", f"-ti:{port}"], capture_output=True, text=True,
    )
    for p in result.stdout.strip().split():
        if p.isdigit():
            try:
                os.kill(int(p), signal.SIGKILL)
            except OSError:
                pass


# --- Commands ---

def cmd_start(port: int, *, foreground: bool = False):
    if foreground:
        cmd = ["uv", "run", "aimux", "web", "--host", "0.0.0.0", "--port", str(port)]
        print(f"Starting AImux web (foreground) on :{port}")
        os.execvp("uv", cmd)

    pid = _is_running()
    if pid:
        print(f"Already running  PID {pid}  :{port}")
        return

    cmd = ["uv", "run", "aimux", "web", "--host", "0.0.0.0", "--port", str(port)]
    log = open(LOG_FILE, "a")
    proc = subprocess.Popen(cmd, cwd=PROJECT_DIR, stdout=log, stderr=log)
    _write_pid(proc.pid, port)

    if _wait_for_port(proc.pid, port):
        lan_ip = _get_lan_ip()
        print(f"AImux web       :{port}  PID {proc.pid}")
        print(f"  http://localhost:{port}  |  http://{lan_ip}:{port}")
        print(f"  Log: {LOG_FILE}")
    else:
        tail = subprocess.run(
            ["tail", "-5", str(LOG_FILE)], capture_output=True, text=True,
        )
        print(f"Failed to start (PID {proc.pid})")
        if tail.stdout.strip():
            print(tail.stdout.strip())


def cmd_stop(port: int):
    pid = _is_running()
    if not pid:
        print("Not running")
        return

    os.kill(pid, signal.SIGTERM)
    for _ in range(10):
        if not _is_pid_alive(pid):
            break
        time.sleep(0.5)
    else:
        os.kill(pid, signal.SIGKILL)

    _kill_port_holders(port)
    PID_FILE.unlink(missing_ok=True)
    print(f"Stopped (was PID {pid})")


def cmd_status():
    pid = _is_running()
    if not pid:
        print("AImux web  stopped")
        return

    # Read port from PID file
    port = DEFAULT_PORT
    if PID_FILE.exists():
        for part in PID_FILE.read_text().split("\t"):
            if part.startswith("port="):
                port = int(part.split("=", 1)[1])
            if part.startswith("started="):
                started = part.split("=", 1)[1].strip()

    lan_ip = _get_lan_ip()
    print(f"AImux web       :{port}  PID {pid}")
    if started:
        print(f"  Started: {started}")
    print(f"  http://localhost:{port}  |  http://{lan_ip}:{port}")
    print(f"  Log: {LOG_FILE}")


def cmd_log(lines: int = 50, *, follow: bool = False, grep: str | None = None, clear: bool = False):
    if clear:
        if LOG_FILE.exists():
            LOG_FILE.write_text("")
            print(f"Cleared {LOG_FILE}")
        else:
            print("No log file to clear")
        return

    if not LOG_FILE.exists():
        print(f"Log file not found: {LOG_FILE}")
        return

    if follow:
        cmd = ["tail", "-f", f"-n{lines}", str(LOG_FILE)]
        if grep:
            tail = subprocess.Popen(cmd, stdout=subprocess.PIPE)
            try:
                subprocess.run(["grep", "--line-buffered", grep], stdin=tail.stdout)
            except KeyboardInterrupt:
                tail.terminate()
        else:
            try:
                subprocess.run(cmd)
            except KeyboardInterrupt:
                pass
    else:
        cmd = ["tail", f"-n{lines}", str(LOG_FILE)]
        if grep:
            tail = subprocess.Popen(cmd, stdout=subprocess.PIPE)
            subprocess.run(["grep", grep], stdin=tail.stdout)
        else:
            subprocess.run(cmd)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="service.py",
        description="AImux service manager",
    )
    parser.add_argument(
        "-p", "--port", type=int, default=DEFAULT_PORT,
        help=f"Port (default: {DEFAULT_PORT})",
    )

    sub = parser.add_subparsers(dest="action")

    cmd_start_p = sub.add_parser("start", help="Start web service")
    cmd_start_p.add_argument("--fg", action="store_true", help="Run in foreground")

    sub.add_parser("stop", help="Stop web service")
    sub.add_parser("restart", help="Restart web service")
    sub.add_parser("status", help="Show service status")

    cmd_log_p = sub.add_parser("log", help="View service logs")
    cmd_log_p.add_argument("-n", "--lines", type=int, default=50, help="Number of lines")
    cmd_log_p.add_argument("-f", "--follow", action="store_true", help="Follow log output")
    cmd_log_p.add_argument("-g", "--grep", type=str, help="Filter lines by pattern")
    cmd_log_p.add_argument("--clear", action="store_true", help="Clear log file")

    return parser


def main():
    parser = _build_parser()
    args = parser.parse_args()

    match args.action:
        case "start":
            cmd_start(args.port, foreground=args.fg)
        case "stop":
            cmd_stop(args.port)
        case "restart":
            cmd_stop(args.port)
            cmd_start(args.port)
        case "status":
            cmd_status()
        case "log":
            cmd_log(args.lines, follow=args.follow, grep=args.grep, clear=args.clear)
        case _:
            parser.print_help()


if __name__ == "__main__":
    main()
