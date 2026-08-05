"""S8: sg status / sync — drift detection and convergence.

Every test drives state.status / state.sync_groups directly and asserts on
the filesystem (no subprocess; the CLI layer has its own suite). Covered
scenarios: added skill (S8a), changed source (S8b), stale skill (S8c),
external conflict (S8d), deleted link (S8e), and a vanished cache dir.
"""

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from sg import groups, lockfile, mount, state, util


class StatusSyncTestCase(unittest.TestCase):
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

    def skills_dir(self):
        return self.root / ".agents" / "skills"

    def state_of(self, skill_id):
        for item in state.status(self.root):
            if item["skill"] == skill_id:
                return item["state"]
        return None

    def remove_skill_from_group(self, group_name, skill_id):
        group = groups.read_group(group_name)
        group["skills"] = [s for s in group["skills"] if s["id"] != skill_id]
        groups.write_group(group)

    def change_skill_source(self, group_name, skill_id, new_source):
        group = groups.read_group(group_name)
        for skill in group["skills"]:
            if skill["id"] == skill_id:
                skill["source"] = new_source
        groups.write_group(group)


class TestStatusOk(StatusSyncTestCase):
    def test_used_skill_reports_ok(self):
        src = self.local_skill("lint")
        self.create_group("python", [("lint", {"type": "local", "path": str(src)})])
        state.init_project(self.root)
        self.use_group(["python"])

        self.assertEqual(
            state.status(self.root),
            [{"skill": "lint", "group": "python", "state": "ok"}],
        )

    def test_empty_project_has_no_entries(self):
        state.init_project(self.root)
        self.assertEqual(state.status(self.root), [])
        self.assertEqual(state.sync_groups(self.root), [])


class TestS8aAddedSkill(StatusSyncTestCase):
    def test_new_group_skill_is_missing_link_then_synced(self):
        src1 = self.local_skill("lint")
        src2 = self.local_skill("fmt")
        self.create_group("python", [("lint", {"type": "local", "path": str(src1)})])
        state.init_project(self.root)
        self.use_group(["python"])

        groups.add_skill("python", "fmt", {"type": "local", "path": str(src2)})

        states = {item["skill"]: item for item in state.status(self.root)}
        self.assertEqual(states["fmt"]["state"], "missing-link")
        self.assertEqual(states["fmt"]["group"], "python")
        self.assertEqual(states["lint"]["state"], "ok")

        actions = {a["skill"]: a["action"] for a in state.sync_groups(self.root)}
        self.assertEqual(actions["fmt"], "relinked")
        self.assertEqual(actions["lint"], "unchanged")

        link = self.skills_dir() / "fmt"
        self.assertTrue((link / "SKILL.md").is_file())
        lock = lockfile.read_lock(self.root)
        self.assertIn("fmt", lock)
        self.assertEqual(lock["fmt"]["group"], "python")
        self.assertEqual(lock["fmt"]["source"], {"type": "local", "path": str(src2)})
        self.assertEqual(self.state_of("fmt"), "ok")
        self.assertEqual(self.state_of("lint"), "ok")

        # idempotent: a second sync performs no repair
        actions = {a["skill"]: a["action"] for a in state.sync_groups(self.root)}
        self.assertEqual(actions["fmt"], "unchanged")


class TestS8bChangedSource(StatusSyncTestCase):
    def test_source_change_is_drift_then_remounted(self):
        src1 = self.local_skill("lint", b"# v1\n")
        self.create_group("python", [("lint", {"type": "local", "path": str(src1)})])
        state.init_project(self.root)
        self.use_group(["python"])

        src2 = self.root / "src-lint-v2"
        src2.mkdir()
        (src2 / "SKILL.md").write_bytes(b"# v2\n")
        self.change_skill_source("python", "lint", {"type": "local", "path": str(src2)})

        states = {item["skill"]: item["state"] for item in state.status(self.root)}
        self.assertEqual(states["lint"], "drift")

        actions = {a["skill"]: a["action"] for a in state.sync_groups(self.root)}
        self.assertEqual(actions["lint"], "remounted")

        lock = lockfile.read_lock(self.root)
        cache_dir = Path(lock["lint"]["cache_dir"])
        self.assertEqual((cache_dir / "SKILL.md").read_bytes(), b"# v2\n")
        self.assertEqual(
            (self.skills_dir() / "lint" / "SKILL.md").read_bytes(), b"# v2\n"
        )
        self.assertEqual(
            {item["skill"]: item["state"] for item in state.status(self.root)},
            {"lint": "ok"},
        )


class TestS8cStale(StatusSyncTestCase):
    def test_removed_group_skill_is_stale_then_removed(self):
        src = self.local_skill("lint")
        self.create_group("python", [("lint", {"type": "local", "path": str(src)})])
        state.init_project(self.root)
        self.use_group(["python"])

        self.remove_skill_from_group("python", "lint")

        states = {item["skill"]: item["state"] for item in state.status(self.root)}
        self.assertEqual(states["lint"], "stale")

        actions = {a["skill"]: a["action"] for a in state.sync_groups(self.root)}
        self.assertEqual(actions["lint"], "removed")

        self.assertFalse((self.skills_dir() / "lint").exists())
        self.assertEqual(lockfile.read_lock(self.root), {})
        self.assertEqual(state.status(self.root), [])


class TestS8dConflict(StatusSyncTestCase):
    def test_unlocked_directory_is_conflict_and_survives_sync(self):
        src = self.local_skill("lint")
        self.create_group("python", [("lint", {"type": "local", "path": str(src)})])
        state.init_project(self.root)
        self.use_group(["python"])

        foreign = self.skills_dir() / "external"
        foreign.mkdir()
        (foreign / "SKILL.md").write_text("# mine\n", encoding="utf-8")

        states = {item["skill"]: item for item in state.status(self.root)}
        self.assertEqual(states["external"]["state"], "conflict")
        self.assertEqual(states["external"]["group"], "")
        self.assertEqual(states["lint"]["state"], "ok")

        actions = {a["skill"]: a["action"] for a in state.sync_groups(self.root)}
        self.assertEqual(actions["external"], "skipped")
        self.assertEqual(actions["lint"], "unchanged")

        self.assertTrue(foreign.is_dir())
        self.assertEqual(
            (foreign / "SKILL.md").read_text(encoding="utf-8"), "# mine\n"
        )
        self.assertEqual(set(lockfile.read_lock(self.root)), {"lint"})


class TestS8eMissingLink(StatusSyncTestCase):
    def test_deleted_link_is_missing_link_then_relinked(self):
        src = self.local_skill("lint")
        self.create_group("python", [("lint", {"type": "local", "path": str(src)})])
        state.init_project(self.root)
        self.use_group(["python"])

        link = self.skills_dir() / "lint"
        mount.unmount_skill(link, state._mount_kind(link))
        self.assertFalse(os.path.lexists(link))

        states = {item["skill"]: item["state"] for item in state.status(self.root)}
        self.assertEqual(states["lint"], "missing-link")

        actions = {a["skill"]: a["action"] for a in state.sync_groups(self.root)}
        self.assertEqual(actions["lint"], "relinked")

        self.assertTrue(os.path.lexists(link))
        self.assertTrue((link / "SKILL.md").is_file())
        self.assertEqual(
            {item["skill"]: item["state"] for item in state.status(self.root)},
            {"lint": "ok"},
        )


class TestVanishedCache(StatusSyncTestCase):
    def test_gone_cache_dir_is_reported_missing_link_then_restored(self):
        src = self.local_skill("lint")
        self.create_group("python", [("lint", {"type": "local", "path": str(src)})])
        state.init_project(self.root)
        self.use_group(["python"])

        cache_dir = Path(lockfile.read_lock(self.root)["lint"]["cache_dir"])
        shutil.rmtree(cache_dir)

        states = {item["skill"]: item["state"] for item in state.status(self.root)}
        self.assertEqual(states["lint"], "missing-link")

        actions = {a["skill"]: a["action"] for a in state.sync_groups(self.root)}
        self.assertEqual(actions["lint"], "relinked")

        self.assertTrue((self.skills_dir() / "lint" / "SKILL.md").is_file())
        self.assertTrue(cache_dir.is_dir())
        self.assertEqual(self.state_of("lint"), "ok")


if __name__ == "__main__":
    unittest.main()
