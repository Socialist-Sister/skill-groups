"""S1 happy path for sg.state: init -> use -> mount, byte-identical content,
declaration groups, lock entries. Also S4 idempotent re-use.
"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from sg import errors, groups, lockfile, state, util


class StateTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self._env = mock.patch.dict(os.environ, {"SG_HOME": str(self.root / "home")})
        self._env.start()
        self.addCleanup(self._env.stop)

    def local_skill(self, name, content=b"# demo\n"):
        src = self.root / f"src-{name}"
        src.mkdir()
        (src / "SKILL.md").write_bytes(content)
        return src

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


class TestInitProject(StateTestCase):
    def test_init_writes_declaration_and_gitignore(self):
        state.init_project(self.root)
        self.assertEqual(
            util.read_json(lockfile.sg_json(self.root)),
            {"version": "1", "groups": [], "agent": "agents", "mode": "auto"},
        )
        self.assertEqual(
            (self.root / ".gitignore").read_text(encoding="utf-8").splitlines(),
            [".agents/skills"],
        )

    def test_init_twice_raises_user_error(self):
        state.init_project(self.root)
        with self.assertRaises(errors.UserError) as ctx:
            state.init_project(self.root)
        self.assertIn("already initialized", str(ctx.exception))

    def test_init_preserves_existing_gitignore_lines(self):
        (self.root / ".gitignore").write_text("__pycache__/\n", encoding="utf-8")
        state.init_project(self.root)
        self.assertEqual(
            (self.root / ".gitignore").read_text(encoding="utf-8").splitlines(),
            ["__pycache__/", ".agents/skills"],
        )

    def test_init_does_not_duplicate_gitignore_line(self):
        (self.root / ".gitignore").write_text(".agents/skills\n", encoding="utf-8")
        state.init_project(self.root)
        self.assertEqual(
            (self.root / ".gitignore").read_text(encoding="utf-8").splitlines(),
            [".agents/skills"],
        )

    def test_init_custom_agent_uses_its_skills_dir(self):
        state.init_project(self.root, agent="claude", mode="copy")
        decl = util.read_json(lockfile.sg_json(self.root))
        self.assertEqual(decl["agent"], "claude")
        self.assertEqual(decl["mode"], "copy")
        self.assertIn(
            ".claude/skills", (self.root / ".gitignore").read_text(encoding="utf-8")
        )

    def test_init_unknown_agent_raises_user_error(self):
        with self.assertRaises(errors.UserError):
            state.init_project(self.root, agent="nope")

    def test_project_skills_dir_resolves_declared_agent(self):
        state.init_project(self.root, agent="codex")
        decl = lockfile.read_declaration(self.root)
        self.assertEqual(
            state.project_skills_dir(self.root, decl),
            self.root / ".codex" / "skills",
        )


class TestUseHappyPath(StateTestCase):
    def test_use_mounts_byte_identical_and_records_state(self):
        content = b"# lint\nunicode \xe4\xb8\xad\xe6\x96\x87 ok\n"
        src = self.local_skill("lint", content)
        self.create_group("python", [("lint", {"type": "local", "path": str(src)})])

        state.init_project(self.root)
        state.use_groups(self.root, ["python"])
        self.track_unuse(["python"])

        link = self.skills_dir() / "lint"
        self.assertTrue((link / "SKILL.md").is_file())
        self.assertEqual((link / "SKILL.md").read_bytes(), content)

        self.assertEqual(state.list_mounted(self.root), [("lint", "local")])

        decl = util.read_json(lockfile.sg_json(self.root))
        self.assertEqual(decl["groups"], ["python"])

        lock = lockfile.read_lock(self.root)
        self.assertIn("lint", lock)
        entry = lock["lint"]
        self.assertEqual(entry["group"], "python")
        self.assertEqual(entry["source"], {"type": "local", "path": str(src)})
        self.assertIsNone(entry["resolved_sha"])
        self.assertTrue(Path(entry["cache_dir"]).is_dir())


class TestUseWithoutInit(StateTestCase):
    def test_use_without_init_raises_user_error_hinting_init(self):
        self.create_group("python", [])
        with self.assertRaises(errors.UserError) as ctx:
            state.use_groups(self.root, ["python"])
        self.assertIn("init", str(ctx.exception))

    def test_use_unknown_group_raises_user_error_with_name(self):
        state.init_project(self.root)
        with self.assertRaises(errors.UserError) as ctx:
            state.use_groups(self.root, ["nope"])
        self.assertIn("nope", str(ctx.exception))


class TestIdempotentUse(StateTestCase):
    def test_reusing_same_group_mounts_no_duplicates(self):
        src = self.local_skill("lint")
        self.create_group("python", [("lint", {"type": "local", "path": str(src)})])
        state.init_project(self.root)

        state.use_groups(self.root, ["python"])
        state.use_groups(self.root, ["python"])
        self.track_unuse(["python"])

        self.assertEqual([e.name for e in os.scandir(self.skills_dir())], ["lint"])
        self.assertEqual(state.list_mounted(self.root), [("lint", "local")])

    def test_second_use_merges_declaration_preserving_order(self):
        src1 = self.local_skill("lint")
        src2 = self.local_skill("fmt")
        self.create_group("python", [("lint", {"type": "local", "path": str(src1)})])
        self.create_group("web", [("fmt", {"type": "local", "path": str(src2)})])
        state.init_project(self.root)

        state.use_groups(self.root, ["python"])
        state.use_groups(self.root, ["web", "python"])
        self.track_unuse(["python", "web"])

        decl = util.read_json(lockfile.sg_json(self.root))
        self.assertEqual(decl["groups"], ["python", "web"])
        self.assertEqual(
            sorted(e.name for e in os.scandir(self.skills_dir())), ["fmt", "lint"]
        )


class TestListMounted(StateTestCase):
    def test_sorted_by_group_then_skill_id(self):
        src1 = self.local_skill("zeta")
        src2 = self.local_skill("alpha")
        self.create_group("bb", [("zeta", {"type": "local", "path": str(src1)})])
        self.create_group("aa", [("alpha", {"type": "local", "path": str(src2)})])
        state.init_project(self.root)
        state.use_groups(self.root, ["aa", "bb"])
        self.track_unuse(["aa", "bb"])

        self.assertEqual(
            state.list_mounted(self.root), [("alpha", "local"), ("zeta", "local")]
        )

    def test_list_mounted_empty_before_any_use(self):
        state.init_project(self.root)
        self.assertEqual(state.list_mounted(self.root), [])


if __name__ == "__main__":
    unittest.main()
