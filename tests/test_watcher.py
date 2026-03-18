"""Tests for watcher.wait_for_idle race condition fix."""

import json
import sys
import time
from unittest.mock import MagicMock, patch

import pytest

from aimux.watcher import (
    EXIT_IDLE,
    EXIT_NO_PANE,
    EXIT_TIMEOUT,
    EXIT_WAITING_INPUT,
    STATE_GENERATING,
    STATE_IDLE,
    STATE_UNKNOWN,
    STATE_WAITING_INPUT,
    wait_for_idle,
)


@pytest.fixture
def mock_pane():
    """Standard mock pane for tests."""
    return {"pane_id": "%0", "cwd": "/tmp/test", "command": "claude"}


def _run_wait(pane, screen_states, jsonl_sizes, jsonl_last_assistant, timeout=30):
    """Helper: run wait_for_idle with mocked dependencies.

    screen_states: list of states returned by detect_screen_state on each poll
    jsonl_sizes: list of sizes returned by _get_jsonl_size on each call
    jsonl_last_assistant: list of booleans for jsonl_last_is_assistant
    """
    screen_iter = iter(screen_states)
    size_iter = iter(jsonl_sizes)
    assistant_iter = iter(jsonl_last_assistant)

    with (
        patch("aimux.watcher.find_pane", return_value=pane),
        patch("aimux.watcher.detect_agent_type", return_value="claude"),
        patch("aimux.watcher.detect_screen_state", side_effect=lambda _: next(screen_iter, STATE_UNKNOWN)),
        patch("aimux.watcher._get_jsonl_size", side_effect=lambda *a: next(size_iter, 0)),
        patch("aimux.watcher.jsonl_last_is_assistant", side_effect=lambda *a: next(assistant_iter, False)),
        patch("aimux.watcher.get_last_reply", return_value="test reply"),
        patch("time.sleep"),
        patch("sys.stdout", new_callable=lambda: MagicMock(spec=sys.stdout)),
    ):
        return wait_for_idle("%0", timeout=timeout, interval=2)


class TestStabilityCheck:
    """The core fix: require JSONL stability before declaring idle."""

    def test_single_idle_check_not_enough(self, mock_pane):
        """One idle check should NOT exit — need 2 consecutive stable checks."""
        # Poll 0 (init size call): size=100
        # Poll 1: screen=idle, size=200 (grew, stable vs prev=100? no, prev starts at 100)
        #   Actually: initial=100, prev starts at initial=100
        #   Poll 1 size call: 200. grew=True, stable=(200==100)=False → consecutive=0
        # Poll 2: screen=idle, size=200. grew=True, stable=(200==200)=True, assistant=True → consecutive=1
        # Poll 3: screen=idle, size=200. grew=True, stable=(200==200)=True, assistant=True → consecutive=2 → EXIT
        result = _run_wait(
            mock_pane,
            screen_states=[STATE_IDLE, STATE_IDLE, STATE_IDLE],
            jsonl_sizes=[100, 200, 200, 200],  # init + 3 polls
            jsonl_last_assistant=[True, True, True],
        )
        assert result == EXIT_IDLE

    def test_racing_tool_call_not_false_positive(self, mock_pane):
        """Simulate Claude Code between tool calls: screen flickers idle briefly."""
        # init size: 100
        # Poll 1: screen=idle, size=200 (grew, not stable vs 100) → consec=0
        # Poll 2: screen=generating (tool running) → consec=0
        # Poll 3: screen=idle, size=500 (grew, not stable vs prev ~500?)
        # Poll 4: screen=idle, size=500 (stable!) → consec=1
        # Poll 5: screen=idle, size=500 (stable!) → consec=2 → EXIT
        result = _run_wait(
            mock_pane,
            screen_states=[STATE_IDLE, STATE_GENERATING, STATE_IDLE, STATE_IDLE, STATE_IDLE],
            jsonl_sizes=[100, 200, 300, 500, 500, 500],  # init + 5 polls
            jsonl_last_assistant=[True, True, True, True, True],
        )
        assert result == EXIT_IDLE

    def test_jsonl_still_growing_resets_consecutive(self, mock_pane):
        """If JSONL keeps growing between checks, don't declare idle."""
        # init: 100
        # Poll 1: idle, size=200 (not stable) → 0
        # Poll 2: idle, size=300 (not stable) → 0
        # Poll 3: idle, size=400 (not stable) → 0
        # → timeout
        result = _run_wait(
            mock_pane,
            screen_states=[STATE_IDLE, STATE_IDLE, STATE_IDLE],
            jsonl_sizes=[100, 200, 300, 400],
            jsonl_last_assistant=[True, True, True],
            timeout=0.01,  # force timeout quickly
        )
        assert result == EXIT_TIMEOUT


class TestBasicBehavior:
    """Existing behavior should be preserved."""

    def test_pane_not_found(self):
        with (
            patch("aimux.watcher.find_pane", return_value=None),
            patch("sys.stdout", new_callable=lambda: MagicMock(spec=sys.stdout)),
        ):
            result = wait_for_idle("%99")
            assert result == EXIT_NO_PANE

    def test_waiting_input_returns_immediately(self, mock_pane):
        result = _run_wait(
            mock_pane,
            screen_states=[STATE_WAITING_INPUT],
            jsonl_sizes=[100],
            jsonl_last_assistant=[],
        )
        assert result == EXIT_WAITING_INPUT

    def test_jsonl_not_grown_stays_waiting(self, mock_pane):
        """If JSONL hasn't grown at all, task hasn't started — don't exit."""
        # init: 100, all subsequent: 100 (no growth)
        result = _run_wait(
            mock_pane,
            screen_states=[STATE_IDLE, STATE_IDLE, STATE_IDLE],
            jsonl_sizes=[100, 100, 100, 100],
            jsonl_last_assistant=[True, True, True],
            timeout=0.01,
        )
        assert result == EXIT_TIMEOUT

    def test_last_not_assistant_stays_waiting(self, mock_pane):
        """If JSONL last entry is user (not assistant), agent is still processing."""
        # init: 100
        # Poll 1: idle, size=200 (grew, not stable) → 0
        # Poll 2: idle, size=200 (grew, stable, but NOT assistant) → 0
        result = _run_wait(
            mock_pane,
            screen_states=[STATE_IDLE, STATE_IDLE],
            jsonl_sizes=[100, 200, 200],
            jsonl_last_assistant=[False, False],
            timeout=0.01,
        )
        assert result == EXIT_TIMEOUT
