"""S5: the same skill name from two sources is a conflict, resolved
atomically — either every skill mounts or none does (pre-flight).
"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from sg import errors, groups, lockfile, state, util


class ConflictTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self._env = mock.patch.dict(os.environ, {"SG_HOME": str(self.root / "home")})
        self._env.start()
        self.addCleanup(self._env.stop)

    def local_skill(self, name, content):
        src = self.root / f"src-{name}"
        src.mkdir()
        (src / "SKILL.md").write_bytes(content)
        return src

    def create_group(self, name, skills):
        groups.create_group(name)
        for skill_id, source in skills:
            groups.add_skill(name, skill_id, source)
        return groups.read_group(name)

    def skills_dir(self):
        return self.root / ".agents" / "skills"


class TestAllOrNothing(ConflictTestCase):
    def test_two_groups_same_skill_different_source_mounts_nothing(self):
        src_a = self.local_skill("a", b"# a\n")
        src_b = self.local_skill("b", b"# b\n")
        self.create_group("groupa", [("demo", {"type": "local", "path": str(src_a)})])
        self.create_group("groupb", [("demo", {"type": "local", "path": str(src_b)})])
        state.init_project(self.root)

        with self.assertRaises(errors.UserError):
            state.use_groups(self.root, ["groupa", "groupb"])

        # atomicity: nothing was mounted, no state was written
        sd = self.skills_dir()
        if sd.exists():
            self.assertEqual(list(os.scandir(sd)), [])
        self.assertEqual(lockfile.read_lock(self.root), {})
        self.assertEqual(
            util.read_json(lockfile.sg_json(self.root))["groups"], []
        )

    def test_existing_mount_survives_a_conflicting_use(self):
        src_a = self.local_skill("a", b"# a\n")
        src_b = self.local_skill("b", b"# b\n")
        self.create_group("groupa", [("demo", {"type": "local", "path": str(src_a)})])
        self.create_group("groupb", [("demo", {"type": "local", "path": str(src_b)})])
        state.init_project(self.root)

        state.use_groups(self.root, ["groupa"])
        self.addCleanup(state.unuse_groups, self.root, ["groupa"])

        with self.assertRaises(errors.UserError):
            state.use_groups(self.root, ["groupb"])

        self.assertEqual(
            sorted(e.name for e in os.scandir(self.skills_dir())), ["demo"]
        )
        self.assertEqual(set(lockfile.read_lock(self.root)), {"demo"})
        self.assertEqual(
            util.read_json(lockfile.sg_json(self.root))["groups"], ["groupa"]
        )

    def test_plain_directory_at_target_is_a_conflict_and_is_preserved(self):
        src = self.local_skill("demo", b"# cached\n")
        self.create_group("python", [("demo", {"type": "local", "path": str(src)})])
        state.init_project(self.root)
        blocker = self.skills_dir() / "demo"
        blocker.mkdir(parents=True)
        (blocker / "SKILL.md").write_text("# mine\n", encoding="utf-8")

        with self.assertRaises(errors.UserError):
            state.use_groups(self.root, ["python"])

        self.assertEqual(
            (blocker / "SKILL.md").read_text(encoding="utf-8"), "# mine\n"
        )
        self.assertEqual(lockfile.read_lock(self.root), {})
        self.assertEqual(
            util.read_json(lockfile.sg_json(self.root))["groups"], []
        )


if __name__ == "__main__":
    unittest.main()
