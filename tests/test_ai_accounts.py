"""Tests for workspace account selection."""

import builtins
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import ai_accounts  # noqa: E402


class _Terminal(io.StringIO):
    def isatty(self):
        return True


class ProjectAccountTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name).resolve()
        self.config = self.root / "config"
        config_patch = mock.patch.object(ai_accounts, "config_dir", return_value=self.config)
        self.addCleanup(config_patch.stop)
        config_patch.start()
        self.project = self.root / "project"
        self.project.mkdir()
        self.profile = ai_accounts.Profile(
            "Lovely", self.root / "Claude Profile", self.root / "Codex Profile"
        )
        for provider in ai_accounts.PROVIDERS:
            self.profile.home(provider).mkdir()
        path = ai_accounts._profile_path("Lovely")
        path.parent.mkdir(parents=True)
        ai_accounts._write_private(
            path,
            json.dumps({
                "label": self.profile.label,
                "claude": str(self.profile.claude),
                "codex": str(self.profile.codex),
            }),
            replace=False,
        )

    def test_nested_directory_uses_binding(self):
        nested = self.project / "src"
        nested.mkdir()
        self.assertIsNone(ai_accounts.project_profile(nested))
        ai_accounts._save_binding(self.project, self.profile, replace=False)
        self.assertEqual(ai_accounts.project_profile(nested), self.profile)
        self.assertEqual(
            self.profile.session_options("claude"),
            {"projects_root": self.profile.claude / "projects"},
        )
        with self.assertRaisesRegex(ai_accounts.AccountError, "already exists"):
            ai_accounts._save_binding(self.project, self.profile, replace=False)

    def test_git_excludes_binding_and_linked_worktree_inherits_it(self):
        def git(*args):
            return subprocess.run(
                ["/usr/bin/git", "-C", str(self.project), *args],
                check=True, capture_output=True, text=True,
            )

        git("init")
        git("-c", "user.name=Test", "-c", "user.email=test@example.com",
            "commit", "--allow-empty", "-m", "Initial commit")
        worktree = self.root / "worktree"
        git("worktree", "add", "--detach", str(worktree))
        ai_accounts._save_binding(self.project, self.profile, replace=False)
        self.assertEqual(
            git("check-ignore", ai_accounts.BINDING).stdout.strip(),
            ai_accounts.BINDING,
        )
        self.assertEqual(ai_accounts.project_profile(worktree), self.profile)

    def test_invalid_binding_fails_closed(self):
        marker = self.project / ai_accounts.BINDING
        marker.write_text("Lovely\n")
        marker.chmod(0o644)
        with self.assertRaisesRegex(ai_accounts.AccountError, "owner-only"):
            ai_accounts.project_profile(self.project)
        marker.chmod(0o600)
        marker.write_text("Missing\n")
        with self.assertRaisesRegex(ai_accounts.AccountError, "is missing"):
            ai_accounts.project_profile(self.project)

    def test_tmux_applies_profile_inside_pane_and_removes_inherited_api_key(self):
        command = self.profile.tmux_command(
            "claude", ["/usr/bin/printenv", "CLAUDE_CONFIG_DIR"]
        )
        output = subprocess.check_output(
            command, env={**os.environ, "CLAUDE_CONFIG_DIR": "/some/other/account"},
            text=True,
        )
        self.assertEqual(output.strip(), str(self.profile.claude))
        command = self.profile.tmux_command(
            "claude", ["/usr/bin/printenv", "ANTHROPIC_API_KEY"]
        )
        result = subprocess.run(
            command, env={**os.environ, "ANTHROPIC_API_KEY": "inherited"},
            capture_output=True,
        )
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, b"")

    def _pick(self, answers, signed_in=lambda profile, provider: False):
        with mock.patch.object(sys.stdin, "isatty", return_value=True), \
             mock.patch.object(ai_accounts, "_signed_in", side_effect=signed_in), \
             mock.patch.object(ai_accounts, "_login", return_value=True) as login, \
             mock.patch.object(ai_accounts.shutil, "which", return_value="/bin/true"), \
             mock.patch.object(builtins, "input", side_effect=answers), \
             mock.patch.object(sys, "stderr", new=_Terminal()):
            return ai_accounts._choose_profile(self.project, "claude", False), login

    def test_picker_signs_in_unsigned_profile(self):
        profile, login = self._pick(["1", "n"])
        self.assertEqual(profile, self.profile)
        login.assert_called_once_with(self.profile, "claude")

    def test_picker_skips_sign_in_for_ready_profile(self):
        profile, login = self._pick(["Lovely"], signed_in=lambda p, provider: True)
        self.assertEqual(profile, self.profile)
        login.assert_not_called()

    def test_picker_adds_new_profile(self):
        profile, login = self._pick(["2", "bad name!", "lovely", "Work", "y"])
        self.assertEqual(profile.label, "Work")
        self.assertEqual(profile.claude, self.config / "accounts" / "work" / "claude")
        self.assertEqual(ai_accounts.load_profile("Work"), profile)
        self.assertEqual(
            [call.args for call in login.call_args_list],
            [(profile, "claude"), (profile, "codex")],
        )


if __name__ == "__main__":
    unittest.main()
