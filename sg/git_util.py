"""Git shallow-clone helpers: URL mapping, sha detection, shallow_clone, head_sha.

Used by skill-group git-source fetching (cache.py depends on this module).
All git invocations use subprocess list form (never shell string concat), so
paths containing spaces are handled by subprocess itself.
"""

import re
import shutil
import subprocess
from pathlib import Path

from . import errors

_TIMEOUT = 120  # seconds, per subprocess call
_STDERR_LIMIT = 500  # chars of git stderr carried into error messages

_OWNER_REPO = re.compile(r"^[^/\\]+/[^/\\]+$")
_DRIVE = re.compile(r"^[a-zA-Z]:")
_SHA = re.compile(r"[0-9a-fA-F]{40}")


def git_available():
    """True when a git executable is on PATH."""
    return shutil.which("git") is not None


def clone_url(repo):
    """Map a bare owner/repo to its GitHub clone URL; pass everything else through.

    Only owner/repo strings (no ://, no drive letter) become
    https://github.com/<owner/repo>.git. URLs and local absolute paths
    (Windows drive paths or forward-slash paths) are returned unchanged.
    """
    if _OWNER_REPO.match(repo) and "://" not in repo and not _DRIVE.match(repo):
        return f"https://github.com/{repo}.git"
    return repo


def is_commit_sha(rev):
    """True when rev is a full 40-char hex commit sha."""
    return isinstance(rev, str) and bool(_SHA.fullmatch(rev))


def _truncate_stderr(stderr):
    """Strip and truncate git stderr for embedding in error messages."""
    text = (stderr or "").strip()
    if len(text) > _STDERR_LIMIT:
        return text[:_STDERR_LIMIT] + "... (truncated)"
    return text


def _run(cmd, cwd=None):
    """Run a git command (list form); nonzero exit -> UserError with stderr."""
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=_TIMEOUT, cwd=cwd
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise errors.EnvError(f"git command failed: {exc}") from exc
    if proc.returncode != 0:
        raise errors.UserError(
            f"git {' '.join(cmd[1:])} failed: {_truncate_stderr(proc.stderr)}"
        )
    return proc


def _ensure_empty_dest(dest):
    """Return dest as a Path; refuse a dest that exists and is not empty."""
    dest = Path(dest)
    if dest.exists():
        if not dest.is_dir() or any(dest.iterdir()):
            raise errors.EnvError(f"cache dir not empty: {dest}")
    return dest


def shallow_clone(url, dest, rev=None):
    """Shallow-clone url into dest; rev may be a branch name or a full 40-hex sha.

    - rev is None: git clone --depth 1 <url> <dest> (default branch).
    - rev is a branch/tag name: git clone --depth 1 --branch <rev> <url> <dest>.
    - rev is a 40-hex sha: git init + git remote add origin + git fetch
      --depth 1 origin <rev> + git checkout FETCH_HEAD (a sha is not a ref
      name, so a plain clone cannot pin it).
    dest must not exist, or exist and be empty; a non-empty dest raises
    EnvError to avoid polluting the cache.
    """
    if not git_available():
        raise errors.EnvError("git not found on PATH")
    dest = _ensure_empty_dest(dest)
    if rev is None:
        _run(["git", "clone", "--depth", "1", url, str(dest)])
    elif is_commit_sha(rev):
        _run(["git", "init", str(dest)])
        _run(["git", "remote", "add", "origin", url], cwd=dest)
        _run(["git", "fetch", "--depth", "1", "origin", rev], cwd=dest)
        _run(["git", "checkout", "FETCH_HEAD"], cwd=dest)
    else:
        _run(["git", "clone", "--depth", "1", "--branch", rev, url, str(dest)])
    return dest


def head_sha(dest):
    """Return the 40-char HEAD sha of the git repo at dest; EnvError otherwise."""
    if not git_available():
        raise errors.EnvError("git not found on PATH")
    try:
        proc = subprocess.run(
            ["git", "-C", str(dest), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=_TIMEOUT,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise errors.EnvError(f"git rev-parse failed: {exc}") from exc
    if proc.returncode != 0:
        raise errors.EnvError(
            f"failed to read HEAD sha of {dest}: {_truncate_stderr(proc.stderr)}"
        )
    return proc.stdout.strip()


def refresh_repo(repo_dir, rev=None):
    """Fetch the latest upstream into an existing clone and reset the tree to it.

    rev None refreshes the clone's default branch (origin/HEAD); a branch or
    tag rev fetches exactly that ref and resets to it. A 40-hex commit sha is
    never passed here (callers skip pinned shas by contract). The fetch runs
    first and the working tree is only reset after it succeeds, so a network
    failure leaves the previous content untouched.
    """
    if not git_available():
        raise errors.EnvError("git not found on PATH")
    repo_dir = Path(repo_dir)
    if rev is None:
        _run(["git", "fetch", "origin", "--depth", "1"], cwd=repo_dir)
        # origin/HEAD tracks the clone-time default branch; set-head -a
        # re-derives it for shallow clones that lack the symref.
        _run(["git", "remote", "set-head", "origin", "-a"], cwd=repo_dir)
        _run(["git", "reset", "--hard", "origin/HEAD"], cwd=repo_dir)
    else:
        _run(["git", "fetch", "--depth", "1", "origin", rev], cwd=repo_dir)
        _run(["git", "reset", "--hard", "FETCH_HEAD"], cwd=repo_dir)
