import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from sg import config, errors


def _env_without_sg_home():
    """Return a patched environment copy with SG_HOME removed."""
    return {k: v for k, v in os.environ.items() if k != "SG_HOME"}


class TestSgHome(unittest.TestCase):
    def test_returns_sg_home_when_set(self):
        with tempfile.TemporaryDirectory() as td:
            with mock.patch.dict(os.environ, {"SG_HOME": td}):
                self.assertEqual(config.sg_home(), Path(td))

    def test_returns_home_dot_sg_when_unset(self):
        with mock.patch.dict(os.environ, _env_without_sg_home(), clear=True):
            self.assertEqual(config.sg_home(), Path.home() / ".sg")

    def test_file_as_sg_home_raises_env_error(self):
        with tempfile.TemporaryDirectory() as td:
            blocker = Path(td) / "afile"
            blocker.write_text("not a dir", encoding="utf-8")
            with mock.patch.dict(os.environ, {"SG_HOME": str(blocker)}):
                with self.assertRaises(errors.EnvError):
                    config.sg_home()


class TestSubDirs(unittest.TestCase):
    def test_subdirs_live_under_sg_home(self):
        with tempfile.TemporaryDirectory() as td:
            with mock.patch.dict(os.environ, {"SG_HOME": td}):
                self.assertEqual(config.cache_dir(), Path(td) / "cache")
                self.assertEqual(config.groups_dir(), Path(td) / "groups")
                self.assertEqual(config.config_file(), Path(td) / "config.json")

    def test_subdirs_return_path(self):
        with tempfile.TemporaryDirectory() as td:
            with mock.patch.dict(os.environ, {"SG_HOME": td}):
                self.assertIsInstance(config.cache_dir(), Path)
                self.assertIsInstance(config.groups_dir(), Path)
                self.assertIsInstance(config.config_file(), Path)

    def test_explicit_home_overrides_environment(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td) / "custom"
            self.assertEqual(config.cache_dir(home=home), home / "cache")
            self.assertEqual(config.groups_dir(home=home), home / "groups")
            self.assertEqual(config.config_file(home=home), home / "config.json")


class TestAgentDirs(unittest.TestCase):
    def test_mapping_contains_expected_agents(self):
        self.assertEqual(
            config.AGENT_DIRS,
            {
                "agents": ".agents/skills",
                "claude": ".claude/skills",
                "codex": ".codex/skills",
                "opencode": ".opencode/skills",
            },
        )

    def test_resolve_known_agents(self):
        self.assertEqual(config.resolve_agent_dir("agents"), ".agents/skills")
        self.assertEqual(config.resolve_agent_dir("claude"), ".claude/skills")
        self.assertEqual(config.resolve_agent_dir("codex"), ".codex/skills")
        self.assertEqual(config.resolve_agent_dir("opencode"), ".opencode/skills")

    def test_resolve_unknown_agent_raises_user_error(self):
        with self.assertRaises(errors.UserError) as ctx:
            config.resolve_agent_dir("vim")
        self.assertIn("unknown agent", str(ctx.exception))


class TestConfig(unittest.TestCase):
    def test_default_config(self):
        self.assertEqual(
            config.default_config(),
            {"version": 1, "mode": "auto", "agent": "agents"},
        )
        self.assertIsInstance(config.default_config()["version"], int)

    def test_load_returns_defaults_when_config_missing(self):
        with tempfile.TemporaryDirectory() as td:
            with mock.patch.dict(os.environ, {"SG_HOME": td}):
                self.assertEqual(config.load_config(), config.default_config())

    def test_load_merges_defaults_with_file_values(self):
        with tempfile.TemporaryDirectory() as td:
            with mock.patch.dict(os.environ, {"SG_HOME": td}):
                config.save_config({"agent": "claude"}, home=Path(td))
                self.assertEqual(
                    config.load_config(),
                    {"version": 1, "mode": "auto", "agent": "claude"},
                )

    def test_load_bad_json_raises_env_error(self):
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "config.json").write_text("{broken", encoding="utf-8")
            with mock.patch.dict(os.environ, {"SG_HOME": td}):
                with self.assertRaises(errors.EnvError):
                    config.load_config()

    def test_save_then_load_roundtrips(self):
        with tempfile.TemporaryDirectory() as td:
            with mock.patch.dict(os.environ, {"SG_HOME": td}):
                cfg = {"version": 1, "mode": "manual", "agent": "codex"}
                config.save_config(cfg)
                self.assertEqual(config.load_config(), cfg)


if __name__ == "__main__":
    unittest.main()
