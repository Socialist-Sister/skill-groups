"""Mount primitives: Windows junction / symlink / copy.

This is the highest-risk module in skill-groups: a junction is a reparse
point and a wrong delete walks into the target directory. Every function
here is written so that unmounting can never touch the target — junctions
are removed with os.rmdir (which deletes only the reparse point) or
`cmd /c rmdir`, and are NEVER passed to shutil.rmtree.

Zero dependencies beyond the standard library.
"""

import ctypes
import os
import shutil
import subprocess
import sys
from pathlib import Path

from . import errors, util

MODES = ("auto", "symlink", "copy")

# Windows FILE_ATTRIBUTE_REPARSE_POINT: set on junctions and symlinks.
FILE_ATTRIBUTE_REPARSE_POINT = 0x400

_IS_WINDOWS = sys.platform == "win32"


def resolve_mode(mode):
    """Validate a mount mode string; raise UserError for unknown values."""
    if mode not in MODES:
        raise errors.UserError(
            f"unknown mount mode {mode!r} (expected one of: {', '.join(MODES)})"
        )
    return mode


def is_junction(path):
    """True if path is a Windows junction (reparse point).

    Uses os.path.isjunction (Python 3.12+) when available; otherwise falls
    back to checking FILE_ATTRIBUTE_REPARSE_POINT via ctypes. Always False
    on non-Windows platforms and for missing paths.
    """
    if not _IS_WINDOWS:
        return False
    path = os.fspath(path)
    isjunction = getattr(os.path, "isjunction", None)
    if isjunction is not None:
        return isjunction(path)
    attrs = ctypes.windll.kernel32.GetFileAttributesW(str(path))
    if attrs == -1:  # INVALID_FILE_ATTRIBUTES: path does not exist
        return False
    return bool(attrs & FILE_ATTRIBUTE_REPARSE_POINT)


def is_link_or_junction(path):
    """True if path is a symlink or a junction (any reparse mount)."""
    return os.path.islink(path) or is_junction(path)


def resolve_link_target(link):
    """Absolute Path a link/junction points at, or None.

    os.readlink on a junction returns an extended-length path prefixed with
    ``\\?\\``; the prefix is stripped so the result compares cleanly against
    an ordinary absolute path. Returns None for plain directories and
    missing paths.
    """
    try:
        raw = os.readlink(link)
    except OSError:
        return None
    raw = str(raw)
    if raw.startswith("\\\\?\\"):
        raw = raw[4:]
    return Path(raw)


def create_junction(target, link):
    """Create a directory junction at link pointing to target.

    target must be an absolute path (junction targets cannot be relative),
    otherwise UserError. mklink is a cmd builtin, so it is invoked through
    `cmd /c`. Each token is passed as a separate argv entry: a single
    pre-quoted string (`mklink /J "link" "target"`) makes cmd's /c
    quote-stripping mangle valid paths into "syntax is incorrect" errors on
    real Windows, so that form is deliberately not used. The mklink output
    is never parsed — it is localized (e.g. Chinese Windows) and useless
    for machines — success is verified with os.path instead. cmd missing ->
    EnvError.
    """
    target = os.fspath(target)
    link = os.fspath(link)
    if not os.path.isabs(target):
        raise errors.UserError(f"junction target must be an absolute path: {target}")
    if util.which("cmd") is None:
        raise errors.EnvError("cmd.exe not found on PATH; cannot create a junction")
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(target)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise errors.EnvError(f"failed to create junction {link} -> {target}")
    if not is_junction(link):
        raise errors.EnvError(f"junction was not created at {link}")
    return Path(link)


def create_symlink(target, link):
    """Create a directory symlink; OSError propagates unwrapped so callers
    can fall back to a copy (no developer mode / no admin rights)."""
    os.symlink(target, link, target_is_directory=True)


def create_copy(target, link):
    """Mount by copying the tree; failures become EnvError (util.copy_tree)."""
    util.copy_tree(target, link)


def mount_skill(skills_dir, skill_id, cache_dir, mode):
    """Mount cache_dir at skills_dir/skill_id; return "created" or "exists".

    Idempotent: if the link already points at cache_dir, returns "exists".
    An existing entry pointing elsewhere (or a plain file/dir) raises
    UserError — an existing mount is never silently overwritten.
    """
    link = Path(skills_dir) / skill_id
    cache_dir = Path(cache_dir)
    mode = resolve_mode(mode)

    if os.path.lexists(link):
        if is_link_or_junction(link):
            target = resolve_link_target(link)
            if target is not None and os.path.abspath(target) == os.path.abspath(
                cache_dir
            ):
                return "exists"
        raise errors.UserError(
            f"target {skill_id} already exists with a different source"
        )

    if mode == "auto":
        if _IS_WINDOWS:
            try:
                create_junction(cache_dir, link)
            except (errors.EnvError, OSError):
                try:
                    create_symlink(cache_dir, link)
                except OSError:
                    create_copy(cache_dir, link)
                    print(
                        f"warning: fell back to copy for {skill_id}", file=sys.stderr
                    )
        else:
            try:
                create_symlink(cache_dir, link)
            except OSError:
                create_copy(cache_dir, link)
                print(f"warning: fell back to copy for {skill_id}", file=sys.stderr)
    elif mode == "symlink":
        create_symlink(cache_dir, link)
    else:  # mode == "copy"
        create_copy(cache_dir, link)
    return "created"


def unmount_skill(link, mode):
    """Remove a mount without ever touching the target directory.

    mode is the actual mount kind: "junction", "symlink" or "copy".
    Junctions are removed with os.rmdir (reparse point only), falling back
    to `cmd /c rmdir`. A junction is never passed to shutil.rmtree, no
    matter what mode says. Missing paths are tolerated (no-op).
    """
    link = Path(link)
    if not os.path.lexists(link):
        return
    if mode == "junction":
        _remove_junction(link)
    elif mode == "symlink":
        os.unlink(link)
    else:  # mode == "copy"
        if is_junction(link):
            # Red line: rmtree on a junction walks into the target.
            _remove_junction(link)
        elif os.path.islink(link):
            os.unlink(link)
        else:
            shutil.rmtree(link)


def _remove_junction(link):
    """Delete a junction reparse point; never recurse into the target."""
    try:
        os.rmdir(link)
        return
    except OSError:
        pass
    # Fallback: cmd's rmdir also removes only the junction, not the target.
    subprocess.run(
        ["cmd", "/c", "rmdir", str(link)],
        capture_output=True,
        text=True,
    )
