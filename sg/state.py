"""Project state orchestration: init / use / unuse / ls.

This module is the integration seam of skill-groups. It owns the scenario
contract (S1-S6): declaring groups in .sg.json, resolving + caching skills
into the shared home, mounting them into the project's agent skills dir,
and keeping sg.lock in sync. All-or-nothing (S5) is enforced by a pre-flight
pass that resolves every skill cache and checks every target link before a
single mount happens.
"""

import os
from pathlib import Path

from . import cache, config, errors, groups, lockfile, mount, util


def init_project(root, agent="agents", mode="auto", home=None):
    """Create .sg.json and make sure the agent skills dir is git-ignored.

    Refuses to overwrite an existing declaration. ``home`` is accepted for
    signature symmetry with the other state functions but not used here.
    """
    root = Path(root)
    if lockfile.sg_json(root).exists():
        raise errors.UserError(
            f"{root} is already initialized as a skill-groups project"
        )
    rel_skills_dir = config.resolve_agent_dir(agent)
    lockfile.write_declaration(root, [], agent, mount.resolve_mode(mode))
    _append_gitignore(root, rel_skills_dir)


def project_skills_dir(root, decl):
    """Absolute agent skills directory inside the project."""
    return Path(root) / config.resolve_agent_dir(decl["agent"])


def use_groups(root, group_names, home=None):
    """Mount every skill of the named groups, atomically.

    Reads the declaration and lock, resolves every skill into the cache,
    then pre-flights every target link. Any conflict aborts the whole batch
    before a single mount (S5). On success the declaration groups are merged
    (deduped, order preserved) and sg.lock records each mount.
    """
    decl = _read_declaration(root)
    lock = lockfile.read_lock(root)
    skills_dir = project_skills_dir(root, decl)
    mode = decl.get("mode", "auto")

    entries = _collect_skills(group_names)
    resolved, conflicts = _preflight(entries, skills_dir, home)
    if conflicts:
        raise errors.UserError(_conflict_message(conflicts))

    util.ensure_dir(skills_dir)
    for skill_id, _group, _source in entries:
        cache_dir, _sha = resolved[skill_id]
        mount.mount_skill(skills_dir, skill_id, cache_dir, mode)

    merged = _merge_group_names(decl.get("groups", []), group_names)
    lockfile.write_declaration(root, merged, decl["agent"], decl["mode"])
    new_lock = dict(lock)
    for skill_id, group_name, source in entries:
        cache_dir, sha = resolved[skill_id]
        new_lock[skill_id] = {
            "group": group_name,
            "source": source,
            "resolved_sha": sha,
            "cache_dir": str(cache_dir),
        }
    lockfile.write_lock(root, new_lock)


def unuse_groups(root, group_names, home=None):
    """Un-mount the skills of the named groups.

    Unknown group names are ignored (idempotent). A skill is un-mounted only
    when no remaining declared group still references it, so groups sharing a
    skill keep it mounted (S2). The declaration drops the group names and
    sg.lock loses the un-mounted entries.
    """
    decl = _read_declaration(root)
    lock = lockfile.read_lock(root)
    skills_dir = project_skills_dir(root, decl)

    declared = list(decl.get("groups", []))
    to_remove = [name for name in group_names if name in declared]
    if not to_remove:
        return
    remaining = [name for name in declared if name not in to_remove]

    removed_skills = _skills_of_groups(to_remove, lock)
    referenced = _skills_of_groups(remaining, lock)
    for skill_id in sorted(removed_skills - referenced):
        link = skills_dir / skill_id
        mount.unmount_skill(link, _mount_kind(link))
        lock.pop(skill_id, None)

    lockfile.write_declaration(root, remaining, decl["agent"], decl["mode"])
    lockfile.write_lock(root, lock)


def list_mounted(root, home=None):
    """[(skill_id, source_type), ...] from sg.lock, sorted by (group, skill_id)."""
    lock = lockfile.read_lock(root)

    def sort_key(item):
        skill_id, entry = item
        return (entry.get("group", ""), skill_id)

    return [
        (skill_id, entry.get("source", {}).get("type", "unknown"))
        for skill_id, entry in sorted(lock.items(), key=sort_key)
    ]


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def _read_declaration(root):
    """Read .sg.json, or raise a beginner-friendly hint to run sg init."""
    try:
        return lockfile.read_declaration(root)
    except errors.UserError as exc:
        raise errors.UserError(
            f"not initialized: run `sg init` in {root} first ({exc})"
        ) from None


def _append_gitignore(root, rel_skills_dir):
    """Append the skills dir line to .gitignore, creating it if missing."""
    gitignore = Path(root) / ".gitignore"
    lines = []
    if gitignore.exists():
        lines = gitignore.read_text(encoding="utf-8").splitlines()
    if rel_skills_dir in lines:
        return
    try:
        gitignore.write_text(
            "\n".join(lines + [rel_skills_dir]) + "\n", encoding="utf-8"
        )
    except OSError as exc:
        raise errors.EnvError(f"failed to update {gitignore}: {exc}") from exc


def _collect_skills(group_names):
    """[(skill_id, group_name, source), ...] in declaration order.

    Duplicate group names in the request are skipped; missing groups raise
    UserError through groups.read_group (the name is in the message).
    """
    entries = []
    seen = set()
    for name in group_names:
        if name in seen:
            continue
        seen.add(name)
        group = groups.read_group(name)
        for skill in group["skills"]:
            entries.append((skill["id"], name, skill["source"]))
    return entries


def _preflight(entries, skills_dir, home):
    """Resolve every cache and detect conflicts; nothing is written.

    Returns (resolved, conflicts). resolved maps skill_id ->
    (cache_dir, sha). A conflict is either the same skill_id resolving to
    two different cache dirs within one batch, or a target link that already
    exists without pointing at this skill's cache dir.
    """
    resolved = {}
    conflicts = []
    seen = set()

    def report(message):
        if message not in seen:
            seen.add(message)
            conflicts.append(message)

    for skill_id, _group, source in entries:
        cache_dir, sha = cache.ensure_skill_cached(source, skill_id, home)
        prev = resolved.get(skill_id)
        if prev is not None and prev[0] != cache_dir:
            report(
                f"skill {skill_id!r} is declared by two groups with different "
                f"sources and cannot be mounted twice"
            )
        resolved.setdefault(skill_id, (cache_dir, sha))

        link = skills_dir / skill_id
        if os.path.lexists(link) and not _points_at(link, cache_dir):
            report(
                f"target {skill_id} already exists and is not mounted from "
                f"this skill's cache; remove it or use another name"
            )
    return resolved, conflicts


def _points_at(link, cache_dir):
    """True when link is a junction/symlink whose target is cache_dir."""
    if not mount.is_link_or_junction(link):
        return False
    target = mount.resolve_link_target(link)
    return target is not None and os.path.abspath(target) == os.path.abspath(cache_dir)


def _conflict_message(conflicts):
    lines = "\n".join(f"  - {c}" for c in conflicts)
    return (
        "cannot use these groups: the following conflicts block the mount. "
        "Nothing was changed.\n" + lines
    )


def _merge_group_names(existing, group_names):
    """Dedupe-merge group names, preserving the original order."""
    merged = list(existing)
    seen = set(merged)
    for name in group_names:
        if name not in seen:
            seen.add(name)
            merged.append(name)
    return merged


def _skills_of_groups(group_names, lock):
    """Skill ids belonging to the given groups.

    Reads each group from the registry; the lock is a fallback for groups
    whose definition files were deleted.
    """
    skills = set()
    names = set(group_names)
    for name in names:
        try:
            group = groups.read_group(name)
        except errors.UserError:
            continue
        skills.update(skill["id"] for skill in group["skills"])
    for skill_id, entry in lock.items():
        if entry.get("group") in names:
            skills.add(skill_id)
    return skills


def _mount_kind(link):
    """Actual mount kind of an existing link, for unmount_skill.

    Junction must be checked before symlink: on Windows a junction also
    reports as a symlink. Plain directories (copy mounts) report "copy".
    """
    if mount.is_junction(link):
        return "junction"
    if os.path.islink(link):
        return "symlink"
    return "copy"
