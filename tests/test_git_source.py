"""S7 end-to-end: git skill source through the full flow.

groups(read_group) -> cache (identity -> shallow clone -> head sha ->
copy) -> mount -> state (use_groups pre-flight + lock resolved_sha).
Real local git repositories only, no network. The source repo directory
name contains a space on purpose: exercises path handling end to end.
"""

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from sg import cache, errors, git_util, groups, lockfile, state, util


def _git(*args, cwd=None):
    """Run git synchronously for test fixtures."""
    return subprocess.run(
        ["git", *args], capture_output=True, text=True, cwd=cwd, timeout=120
    )


class GitSourceTestCase(unittest.TestCase):
    """Builds a real local source repo; skip entirely when git is missing."""

    def setUp(self):
        if shutil.which("git") is None:
            self.skipTest("git not available")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self._env = mock.patch.dict(os.environ, {"SG_HOME": str(self.root / "home")})
        self._env.start()
        self.addCleanup(self._env.stop)

        # Repo directory name contains a space: exercises path handling.
        self.src_repo = self.root / "src repo"
        (self.src_repo / "skills" / "python").mkdir(parents=True)
        (self.src_repo / "skills" / "python" / "SKILL.md").write_bytes(
            b"# python\nunicode \xe4\xb8\xad\xe6\x96\x87 ok\n"
        )
        (self.src_repo / "skills" / "python" / "tool.py").write_bytes(b"x=1\n")
        (self.src_repo / "README.md").write_text("readme\n", encoding="utf-8")
        # Never convert line endings: byte-identical checkout on Windows.
        (self.src_repo / ".gitattributes").write_text("* -text\n", encoding="utf-8")
        _git("init", "-b", "main", cwd=self.src_repo)
        _git("config", "user.name", "t", cwd=self.src_repo)
        _git("config", "user.email", "t@t", cwd=self.src_repo)
        _git("add", ".", cwd=self.src_repo)
        proc = _git("commit", "-m", "init", cwd=self.src_repo)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.head = _git("rev-parse", "HEAD", cwd=self.src_repo).stdout.strip()
        self.assertEqual(len(self.head), 40)

    def git_source(self, **overrides):
        source = {"type": "git", "repo": str(self.src_repo)}
        source.update(overrides)
        return source

    def create_group(self, name, skills):
        groups.create_group(name)
        for skill_id, source in skills:
            groups.add_skill(name, skill_id, source)
        return groups.read_group(name)

    def track_unuse(self, names):
        """Un-mount before the temp dir is removed (junction safety on Windows)."""
        self.addCleanup(state.unuse_groups, self.root, names)

    def skills_dir(self):
        return self.root / ".agents" / "skills"


class TestGitSourceEndToEnd(GitSourceTestCase):
    def test_full_flow_mounts_byte_identical_and_pins_head_sha(self):
        source = self.git_source(path="skills/python", rev="main")
        self.create_group("python", [("python-skill", source)])
        state.init_project(self.root)
        state.use_groups(self.root, ["python"])
        self.track_unuse(["python"])

        mounted = self.skills_dir() / "python-skill"
        self.assertTrue((mounted / "SKILL.md").is_file())
        self.assertEqual(
            (mounted / "SKILL.md").read_bytes(),
            (self.src_repo / "skills" / "python" / "SKILL.md").read_bytes(),
        )

        expected_cache = cache.skill_cache_dir(source, "python-skill")
        self.assertTrue((expected_cache / ".repo").is_dir())

        entry = lockfile.read_lock(self.root)["python-skill"]
        self.assertEqual(entry["resolved_sha"], self.head)
        self.assertEqual(len(entry["resolved_sha"]), 40)
        self.assertTrue(set(entry["resolved_sha"]) <= set("0123456789abcdef"))
        self.assertEqual(Path(entry["cache_dir"]), expected_cache)

    def test_second_use_does_not_reclone(self):
        source = self.git_source(path="skills/python", rev="main")
        self.create_group("python", [("python-skill", source)])
        state.init_project(self.root)
        state.use_groups(self.root, ["python"])
        self.track_unuse(["python"])

        with mock.patch.object(
            cache.git_util, "shallow_clone", wraps=git_util.shallow_clone
        ) as clone:
            state.use_groups(self.root, ["python"])
            clone.assert_not_called()

    def test_git_source_without_path_mounts_repo_root(self):
        source = self.git_source(rev="main")
        self.create_group("python", [("root-skill", source)])
        state.init_project(self.root)
        state.use_groups(self.root, ["python"])
        self.track_unuse(["python"])

        mounted = self.skills_dir() / "root-skill"
        self.assertTrue((mounted / "README.md").is_file())
        self.assertEqual(
            (mounted / "README.md").read_bytes(),
            (self.src_repo / "README.md").read_bytes(),
        )
        entry = lockfile.read_lock(self.root)["root-skill"]
        self.assertEqual(entry["resolved_sha"], self.head)

    def test_missing_subpath_raises_user_error_and_mounts_nothing(self):
        source = self.git_source(path="nope/missing", rev="main")
        self.create_group("python", [("bad-skill", source)])
        state.init_project(self.root)

        with self.assertRaises(errors.UserError):
            state.use_groups(self.root, ["python"])

        self.assertFalse((self.skills_dir() / "bad-skill").exists())
        self.assertNotIn("bad-skill", lockfile.read_lock(self.root))
        decl = util.read_json(lockfile.sg_json(self.root))
        self.assertEqual(decl["groups"], [])

    def test_commit_sha_rev_resolves_to_that_sha(self):
        source = self.git_source(path="skills/python", rev=self.head)
        self.create_group("python", [("python-skill", source)])
        state.init_project(self.root)
        state.use_groups(self.root, ["python"])
        self.track_unuse(["python"])

        entry = lockfile.read_lock(self.root)["python-skill"]
        self.assertEqual(entry["resolved_sha"], self.head)
        self.assertEqual(
            (self.skills_dir() / "python-skill" / "SKILL.md").read_bytes(),
            (self.src_repo / "skills" / "python" / "SKILL.md").read_bytes(),
        )


class TestGitSourceUnits(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self._env = mock.patch.dict(
            os.environ, {"SG_HOME": str(Path(self.tmp.name) / "home")}
        )
        self._env.start()
        self.addCleanup(self._env.stop)

    def test_clone_url_owner_repo_becomes_github_url(self):
        self.assertEqual(
            git_util.clone_url("owner/repo"), "https://github.com/owner/repo.git"
        )

    def test_same_git_source_different_rev_different_cache_key(self):
        base = {"type": "git", "repo": "owner/repo", "path": "skills/python"}
        self.assertNotEqual(
            cache.cache_key({**base, "rev": "main"}),
            cache.cache_key({**base, "rev": "dev"}),
        )


if __name__ == "__main__":
    unittest.main()
