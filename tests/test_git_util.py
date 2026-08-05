"""Tests for sg.git_util: URL mapping, sha detection, shallow cloning, head_sha."""

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from sg import errors, git_util


def _git(*args, cwd=None):
    """Run git synchronously for test fixtures."""
    return subprocess.run(
        ["git", *args], capture_output=True, text=True, cwd=cwd, timeout=120
    )


class TestCloneUrl(unittest.TestCase):
    def test_owner_repo_becomes_github_url(self):
        self.assertEqual(
            git_util.clone_url("owner/repo"), "https://github.com/owner/repo.git"
        )

    def test_owner_with_dot_becomes_github_url(self):
        self.assertEqual(
            git_util.clone_url("octo-org/some.repo"),
            "https://github.com/octo-org/some.repo.git",
        )

    def test_full_url_returned_unchanged(self):
        self.assertEqual(
            git_util.clone_url("https://example.com/x.git"), "https://example.com/x.git"
        )

    def test_local_absolute_paths_returned_unchanged(self):
        for path in (
            r"C:\Users\me\repo",
            "C:/Users/me/repo",
            r"D:\opencode\skills test\skill-groups",
            "/home/user/repo",
        ):
            self.assertEqual(git_util.clone_url(path), path)


class TestIsCommitSha(unittest.TestCase):
    def test_40_hex_is_true(self):
        for sha in ("a" * 40, "0" * 40, "deadbeef" * 5):
            self.assertTrue(git_util.is_commit_sha(sha))

    def test_wrong_lengths_are_false(self):
        for length in (36, 39, 41):
            self.assertFalse(git_util.is_commit_sha("a" * length))

    def test_non_hex_characters_are_false(self):
        self.assertFalse(git_util.is_commit_sha("a" * 39 + "g"))
        self.assertFalse(git_util.is_commit_sha("a" * 20 + " " + "a" * 19))
        self.assertFalse(git_util.is_commit_sha(""))

    def test_branch_names_are_false(self):
        self.assertFalse(git_util.is_commit_sha("main"))
        self.assertFalse(git_util.is_commit_sha("feature/x"))


class GitTestCase(unittest.TestCase):
    """Builds a real local git repo with one commit; skip if git is missing."""

    def setUp(self):
        if shutil.which("git") is None:
            self.skipTest("git not available")
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        # Directory name contains a space on purpose: exercises path handling.
        self.src = self.root / "src repo"
        self.src.mkdir()
        _git("init", "-b", "main", cwd=self.src)
        _git("config", "user.name", "t", cwd=self.src)
        _git("config", "user.email", "t@t", cwd=self.src)
        (self.src / "SKILL.md").write_text("# demo\n", encoding="utf-8")
        _git("add", ".", cwd=self.src)
        proc = _git("commit", "-m", "init", cwd=self.src)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.head = _git("rev-parse", "HEAD", cwd=self.src).stdout.strip()
        self.file_url = self.src.as_uri()

    def tearDown(self):
        self._tmp.cleanup()


class TestShallowClone(GitTestCase):
    def test_branch_name_clones_successfully(self):
        dest = self.root / "branch"
        git_util.shallow_clone(self.file_url, dest, rev="main")
        self.assertTrue((dest / "SKILL.md").is_file())
        self.assertTrue((dest / ".git" / "shallow").is_file())

    def test_no_rev_clones_default_branch(self):
        dest = self.root / "default"
        git_util.shallow_clone(self.file_url, dest)
        self.assertTrue((dest / "SKILL.md").is_file())
        self.assertTrue((dest / ".git" / "shallow").is_file())

    def test_local_path_url_clones_successfully(self):
        dest = self.root / "local"
        git_util.shallow_clone(str(self.src), dest)
        self.assertTrue((dest / "SKILL.md").is_file())

    def test_commit_sha_clones_successfully(self):
        dest = self.root / "sha"
        git_util.shallow_clone(self.file_url, dest, rev=self.head)
        self.assertTrue((dest / "SKILL.md").is_file())
        self.assertEqual(git_util.head_sha(dest), self.head)

    def test_nonexistent_repo_raises_user_error_with_stderr(self):
        url = (self.root / "missing").as_uri()
        with self.assertRaises(errors.UserError) as cm:
            git_util.shallow_clone(url, self.root / "dest")
        self.assertIn("fatal", str(cm.exception))

    def test_nonempty_existing_dest_raises_env_error(self):
        dest = self.root / "dest"
        dest.mkdir()
        (dest / "junk.txt").write_text("junk", encoding="utf-8")
        with self.assertRaises(errors.EnvError) as cm:
            git_util.shallow_clone(self.file_url, dest)
        self.assertIn("cache dir not empty", str(cm.exception))

    def test_empty_existing_dest_is_reused(self):
        dest = self.root / "dest"
        dest.mkdir()
        git_util.shallow_clone(self.file_url, dest)
        self.assertTrue((dest / "SKILL.md").is_file())

    def test_git_unavailable_raises_env_error(self):
        with mock.patch("sg.git_util.git_available", return_value=False):
            with self.assertRaises(errors.EnvError):
                git_util.shallow_clone(self.file_url, self.root / "dest")


class TestHeadSha(GitTestCase):
    def test_valid_repo_returns_40_hex(self):
        dest = self.root / "clone"
        git_util.shallow_clone(self.file_url, dest)
        sha = git_util.head_sha(dest)
        self.assertEqual(len(sha), 40)
        self.assertTrue(set(sha) <= set("0123456789abcdef"))
        self.assertEqual(sha, self.head)

    def test_non_repo_directory_raises_env_error(self):
        empty = self.root / "empty"
        empty.mkdir()
        with self.assertRaises(errors.EnvError):
            git_util.head_sha(empty)

    def test_git_unavailable_raises_env_error(self):
        with mock.patch("sg.git_util.git_available", return_value=False):
            with self.assertRaises(errors.EnvError):
                git_util.head_sha(self.root / "nope")


if __name__ == "__main__":
    unittest.main()
