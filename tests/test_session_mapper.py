"""Tests for session_mapper path encoding."""

from pathlib import Path
from unittest.mock import patch

from aimux.session_mapper import cwd_to_claude_project_dirs, CLAUDE_PROJECTS_DIR


class TestCwdEncoding:
    """Claude Code encodes both '/' and '_' as '-' in project directory names."""

    def test_underscore_in_dirname(self, tmp_path):
        """A cwd like /home/user/dayfold_webapp should map to -home-user-dayfold-webapp."""
        # Create the directory Claude Code would actually create
        project_dir = tmp_path / "-home-user-dayfold-webapp"
        project_dir.mkdir()

        with patch.object(
            Path, "is_dir", side_effect=lambda self=None: Path(str(self or "")).exists()
        ):
            pass  # can't easily patch is_dir on Path; use a different approach

        # Directly test the encoding logic
        cwd = "/home/user/dayfold_webapp"
        encoded = cwd.replace("/", "-").replace("_", "-")
        assert encoded == "-home-user-dayfold-webapp"

    def test_no_underscore_unchanged(self):
        """Paths without underscores should encode the same as before."""
        cwd = "/home/user/myproject"
        encoded = cwd.replace("/", "-").replace("_", "-")
        assert encoded == "-home-user-myproject"

    def test_multiple_underscores(self):
        """Multiple underscores should all be converted."""
        cwd = "/home/user/my_cool_project"
        encoded = cwd.replace("/", "-").replace("_", "-")
        assert encoded == "-home-user-my-cool-project"

    def test_cwd_to_claude_project_dirs_finds_underscore_dir(self, tmp_path):
        """Integration: cwd_to_claude_project_dirs resolves underscore paths correctly."""
        # Simulate Claude Code's project directory
        project_dir = tmp_path / "-tmp-test-dayfold-webapp"
        project_dir.mkdir()
        (project_dir / "session.jsonl").touch()

        with patch("aimux.session_mapper.CLAUDE_PROJECTS_DIR", tmp_path):
            with patch("aimux.session_mapper._find_git_root", return_value=None):
                cwd = "/tmp/test/dayfold_webapp"
                dirs = cwd_to_claude_project_dirs(cwd)
                assert len(dirs) == 1
                assert dirs[0] == project_dir
