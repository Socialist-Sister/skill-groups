"""Project declaration (.sg.json) and skill lock (sg.lock): read/write + drift diff.

The declaration lists which skill groups a project opts into. The lock records
the exact resolved state (source dict, pinned sha, cache location) per skill.
Drift detection (diff_decl_lock) is the foundation for `sg status` / `sg sync`.
"""

from pathlib import Path

from . import errors, util

_VERSION = "1"


def sg_json(root):
    """Path to the project declaration file (.sg.json) under root."""
    return Path(root) / ".sg.json"


def lock_file(root):
    """Path to the lock file (sg.lock) under root."""
    return Path(root) / "sg.lock"


def write_declaration(root, groups, agent, mode, skills=None):
    """Write .sg.json and return the path written.

    ``skills`` is the optional list of standalone skill entries
    ({"id", "source"}). When omitted (None) the field is not written, so
    legacy declarations keep their exact shape.
    """
    path = sg_json(root)
    data = {"version": _VERSION, "groups": groups, "agent": agent, "mode": mode}
    if skills is not None:
        data["skills"] = skills
    util.write_json(path, data)
    return path


def read_declaration(root):
    """Read .sg.json. UserError if the project has no declaration."""
    path = sg_json(root)
    if not path.exists():
        raise errors.UserError(f"{path} is not a skill-groups project")
    data = util.read_json(path)
    if not isinstance(data, dict):
        raise errors.UserError(f"{path} is not a skill-groups project")
    return data


def write_lock(root, entries):
    """Write sg.lock with the versioned shape; return the path written.

    entries maps skill_id -> {"group", "source", "resolved_sha", "cache_dir"}.
    """
    path = lock_file(root)
    util.write_json(path, {"version": _VERSION, "skills": entries})
    return path


def read_lock(root):
    """Read the sg.lock skills dict. Missing lock -> {} (no error)."""
    path = lock_file(root)
    if not path.exists():
        return {}
    data = util.read_json(path)
    if not isinstance(data, dict) or not isinstance(data.get("skills"), dict):
        raise errors.EnvError(f"lock file {path} has no skills object")
    return data["skills"]


def diff_decl_lock(decl_skills, lock_skills):
    """Pure drift diff between declared and locked skills (no IO).

    decl_skills: {skill_id: {"group": str, "source": dict}}
    lock_skills: {skill_id: {"group": str, "source": dict,
                             "resolved_sha": str|None, "cache_dir": str}}

    Returns {"added", "removed", "changed", "unchanged"} — each a sorted list
    of skill_ids. A skill on both sides is "changed" when its group or source
    differs from the declaration, or when the lock entry was never resolved
    (resolved_sha is None). The declaration carries no sha of its own; a lock
    sha only has to be present, not match a declared value.
    """
    result = {"added": [], "removed": [], "changed": [], "unchanged": []}
    decl_ids = set(decl_skills)
    lock_ids = set(lock_skills)
    result["added"] = sorted(decl_ids - lock_ids)
    result["removed"] = sorted(lock_ids - decl_ids)
    for skill_id in sorted(decl_ids & lock_ids):
        if _matches(decl_skills[skill_id], lock_skills[skill_id]):
            result["unchanged"].append(skill_id)
        else:
            result["changed"].append(skill_id)
    return result


def _matches(decl, lock):
    """True when the locked skill still reflects the declaration."""
    return (
        decl.get("group") == lock.get("group")
        and decl.get("source") == lock.get("source")
        and lock.get("resolved_sha") is not None
    )
