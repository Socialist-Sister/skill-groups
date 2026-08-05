import tempfile
import unittest
from pathlib import Path

from sg import errors, lockfile, util


class TestPaths(unittest.TestCase):
    def test_sg_json_points_under_root(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self.assertEqual(lockfile.sg_json(root), root / ".sg.json")

    def test_lock_file_points_under_root(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self.assertEqual(lockfile.lock_file(root), root / "sg.lock")


class TestDeclaration(unittest.TestCase):
    def test_write_creates_sg_json_with_correct_content(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            lockfile.write_declaration(root, ["python"], "agents", "auto")
            self.assertTrue(lockfile.sg_json(root).is_file())
            self.assertEqual(
                util.read_json(lockfile.sg_json(root)),
                {
                    "version": "1",
                    "groups": ["python"],
                    "agent": "agents",
                    "mode": "auto",
                },
            )

    def test_read_returns_what_was_written(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            lockfile.write_declaration(root, ["python", "web"], "claude", "manual")
            self.assertEqual(
                lockfile.read_declaration(root),
                {
                    "version": "1",
                    "groups": ["python", "web"],
                    "agent": "claude",
                    "mode": "manual",
                },
            )

    def test_read_missing_raises_user_error(self):
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(errors.UserError) as ctx:
                lockfile.read_declaration(Path(td))
            self.assertIn("not a skill-groups project", str(ctx.exception))


class TestLock(unittest.TestCase):
    def test_write_then_read_roundtrips(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            entries = {
                "fmt": {
                    "group": "python",
                    "source": {"type": "git", "url": "https://example.com/fmt"},
                    "resolved_sha": "abc123",
                    "cache_dir": "python/fmt",
                }
            }
            lockfile.write_lock(root, entries)
            self.assertEqual(lockfile.read_lock(root), entries)

    def test_write_persists_versioned_shape(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            entries = {
                "fmt": {
                    "group": "python",
                    "source": {"type": "git", "url": "https://example.com/fmt"},
                    "resolved_sha": None,
                    "cache_dir": "python/fmt",
                }
            }
            lockfile.write_lock(root, entries)
            self.assertTrue(lockfile.lock_file(root).is_file())
            self.assertEqual(
                util.read_json(lockfile.lock_file(root)),
                {"version": "1", "skills": entries},
            )

    def test_read_missing_returns_empty_dict(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(lockfile.read_lock(Path(td)), {})


class TestDiffDeclLock(unittest.TestCase):
    SOURCE = {"type": "git", "url": "https://example.com/repo"}

    def test_result_contains_all_four_keys(self):
        self.assertEqual(
            set(lockfile.diff_decl_lock({}, {})),
            {"added", "removed", "changed", "unchanged"},
        )

    def test_decl_only_skills_are_added(self):
        decl = {
            "new": {"group": "python", "source": self.SOURCE},
            "keep": {"group": "python", "source": self.SOURCE},
        }
        lock = {
            "keep": {
                "group": "python",
                "source": self.SOURCE,
                "resolved_sha": "abc",
                "cache_dir": "python/keep",
            }
        }
        result = lockfile.diff_decl_lock(decl, lock)
        self.assertEqual(result["added"], ["new"])
        self.assertEqual(result["removed"], [])
        self.assertEqual(result["changed"], [])
        self.assertEqual(result["unchanged"], ["keep"])

    def test_lock_only_skills_are_removed(self):
        decl = {"keep": {"group": "python", "source": self.SOURCE}}
        lock = {
            "keep": {
                "group": "python",
                "source": self.SOURCE,
                "resolved_sha": "abc",
                "cache_dir": "python/keep",
            },
            "stale": {
                "group": "python",
                "source": self.SOURCE,
                "resolved_sha": "def",
                "cache_dir": "python/stale",
            },
        }
        result = lockfile.diff_decl_lock(decl, lock)
        self.assertEqual(result["removed"], ["stale"])
        self.assertEqual(result["added"], [])
        self.assertEqual(result["changed"], [])
        self.assertEqual(result["unchanged"], ["keep"])

    def test_different_source_is_changed(self):
        decl = {"a": {"group": "python", "source": {"url": "one"}}}
        lock = {
            "a": {
                "group": "python",
                "source": {"url": "two"},
                "resolved_sha": "abc",
                "cache_dir": "python/a",
            }
        }
        result = lockfile.diff_decl_lock(decl, lock)
        self.assertEqual(result["changed"], ["a"])
        self.assertEqual(result["unchanged"], [])

    def test_different_group_is_changed(self):
        decl = {"a": {"group": "python", "source": self.SOURCE}}
        lock = {
            "a": {
                "group": "web",
                "source": self.SOURCE,
                "resolved_sha": "abc",
                "cache_dir": "web/a",
            }
        }
        result = lockfile.diff_decl_lock(decl, lock)
        self.assertEqual(result["changed"], ["a"])
        self.assertEqual(result["unchanged"], [])

    def test_unresolved_rev_is_changed(self):
        decl = {"a": {"group": "python", "source": self.SOURCE}}
        lock = {
            "a": {
                "group": "python",
                "source": self.SOURCE,
                "resolved_sha": None,
                "cache_dir": "python/a",
            }
        }
        result = lockfile.diff_decl_lock(decl, lock)
        self.assertEqual(result["changed"], ["a"])
        self.assertEqual(result["unchanged"], [])

    def test_identical_entries_are_unchanged(self):
        decl = {"a": {"group": "python", "source": self.SOURCE}}
        lock = {
            "a": {
                "group": "python",
                "source": self.SOURCE,
                "resolved_sha": "abc",
                "cache_dir": "python/a",
            }
        }
        result = lockfile.diff_decl_lock(decl, lock)
        self.assertEqual(result["unchanged"], ["a"])
        self.assertEqual(result["changed"], [])

    def test_exercise_all_categories_together(self):
        decl = {
            "added_skill": {"group": "python", "source": self.SOURCE},
            "changed_skill": {"group": "python", "source": self.SOURCE},
            "unchanged_skill": {"group": "python", "source": self.SOURCE},
        }
        lock = {
            "removed_skill": {
                "group": "python",
                "source": self.SOURCE,
                "resolved_sha": "aaa",
                "cache_dir": "python/removed_skill",
            },
            "changed_skill": {
                "group": "python",
                "source": {"type": "git", "url": "https://example.com/other"},
                "resolved_sha": "bbb",
                "cache_dir": "python/changed_skill",
            },
            "unchanged_skill": {
                "group": "python",
                "source": self.SOURCE,
                "resolved_sha": "ccc",
                "cache_dir": "python/unchanged_skill",
            },
        }
        result = lockfile.diff_decl_lock(decl, lock)
        self.assertEqual(result["added"], ["added_skill"])
        self.assertEqual(result["removed"], ["removed_skill"])
        self.assertEqual(result["changed"], ["changed_skill"])
        self.assertEqual(result["unchanged"], ["unchanged_skill"])


if __name__ == "__main__":
    unittest.main()
