"""S6: two projects sharing one SG_HOME do not leak state into each other."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from sg import groups, lockfile, state


class IsolationTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / "home"
        self._env = mock.patch.dict(os.environ, {"SG_HOME": str(self.home)})
        self._env.start()
        self.addCleanup(self._env.stop)
        self.proj_a = Path(self.tmp.name) / "proj-a"
        self.proj_b = Path(self.tmp.name) / "proj-b"

    def local_skill(self, proj, name, content):
        src = proj / f"src-{name}"
        src.mkdir(parents=True)
        (src / "SKILL.md").write_bytes(content)
        return src

    def create_group(self, name, skills):
        groups.create_group(name)
        for skill_id, source in skills:
            groups.add_skill(name, skill_id, source)
        return groups.read_group(name)

    def skills_of(self, proj):
        sd = proj / ".agents" / "skills"
        if not sd.is_dir():
            return []
        return sorted(e.name for e in os.scandir(sd))

    def test_projects_share_groups_but_keep_own_mounts(self):
        content_py = b"# python lint\n"
        content_docs = b"# docs guide\n"
        src_py = self.local_skill(self.proj_a, "lint", content_py)
        src_docs = self.local_skill(self.proj_b, "guide", content_docs)
        self.create_group("python", [("lint", {"type": "local", "path": str(src_py)})])
        self.create_group("docs", [("guide", {"type": "local", "path": str(src_docs)})])

        state.init_project(self.proj_a)
        state.init_project(self.proj_b)
        state.use_groups(self.proj_a, ["python"])
        state.use_groups(self.proj_b, ["docs"])
        self.addCleanup(state.unuse_groups, self.proj_a, ["python"])
        self.addCleanup(state.unuse_groups, self.proj_b, ["docs"])

        # A has only A's skill, B has only B's skill
        self.assertEqual(self.skills_of(self.proj_a), ["lint"])
        self.assertEqual(self.skills_of(self.proj_b), ["guide"])
        self.assertEqual(
            (self.proj_a / ".agents" / "skills" / "lint" / "SKILL.md").read_bytes(),
            content_py,
        )
        self.assertEqual(
            (self.proj_b / ".agents" / "skills" / "guide" / "SKILL.md").read_bytes(),
            content_docs,
        )

        # declarations are independent
        self.assertEqual(lockfile.read_declaration(self.proj_a)["groups"], ["python"])
        self.assertEqual(lockfile.read_declaration(self.proj_b)["groups"], ["docs"])

    def test_each_project_reads_only_its_own_declaration(self):
        state.init_project(self.proj_a, agent="claude")
        state.init_project(self.proj_b, agent="codex")

        self.assertEqual(
            state.project_skills_dir(
                self.proj_a, lockfile.read_declaration(self.proj_a)
            ),
            self.proj_a / ".claude" / "skills",
        )
        self.assertEqual(
            state.project_skills_dir(
                self.proj_b, lockfile.read_declaration(self.proj_b)
            ),
            self.proj_b / ".codex" / "skills",
        )
        self.assertEqual(lockfile.read_lock(self.proj_a), {})
        self.assertEqual(lockfile.read_lock(self.proj_b), {})


if __name__ == "__main__":
    unittest.main()
