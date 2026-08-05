"""Command-line interface for skill-groups.

argparse command surface (init / use / unuse / ls / status / sync /
group / doctor) with a strict exit-code contract: 0 success, 1 user
error (UserError), 2 environment or unexpected error (EnvError / unknown
exception).  Every print flushes so subprocess capture is reliable.
"""

import argparse
import json
import os
import platform
import sys
import tempfile
from pathlib import Path

from . import __version__
from . import config, errors, git_util, groups, lockfile, mount, state, util


def build_parser():
    parser = argparse.ArgumentParser(
        prog="sg",
        description="Organize AI-agent skills into named groups and mount "
        "them per project.",
    )
    parser.add_argument(
        "--version", action="version", version=__version__,
        help="print the version and exit",
    )
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")
    sub.required = True

    p = sub.add_parser("init", help="initialize the current directory")
    p.add_argument(
        "--agent", choices=list(config.AGENT_DIRS), default="agents",
        help="target agent skills dir",
    )
    p.add_argument(
        "--mode", choices=list(mount.MODES), default="auto",
        help="mount mode (auto picks per platform)",
    )
    p.add_argument(
        "--force", action="store_true",
        help="rewrite the declaration even if already initialized",
    )
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("use", help="mount the skills of named groups")
    p.add_argument("groups", nargs="+", metavar="GROUP")
    p.set_defaults(func=cmd_use)

    p = sub.add_parser("unuse", help="unmount the skills of named groups")
    p.add_argument("groups", nargs="+", metavar="GROUP")
    p.set_defaults(func=cmd_unuse)

    p = sub.add_parser("ls", help="list mounted skills")
    p.set_defaults(func=cmd_ls)

    p = sub.add_parser("status", help="show project skill status")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("sync", help="repair project mounts to match the declaration")
    p.set_defaults(func=cmd_sync)

    p = sub.add_parser("group", help="manage skill groups")
    gsub = p.add_subparsers(dest="group_command", metavar="ACTION")
    gsub.required = True

    g = gsub.add_parser("create", help="create a new empty group")
    g.add_argument("name")
    g.add_argument("--description", default="")
    g.set_defaults(func=cmd_group_create)

    g = gsub.add_parser("add", help="add a skill to a group")
    g.add_argument("group")
    g.add_argument("skill_id", metavar="SKILL_ID")
    g.add_argument(
        "--type", dest="source_type", choices=("local", "git"), default="local",
        help="skill source type (default: local)",
    )
    g.add_argument("--path", help="local path (required for --type local)")
    g.add_argument("--repo", help="git repository (required for --type git)")
    g.add_argument("--rev", help="git revision or branch")
    g.set_defaults(func=cmd_group_add)

    g = gsub.add_parser("list", help="list registered group names")
    g.set_defaults(func=cmd_group_list)

    g = gsub.add_parser("show", help="show one group as JSON")
    g.add_argument("name")
    g.set_defaults(func=cmd_group_show)

    p = sub.add_parser("doctor", help="report the environment")
    p.set_defaults(func=cmd_doctor)

    return parser


def cmd_init(args):
    root = Path.cwd()
    if args.force and lockfile.sg_json(root).exists():
        existing = _declared_groups(root)
        lockfile.write_declaration(root, existing, args.agent, args.mode)
        state._append_gitignore(root, config.resolve_agent_dir(args.agent))
        print(f"reinitialized {root}", flush=True)
        return 0
    state.init_project(root, agent=args.agent, mode=args.mode)
    print(f"initialized {root}", flush=True)
    return 0


def _declared_groups(root):
    """Groups already declared, preserved when force-rewriting a declaration."""
    try:
        declared = lockfile.read_declaration(root).get("groups", [])
    except (errors.UserError, errors.EnvError):
        declared = []
    return declared if isinstance(declared, list) else []


def cmd_use(args):
    state.use_groups(Path.cwd(), args.groups)
    print(f"used: {', '.join(args.groups)}", flush=True)
    return 0


def cmd_unuse(args):
    state.unuse_groups(Path.cwd(), args.groups)
    print(f"unused: {', '.join(args.groups)}", flush=True)
    return 0


def cmd_ls(args):
    lock = lockfile.read_lock(Path.cwd())
    items = sorted(lock.items(), key=lambda kv: (kv[1].get("group", ""), kv[0]))
    for skill_id, entry in items:
        group = entry.get("group", "")
        source_type = entry.get("source", {}).get("type", "unknown")
        print(f"{skill_id} ({group}, {source_type})", flush=True)
    return 0


def cmd_status(args):
    """Print one "skill_id (group): state" line per status entry."""
    for item in state.status(Path.cwd()):
        group = item["group"]
        prefix = f"{item['skill']} ({group})" if group else item["skill"]
        print(f"{prefix}: {item['state']}", flush=True)
    return 0


def cmd_sync(args):
    """Repair mounts to match the declaration, one "skill_id: action" line."""
    for item in state.sync_groups(Path.cwd()):
        print(f"{item['skill']}: {item['action']}", flush=True)
    return 0


def cmd_group_create(args):
    groups.create_group(args.name, args.description)
    print(f"created group {args.name}", flush=True)
    return 0


def cmd_group_add(args):
    source = _source_from_args(args)
    groups.add_skill(args.group, args.skill_id, source)
    print(f"added {args.skill_id} to group {args.group}", flush=True)
    return 0


def _source_from_args(args):
    """Build a source dict from CLI flags; missing required fields -> UserError."""
    if args.source_type == "git":
        if not args.repo:
            raise errors.UserError("git source requires --repo")
        source = {"type": "git", "repo": args.repo}
        if args.path:
            source["path"] = args.path
        if args.rev:
            source["rev"] = args.rev
        return source
    if not args.path:
        raise errors.UserError("local source requires --path")
    return {"type": "local", "path": args.path}


def cmd_group_list(args):
    for name in groups.list_groups():
        print(name, flush=True)
    return 0


def cmd_group_show(args):
    group = groups.group_show(args.name)
    print(json.dumps(group, indent=2, ensure_ascii=False), flush=True)
    return 0


def cmd_doctor(args):
    print(f"python: {platform.python_version()}", flush=True)
    print(f"git: {'available' if git_util.git_available() else 'not found'}", flush=True)
    home_desc, writable = _sg_home_report()
    print(f"sg_home: {home_desc}", flush=True)
    print(f"sg_home_writable: {writable}", flush=True)
    print(f"junction: {_probe_junction()}", flush=True)
    return 0


def _sg_home_report():
    """(description, writable) for the resolved SG_HOME directory."""
    try:
        home = config.sg_home()
        util.ensure_dir(home)
    except errors.EnvError as exc:
        return f"error ({exc})", "unknown"
    return str(home), ("yes" if os.access(home, os.W_OK) else "no")


def _probe_junction():
    """Attempt one junction in a temp dir; return a support verdict."""
    if sys.platform != "win32" or util.which("cmd") is None:
        return "not-applicable"
    with tempfile.TemporaryDirectory(prefix="sg-probe-") as td:
        target = Path(td) / "target"
        link = Path(td) / "link"
        target.mkdir()
        try:
            mount.create_junction(target, link)
        except (errors.EnvError, errors.UserError):
            return "unsupported"
        if not link.exists():
            return "unsupported"
        try:
            mount.unmount_skill(link, "junction")
        except errors.EnvError:
            pass
        return "supported"


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except errors.UserError as exc:
        print(f"error: {exc}", file=sys.stderr, flush=True)
        return 1
    except errors.EnvError as exc:
        print(f"error: {exc}", file=sys.stderr, flush=True)
        return 2
    except Exception as exc:  # unexpected: fail closed like an EnvError
        print(f"error: {exc}", file=sys.stderr, flush=True)
        return 2
