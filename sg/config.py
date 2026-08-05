"""Global configuration: SG_HOME resolution, agent dir mapping, config read/write.

SG_HOME (env var) is the single entry point for global state. Every module
that needs a global directory goes through this module so the layout stays
the only source of truth.
"""

import os
from pathlib import Path

from . import errors, util

# agent name -> relative skills directory inside a project root
AGENT_DIRS = {
    "agents": ".agents/skills",
    "claude": ".claude/skills",
    "codex": ".codex/skills",
    "opencode": ".opencode/skills",
}

_DEFAULT_CONFIG = {"version": 1, "mode": "auto", "agent": "agents"}


def sg_home():
    """Return the global home dir: $SG_HOME, or ~/.sg.

    Raises EnvError if the resolved path exists and is not a directory.
    """
    raw = os.environ.get("SG_HOME")
    home = Path(raw) if raw else Path.home() / ".sg"
    if home.exists() and not home.is_dir():
        raise errors.EnvError(f"SG_HOME {home} exists and is not a directory")
    return home


def cache_dir(home=None):
    """Cache directory (transient downloads, extracted archives)."""
    return (home or sg_home()) / "cache"


def groups_dir(home=None):
    """Directory holding registered skill groups."""
    return (home or sg_home()) / "groups"


def config_file(home=None):
    """Path to the global config.json."""
    return (home or sg_home()) / "config.json"


def resolve_agent_dir(agent):
    """Return the relative skills dir for a known agent name.

    Raises UserError for unknown agent names.
    """
    try:
        return AGENT_DIRS[agent]
    except KeyError:
        raise errors.UserError(f"unknown agent: {agent}") from None


def default_config():
    """Fresh copy of the built-in defaults (callers may mutate freely)."""
    return dict(_DEFAULT_CONFIG)


def load_config(home=None):
    """Read global config, merging file values over defaults.

    Missing file -> defaults. Unreadable/corrupt file -> EnvError.
    """
    path = config_file(home)
    if not path.exists():
        return default_config()
    data = util.read_json(path)
    if not isinstance(data, dict):
        raise errors.EnvError(f"config {path} must be a JSON object")
    return {**default_config(), **data}


def save_config(cfg, home=None):
    """Persist global config. Any IO failure -> EnvError."""
    util.write_json(config_file(home), cfg)
