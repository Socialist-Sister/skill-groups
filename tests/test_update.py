"""S9: sg update — refresh git-source skills to their latest upstream.

Real local git repos only, no network. Covers: no-op update, upstream
movement (sha change, content refreshed, lock rewritten), local sources
left alone, pinned-sha revs left alone, stale skills skipped, and a fetch
failure that leaves mounts and lock untouched.
"""

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from sg import errors, git_util, groups, lockfile, state


def _git(*args, cwd=None):
    """Run git synchronously for test fixtures."""
    return subprocess.run(
        ["git", *args], capture_output=True, text=True, cwd=cwd, timeout=120
    )


class UpdateTestCase(unittest.TestCase):
    def setUp(self):
        if shutil.which("git") is None:
            self.skipTest("git not available")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self._env = mock.patch.dict(os.environ, {"SG_HOME": str(self.root / "home")})
        self._env.start()
        self.addCleanup(self._env.stop)

        self.src_repo = self.root / "src repo"
        (self.src_repo / "skills" / "python").mkdir(parents=True)
        (self.src_repo / "skills" / "python" / "SKILL.md").write_bytes(b"# v1\n")
        # Never convert line endings: byte-identical checkout on Windows.
        (self.src_repo / ".gitattributes").write_text("* -text\n", encoding="utf-8")
        _git("init", "-b", "main", cwd=self.src_repo)
        _git("config", "user.name", "t", cwd=self.src_repo)
        _git("config", "user.email", "t@t", cwd=self.src_repo)
        _git("add", ".", cwd=self.src_repo)
        self._commit("init")
        self.head1 = _git("rev-parse", "HEAD", cwd=self.src_repo).stdout.strip()
        self.assertEqual(len(self.head1), 40)

    def _commit(self, message):
        proc = _git("commit", "-m", message, cwd=self.src_repo)
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def git_source(self, **overrides):
        source = {
            "type": "git",
            "repo": str(self.src_repo),
            "path": "skills/python",
        }
        source.update(overrides)
        return source

    def create_group(self, name, skills):
        groups.create_group(name)
        for skill_id, source in skills:
            groups.add_skill(name, skill_id, source)
        return groups.read_group(name)

    def use_group(self, names):
        """Use groups and un-mount before the temp dir is removed."""
        state.use_groups(self.root, names)
        self.addCleanup(state.unuse_groups, self.root, names)

    def skills_dir(self):
        return self.root / ".agents" / "skills"

    def actions_of(self):
        return {a["skill"]: a["action"] for a in state.update_groups(self.root)}


class TestUpdateBasics(UpdateTestCase):
    def test_no_upstream_change_reports_unchanged(self):
        source = self.git_source(rev="main")
        self.create_group("python", [("python-skill", source)])
        state.init_project(self.root)
        self.use_group(["python"])

        self.assertEqual(self.actions_of(), {"python-skill": "unchanged"})
        lock = lockfile.read_lock(self.root)
        self.assertEqual(lock["python-skill"]["resolved_sha"], self.head1)

    def test_upstream_move_updates_sha_content_and_mount(self):
        source = self.git_source(rev="main")
        self.create_group("python", [("python-skill", source)])
        state.init_project(self.root)
        self.use_group(["python"])

        # upstream moves: new commit changes the skill content
        (self.src_repo / "skills" / "python" / "SKILL.md").write_bytes(b"# v2\n")
        _git("add", ".", cwd=self.src_repo)
        self._commit("bump")
        head2 = _git("rev-parse", "HEAD", cwd=self.src_repo).stdout.strip()
        self.assertNotEqual(head2, self.head1)

        self.assertEqual(self.actions_of(), {"python-skill": "updated"})
        lock = lockfile.read_lock(self.root)
        self.assertEqual(lock["python-skill"]["resolved_sha"], head2)
        # the mount sees the new content through the same cache dir
        self.assertEqual(
            (self.skills_dir() / "python-skill" / "SKILL.md").read_bytes(), b"# v2\n"
        )
        self.assertEqual(
            {i["skill"]: i["state"] for i in state.status(self.root)},
            {"python-skill": "ok"},
        )

    def test_local_source_is_unchanged(self):
        src = self.root / "local-skill"
        src.mkdir()
        (src / "SKILL.md").write_bytes(b"# local\n")
        self.create_group("python", [("lint", {"type": "local", "path": str(src)})])
        state.init_project(self.root)
        self.use_group(["python"])

        self.assertEqual(self.actions_of(), {"lint": "unchanged"})

    def test_pinned_sha_rev_is_unchanged(self):
        source = self.git_source(rev=self.head1)
        self.create_group("python", [("python-skill", source)])
        state.init_project(self.root)
        self.use_group(["python"])

        # upstream moves but the pinned rev is not refreshed
        (self.src_repo / "skills" / "python" / "SKILL.md").write_bytes(b"# v2\n")
        _git("add", ".", cwd=self.src_repo)
        self._commit("bump")

        self.assertEqual(self.actions_of(), {"python-skill": "unchanged"})
        lock = lockfile.read_lock(self.root)
        self.assertEqual(lock["python-skill"]["resolved_sha"], self.head1)

    def test_stale_skill_is_skipped(self):
        source = self.git_source(rev="main")
        self.create_group("python", [("python-skill", source)])
        state.init_project(self.root)
        self.use_group(["python"])

        # skill removed from the group definition: locked but stale
        group = groups.read_group("python")
        group["skills"] = []
        groups.write_group(group)

        self.assertEqual(self.actions_of(), {})
        self.assertIn("python-skill", lockfile.read_lock(self.root))


class TestUpdateFailure(UpdateTestCase):
    def test_fetch_failure_leaves_everything_untouched(self):
        source = self.git_source(rev="main")
        self.create_group("python", [("python-skill", source)])
        state.init_project(self.root)
        self.use_group(["python"])

        with mock.patch.object(
            git_util, "refresh_repo", side_effect=errors.EnvError("boom")
        ):
            with self.assertRaises(errors.EnvError):
                state.update_groups(self.root)

        # mount and lock untouched after the failure
        self.assertEqual(
            (self.skills_dir() / "python-skill" / "SKILL.md").read_bytes(), b"# v1\n"
        )
        lock = lockfile.read_lock(self.root)
        self.assertEqual(lock["python-skill"]["resolved_sha"], self.head1)


if __name__ == "__main__":
    unittest.main()
