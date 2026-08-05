"""S2: unuse removes exactly the requested groups' skills; groups that share
skills keep them mounted; unknown group names are ignored (idempotent).
"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from sg import groups, lockfile, state, util


class UnuseTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self._env = mock.patch.dict(os.environ, {"SG_HOME": str(self.root / "home")})
        self._env.start()
        self.addCleanup(self._env.stop)

    def local_skill(self, name):
        src = self.root / f"src-{name}"
        src.mkdir()
        (src / "SKILL.md").write_text(f"# {name}\n", encoding="utf-8")
        return src

    def create_group(self, name, skills):
        groups.create_group(name)
        for skill_id, source in skills:
            groups.add_skill(name, skill_id, source)
        return groups.read_group(name)

    def skills(self):
        sd = self.root / ".agents" / "skills"
        return sorted(e.name for e in os.scandir(sd))


class TestUnuse(UnuseTestCase):
    def test_unuse_removes_only_requested_group(self):
        src1 = self.local_skill("lint")
        src2 = self.local_skill("fmt")
        self.create_group("python", [("lint", {"type": "local", "path": str(src1)})])
        self.create_group("web", [("fmt", {"type": "local", "path": str(src2)})])
        state.init_project(self.root)
        state.use_groups(self.root, ["python", "web"])
        self.addCleanup(state.unuse_groups, self.root, ["web"])

        state.unuse_groups(self.root, ["python"])

        self.assertEqual(self.skills(), ["fmt"])
        self.assertEqual(set(lockfile.read_lock(self.root)), {"fmt"})
        self.assertEqual(
            util.read_json(lockfile.sg_json(self.root))["groups"], ["web"]
        )

    def test_unuse_unknown_group_is_a_noop(self):
        src1 = self.local_skill("lint")
        self.create_group("python", [("lint", {"type": "local", "path": str(src1)})])
        state.init_project(self.root)
        state.use_groups(self.root, ["python"])
        self.addCleanup(state.unuse_groups, self.root, ["python"])

        state.unuse_groups(self.root, ["nope"])

        self.assertEqual(self.skills(), ["lint"])
        self.assertEqual(set(lockfile.read_lock(self.root)), {"lint"})
        self.assertEqual(
            util.read_json(lockfile.sg_json(self.root))["groups"], ["python"]
        )

    def test_shared_skill_survives_when_one_group_is_unused(self):
        src1 = self.local_skill("lint")
        self.create_group("python", [("lint", {"type": "local", "path": str(src1)})])
        self.create_group("common", [("lint", {"type": "local", "path": str(src1)})])
        state.init_project(self.root)
        state.use_groups(self.root, ["python", "common"])
        self.addCleanup(state.unuse_groups, self.root, ["common"])

        state.unuse_groups(self.root, ["python"])

        self.assertEqual(self.skills(), ["lint"])
        self.assertEqual(set(lockfile.read_lock(self.root)), {"lint"})
        self.assertEqual(
            util.read_json(lockfile.sg_json(self.root))["groups"], ["common"]
        )


if __name__ == "__main__":
    unittest.main()
