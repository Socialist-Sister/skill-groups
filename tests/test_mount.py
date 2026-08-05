"""Tests for sg.mount — Windows junction / symlink / copy mounting primitives.

These tests run for real on Windows. Cleanup order matters: unmount must
run BEFORE the TemporaryDirectory is removed, otherwise Windows cannot
remove a directory that still contains a live junction. addCleanup is
LIFO, so td.cleanup is registered first (runs last) and every unmount is
registered after it (runs first).
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path

from sg import errors, mount

IS_WIN = sys.platform == "win32"


class TestResolveMode(unittest.TestCase):
    def test_valid_modes_round_trip(self):
        for mode in mount.MODES:
            self.assertEqual(mount.resolve_mode(mode), mode)

    def test_unknown_mode_raises_user_error(self):
        for bad in ("hardlink", "bind", "junction", "", None):
            with self.assertRaises(errors.UserError):
                mount.resolve_mode(bad)


class TestResolveLinkTarget(unittest.TestCase):
    def test_plain_directory_returns_none(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertIsNone(mount.resolve_link_target(Path(td)))

    def test_missing_path_returns_none(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertIsNone(mount.resolve_link_target(Path(td) / "nope"))


class TestJunction(unittest.TestCase):
    def test_create_junction_lists_target_contents_and_target_intact(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        root = Path(td.name)
        target = root / "target"
        (target / "sub").mkdir(parents=True)
        (target / "hello.txt").write_text("hi", encoding="utf-8")
        link = root / "link"
        mount.create_junction(target, link)
        self.addCleanup(lambda: mount.unmount_skill(link, "junction"))

        self.assertTrue(mount.is_junction(link))
        self.assertEqual(sorted(p.name for p in link.iterdir()), ["hello.txt", "sub"])
        self.assertEqual((link / "hello.txt").read_text(encoding="utf-8"), "hi")
        # target directory must remain fully intact
        self.assertTrue((target / "hello.txt").exists())
        self.assertTrue((target / "sub").is_dir())

    def test_relative_target_raises_user_error(self):
        with tempfile.TemporaryDirectory() as td:
            link = Path(td) / "link"
            with self.assertRaises(errors.UserError):
                mount.create_junction("relative/target", link)

    @unittest.skipUnless(IS_WIN, "junctions only exist on Windows")
    def test_is_junction_plain_dir_is_false(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertFalse(mount.is_junction(Path(td)))

    def test_is_junction_never_blows_up(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertIsNotNone(mount.is_junction(Path(td)))
            self.assertFalse(mount.is_junction(Path(td) / "missing"))


class TestMountSkill(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        self.root = Path(self.td.name)
        self.skills = self.root / "skills"
        self.skills.mkdir()
        self.cache = self.root / "cache"
        (self.cache / "SKILL.md").mkdir(parents=True)
        (self.cache / "SKILL.md" / "SKILL.md").write_text("# demo", encoding="utf-8")
        self.link = self.skills / "demo"

    def test_first_mount_created_second_same_source_exists(self):
        self.assertEqual(
            mount.mount_skill(self.skills, "demo", self.cache, "auto"), "created"
        )
        self.addCleanup(lambda: mount.unmount_skill(self.link, "junction"))
        self.assertEqual(
            mount.mount_skill(self.skills, "demo", self.cache, "auto"), "exists"
        )
        # still exists on a third call — idempotent
        self.assertEqual(
            mount.mount_skill(self.skills, "demo", self.cache, "auto"), "exists"
        )

    def test_existing_different_source_raises_user_error_without_overwrite(self):
        other = self.root / "other"
        other.mkdir()
        self.assertEqual(
            mount.mount_skill(self.skills, "demo", self.cache, "auto"), "created"
        )
        self.addCleanup(lambda: mount.unmount_skill(self.link, "junction"))
        with self.assertRaises(errors.UserError):
            mount.mount_skill(self.skills, "demo", other, "auto")
        # original mount is untouched
        self.assertTrue(mount.is_link_or_junction(self.link))
        self.assertEqual(
            mount.resolve_link_target(self.link),
            Path(os.path.abspath(self.cache)),
        )

    def test_existing_plain_dir_raises_user_error(self):
        self.link.mkdir()
        with self.assertRaises(errors.UserError):
            mount.mount_skill(self.skills, "demo", self.cache, "auto")


class TestCopyMode(unittest.TestCase):
    def test_copy_mount_then_unmount_removes_link_keeps_target(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            skills = root / "skills"
            skills.mkdir()
            cache = root / "cache"
            cache.mkdir()
            (cache / "SKILL.md").write_text("# demo", encoding="utf-8")
            link = skills / "demo"
            self.assertEqual(
                mount.mount_skill(skills, "demo", cache, "copy"), "created"
            )
            self.assertTrue(link.exists())
            self.assertEqual((link / "SKILL.md").read_text(encoding="utf-8"), "# demo")
            mount.unmount_skill(link, "copy")
            self.assertFalse(link.exists())
            self.assertTrue((cache / "SKILL.md").exists())


class TestSymlinkMode(unittest.TestCase):
    def test_symlink_mount_creates_link_or_raises_oserror(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        root = Path(td.name)
        skills = root / "skills"
        skills.mkdir()
        cache = root / "cache"
        cache.mkdir()
        link = skills / "demo"
        try:
            mount.mount_skill(skills, "demo", cache, "symlink")
        except OSError as exc:
            # No symlink privilege (developer mode off / no admin): an
            # unwrapped OSError is the correct, expected behavior.
            self.assertIsInstance(exc, OSError)
            return
        self.assertTrue(os.path.islink(link))
        self.assertEqual(
            mount.mount_skill(skills, "demo", cache, "symlink"), "exists"
        )
        self.addCleanup(lambda: mount.unmount_skill(link, "symlink"))

    def test_create_symlink_raises_oserror_unwrapped_on_failure(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        root = Path(td.name)
        target = root / "target"
        target.mkdir()
        link = root / "link"
        try:
            mount.create_symlink(target, link)
        except OSError as exc:
            self.assertIsInstance(exc, OSError)
            return
        self.assertTrue(os.path.islink(link))
        self.addCleanup(lambda: mount.unmount_skill(link, "symlink"))


class TestUnmount(unittest.TestCase):
    def test_unmount_junction_removes_link_keeps_target(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        root = Path(td.name)
        target = root / "target"
        target.mkdir()
        (target / "keep.txt").write_text("keep", encoding="utf-8")
        link = root / "link"
        mount.create_junction(target, link)
        self.assertTrue(mount.is_junction(link))
        mount.unmount_skill(link, "junction")
        self.assertFalse(mount.is_junction(link))
        self.assertFalse(os.path.lexists(link))
        self.assertEqual((target / "keep.txt").read_text(encoding="utf-8"), "keep")

    def test_unmount_missing_path_is_tolerant(self):
        with tempfile.TemporaryDirectory() as td:
            missing = Path(td) / "nope"
            for mode in ("junction", "symlink", "copy"):
                mount.unmount_skill(missing, mode)  # must not raise


if __name__ == "__main__":
    unittest.main()
