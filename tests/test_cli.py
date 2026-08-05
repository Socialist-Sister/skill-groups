"""CLI contract tests: every command runs as a real subprocess.

Each test spawns ``python -m sg ...`` with cwd set to an isolated temp
project dir, SG_HOME pointing at an isolated temp home, and PYTHONPATH
pointing at the repo root so the ``sg`` package always resolves no matter
what cwd the test process runs from.
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

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


class TestCli(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="sg-cli-")
        self.base = Path(self._tmp.name)
        self.project = self.base / "project"
        self.project.mkdir()
        self.sg_home = self.base / "home"
        self.env = {"SG_HOME": str(self.sg_home)}

    def tearDown(self):
        self._tmp.cleanup()

    def sg(self, *args, env=None):
        merged = dict(self.env)
        if env:
            merged.update(env)
        return run_sg(self.project, *args, env=merged)

    def make_skill(self, skill_id, tag=""):
        """Create a local skill dir (with SKILL.md) and return its path."""
        folder = f"{skill_id}-{tag}" if tag else skill_id
        skill = self.base / "skills" / folder
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text(f"# {skill_id}\n", encoding="utf-8")
        return skill

    def test_init_use_mounts_skill(self):
        """S1: init + use mounts a SKILL.md into .agents/skills."""
        skill = self.make_skill("demo")
        self.assertEqual(self.sg("group", "create", "web").returncode, 0)
        add = self.sg(
            "group", "add", "web", "demo", "--type", "local", "--path", str(skill)
        )
        self.assertEqual(add.returncode, 0)
        self.assertEqual(self.sg("init").returncode, 0)
        result = self.sg("use", "web")
        self.assertEqual(result.returncode, 0)
        self.assertTrue(
            (self.project / ".agents" / "skills" / "demo" / "SKILL.md").exists()
        )

    def test_use_unknown_group_fails(self):
        """S3: using a group that does not exist -> exit 1, name in stderr."""
        self.assertEqual(self.sg("init").returncode, 0)
        result = self.sg("use", "missing-group")
        self.assertEqual(result.returncode, 1)
        self.assertIn("missing-group", result.stderr)

    def test_use_twice_is_idempotent(self):
        """S4: repeat use -> exit 0 and no duplicate lock entries."""
        skill = self.make_skill("demo")
        self.sg("group", "create", "web")
        self.sg("group", "add", "web", "demo", "--type", "local", "--path", str(skill))
        self.sg("init")
        self.assertEqual(self.sg("use", "web").returncode, 0)
        self.assertEqual(self.sg("use", "web").returncode, 0)
        lock = json.loads((self.project / "sg.lock").read_text(encoding="utf-8"))
        self.assertEqual(list(lock["skills"]), ["demo"])

    def test_conflicting_use_aborts_without_mounts(self):
        """S5: same id from two sources -> exit 1, nothing mounted."""
        skill_a = self.make_skill("shared", tag="a")
        skill_b = self.make_skill("shared", tag="b")
        self.sg("group", "create", "ga")
        self.sg(
            "group", "add", "ga", "shared", "--type", "local", "--path", str(skill_a)
        )
        self.sg("group", "create", "gb")
        self.sg(
            "group", "add", "gb", "shared", "--type", "local", "--path", str(skill_b)
        )
        self.sg("init")
        result = self.sg("use", "ga", "gb")
        self.assertEqual(result.returncode, 1)
        self.assertTrue(result.stderr.strip())
        skills_dir = self.project / ".agents" / "skills"
        if skills_dir.exists():
            self.assertEqual(list(skills_dir.iterdir()), [])

    def test_use_without_init_fails_with_hint(self):
        """Running use in a fresh project -> exit 1 with an 'sg init' hint."""
        result = self.sg("use", "web")
        self.assertEqual(result.returncode, 1)
        self.assertIn("sg init", result.stderr)

    def test_unuse_removes_mount(self):
        """S2: unuse unmounts the skill and exit 0."""
        skill = self.make_skill("demo")
        self.sg("group", "create", "web")
        self.sg("group", "add", "web", "demo", "--type", "local", "--path", str(skill))
        self.sg("init")
        self.assertEqual(self.sg("use", "web").returncode, 0)
        result = self.sg("unuse", "web")
        self.assertEqual(result.returncode, 0)
        self.assertFalse(
            (self.project / ".agents" / "skills" / "demo").exists()
        )

    def test_version_prints_exact_version(self):
        result = self.sg("--version")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "0.1.0")

    def test_help_works(self):
        result = self.sg("--help")
        self.assertEqual(result.returncode, 0)
        self.assertIn("init", result.stdout)

    def test_group_crud_chain(self):
        """create/add/list/show all exit 0 and the group file takes effect."""
        skill = self.make_skill("demo")
        r = self.sg("group", "create", "web", "--description", "Web skills")
        self.assertEqual(r.returncode, 0)
        r = self.sg(
            "group", "add", "web", "demo", "--type", "local", "--path", str(skill)
        )
        self.assertEqual(r.returncode, 0)
        r = self.sg("group", "list")
        self.assertEqual(r.returncode, 0)
        self.assertIn("web", r.stdout)
        r = self.sg("group", "show", "web")
        self.assertEqual(r.returncode, 0)
        self.assertIn("demo", r.stdout)
        group_file = self.sg_home / "groups" / "web.json"
        self.assertTrue(group_file.exists())
        data = json.loads(group_file.read_text(encoding="utf-8"))
        self.assertEqual(data["description"], "Web skills")
        self.assertEqual(data["skills"][0]["id"], "demo")

    def test_ls_lists_mounted_skill(self):
        skill = self.make_skill("demo")
        self.sg("group", "create", "web")
        self.sg("group", "add", "web", "demo", "--type", "local", "--path", str(skill))
        self.sg("init")
        self.sg("use", "web")
        result = self.sg("ls")
        self.assertEqual(result.returncode, 0)
        self.assertIn("demo", result.stdout)

    def test_status_and_sync_exit_zero_on_empty_project(self):
        self.sg("init")
        status = self.sg("status")
        self.assertEqual(status.returncode, 0)
        self.assertEqual(status.stdout.strip(), "")
        self.assertEqual(self.sg("sync").returncode, 0)

    def test_status_without_init_hints_at_init(self):
        result = self.sg("status")
        self.assertEqual(result.returncode, 1)
        self.assertIn("sg init", result.stderr)

    def test_status_reports_ok_line_and_sync_exits_zero(self):
        skill = self.make_skill("demo")
        self.sg("group", "create", "web")
        self.sg("group", "add", "web", "demo", "--type", "local", "--path", str(skill))
        self.sg("init")
        self.sg("use", "web")
        status = self.sg("status")
        self.assertEqual(status.returncode, 0)
        self.assertIn("demo (web): ok", status.stdout)
        sync = self.sg("sync")
        self.assertEqual(sync.returncode, 0)
        self.assertIn("demo: unchanged", sync.stdout)

    def test_doctor_reports_environment(self):
        result = self.sg("doctor")
        self.assertEqual(result.returncode, 0)
        self.assertIn("python", result.stdout)
        self.assertIn("git", result.stdout)
        self.assertIn("junction", result.stdout)

    def test_group_add_local_without_path_fails(self):
        self.sg("group", "create", "web")
        result = self.sg("group", "add", "web", "demo", "--type", "local")
        self.assertEqual(result.returncode, 1)
        self.assertTrue(result.stderr.strip())

    def test_group_add_git_without_repo_fails(self):
        self.sg("group", "create", "web")
        result = self.sg("group", "add", "web", "demo", "--type", "git")
        self.assertEqual(result.returncode, 1)
        self.assertIn("--repo", result.stderr)

    def test_init_force_on_initialized_project(self):
        self.assertEqual(self.sg("init").returncode, 0)
        self.assertEqual(self.sg("init").returncode, 1)
        result = self.sg("init", "--force")
        self.assertEqual(result.returncode, 0)
        self.assertTrue((self.project / ".sg.json").exists())

    def test_env_error_exits_2(self):
        # SG_HOME pointing at a plain file is an environment error (exit 2).
        not_a_dir = self.base / "not-a-dir"
        not_a_dir.write_text("x", encoding="utf-8")
        result = self.sg("group", "create", "demo", env={"SG_HOME": str(not_a_dir)})
        self.assertEqual(result.returncode, 2)
        self.assertTrue(result.stderr.strip())

    def test_group_rm_removes_group(self):
        """group create demo + group rm demo -> exit 0, list no longer shows demo."""
        self.assertEqual(self.sg("group", "create", "demo").returncode, 0)
        result = self.sg("group", "rm", "demo")
        self.assertEqual(result.returncode, 0)
        listing = self.sg("group", "list")
        self.assertEqual(listing.returncode, 0)
        self.assertNotIn("demo", listing.stdout)

    def test_group_rm_missing_group_fails(self):
        """group rm nonexistent -> exit 1, stderr names the group."""
        result = self.sg("group", "rm", "nonexistent")
        self.assertEqual(result.returncode, 1)
        self.assertTrue(result.stderr.strip())
        self.assertIn("nonexistent", result.stderr)


if __name__ == "__main__":
    unittest.main()
