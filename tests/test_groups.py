import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from sg import errors, groups


class TestValidateSource(unittest.TestCase):
    def test_local_source_normalizes(self):
        self.assertEqual(
            groups.validate_source({"type": "local", "path": "C:/skills/lint"}),
            {"type": "local", "path": "C:/skills/lint"},
        )

    def test_git_source_normalizes(self):
        self.assertEqual(
            groups.validate_source(
                {"type": "git", "repo": "owner/repo", "path": "skills/pytest", "rev": "main"}
            ),
            {"type": "git", "repo": "owner/repo", "path": "skills/pytest", "rev": "main"},
        )

    def test_git_source_allows_only_required_repo(self):
        self.assertEqual(
            groups.validate_source({"type": "git", "repo": "a/b"}),
            {"type": "git", "repo": "a/b"},
        )

    def test_unknown_type_raises_user_error(self):
        with self.assertRaises(errors.UserError):
            groups.validate_source({"type": "ftp", "path": "x"})

    def test_local_missing_path_raises_user_error(self):
        with self.assertRaises(errors.UserError):
            groups.validate_source({"type": "local"})

    def test_local_non_string_path_raises_user_error(self):
        with self.assertRaises(errors.UserError):
            groups.validate_source({"type": "local", "path": 42})

    def test_git_missing_repo_raises_user_error(self):
        with self.assertRaises(errors.UserError):
            groups.validate_source({"type": "git"})

    def test_non_dict_source_raises_user_error(self):
        with self.assertRaises(errors.UserError):
            groups.validate_source("local")


class TestValidateGroup(unittest.TestCase):
    def test_full_group_roundtrips(self):
        group = {
            "name": "python",
            "description": "py tooling",
            "skills": [
                {"id": "lint", "source": {"type": "local", "path": "C:/skills/lint"}},
                {"id": "pytest", "source": {"type": "git", "repo": "o/r"}},
            ],
        }
        self.assertEqual(groups.validate_group(group), group)

    def test_description_defaults_to_empty_string(self):
        result = groups.validate_group({"name": "python", "skills": []})
        self.assertEqual(result["description"], "")

    def test_missing_name_raises_user_error(self):
        with self.assertRaises(errors.UserError):
            groups.validate_group({"skills": []})

    def test_non_string_name_raises_user_error(self):
        with self.assertRaises(errors.UserError):
            groups.validate_group({"name": 42, "skills": []})

    def test_skills_must_be_list(self):
        with self.assertRaises(errors.UserError):
            groups.validate_group({"name": "x", "skills": "nope"})

    def test_duplicate_skill_id_raises_user_error(self):
        group = {
            "name": "x",
            "skills": [
                {"id": "a", "source": {"type": "local", "path": "p"}},
                {"id": "a", "source": {"type": "local", "path": "q"}},
            ],
        }
        with self.assertRaises(errors.UserError):
            groups.validate_group(group)

    def test_skill_missing_id_raises_user_error(self):
        group = {"name": "x", "skills": [{"source": {"type": "local", "path": "p"}}]}
        with self.assertRaises(errors.UserError):
            groups.validate_group(group)

    def test_skill_missing_source_raises_user_error(self):
        group = {"name": "x", "skills": [{"id": "a"}]}
        with self.assertRaises(errors.UserError):
            groups.validate_group(group)

    def test_non_dict_group_raises_user_error(self):
        with self.assertRaises(errors.UserError):
            groups.validate_group("python")


class TestGroupPath(unittest.TestCase):
    def test_valid_name_is_json_file_under_groups_dir(self):
        with tempfile.TemporaryDirectory() as td:
            with mock.patch.dict(os.environ, {"SG_HOME": td}):
                self.assertEqual(
                    groups.group_path("python"), Path(td) / "groups" / "python.json"
                )

    def test_empty_name_raises_user_error(self):
        with tempfile.TemporaryDirectory() as td:
            with mock.patch.dict(os.environ, {"SG_HOME": td}):
                with self.assertRaises(errors.UserError):
                    groups.group_path("")

    def test_forward_slash_raises_user_error(self):
        with tempfile.TemporaryDirectory() as td:
            with mock.patch.dict(os.environ, {"SG_HOME": td}):
                with self.assertRaises(errors.UserError):
                    groups.group_path("a/b")

    def test_backslash_raises_user_error(self):
        with tempfile.TemporaryDirectory() as td:
            with mock.patch.dict(os.environ, {"SG_HOME": td}):
                with self.assertRaises(errors.UserError):
                    groups.group_path("a\\b")


class TestReadGroup(unittest.TestCase):
    def test_missing_group_raises_user_error_mentioning_unknown_group(self):
        with tempfile.TemporaryDirectory() as td:
            with mock.patch.dict(os.environ, {"SG_HOME": td}):
                with self.assertRaises(errors.UserError) as ctx:
                    groups.read_group("missing")
                self.assertIn("unknown group", str(ctx.exception))


class TestCreateGroup(unittest.TestCase):
    def test_create_then_read_roundtrip(self):
        with tempfile.TemporaryDirectory() as td:
            with mock.patch.dict(os.environ, {"SG_HOME": td}):
                group = groups.create_group("python")
                self.assertEqual(groups.read_group("python"), group)
                self.assertEqual(group["name"], "python")
                self.assertEqual(group["skills"], [])

    def test_default_description_is_empty(self):
        with tempfile.TemporaryDirectory() as td:
            with mock.patch.dict(os.environ, {"SG_HOME": td}):
                self.assertEqual(groups.create_group("python")["description"], "")

    def test_explicit_description_is_stored(self):
        with tempfile.TemporaryDirectory() as td:
            with mock.patch.dict(os.environ, {"SG_HOME": td}):
                self.assertEqual(
                    groups.create_group("python", description="tools")["description"],
                    "tools",
                )

    def test_duplicate_create_raises_user_error(self):
        with tempfile.TemporaryDirectory() as td:
            with mock.patch.dict(os.environ, {"SG_HOME": td}):
                groups.create_group("python")
                with self.assertRaises(errors.UserError):
                    groups.create_group("python")


class TestListGroups(unittest.TestCase):
    def test_no_groups_dir_returns_empty(self):
        with tempfile.TemporaryDirectory() as td:
            with mock.patch.dict(os.environ, {"SG_HOME": td}):
                self.assertEqual(groups.list_groups(), [])

    def test_returns_names_sorted(self):
        with tempfile.TemporaryDirectory() as td:
            with mock.patch.dict(os.environ, {"SG_HOME": td}):
                for name in ("zeta", "alpha", "mid"):
                    groups.create_group(name)
                self.assertEqual(groups.list_groups(), ["alpha", "mid", "zeta"])


class TestAddSkill(unittest.TestCase):
    def test_added_skill_reads_back(self):
        with tempfile.TemporaryDirectory() as td:
            with mock.patch.dict(os.environ, {"SG_HOME": td}):
                groups.create_group("python")
                source = {"type": "local", "path": "C:/skills/lint"}
                groups.add_skill("python", "lint", source)
                group = groups.read_group("python")
                self.assertEqual(group["skills"], [{"id": "lint", "source": source}])

    def test_duplicate_skill_id_raises_user_error(self):
        with tempfile.TemporaryDirectory() as td:
            with mock.patch.dict(os.environ, {"SG_HOME": td}):
                groups.create_group("python")
                groups.add_skill("python", "lint", {"type": "local", "path": "p"})
                with self.assertRaises(errors.UserError):
                    groups.add_skill("python", "lint", {"type": "local", "path": "q"})

    def test_missing_group_raises_user_error(self):
        with tempfile.TemporaryDirectory() as td:
            with mock.patch.dict(os.environ, {"SG_HOME": td}):
                with self.assertRaises(errors.UserError):
                    groups.add_skill("nope", "lint", {"type": "local", "path": "p"})


class TestGroupShow(unittest.TestCase):
    def test_show_returns_name_description_skills(self):
        with tempfile.TemporaryDirectory() as td:
            with mock.patch.dict(os.environ, {"SG_HOME": td}):
                groups.create_group("python", description="tools")
                groups.add_skill("python", "lint", {"type": "local", "path": "C:/skills/lint"})
                shown = groups.group_show("python")
                self.assertEqual(set(shown), {"name", "description", "skills"})
                self.assertEqual(shown["name"], "python")
                self.assertEqual(shown["description"], "tools")
                self.assertEqual(shown["skills"][0]["id"], "lint")


class TestWriteGroup(unittest.TestCase):
    def test_non_dict_raises_user_error(self):
        with tempfile.TemporaryDirectory() as td:
            with mock.patch.dict(os.environ, {"SG_HOME": td}):
                with self.assertRaises(errors.UserError):
                    groups.write_group("python")

    def test_missing_name_raises_user_error(self):
        with tempfile.TemporaryDirectory() as td:
            with mock.patch.dict(os.environ, {"SG_HOME": td}):
                with self.assertRaises(errors.UserError):
                    groups.write_group({"skills": []})

    def test_writes_valid_group_and_reads_back(self):
        with tempfile.TemporaryDirectory() as td:
            with mock.patch.dict(os.environ, {"SG_HOME": td}):
                group = {"name": "rust", "description": "", "skills": []}
                groups.write_group(group)
                self.assertEqual(groups.read_group("rust"), group)


if __name__ == "__main__":
    unittest.main()
