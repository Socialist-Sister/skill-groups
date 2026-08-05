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


def use_groups(root, group_names, extra_skills=None, home=None):
    """Mount every skill of the named groups plus standalone skills, atomically.

    Reads the declaration and lock, resolves every skill into the cache,
    then pre-flights every target link. Any conflict aborts the whole batch
    before a single mount (S5). ``extra_skills`` is an optional list of
    {"id", "source"} dicts for skills declared directly in the project (no
    group); group and standalone skills share one all-or-nothing batch, so
    a same-id/different-source clash anywhere aborts everything. On success
    the declaration groups and skills lists are merged (deduped, order
    preserved) and sg.lock records each mount — standalone entries carry
    group "".
    """
    decl = _read_declaration(root)
    lock = lockfile.read_lock(root)
    skills_dir = project_skills_dir(root, decl)
    mode = decl.get("mode", "auto")

    entries = _collect_skills(group_names)
    for extra in extra_skills or []:
        skill_id = extra.get("id")
        if not isinstance(skill_id, str):
            raise errors.UserError("each standalone skill requires a string id")
        source = groups.validate_source(extra.get("source"))
        entries.append((skill_id, "", source))

    resolved, conflicts = _preflight(entries, skills_dir, home)
    if conflicts:
        raise errors.UserError(_conflict_message(conflicts))

    entries = _dedupe_by_skill_id(entries)

    util.ensure_dir(skills_dir)
    for skill_id, _group, _source in entries:
        cache_dir, _sha = resolved[skill_id]
        mount.mount_skill(skills_dir, skill_id, cache_dir, mode)

    merged = _merge_group_names(decl.get("groups", []), group_names)
    merged_skills = _merge_skills(decl.get("skills", []), extra_skills)
    lockfile.write_declaration(
        root, merged, decl["agent"], decl["mode"], skills=merged_skills
    )
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


def unuse_groups(root, group_names, extra_skill_ids=None, home=None):
    """Un-mount the skills of the named groups and standalone skills.

    Unknown group names and undeclared standalone skill ids are ignored
    (idempotent). A skill is un-mounted only when no remaining declared
    group still references it and it is no longer declared as a standalone
    skill, so groups and skills sharing a skill keep it mounted (S2). The
    declaration drops the group names and the standalone skills and sg.lock
    loses the un-mounted entries.
    """
    decl = _read_declaration(root)
    lock = lockfile.read_lock(root)
    skills_dir = project_skills_dir(root, decl)

    declared_groups = list(decl.get("groups", []))
    declared_skills = list(decl.get("skills", []))
    remove_groups = [name for name in group_names if name in declared_groups]
    remaining_groups = [name for name in declared_groups if name not in remove_groups]

    declared_ids = {skill["id"] for skill in declared_skills}
    remove_skill_ids = [sid for sid in (extra_skill_ids or []) if sid in declared_ids]
    remaining_skills = [s for s in declared_skills if s["id"] not in remove_skill_ids]

    if not remove_groups and not remove_skill_ids:
        return

    removed_skills = _skills_of_groups(remove_groups, lock)
    referenced = _skills_of_groups(remaining_groups, lock)
    referenced.update(skill["id"] for skill in remaining_skills)
    candidates = removed_skills | set(remove_skill_ids)
    for skill_id in sorted(candidates - referenced):
        if skill_id not in lock:
            continue
        link = skills_dir / skill_id
        mount.unmount_skill(link, _mount_kind(link))
        lock.pop(skill_id, None)

    lockfile.write_declaration(
        root, remaining_groups, decl["agent"], decl["mode"], skills=remaining_skills
    )
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


def status(root, home=None):
    """Report the drift state of every declared and locked skill (S8).

    Returns {"skill", "group", "state"} entries sorted by skill id. The
    declaration groups are re-read from their definition files and diffed
    against sg.lock with diff_decl_lock; the filesystem decides the final
    state:

      ok            locked, link present, resolving to the locked cache
                    dir, and that cache dir still exists
      missing-link  locked but the link is gone, points elsewhere, or the
                    cache dir it points at vanished
      drift         locked but the group definition's source/rev changed
                    (or the group differs) and has not been synced
      conflict      a skills-dir entry exists with no lock entry
                    (externally created / hand-edited)
      stale         locked but no declared group contains the skill

    home is accepted for signature symmetry; the cache is not touched here.
    """
    decl = _read_declaration(root)
    lock = lockfile.read_lock(root)
    skills_dir = project_skills_dir(root, decl)
    return [
        {"skill": skill_id, "group": group, "state": state}
        for skill_id, group, state in _assess(decl, lock, skills_dir)
    ]


def sync_groups(root, home=None):
    """Converge mounts to the declaration; return the repair actions (S8).

    Each action is {"skill", "action"}: drift becomes "remounted" when the
    source change moved the cache dir, "renewed" when only the lock entry
    needed refreshing; missing-link becomes "relinked" (cache re-fetched if
    it vanished, wrong mount replaced); stale becomes "removed"; conflict
    is "skipped" — user-owned directories are never deleted; ok is
    "unchanged". Idempotent: a second run on a converged project changes
    nothing on disk and never rewrites sg.lock.
    """
    decl = _read_declaration(root)
    lock = lockfile.read_lock(root)
    skills_dir = project_skills_dir(root, decl)
    mode = decl.get("mode", "auto")
    declared = _declared_skills(decl)
    new_lock = dict(lock)
    actions = []

    for skill_id, _group, state in _assess(decl, lock, skills_dir):
        link = skills_dir / skill_id
        if state == "ok":
            actions.append({"skill": skill_id, "action": "unchanged"})
        elif state == "conflict":
            actions.append({"skill": skill_id, "action": "skipped"})
        elif state == "stale":
            if os.path.lexists(link):
                mount.unmount_skill(link, _mount_kind(link))
            new_lock.pop(skill_id, None)
            actions.append({"skill": skill_id, "action": "removed"})
        elif state == "missing-link":
            info = declared[skill_id]
            cache_dir, sha = cache.ensure_skill_cached(
                info["source"], skill_id, home
            )
            _repair_link(link, cache_dir, mode)
            new_lock[skill_id] = _lock_entry(
                info["group"], info["source"], sha, cache_dir
            )
            actions.append({"skill": skill_id, "action": "relinked"})
        else:  # drift: declaration source differs from the lock
            info = declared[skill_id]
            cache_dir, sha = cache.ensure_skill_cached(
                info["source"], skill_id, home
            )
            if str(cache_dir) != lock.get(skill_id, {}).get("cache_dir"):
                _repair_link(link, cache_dir, mode, replace=True)
                action = "remounted"
            else:
                if not _link_ok(link, cache_dir):
                    _repair_link(link, cache_dir, mode, replace=True)
                action = "renewed"
            new_lock[skill_id] = _lock_entry(
                info["group"], info["source"], sha, cache_dir
            )
            actions.append({"skill": skill_id, "action": action})

    if new_lock != lock:
        lockfile.write_lock(root, new_lock)
    return actions


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
                f"skill {skill_id!r} is declared by two groups or skills with "
                f"different sources and cannot be mounted twice"
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
        "cannot use the requested groups/skills: the following conflicts block "
        "the mount. Nothing was changed.\n" + lines
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


def _merge_skills(existing, extra_skills):
    """Dedupe-merge standalone skills by id, preserving the original order."""
    merged = list(existing)
    seen = {skill["id"] for skill in merged}
    for extra in extra_skills or []:
        skill_id = extra["id"]
        if skill_id not in seen:
            seen.add(skill_id)
            merged.append({"id": skill_id, "source": extra["source"]})
    return merged


def _dedupe_by_skill_id(entries):
    """Drop later entries sharing a skill id; the first occurrence wins.

    Only safe after a conflict-free preflight: any same-id entries still in
    the batch then resolve to the same cache dir, so the first (group)
    entry is the one recorded in the lock.
    """
    seen = set()
    unique = []
    for entry in entries:
        if entry[0] in seen:
            continue
        seen.add(entry[0])
        unique.append(entry)
    return unique


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


def _declared_skills(decl):
    """{skill_id: {"group", "source"}} from the declaration's group files and
    its standalone skill entries.

    Group skills are read in declaration order, then the declaration's own
    "skills" entries (group ""); the first occurrence of a skill id wins (a
    skill shared by two groups keeps the first group's source, matching
    use_groups). A group whose definition file is missing or invalid
    contributes no skills — its locked skills surface as stale.
    """
    declared = {}
    for name in decl.get("groups", []):
        try:
            group = groups.read_group(name)
        except errors.UserError:
            continue
        for skill in group["skills"]:
            declared.setdefault(
                skill["id"], {"group": name, "source": skill["source"]}
            )
    for skill in decl.get("skills", []):
        declared.setdefault(
            skill["id"], {"group": "", "source": skill["source"]}
        )
    return declared


def _link_ok(link, cache_dir):
    """True when a locked mount is healthy: link exists, the cache dir
    still exists, and a link/junction resolves to the locked cache dir.

    A plain directory at the target is treated as a copy mount and counts
    as healthy while the cache dir exists (content cannot be cheaply
    compared). A link/junction resolving anywhere else is broken.
    """
    if not os.path.lexists(link):
        return False
    if not Path(cache_dir).is_dir():
        return False
    if mount.is_link_or_junction(link):
        return _points_at(link, cache_dir)
    return True


def _lock_entry(group, source, sha, cache_dir):
    """Fresh lock entry matching the shape use_groups writes."""
    return {
        "group": group,
        "source": source,
        "resolved_sha": sha,
        "cache_dir": str(cache_dir),
    }


def _repair_link(link, cache_dir, mode, replace=False):
    """Mount cache_dir at link, unmounting a stale target first.

    With replace=True the existing target is always removed before
    mounting (the cache moved and the old link must not survive). Without
    it, only a target that does not resolve to cache_dir is replaced —
    mount_skill would refuse such a target anyway. A missing target is
    simply mounted.
    """
    if os.path.lexists(link) and (replace or not _points_at(link, cache_dir)):
        mount.unmount_skill(link, _mount_kind(link))
    util.ensure_dir(link.parent)
    mount.mount_skill(link.parent, link.name, cache_dir, mode)


def _assess(decl, lock, skills_dir):
    """[(skill_id, group, state), ...] sorted by skill id.

    Classifies every declared skill (from the live group definitions) and
    every locked skill against the filesystem, plus any lock-less entry in
    the skills dir (external conflict). diff_decl_lock splits declared vs
    locked; the filesystem check decides ok vs missing-link. Lock-less
    targets are conflicts regardless of the declaration, so an "added"
    skill whose link already exists surfaces as a conflict, not a mount.
    """
    declared = _declared_skills(decl)
    diff = lockfile.diff_decl_lock(declared, lock)
    entries = []

    for skill_id in diff["added"]:
        if not os.path.lexists(skills_dir / skill_id):
            entries.append((skill_id, declared[skill_id]["group"], "missing-link"))
    for skill_id in diff["changed"]:
        # diff_decl_lock also flags entries with resolved_sha None (local
        # sources lock a None sha by design), so drift is decided on the
        # source dict itself: only a source change is drift. A group-only
        # change falls through to the plain link check.
        entry = lock[skill_id]
        if entry.get("source") != declared[skill_id]["source"]:
            entries.append((skill_id, declared[skill_id]["group"], "drift"))
        else:
            state = (
                "ok"
                if _link_ok(skills_dir / skill_id, entry.get("cache_dir", ""))
                else "missing-link"
            )
            entries.append((skill_id, entry.get("group", ""), state))
    for skill_id in diff["removed"]:
        entries.append((skill_id, lock[skill_id].get("group", ""), "stale"))
    for skill_id in diff["unchanged"]:
        entry = lock[skill_id]
        state = (
            "ok"
            if _link_ok(skills_dir / skill_id, entry.get("cache_dir", ""))
            else "missing-link"
        )
        entries.append((skill_id, entry.get("group", ""), state))

    if skills_dir.is_dir():
        for name in sorted(e.name for e in os.scandir(skills_dir)):
            if name not in lock:
                entries.append((name, "", "conflict"))

    return sorted(entries, key=lambda item: item[0])


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
