"""Skill-group data layer: schema validation and CRUD over groups_dir.

A group is a JSON file at <groups_dir>/<name>.json shaped like::

    {
      "name": "python",
      "description": "Python tooling",
      "skills": [
        {"id": "lint", "source": {"type": "local", "path": "C:/skills/lint"}},
        {"id": "pytest", "source": {"type": "git",
                                     "repo": "owner/repo",
                                     "path": "skills/pytest",
                                     "rev": "main"}}
      ]
    }

Validation failures raise errors.UserError; IO failures surface as
errors.EnvError from util.read_json/write_json. All state lives under
config.groups_dir() so tests redirect it with SG_HOME.
"""

from . import config, errors, util


def validate_source(source):
    """Validate and normalize a skill source dict; UserError on violation.

    type must be "local" (requires a string path) or "git" (requires a
    string repo; path and rev are optional strings). Returns a canonical
    dict carrying only the recognized keys.
    """
    if not isinstance(source, dict):
        raise errors.UserError(
            f"skill source must be an object, got {type(source).__name__}"
        )
    stype = source.get("type")
    if stype == "local":
        path = source.get("path")
        if not isinstance(path, str):
            raise errors.UserError("local source requires a string path")
        return {"type": "local", "path": path}
    if stype == "git":
        repo = source.get("repo")
        if not isinstance(repo, str):
            raise errors.UserError("git source requires a string repo")
        result = {"type": "git", "repo": repo}
        for key in ("path", "rev"):
            if key in source:
                value = source[key]
                if not isinstance(value, str):
                    raise errors.UserError(f"git source {key} must be a string")
                result[key] = value
        return result
    raise errors.UserError(f"unknown source type: {stype!r}")


def _validate_skill(entry):
    """Validate one skill entry (id + source); UserError on violation."""
    if not isinstance(entry, dict):
        raise errors.UserError("each skill must be an object")
    skill_id = entry.get("id")
    if not isinstance(skill_id, str):
        raise errors.UserError("each skill requires a string id")
    if "source" not in entry:
        raise errors.UserError(f"skill {skill_id!r} requires a source")
    return {"id": skill_id, "source": validate_source(entry["source"])}


def validate_group(group):
    """Validate and normalize a group dict; UserError on violation.

    name is required (non-empty str); description defaults to ""; skills
    must be a list of skill entries with unique ids.
    """
    if not isinstance(group, dict):
        raise errors.UserError(
            f"group must be an object, got {type(group).__name__}"
        )
    name = group.get("name")
    if not isinstance(name, str) or not name:
        raise errors.UserError("group requires a non-empty string name")
    description = group.get("description", "")
    if not isinstance(description, str):
        raise errors.UserError("group description must be a string")
    skills = group.get("skills")
    if not isinstance(skills, list):
        raise errors.UserError("group skills must be a list")
    seen = set()
    normalized_skills = []
    for entry in skills:
        skill = _validate_skill(entry)
        if skill["id"] in seen:
            raise errors.UserError(f"duplicate skill id: {skill['id']}")
        seen.add(skill["id"])
        normalized_skills.append(skill)
    return {"name": name, "description": description, "skills": normalized_skills}


def group_path(name):
    """Return groups_dir()/<name>.json; reject empty or separator names."""
    if not isinstance(name, str) or not name:
        raise errors.UserError("group name must be a non-empty string")
    if "/" in name or "\\" in name:
        raise errors.UserError(f"invalid group name: {name!r}")
    return config.groups_dir() / f"{name}.json"


def read_group(name):
    """Read and validate a group by name; unknown group -> UserError."""
    path = group_path(name)
    if not path.exists():
        raise errors.UserError(f"unknown group: {name}")
    return validate_group(util.read_json(path))


def write_group(group):
    """Validate and persist a group dict; returns its file path."""
    normalized = validate_group(group)
    path = group_path(normalized["name"])
    util.write_json(path, normalized)
    return path


def list_groups():
    """Return registered group names sorted alphabetically."""
    gdir = config.groups_dir()
    if not gdir.is_dir():
        return []
    return sorted(path.stem for path in gdir.glob("*.json"))


def create_group(name, description=""):
    """Create a new empty group; duplicate name -> UserError."""
    path = group_path(name)
    if path.exists():
        raise errors.UserError(f"group already exists: {name}")
    write_group({"name": name, "description": description, "skills": []})
    return read_group(name)


def add_skill(group_name, skill_id, source):
    """Append a skill to an existing group; duplicate id -> UserError."""
    group = read_group(group_name)
    seen = {skill["id"] for skill in group["skills"]}
    if skill_id in seen:
        raise errors.UserError(f"duplicate skill id: {skill_id}")
    skill = _validate_skill({"id": skill_id, "source": source})
    group["skills"].append(skill)
    write_group(group)
    return group


def group_show(name):
    """Return the group dict (name/description/skills) for display."""
    return read_group(name)
