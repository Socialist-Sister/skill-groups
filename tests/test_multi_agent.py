"""S10: multi-agent projects — one declaration, several agent skills dirs.

A project initialized with several agents (e.g. `sg init --agent claude
--agent opencode`) mounts every declared skill into every skills dir;
status aggregates across dirs and sync repairs every dir. Legacy v1
declarations (single "agent" string) keep working unchanged.
"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from sg import groups, lockfile, mount, state, util


class MultiAgentTestCase(unittest.TestCase):
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

    def use_group(self, names):
        """Use groups and un-mount before the temp dir is removed."""
        state.use_groups(self.root, names)
        self.addCleanup(state.unuse_groups, self.root, names)

    def skills_dirs(self):
        return [self.root / ".claude" / "skills", self.root / ".opencode" / "skills"]


class TestMultiAgentInit(MultiAgentTestCase):
    def test_init_two_agents_writes_v2_and_gitignores_both(self):
        state.init_project(self.root, agents=["claude", "opencode"])
        decl = util.read_json(lockfile.sg_json(self.root))
        self.assertEqual(decl["version"], "2")
        self.assertEqual(decl["agents"], ["claude", "opencode"])
        self.assertEqual(
            (self.root / ".gitignore").read_text(encoding="utf-8").splitlines(),
            [".claude/skills", ".opencode/skills"],
        )

    def test_init_default_is_single_agents_dir(self):
        state.init_project(self.root)
        decl = util.read_json(lockfile.sg_json(self.root))
        self.assertEqual(decl["version"], "2")
        self.assertEqual(decl["agents"], ["agents"])
        self.assertEqual(decl.get("agent"), None)

    def test_init_force_keeps_groups_and_retargets_agents(self):
        src = self.local_skill("lint")
        self.create_group("python", [("lint", {"type": "local", "path": str(src)})])
        state.init_project(self.root)
        state.use_groups(self.root, ["python"])
        self.addCleanup(state.unuse_groups, self.root, ["python"])

        state.init_project(self.root, agents=["claude"], force=True)
        decl = util.read_json(lockfile.sg_json(self.root))
        self.assertEqual(decl["groups"], ["python"])
        self.assertEqual(decl["skills"], [])
        self.assertEqual(decl["agents"], ["claude"])

    def test_unknown_agent_raises_user_error(self):
        with self.assertRaises(Exception):
            state.init_project(self.root, agents=["nope"])


class TestMultiAgentMount(MultiAgentTestCase):
    def test_use_mounts_into_every_agent_dir(self):
        src = self.local_skill("lint")
        self.create_group("python", [("lint", {"type": "local", "path": str(src)})])
        state.init_project(self.root, agents=["claude", "opencode"])
        self.use_group(["python"])
        for d in self.skills_dirs():
            self.assertTrue((d / "lint" / "SKILL.md").is_file())

    def test_status_ok_when_healthy_everywhere(self):
        src = self.local_skill("lint")
        self.create_group("python", [("lint", {"type": "local", "path": str(src)})])
        state.init_project(self.root, agents=["claude", "opencode"])
        self.use_group(["python"])
        self.assertEqual(
            state.status(self.root),
            [{"skill": "lint", "group": "python", "state": "ok"}],
        )

    def test_missing_link_in_one_dir_reports_missing_link(self):
        src = self.local_skill("lint")
        self.create_group("python", [("lint", {"type": "local", "path": str(src)})])
        state.init_project(self.root, agents=["claude", "opencode"])
        self.use_group(["python"])

        broken = self.root / ".claude" / "skills" / "lint"
        mount.unmount_skill(broken, state._mount_kind(broken))
        self.assertFalse(os.path.lexists(broken))

        states = {item["skill"]: item["state"] for item in state.status(self.root)}
        self.assertEqual(states["lint"], "missing-link")

        actions = {a["skill"]: a["action"] for a in state.sync_groups(self.root)}
        self.assertEqual(actions["lint"], "relinked")
        # both dirs healthy again after sync
        for d in self.skills_dirs():
            self.assertTrue((d / "lint" / "SKILL.md").is_file())
        self.assertEqual(
            state.status(self.root),
            [{"skill": "lint", "group": "python", "state": "ok"}],
        )

    def test_conflict_in_one_dir_reports_conflict(self):
        src = self.local_skill("lint")
        self.create_group("python", [("lint", {"type": "local", "path": str(src)})])
        state.init_project(self.root, agents=["claude", "opencode"])
        self.use_group(["python"])

        foreign = self.root / ".claude" / "skills" / "external"
        foreign.mkdir()
        (foreign / "SKILL.md").write_text("# mine\n", encoding="utf-8")

        states = {item["skill"]: item for item in state.status(self.root)}
        self.assertEqual(states["external"]["state"], "conflict")
        self.assertEqual(states["lint"]["state"], "ok")

    def test_unuse_unmounts_every_agent_dir(self):
        src = self.local_skill("lint")
        self.create_group("python", [("lint", {"type": "local", "path": str(src)})])
        state.init_project(self.root, agents=["claude", "opencode"])
        state.use_groups(self.root, ["python"])

        state.unuse_groups(self.root, ["python"])
        for d in self.skills_dirs():
            self.assertFalse(os.path.lexists(d / "lint"))
        self.assertEqual(lockfile.read_lock(self.root), {})


class TestLegacyV1Compatibility(MultiAgentTestCase):
    def _write_v1_declaration(self, root, agent="claude"):
        util.write_json(
            lockfile.sg_json(root),
            {"version": "1", "groups": [], "agent": agent, "mode": "auto"},
        )

    def test_v1_declaration_use_mounts_into_legacy_agent_dir(self):
        src = self.local_skill("lint")
        self.create_group("python", [("lint", {"type": "local", "path": str(src)})])
        self._write_v1_declaration(self.root)
        state.use_groups(self.root, ["python"])
        self.addCleanup(state.unuse_groups, self.root, ["python"])

        self.assertTrue(
            (self.root / ".claude" / "skills" / "lint" / "SKILL.md").is_file()
        )
        decl = util.read_json(lockfile.sg_json(self.root))
        # declaration is rewritten in v2 shape after use
        self.assertEqual(decl["version"], "2")
        self.assertEqual(decl["agents"], ["claude"])
        self.assertEqual(
            state.status(self.root),
            [{"skill": "lint", "group": "python", "state": "ok"}],
        )

    def test_v1_declaration_status_works_without_rewrite(self):
        src = self.local_skill("lint")
        self.create_group("python", [("lint", {"type": "local", "path": str(src)})])
        self._write_v1_declaration(self.root)
        state.use_groups(self.root, ["python"])
        self.addCleanup(state.unuse_groups, self.root, ["python"])

        # status on a project whose declaration is still v1 (hand-written)
        util.write_json(
            lockfile.sg_json(self.root),
            {
                "version": "1",
                "groups": ["python"],
                "agent": "claude",
                "mode": "auto",
            },
        )
        self.assertEqual(
            state.status(self.root),
            [{"skill": "lint", "group": "python", "state": "ok"}],
        )


if __name__ == "__main__":
    unittest.main()
