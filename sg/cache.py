"""Skill caching: source identity -> cache dir -> local/git fetch.

Core data flow: group -> source -> cache -> mount.  For a given skill
source, ensure_skill_cached produces a stable per-source cache directory
whose content is a copy of the skill (a local path, or a sub-path of a
git repo).  The cache key derives from the source identity, and for git
sources the requested rev is part of the identity: a rev change yields a
new cache directory, so a stale cache can never be served for a new rev.

Python 3.9 compatible (no match statements, no os.path.isjunction).
"""

import os
import shutil
import stat
from pathlib import Path

from . import config, errors, git_util, util


def source_identity(source):
    """Stable string identity for a validated skill source dict.

    local: "local:<abspath>".  git: "git:<clone_url>#<path>@<rev>".  The
    clone_url mapping keeps owner/repo and explicit URLs canonical; rev is
    part of the identity so changing it produces a different cache key.
    """
    if source.get("type") == "git":
        path = source.get("path") or ""
        rev = source.get("rev") or ""
        return f"git:{git_util.clone_url(source['repo'])}#{path}@{rev}"
    return f"local:{os.path.abspath(source['path'])}"


def cache_key(source):
    """10-hex sha256 of the source identity (cache dir name component)."""
    return util.sha256_id(source_identity(source))


def skill_cache_dir(source, skill_id, home=None):
    """Cache directory for one skill: <cache>/<key>/<skill_id>/."""
    return config.cache_dir(home) / cache_key(source) / skill_id


def ensure_skill_cached(source, skill_id, home=None):
    """Ensure skill content is cached; return (skill_dir, resolved_sha or None).

    local: copy source["path"] into the skill dir once; a pre-existing
    SKILL.md makes the call idempotent (no re-copy).  resolved_sha is None.
    git: shallow-clone into <skill_dir>/.repo once (reused on later calls),
    copy the repo (or its "path" subdir) into the skill dir, and return the
    clone's HEAD sha.  A missing local path, a missing git sub-path, and a
    missing git binary all surface as errors here (UserError/EnvError).
    """
    skill_dir = skill_cache_dir(source, skill_id, home)
    if source.get("type") == "git":
        return _ensure_git(source, skill_dir)
    return _ensure_local(source, skill_dir)


def _ensure_local(source, skill_dir):
    """Idempotent local fetch: SKILL.md presence gates the copy."""
    if (skill_dir / "SKILL.md").exists():
        return skill_dir, None
    src = source["path"]
    if not src or not Path(src).is_dir():
        raise errors.UserError(f"skill source path not found: {src}")
    util.copy_tree(src, skill_dir)
    return skill_dir, None


def _ensure_git(source, skill_dir):
    """Git fetch: clone once into .repo, copy the skill content, return HEAD sha."""
    repo_dir = skill_dir / ".repo"
    if not repo_dir.is_dir():
        util.ensure_dir(skill_dir)
        git_util.shallow_clone(
            git_util.clone_url(source["repo"]), repo_dir, rev=source.get("rev") or None
        )
    resolved_sha = git_util.head_sha(repo_dir)
    if not (skill_dir / "SKILL.md").exists():
        sub = source.get("path") or ""
        src = repo_dir / sub if sub else repo_dir
        if not src.is_dir():
            raise errors.UserError(
                f"skill path {sub!r} not found in repo {source['repo']}"
            )
        # Merge-copy so the .repo clone survives; skip the clone's .git dir.
        try:
            shutil.copytree(
                src,
                skill_dir,
                dirs_exist_ok=True,
                ignore=shutil.ignore_patterns(".git"),
            )
        except Exception as exc:
            raise errors.EnvError(f"failed to copy {src} -> {skill_dir}: {exc}") from exc
    return skill_dir, resolved_sha


def refresh_skill_cached(source, skill_id, home=None):
    """Re-fetch a git source's cache to the latest upstream; return (dir, sha).

    Only meaningful for git sources; local sources return the existing
    cache untouched (their content is the source itself). The clone is
    refreshed in place — the fetch happens before anything is rewritten,
    so a network failure leaves the previous content and the mounts intact.
    A cache whose clone was manually deleted falls back to a fresh clone.
    """
    skill_dir = skill_cache_dir(source, skill_id, home)
    if source.get("type") != "git":
        return skill_dir, None
    if (skill_dir / ".repo").is_dir():
        return _refresh_git(source, skill_dir)
    return _ensure_git(source, skill_dir)


def _refresh_git(source, skill_dir):
    """Update an existing git cache: fetch, reset, re-copy the skill content.

    The clone's working tree is reset to the fetched ref, the cached skill
    content is wiped (keeping .repo), then re-copied from the clone, so
    upstream deletions are reflected too. Returns (skill_dir, new HEAD sha).
    """
    repo_dir = skill_dir / ".repo"
    git_util.refresh_repo(repo_dir, source.get("rev") or None)
    for child in os.listdir(skill_dir):
        if child != ".repo":
            _force_rmtree(skill_dir / child)
    sub = source.get("path") or ""
    src = repo_dir / sub if sub else repo_dir
    if not src.is_dir():
        raise errors.UserError(
            f"skill path {sub!r} not found in repo {source['repo']}"
        )
    shutil.copytree(
        src,
        skill_dir,
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns(".git"),
    )
    return skill_dir, git_util.head_sha(repo_dir)


def _force_rmtree(path):
    """Remove a file or a directory tree, retrying read-only entries.

    Directories are removed with rmtree (read-only files created by git
    checkouts on Windows are chmodded and retried); plain files are
    unlinked directly, since rmtree on a file always fails on os.scandir.
    """
    path = Path(path)
    if path.is_dir() and not path.is_symlink():
        def _onerror(func, failed_path, _exc_info):
            os.chmod(failed_path, stat.S_IWRITE)
            func(failed_path)

        shutil.rmtree(path, onerror=_onerror)
        return
    try:
        os.unlink(path)
    except OSError:
        os.chmod(path, stat.S_IWRITE)
        os.unlink(path)
