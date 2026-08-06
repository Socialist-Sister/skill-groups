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

from . import cache, config, errors, git_util, groups, lockfile, mount, util


def init_project(root, agents=None, mode="auto", home=None, force=False):
    """Create .sg.json (schema v2) and git-ignore every agent skills dir.

    ``agents`` is a list of agent names (default ["agents"]). Without
    ``force`` an existing declaration is refused; with it, the declaration
    is rewritten keeping the declared groups and standalone skills, so
    `sg init --force --agent claude` can retarget an existing project.
    ``home`` is accepted for signature symmetry with the other state
    functions but not used here.
    """
    root = Path(root)
    agents = _normalize_agents(agents)
    mode = mount.resolve_mode(mode)
    existing = None
    if lockfile.sg_json(root).exists():
        if not force:
            raise errors.UserError(
                f"{root} is already initialized as a skill-groups project"
            )
        existing = lockfile.read_declaration(root)
    groups_list = list(existing.get("groups", [])) if existing else []
    skills_list = list(existing.get("skills", [])) if existing else []
    lockfile.write_declaration(
        root, _decl_dict(groups_list, agents, mode, skills_list)
    )
    _append_gitignore(root, [config.resolve_agent_dir(a) for a in agents])


def _normalize_agents(agents):
    """Coerce an agents argument to a validated, non-empty list of names."""
    if agents is None:
        agents = ["agents"]
    elif isinstance(agents, str):
        agents = [agents]
    result = []
    for name in agents:
        config.resolve_agent_dir(name)  # raises UserError for unknown names
        if name not in result:
            result.append(name)
    return result


def _decl_dict(groups_list, agents, mode, skills_list):
    """Declaration dict in schema v2 (agents is a list)."""
    return {
        "version": "2",
        "groups": groups_list,
        "agents": list(agents),
        "mode": mode,
        "skills": skills_list,
    }


def _agents_of(decl):
    """Agent names from a declaration, reading v2 or legacy v1 shapes."""
    agents = decl.get("agents")
    if isinstance(agents, list) and agents:
        return agents
    agent = decl.get("agent", "agents")
    return [agent] if agent else ["agents"]


def project_skills_dirs(root, decl):
    """Absolute agent skills directories inside the project (one per agent)."""
    return [Path(root) / config.resolve_agent_dir(a) for a in _agents_of(decl)]


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
    skills_dirs = project_skills_dirs(root, decl)
    mode = decl.get("mode", "auto")

    entries = _collect_skills(group_names)
    for extra in extra_skills or []:
        skill_id = extra.get("id")
        if not isinstance(skill_id, str):
            raise errors.UserError("each standalone skill requires a string id")
        source = groups.validate_source(extra.get("source"))
        entries.append((skill_id, "", source))

    resolved, conflicts = _preflight(entries, skills_dirs, home)
    if conflicts:
        raise errors.UserError(_conflict_message(conflicts))

    entries = _dedupe_by_skill_id(entries)

    for d in skills_dirs:
        util.ensure_dir(d)
    for skill_id, _group, _source in entries:
        cache_dir, _sha = resolved[skill_id]
        for d in skills_dirs:
            mount.mount_skill(d, skill_id, cache_dir, mode)

    merged = _merge_group_names(decl.get("groups", []), group_names)
    merged_skills = _merge_skills(decl.get("skills", []), extra_skills)
    lockfile.write_declaration(
        root,
        _decl_dict(merged, _agents_of(decl), mode, merged_skills),
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
    skills_dirs = project_skills_dirs(root, decl)

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
        for d in skills_dirs:
            link = d / skill_id
            mount.unmount_skill(link, _mount_kind(link))
        lock.pop(skill_id, None)

    lockfile.write_declaration(
        root,
        _decl_dict(remaining_groups, _agents_of(decl), decl.get("mode", "auto"), remaining_skills),
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
    state. With multiple agent dirs the state is aggregated across all of
    them (a skill is ok only when healthy in every dir; any dir with a
    lock-less entry for a skill reports conflict):

      ok            locked, link present in every agent dir, resolving to
                    the locked cache dir, and that cache dir still exists
      missing-link  locked but at least one link is gone, points elsewhere,
                    or the cache dir it points at vanished
      drift         locked but the group definition's source/rev changed
                    (or the group differs) and has not been synced
      conflict      a skills-dir entry exists with no lock entry
                    (externally created / hand-edited)
      stale         locked but no declared group contains the skill

    home is accepted for signature symmetry; the cache is not touched here.
    """
    decl = _read_declaration(root)
    lock = lockfile.read_lock(root)
    skills_dirs = project_skills_dirs(root, decl)
    return [
        {"skill": skill_id, "group": group, "state": state}
        for skill_id, group, state in _assess(decl, lock, skills_dirs)
    ]


def sync_groups(root, home=None):
    """Converge mounts to the declaration; return the repair actions (S8).

    Each action is {"skill", "action"}: drift becomes "remounted" when the
    source change moved the cache dir, "renewed" when only the lock entry
    needed refreshing; missing-link becomes "relinked" (cache re-fetched if
    it vanished, wrong mount replaced); stale becomes "removed"; conflict
    is "skipped" — user-owned directories are never deleted; ok is
    "unchanged". Every repair is applied to every agent skills dir.
    Idempotent: a second run on a converged project changes nothing on
    disk and never rewrites sg.lock.
    """
    decl = _read_declaration(root)
    lock = lockfile.read_lock(root)
    skills_dirs = project_skills_dirs(root, decl)
    mode = decl.get("mode", "auto")
    declared = _declared_skills(decl)
    new_lock = dict(lock)
    actions = []

    for skill_id, _group, state in _assess(decl, lock, skills_dirs):
        if state == "ok":
            actions.append({"skill": skill_id, "action": "unchanged"})
        elif state == "conflict":
            actions.append({"skill": skill_id, "action": "skipped"})
        elif state == "stale":
            for d in skills_dirs:
                link = d / skill_id
                if os.path.lexists(link):
                    mount.unmount_skill(link, _mount_kind(link))
            new_lock.pop(skill_id, None)
            actions.append({"skill": skill_id, "action": "removed"})
        elif state == "missing-link":
            info = declared[skill_id]
            cache_dir, sha = cache.ensure_skill_cached(
                info["source"], skill_id, home
            )
            for d in skills_dirs:
                link = d / skill_id
                if not _link_ok(link, cache_dir):
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
                for d in skills_dirs:
                    _repair_link(d / skill_id, cache_dir, mode, replace=True)
                action = "remounted"
            else:
                for d in skills_dirs:
                    link = d / skill_id
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


def update_groups(root, home=None):
    """Refresh git-source skills to their latest upstream; return actions (S9).

    Only skills that are both declared and locked are considered (stale
    locked skills are left alone). A git source pinned to a 40-char commit
    sha cannot move and reports "unchanged"; every other git source is
    re-fetched and the lock's resolved_sha is refreshed when it moved.
    Local sources report "unchanged". Actions are {"skill", "action"} with
    action "updated" or "unchanged". A fetch failure aborts with the error
    before any lock rewrite; previously refreshed skills keep their new
    state, so a partial update is visible in sg.status afterwards.
    """
    decl = _read_declaration(root)
    lock = lockfile.read_lock(root)
    declared = _declared_skills(decl)
    new_lock = dict(lock)
    actions = []

    for skill_id in sorted(lock):
        if skill_id not in declared:
            continue
        source = declared[skill_id]["source"]
        if source.get("type") != "git" or git_util.is_commit_sha(source.get("rev")):
            actions.append({"skill": skill_id, "action": "unchanged"})
            continue
        cache_dir, sha = cache.refresh_skill_cached(source, skill_id, home)
        if sha == lock[skill_id].get("resolved_sha"):
            actions.append({"skill": skill_id, "action": "unchanged"})
            continue
        entry = dict(lock[skill_id])
        entry["resolved_sha"] = sha
        entry["cache_dir"] = str(cache_dir)
        new_lock[skill_id] = entry
        actions.append({"skill": skill_id, "action": "updated"})

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


def _append_gitignore(root, rel_skills_dirs):
    """Append the skills dir lines to .gitignore, creating it if missing."""
    gitignore = Path(root) / ".gitignore"
    lines = []
    if gitignore.exists():
        lines = gitignore.read_text(encoding="utf-8").splitlines()
    missing = [d for d in rel_skills_dirs if d not in lines]
    if not missing:
        return
    try:
        gitignore.write_text(
            "\n".join(lines + missing) + "\n", encoding="utf-8"
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


def _preflight(entries, skills_dirs, home):
    """Resolve every cache and detect conflicts; nothing is written.

    Returns (resolved, conflicts). resolved maps skill_id ->
    (cache_dir, sha). A conflict is either the same skill_id resolving to
    two different cache dirs within one batch, or a target link in any
    agent skills dir that already exists without pointing at this skill's
    cache dir.
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

        for d in skills_dirs:
            link = d / skill_id
            if os.path.lexists(link) and not _points_at(link, cache_dir):
                report(
                    f"target {skill_id} already exists in {d} and is not "
                    f"mounted from this skill's cache; remove it or use "
                    f"another name"
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


_STATE_PRIORITY = {"ok": 0, "missing-link": 1, "stale": 1, "drift": 2, "conflict": 3}


def _assess(decl, lock, skills_dirs):
    """[(skill_id, group, state), ...] sorted by skill id.

    Classifies every declared skill (from the live group definitions) and
    every locked skill against the filesystem, plus any lock-less entry in
    the skills dirs (external conflict). diff_decl_lock splits declared vs
    locked; the filesystem check decides ok vs missing-link. With multiple
    skills dirs, per-dir verdicts are aggregated per skill: missing-link
    when any dir is unhealthy, conflict when any dir holds a lock-less
    entry, and the severest of (conflict > drift > stale > missing-link >
    ok) wins when categories overlap. Lock-less targets are conflicts
    regardless of the declaration, so an "added" skill whose link already
    exists surfaces as a conflict, not a mount.
    """
    declared = _declared_skills(decl)
    diff = lockfile.diff_decl_lock(declared, lock)
    agg = {}

    def record(skill_id, group, state):
        prev = agg.get(skill_id)
        if prev is None or _STATE_PRIORITY[state] > _STATE_PRIORITY[prev[1]]:
            agg[skill_id] = (group, state)

    for skill_id in diff["added"]:
        if not all(os.path.lexists(d / skill_id) for d in skills_dirs):
            record(skill_id, declared[skill_id]["group"], "missing-link")
    for skill_id in diff["changed"]:
        # diff_decl_lock also flags entries with resolved_sha None (local
        # sources lock a None sha by design), so drift is decided on the
        # source dict itself: only a source change is drift. A group-only
        # change falls through to the plain link check.
        entry = lock[skill_id]
        if entry.get("source") != declared[skill_id]["source"]:
            record(skill_id, declared[skill_id]["group"], "drift")
        else:
            state = (
                "ok"
                if all(
                    _link_ok(d / skill_id, entry.get("cache_dir", ""))
                    for d in skills_dirs
                )
                else "missing-link"
            )
            record(skill_id, entry.get("group", ""), state)
    for skill_id in diff["removed"]:
        record(skill_id, lock[skill_id].get("group", ""), "stale")
    for skill_id in diff["unchanged"]:
        entry = lock[skill_id]
        state = (
            "ok"
            if all(
                _link_ok(d / skill_id, entry.get("cache_dir", ""))
                for d in skills_dirs
            )
            else "missing-link"
        )
        record(skill_id, entry.get("group", ""), state)

    for d in skills_dirs:
        if not d.is_dir():
            continue
        for name in sorted(e.name for e in os.scandir(d)):
            if name not in lock:
                record(name, "", "conflict")

    return sorted(
        (skill_id, group, state)
        for skill_id, (group, state) in agg.items()
    )


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
