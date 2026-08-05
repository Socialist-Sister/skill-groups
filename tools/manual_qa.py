"""S9 manual QA: real-Windows junction verification for skill-groups.

Runs the full project API chain (groups.create_group -> groups.add_skill ->
state.init_project -> state.use_groups -> state.unuse_groups) against a
temporary SG_HOME whose path contains spaces, and verifies the mounted
junction end to end: creation, content read-through, directory listing,
target resolution, cache integrity, declaration/lock correctness,
idempotent re-use, clean unmount, and the red line — the cache target must
survive unuse intact (unmounting a junction must never walk into the
target directory).

Each of the nine checks is isolated in its own try/except: a FAIL is
printed and the run continues. The script exits 0 only when all checks
pass, 1 otherwise. Every print is flushed so redirecting stdout captures
a complete, ordered report.
"""
from __future__ import annotations

Zero dependencies: standard library + the in-tree `sg` package. sys.path
is extended with the project root so `python tools/manual_qa.py` works
from anywhere.

# --- How to run ---
#   cd "skill-groups"
#   python tools/manual_qa.py 2>&1 | Out-File -FilePath docs/manual_qa_results.txt -Encoding utf8
#   $LASTEXITCODE   # 0 = all PASS
"""

import datetime
import os
import sys
import tempfile
from pathlib import Path
from typing import Callable, Final, Tuple

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sg import cache, groups, lockfile, mount, state  # noqa: E402

GROUP: Final = "qa-group"
SKILL: Final = "demo-skill"
SKILL_MD: Final = b"# demo skill\nunicode: \xe4\xb8\xad\xe6\x96\x87\xe5\x86\x85\xe5\xae\xb9 ok\n"
EXTRA_FILE: Final = b"extra file\n"
LOCAL_SOURCE: Final = {"type": "local", "path": ""}  # path filled per run


def run_check(number: int, name: str, fn: Callable[[], Tuple[bool, str]]) -> bool:
    """Run one check, print PASS/FAIL, never raise (FAIL does not stop the rest)."""
    try:
        ok, detail = fn()
    except Exception as exc:  # noqa: BROAD_EXCEPT_OK - a QA runner must survive any failure
        ok, detail = False, f"raised {type(exc).__name__}: {exc}"
    status = "PASS" if ok else "FAIL"
    suffix = f" -- {detail}" if detail else ""
    print(f"[{number:02d}] {status} {name}{suffix}", flush=True)
    return ok


def check_junction_created(link: Path) -> Tuple[bool, str]:
    """Check 1: the mount point is a real Windows junction."""
    return (
        bool(mount.is_junction(link)),
        f"is_junction={mount.is_junction(link)} islink={os.path.islink(link)}",
    )


def check_read_through(link: Path, content: bytes) -> Tuple[bool, str]:
    """Check 2: SKILL.md read through the link equals the source bytes."""
    got = (link / "SKILL.md").read_bytes()
    return got == content, f"link {len(got)}B == source {len(content)}B"


def check_listing(link: Path, src: Path) -> Tuple[bool, str]:
    """Check 3: directory listing through the link equals the source listing."""
    link_items = sorted(os.listdir(link))
    src_items = sorted(os.listdir(src))
    return link_items == src_items, f"link={link_items} src={src_items}"


def check_target(link: Path, cache_dir: Path) -> Tuple[bool, str]:
    """Check 4: junction target is absolute and points at the cache dir."""
    target = mount.resolve_link_target(link)
    if target is None:
        return False, "resolve_link_target returned None"
    same = os.path.abspath(target) == os.path.abspath(cache_dir)
    return (
        target.is_absolute() and same,
        f"target={target} cache={os.path.abspath(cache_dir)}",
    )


def check_cache(cache_dir: Path, src: Path, content: bytes) -> Tuple[bool, str]:
    """Check 5/7: cache dir exists with byte-identical, listing-identical content."""
    if not cache_dir.is_dir():
        return False, f"cache dir missing: {cache_dir}"
    same_content = (cache_dir / "SKILL.md").read_bytes() == content
    same_listing = sorted(os.listdir(cache_dir)) == sorted(os.listdir(src))
    return (
        same_content and same_listing,
        f"exists=True content={same_content} listing={same_listing}",
    )


def check_declaration_and_lock(proj: Path, src: Path) -> Tuple[bool, str]:
    """Check 8: .sg.json declares the group; the sg.lock entry is complete."""
    decl = lockfile.read_declaration(proj)
    if GROUP not in decl.get("groups", []):
        return False, f"declaration groups={decl.get('groups')} missing {GROUP!r}"
    lock = lockfile.read_lock(proj)
    entry = lock.get(SKILL)
    if not isinstance(entry, dict):
        return False, f"lock has no entry for {SKILL!r} (keys={sorted(lock)})"
    missing = sorted({"group", "source", "resolved_sha", "cache_dir"} - set(entry))
    ok = (
        not missing
        and entry["group"] == GROUP
        and entry["source"] == {"type": "local", "path": str(src)}
        and Path(entry["cache_dir"]).is_dir()
    )
    return (
        ok,
        f"decl.groups={decl.get('groups')} lock.missing={missing or 'none'} "
        f"cache_dir={entry.get('cache_dir')}",
    )


def check_reuse_idempotent(
    proj: Path, skills_dir: Path, link: Path, cache_dir: Path
) -> Tuple[bool, str]:
    """Check 9: a second use adds no links and keeps the same junction target."""
    before = sorted(os.listdir(skills_dir))
    state.use_groups(proj, [GROUP])
    after = sorted(os.listdir(skills_dir))
    target = mount.resolve_link_target(link)
    same_target = target is not None and os.path.abspath(target) == os.path.abspath(cache_dir)
    return (
        before == after and mount.is_junction(link) and same_target,
        f"entries before={before} after={after} same_target={same_target}",
    )


def check_link_gone(link: Path) -> Tuple[bool, str]:
    """Check 6: after unuse the link is fully gone (no reparse point remains)."""
    return (
        not mount.is_link_or_junction(link) and not os.path.lexists(link),
        f"is_link_or_junction={mount.is_link_or_junction(link)} "
        f"lexists={os.path.lexists(link)}",
    )


def cleanup(proj: Path | None, tmp: tempfile.TemporaryDirectory[str] | None) -> None:
    """Best-effort teardown: unuse, remove leftover mounts, drop the temp tree.

    Must run before TemporaryDirectory.cleanup(): a live junction inside
    the tree blocks Windows from deleting the parent directory. Leftover
    mounts are removed as reparse points only — the target is never
    touched (no rmtree on a junction, ever).
    """
    if proj is not None:
        try:
            state.unuse_groups(proj, [GROUP])
        except Exception:  # noqa: BROAD_EXCEPT_OK - teardown must never raise
            pass
        try:
            decl = lockfile.read_declaration(proj)
            skills_dir = state.project_skills_dir(proj, decl)
            for name in sorted(os.listdir(skills_dir)):
                entry = skills_dir / name
                if mount.is_junction(entry):
                    mount.unmount_skill(entry, "junction")
                elif os.path.islink(entry):
                    mount.unmount_skill(entry, "symlink")
                else:
                    mount.unmount_skill(entry, "copy")
        except Exception:  # noqa: BROAD_EXCEPT_OK - teardown must never raise
            pass
    if tmp is not None:
        try:
            tmp.cleanup()
        except Exception as exc:  # noqa: BROAD_EXCEPT_OK - report, never raise
            print(f"[cleanup] warning: {exc}", flush=True)


def main() -> int:
    started = datetime.datetime.now().isoformat(timespec="seconds")
    print(
        f"MANUAL QA start {started} | python {sys.version.split()[0]} "
        f"| platform {sys.platform} | cwd {os.getcwd()}",
        flush=True,
    )

    tmp: tempfile.TemporaryDirectory[str] | None = None
    proj: Path | None = None
    setup_error: BaseException | None = None
    results: list[bool] = []

    try:
        # Temp dir whose name contains spaces, simulating a real environment.
        tmp = tempfile.TemporaryDirectory(prefix="sg manual qa ")
        base = Path(tmp.name)
        os.environ["SG_HOME"] = str(base / "sg home")

        src = base / "skill source"
        src.mkdir()
        (src / "SKILL.md").write_bytes(SKILL_MD)
        (src / "notes.txt").write_bytes(EXTRA_FILE)

        proj = base / "project dir"
        print(f"  tmp root  {base} (name contains spaces: {' ' in base.name})", flush=True)
        print(f"  SG_HOME   {os.environ['SG_HOME']}", flush=True)
        print(f"  project   {proj}", flush=True)
        print(f"  skill src {src}", flush=True)

        groups.create_group(GROUP)
        groups.add_skill(
            GROUP, SKILL, {"type": "local", "path": str(src)}
        )
        state.init_project(proj)
        state.use_groups(proj, [GROUP])

        decl = lockfile.read_declaration(proj)
        skills_dir = state.project_skills_dir(proj, decl)
        link = skills_dir / SKILL
        cache_dir = cache.skill_cache_dir({"type": "local", "path": str(src)}, SKILL)
        print(f"  skills dir {skills_dir}", flush=True)
        print(f"  link       {link}", flush=True)
        print(f"  cache dir  {cache_dir}", flush=True)

        # Checks 1-5, 8: while mounted.
        results.append(
            run_check(1, "junction created at project skills link",
                      lambda: check_junction_created(link))
        )
        results.append(
            run_check(2, "SKILL.md read through link equals source",
                      lambda: check_read_through(link, SKILL_MD))
        )
        results.append(
            run_check(3, "link listing equals source listing",
                      lambda: check_listing(link, src))
        )
        results.append(
            run_check(4, "junction target is absolute and points at cache dir",
                      lambda: check_target(link, cache_dir))
        )
        results.append(
            run_check(5, "cache dir exists with complete content",
                      lambda: check_cache(cache_dir, src, SKILL_MD))
        )
        results.append(
            run_check(8, "declaration and lock are correct",
                      lambda: check_declaration_and_lock(proj, src))
        )
        # Check 9: idempotent re-use while still mounted.
        results.append(
            run_check(9, "repeated use is idempotent (no new links)",
                      lambda: check_reuse_idempotent(proj, skills_dir, link, cache_dir))
        )

        # Unmount, then checks 6 and 7 (the red line).
        state.unuse_groups(proj, [GROUP])
        results.append(
            run_check(6, "link gone after unuse", lambda: check_link_gone(link))
        )
        results.append(
            run_check(7, "RED LINE: cache target intact after unuse",
                      lambda: check_cache(cache_dir, src, SKILL_MD))
        )
    except Exception as exc:  # noqa: BROAD_EXCEPT_OK - boundary; report and exit 1
        setup_error = exc
    finally:
        cleanup(proj, tmp)

    if setup_error is not None:
        print(f"[setup] FAIL: {type(setup_error).__name__}: {setup_error}", flush=True)
        print("MANUAL QA: SETUP FAILED (exit 1)", flush=True)
        return 1

    total = len(results)
    passed = sum(1 for ok in results if ok)
    print(f"MANUAL QA: {passed}/{total} PASS", flush=True)
    if passed != total:
        print(f"MANUAL QA: {total - passed} check(s) FAILED (exit 1)", flush=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
