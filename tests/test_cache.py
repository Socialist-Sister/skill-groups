"""Tests for sg.cache: source identity, cache keys, local/git skill caching."""

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from sg import cache, config, errors, git_util


def _git(*args, cwd=None):
    """Run git synchronously for test fixtures."""
    return subprocess.run(
        ["git", *args], capture_output=True, text=True, cwd=cwd, timeout=120
    )


class TestSourceIdentity(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.src = self.root / "skills" / "lint"
        self.src.mkdir(parents=True)

    def test_local_includes_absolute_path(self):
        ident = cache.source_identity({"type": "local", "path": str(self.src)})
        self.assertEqual(ident, "local:" + os.path.abspath(str(self.src)))

    def test_local_identity_is_absolute(self):
        ident = cache.source_identity({"type": "local", "path": "sub/dir"})
        self.assertTrue(os.path.isabs(ident[len("local:") :]))

    def test_git_includes_mapped_repo_path_and_rev(self):
        ident = cache.source_identity(
            {"type": "git", "repo": "owner/repo", "path": "skills/x", "rev": "main"}
        )
        self.assertEqual(ident, "git:https://github.com/owner/repo.git#skills/x@main")

    def test_git_omitted_path_and_rev_stay_empty(self):
        ident = cache.source_identity({"type": "git", "repo": "owner/repo"})
        self.assertEqual(ident, "git:https://github.com/owner/repo.git#@")

    def test_git_different_rev_different_identity(self):
        base = {"type": "git", "repo": "owner/repo", "path": "skills/x"}
        self.assertNotEqual(
            cache.source_identity({**base, "rev": "main"}),
            cache.source_identity({**base, "rev": "dev"}),
        )

    def test_git_repo_url_passed_through(self):
        ident = cache.source_identity({"type": "git", "repo": "https://example.com/x.git"})
        self.assertEqual(ident, "git:https://example.com/x.git#@")


class TestCacheKey(unittest.TestCase):
    def test_key_is_10_hex(self):
        key = cache.cache_key({"type": "local", "path": "C:/skills/a"})
        self.assertEqual(len(key), 10)
        self.assertTrue(all(c in "0123456789abcdef" for c in key))

    def test_different_sources_different_keys(self):
        self.assertNotEqual(
            cache.cache_key({"type": "local", "path": "C:/skills/a"}),
            cache.cache_key({"type": "local", "path": "C:/skills/b"}),
        )

    def test_git_rev_change_changes_key(self):
        base = {"type": "git", "repo": "owner/repo", "path": "p"}
        self.assertNotEqual(
            cache.cache_key({**base, "rev": "a"}),
            cache.cache_key({**base, "rev": "b"}),
        )


class TestSkillCacheDir(unittest.TestCase):
    def test_under_cache_dir_with_key_and_skill_id(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            source = {"type": "local", "path": "C:/x"}
            self.assertEqual(
                cache.skill_cache_dir(source, "lint", home=home),
                config.cache_dir(home) / cache.cache_key(source) / "lint",
            )


class LocalCacheTestCase(unittest.TestCase):
    """Local-source caching with SG_HOME redirected to a temp dir."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.src = self.root / "src skill"
        (self.src / "sub").mkdir(parents=True)
        (self.src / "SKILL.md").write_text("# local\n", encoding="utf-8")
        (self.src / "sub" / "tool.py").write_text("print(1)\n", encoding="utf-8")
        self._env = mock.patch.dict(os.environ, {"SG_HOME": str(self.root / "home")})
        self._env.start()
        self.addCleanup(self._env.stop)
        self.source = {"type": "local", "path": str(self.src)}

    def _result(self, source=None):
        return cache.ensure_skill_cached(source or self.source, "lint")


class TestEnsureLocalCached(LocalCacheTestCase):
    def test_first_call_copies_byte_identical(self):
        skill_dir, sha = self._result()
        self.assertIsNone(sha)
        self.assertEqual(
            skill_dir, config.cache_dir() / cache.cache_key(self.source) / "lint"
        )
        for rel in ("SKILL.md", "sub/tool.py"):
            self.assertEqual(
                (skill_dir / rel).read_bytes(), (self.src / rel).read_bytes(), msg=rel
            )

    def test_second_call_reuses_without_recopy(self):
        skill_dir, _ = self._result()
        cached_md = skill_dir / "SKILL.md"
        cached_md.write_text("# edited locally\n", encoding="utf-8")
        again, _ = self._result()
        self.assertEqual(again, skill_dir)
        self.assertEqual(cached_md.read_text(encoding="utf-8"), "# edited locally\n")

    def test_missing_source_path_raises_user_error(self):
        with self.assertRaises(errors.UserError):
            self._result({"type": "local", "path": str(self.root / "nope")})

    def test_source_file_instead_of_dir_raises_user_error(self):
        file_path = self.root / "file.txt"
        file_path.write_text("x", encoding="utf-8")
        with self.assertRaises(errors.UserError):
            self._result({"type": "local", "path": str(file_path)})


class GitCacheTestCase(unittest.TestCase):
    """Real local git repo; skip entirely when git is not installed."""

    def setUp(self):
        if shutil.which("git") is None:
            self.skipTest("git not available")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self._env = mock.patch.dict(os.environ, {"SG_HOME": str(self.root / "home")})
        self._env.start()
        self.addCleanup(self._env.stop)
        # Space in the repo dir name: exercises path handling.
        self.repo = self.root / "src repo"
        self.repo.mkdir()
        (self.repo / "skills" / "lint").mkdir(parents=True)
        (self.repo / "SKILL.md").write_text("# from git\n", encoding="utf-8")
        (self.repo / "README.md").write_text("readme\n", encoding="utf-8")
        (self.repo / "skills" / "lint" / "SKILL.md").write_text(
            "# lint\n", encoding="utf-8"
        )
        (self.repo / "skills" / "lint" / "tool.py").write_text(
            "x=1\n", encoding="utf-8"
        )
        _git("init", "-b", "main", cwd=self.repo)
        _git("config", "user.name", "t", cwd=self.repo)
        _git("config", "user.email", "t@t", cwd=self.repo)
        _git("add", ".", cwd=self.repo)
        proc = _git("commit", "-m", "init", cwd=self.repo)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.head = _git("rev-parse", "HEAD", cwd=self.repo).stdout.strip()
        self.file_url = self.repo.as_uri()

    def _source(self, **overrides):
        source = {"type": "git", "repo": self.file_url}
        source.update(overrides)
        return source

    def _cached(self, source, skill_id="lint"):
        return cache.ensure_skill_cached(source, skill_id)


class TestEnsureGitCached(GitCacheTestCase):
    def test_branch_source_copies_content_and_returns_head_sha(self):
        source = self._source(rev="main")
        skill_dir, sha = self._cached(source)
        self.assertEqual(len(sha), 40)
        self.assertTrue(set(sha) <= set("0123456789abcdef"))
        self.assertEqual(sha, self.head)
        self.assertEqual(
            skill_dir, config.cache_dir() / cache.cache_key(source) / "lint"
        )
        self.assertTrue((skill_dir / ".repo").is_dir())
        self.assertEqual(git_util.head_sha(skill_dir / ".repo"), self.head)
        self.assertEqual(
            (skill_dir / "SKILL.md").read_text(encoding="utf-8"), "# from git\n"
        )
        self.assertEqual(
            (skill_dir / "README.md").read_text(encoding="utf-8"), "readme\n"
        )
        self.assertFalse((skill_dir / ".git").exists())

    def test_second_call_reuses_repo_clone(self):
        source = self._source(rev="main")
        self._cached(source)
        with mock.patch(
            "sg.git_util.shallow_clone", wraps=git_util.shallow_clone
        ) as clone:
            skill_dir, sha = self._cached(source)
            clone.assert_not_called()
        self.assertEqual(sha, self.head)
        self.assertTrue((skill_dir / "SKILL.md").is_file())

    def test_subpath_copies_only_that_directory(self):
        source = self._source(rev="main", path="skills/lint")
        skill_dir, sha = self._cached(source)
        self.assertEqual(sha, self.head)
        self.assertTrue((skill_dir / "SKILL.md").is_file())
        self.assertTrue((skill_dir / "tool.py").is_file())
        self.assertFalse((skill_dir / "README.md").exists())
        self.assertEqual(
            (skill_dir / "SKILL.md").read_text(encoding="utf-8"), "# lint\n"
        )

    def test_missing_subpath_raises_user_error(self):
        source = self._source(rev="main", path="nope/missing")
        with self.assertRaises(errors.UserError):
            self._cached(source)

    def test_commit_sha_source_resolves_to_that_sha(self):
        source = self._source(rev=self.head)
        skill_dir, sha = self._cached(source)
        self.assertEqual(sha, self.head)
        self.assertEqual(
            (skill_dir / "SKILL.md").read_text(encoding="utf-8"), "# from git\n"
        )

    def test_git_unavailable_raises_env_error(self):
        source = self._source(rev="main")
        with mock.patch("sg.git_util.git_available", return_value=False):
            with self.assertRaises(errors.EnvError):
                self._cached(source)


if __name__ == "__main__":
    unittest.main()
