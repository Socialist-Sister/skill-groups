"""S9: `sg use` mounts standalone skills declared via --skill/--path.

A project can mount individual skills directly (without a group). The
declaration gains an optional "skills" list of {"id", "source"} entries;
lock entries for standalone skills carry group "". Tests cover the mixed
group+skill mount, idempotent re-use, all-or-nothing conflicts, unuse of
standalone skills, status/sync drift + missing-link convergence, and the
CLI pair-count contract.
"""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from sg import errors, groups, lockfile, mount, state, util

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def run_sg(cwd, *args, env=None):
    """Run ``python -m sg <args>`` with cwd and an augmented environment."""
    merged = dict(os.environ)
    merged["PYTHONIOENCODING"] = "utf-8"
    merged["PYTHONPATH"] = str(PROJECT_ROOT)
    if env:
        merged.update(env)
    return subprocess.run(
        [sys.executable, "-m", "sg", *args],
        cwd=str(cwd),
        env=merged,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


class UseSkillTestCase(unittest.TestCase):
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

    def skills(self):
        sd = self.root / ".agents" / "skills"
        if not sd.is_dir():
            return []
        return sorted(e.name for e in os.scandir(sd))

    def track_unuse(self, group_names, skill_ids):
        """Un-mount before the temp dir is removed (junction safety)."""
        self.addCleanup(
            state.unuse_groups, self.root, group_names, extra_skill_ids=skill_ids
        )


class TestMixedMount(UseSkillTestCase):
    def test_group_and_standalone_skills_mount_together(self):
        src1 = self.local_skill("lint")
        src2 = self.local_skill("git-commit-writer")
        self.create_group("python", [("lint", {"type": "local", "path": str(src1)})])
        state.init_project(self.root)

        state.use_groups(
            self.root,
            ["python"],
            extra_skills=[
                {"id": "git-commit-writer", "source": {"type": "local", "path": str(src2)}}
            ],
        )
        self.track_unuse(["python"], ["git-commit-writer"])

        self.assertEqual(
            self.skills(), ["git-commit-writer", "lint"]
        )
        self.assertTrue(
            (self.root / ".agents" / "skills" / "git-commit-writer" / "SKILL.md").is_file()
        )

        lock = lockfile.read_lock(self.root)
        self.assertEqual(lock["lint"]["group"], "python")
        self.assertEqual(lock["git-commit-writer"]["group"], "")
        self.assertEqual(
            lock["git-commit-writer"]["source"],
            {"type": "local", "path": str(src2)},
        )

        decl = util.read_json(lockfile.sg_json(self.root))
        self.assertEqual(decl["groups"], ["python"])
        self.assertEqual(
            decl["skills"],
            [{"id": "git-commit-writer", "source": {"type": "local", "path": str(src2)}}],
        )

        self.assertEqual(
            state.list_mounted(self.root),
            [("git-commit-writer", "local"), ("lint", "local")],
        )


class TestIdempotentUseSkill(UseSkillTestCase):
    def test_repeat_use_same_group_and_skill_mounts_no_duplicates(self):
        src1 = self.local_skill("lint")
        src2 = self.local_skill("fmt")
        self.create_group("python", [("lint", {"type": "local", "path": str(src1)})])
        state.init_project(self.root)
        extras = [{"id": "fmt", "source": {"type": "local", "path": str(src2)}}]

        state.use_groups(self.root, ["python"], extra_skills=extras)
        state.use_groups(self.root, ["python"], extra_skills=extras)
        self.track_unuse(["python"], ["fmt"])

        self.assertEqual(self.skills(), ["fmt", "lint"])
        self.assertEqual(
            state.list_mounted(self.root), [("fmt", "local"), ("lint", "local")]
        )
        decl = util.read_json(lockfile.sg_json(self.root))
        self.assertEqual(decl["groups"], ["python"])
        self.assertEqual(decl["skills"], extras)
        self.assertEqual(len(lockfile.read_lock(self.root)), 2)


class TestUseSkillConflicts(UseSkillTestCase):
    def test_extra_skill_clashing_with_group_skill_is_atomic_conflict(self):
        src_a = self.local_skill("demo", b"# a\n")
        src_b = self.local_skill("demo-b", b"# b\n")
        self.create_group("python", [("demo", {"type": "local", "path": str(src_a)})])
        state.init_project(self.root)

        with self.assertRaises(errors.UserError):
            state.use_groups(
                self.root,
                ["python"],
                extra_skills=[
                    {"id": "demo", "source": {"type": "local", "path": str(src_b)}}
                ],
            )

        # atomicity: zero mounts, no declaration or lock change
        self.assertEqual(self.skills(), [])
        self.assertEqual(lockfile.read_lock(self.root), {})
        self.assertEqual(util.read_json(lockfile.sg_json(self.root))["groups"], [])
        self.assertEqual(util.read_json(lockfile.sg_json(self.root)).get("skills"), None)

    def test_two_extra_skills_same_id_different_source_conflict(self):
        src_a = self.local_skill("a")
        src_b = self.local_skill("b")
        self.create_group("python", [])
        state.init_project(self.root)

        with self.assertRaises(errors.UserError):
            state.use_groups(
                self.root,
                ["python"],
                extra_skills=[
                    {"id": "demo", "source": {"type": "local", "path": str(src_a)}},
                    {"id": "demo", "source": {"type": "local", "path": str(src_b)}},
                ],
            )

        self.assertEqual(self.skills(), [])
        self.assertEqual(lockfile.read_lock(self.root), {})
        self.assertEqual(util.read_json(lockfile.sg_json(self.root))["groups"], [])


class TestUnuseSkill(UseSkillTestCase):
    def test_unuse_group_keeps_standalone_skill(self):
        src1 = self.local_skill("lint")
        src2 = self.local_skill("fmt")
        self.create_group("python", [("lint", {"type": "local", "path": str(src1)})])
        state.init_project(self.root)
        state.use_groups(
            self.root,
            ["python"],
            extra_skills=[{"id": "fmt", "source": {"type": "local", "path": str(src2)}}],
        )
        self.track_unuse([], ["fmt"])

        state.unuse_groups(self.root, ["python"])

        self.assertEqual(self.skills(), ["fmt"])
        self.assertEqual(set(lockfile.read_lock(self.root)), {"fmt"})
        decl = util.read_json(lockfile.sg_json(self.root))
        self.assertEqual(decl["groups"], [])
        self.assertEqual([s["id"] for s in decl["skills"]], ["fmt"])

    def test_unuse_skill_keeps_group_skill(self):
        src1 = self.local_skill("lint")
        src2 = self.local_skill("fmt")
        self.create_group("python", [("lint", {"type": "local", "path": str(src1)})])
        state.init_project(self.root)
        state.use_groups(
            self.root,
            ["python"],
            extra_skills=[{"id": "fmt", "source": {"type": "local", "path": str(src2)}}],
        )
        self.track_unuse(["python"], [])

        state.unuse_groups(self.root, [], extra_skill_ids=["fmt"])

        self.assertEqual(self.skills(), ["lint"])
        self.assertEqual(set(lockfile.read_lock(self.root)), {"lint"})
        decl = util.read_json(lockfile.sg_json(self.root))
        self.assertEqual(decl["groups"], ["python"])
        self.assertEqual(decl["skills"], [])

    def test_standalone_still_declared_survives_group_unuse(self):
        src = self.local_skill("lint")
        self.create_group("python", [("lint", {"type": "local", "path": str(src)})])
        state.init_project(self.root)
        # Mount lint standalone first (declaration skills reference it)...
        state.use_groups(
            self.root,
            [],
            extra_skills=[{"id": "lint", "source": {"type": "local", "path": str(src)}}],
        )
        # ...then also through the group.
        state.use_groups(self.root, ["python"])

        state.unuse_groups(self.root, ["python"])

        self.assertEqual(self.skills(), ["lint"])
        self.assertIn("lint", lockfile.read_lock(self.root))
        decl = util.read_json(lockfile.sg_json(self.root))
        self.assertEqual(decl["groups"], [])
        self.assertEqual([s["id"] for s in decl["skills"]], ["lint"])


class TestStatusSyncSkill(UseSkillTestCase):
    def test_used_standalone_reports_ok(self):
        src = self.local_skill("wc")
        self.create_group("python", [("lint", {"type": "local", "path": str(self.local_skill("lint"))})])
        state.init_project(self.root)
        state.use_groups(
            self.root,
            ["python"],
            extra_skills=[{"id": "wc", "source": {"type": "local", "path": str(src)}}],
        )
        self.track_unuse(["python"], ["wc"])

        self.assertEqual(
            {item["skill"]: item["state"] for item in state.status(self.root)},
            {"lint": "ok", "wc": "ok"},
        )

    def test_source_change_is_drift_then_sync_remounts(self):
        src = self.local_skill("wc")
        state.init_project(self.root)
        state.use_groups(
            self.root,
            [],
            extra_skills=[{"id": "wc", "source": {"type": "local", "path": str(src)}}],
        )
        self.track_unuse([], ["wc"])

        src2 = self.root / "src-wc-v2"
        src2.mkdir()
        (src2 / "SKILL.md").write_text("# wc v2\n", encoding="utf-8")
        decl = lockfile.read_declaration(self.root)
        decl["skills"] = [{"id": "wc", "source": {"type": "local", "path": str(src2)}}]
        util.write_json(lockfile.sg_json(self.root), decl)

        states = {item["skill"]: item["state"] for item in state.status(self.root)}
        self.assertEqual(states["wc"], "drift")

        actions = {a["skill"]: a["action"] for a in state.sync_groups(self.root)}
        self.assertEqual(actions["wc"], "remounted")

        states = {item["skill"]: item["state"] for item in state.status(self.root)}
        self.assertEqual(states["wc"], "ok")
        self.assertEqual(
            (self.root / ".agents" / "skills" / "wc" / "SKILL.md").read_text(encoding="utf-8"),
            "# wc v2\n",
        )

    def test_deleted_standalone_link_is_missing_then_relinked(self):
        src = self.local_skill("wc")
        state.init_project(self.root)
        state.use_groups(
            self.root,
            [],
            extra_skills=[{"id": "wc", "source": {"type": "local", "path": str(src)}}],
        )
        self.track_unuse([], ["wc"])

        link = self.root / ".agents" / "skills" / "wc"
        mount.unmount_skill(link, state._mount_kind(link))
        self.assertFalse(os.path.lexists(link))

        states = {item["skill"]: item["state"] for item in state.status(self.root)}
        self.assertEqual(states["wc"], "missing-link")

        actions = {a["skill"]: a["action"] for a in state.sync_groups(self.root)}
        self.assertEqual(actions["wc"], "relinked")

        self.assertTrue((self.root / ".agents" / "skills" / "wc" / "SKILL.md").is_file())
        states = {item["skill"]: item["state"] for item in state.status(self.root)}
        self.assertEqual(states["wc"], "ok")


class TestUseSkillWithoutInit(UseSkillTestCase):
    def test_use_skill_without_init_hints_at_init(self):
        src = self.local_skill("wc")
        with self.assertRaises(errors.UserError) as ctx:
            state.use_groups(
                self.root,
                [],
                extra_skills=[{"id": "wc", "source": {"type": "local", "path": str(src)}}],
            )
        self.assertIn("init", str(ctx.exception))


class TestCliUseSkill(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="sg-skill-")
        self.base = Path(self._tmp.name)
        self.project = self.base / "project"
        self.project.mkdir()
        self.env = {"SG_HOME": str(self.base / "home")}

    def tearDown(self):
        self._tmp.cleanup()

    def sg(self, *args):
        return run_sg(self.project, *args, env=self.env)

    def make_skill(self, skill_id, tag=""):
        folder = f"{skill_id}-{tag}" if tag else skill_id
        skill = self.base / "skills" / folder
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text(f"# {skill_id}\n", encoding="utf-8")
        return skill

    def test_use_group_with_skill_mounts_standalone(self):
        skill = self.make_skill("demo")
        self.assertEqual(self.sg("group", "create", "python").returncode, 0)
        self.assertEqual(self.sg("init").returncode, 0)

        result = self.sg("use", "python", "--skill", "demo", "--path", str(skill))
        self.assertEqual(result.returncode, 0)
        self.assertTrue(
            (self.project / ".agents" / "skills" / "demo" / "SKILL.md").exists()
        )

        ls = self.sg("ls")
        self.assertEqual(ls.returncode, 0)
        self.assertIn("demo (ungrouped, local)", ls.stdout)

    def test_use_skill_without_path_pair_fails(self):
        skill = self.make_skill("demo")
        self.assertEqual(self.sg("group", "create", "python").returncode, 0)
        self.assertEqual(self.sg("init").returncode, 0)

        result = self.sg(
            "use", "python", "--skill", "demo", "--path", str(skill), "--path", str(skill)
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("--skill and --path must come in pairs", result.stderr)

    def test_unuse_skill_unmounts_standalone(self):
        skill = self.make_skill("demo")
        self.sg("group", "create", "python")
        self.sg("init")
        self.assertEqual(
            self.sg("use", "python", "--skill", "demo", "--path", str(skill)).returncode,
            0,
        )

        result = self.sg("unuse", "python", "--skill", "demo")
        self.assertEqual(result.returncode, 0)
        self.assertFalse((self.project / ".agents" / "skills" / "demo").exists())


if __name__ == "__main__":
    unittest.main()
