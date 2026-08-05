---
name: sg-skill
description: Manage AI-agent skills per project using the `sg` CLI (skill-groups). Use when the user wants to configure project skills, mount skill groups, organize skills into groups, pick which agent skills a project uses, add standalone/ungrouped skills to a project, or mentions 技能组、技能挂载、配置技能、给项目配技能、sg 命令. Install sg first (see Workflow step 1); sg is a computer-wide command that mounts skills from a central library into the project's .agents/skills directory.
---

# sg — project-level skill groups

`sg`（skill-groups）把 agent 技能组织成**组**（套餐），按项目挂载到项目内的 `.agents/skills/`（或 `.claude/skills` / `.codex/skills`）。技能本体在全局源/缓存，项目里只是链接（junction/symlink）——因此 agent 零配置即可使用，且技能更新对所有项目生效。

Three layers:
- **Global**: skills placed in `~/.claude/skills` etc. — visible to every project (agent-native, NOT managed by sg).
- **Groups** (`~/.sg/groups/*.json`): named bundles of skills (`sg use <group>`).
- **Standalone**: ungrouped skills mounted per project (`sg use --skill <id> --path <dir>`).

## Workflow (when the user asks to configure skills for a project)

1. **Check sg is installed**: run `sg --version`. If missing, install with
   `pip install skill-groups` (or `pip install git+https://github.com/<owner>/skill-groups.git`). If pip is not usable, tell the user how to install.
2. **See what groups exist**: `sg group list`, then `sg group show <name>` for details (skills + sources).
3. **Decide what to mount** — ask the user, or suggest from the project's files (e.g. `pyproject.toml`/`requirements.txt` → a python group; lots of `*.md` → a docs group). Confirm group names AND any standalone skills before executing.
4. **Initialize** (only if `.sg.json` is absent in the project root): `sg init` — writes `.sg.json` (declaration) and gitignores the mount dir.
5. **Mount**: `sg use <group> [<group>...] [--skill <id> --path <dir>]...`
   - `--skill`/`--path` come in pairs and may be repeated for standalone skills. Standalone sources are local directories containing `SKILL.md`.
   - Inside a group, sources may be `local` (a path) or `git` (repo/path/rev); standalone skills are local only.
6. **Verify**: `sg ls` (mounted list) and `sg status` (per-skill state `ok`/`missing-link`/`drift`/`conflict`/`stale`). Tell the user to **start a new agent session** — mounts are picked up at session start.
7. **Cleanup when asked**: `sg unuse <group>... [--skill <id>...]` unmounts only what the user names; shared skills stay mounted while any remaining group references them. `sg sync` repairs mounts to match the declaration.

## Creating groups on the user's behalf

If the user wants their skills organized into groups:

1. Point the user at the source folder (e.g. `C:\Users\<name>\skills\`), or ask where their skills live.
2. `sg group create <name>` then `sg group add <name> <skill-id> --type local --path <abs dir with SKILL.md>` (or `--type git --repo owner/repo [--path sub/dir] [--rev branch|tag|sha]`).
3. `sg group show <name>` to confirm.

## Command reference (exact)

| Command | Meaning |
|---|---|
| `sg init [--agent agents\|claude\|codex\|opencode] [--mode auto\|symlink\|copy] [--force]` | init project (`.sg.json` + gitignore) |
| `sg use <group...> [--skill ID --path DIR]...` | mount groups and standalone skills |
| `sg unuse <group...> [--skill ID...]` | unmount named groups/standalone skills |
| `sg ls` | list mounted skills (`skill (group, source)`) |
| `sg status` | per-skill state report (exit 0) |
| `sg sync` | repair mounts to match declaration |
| `sg group create/add/list/show` | manage group definitions |
| `sg doctor` | environment report (python/git/SG_HOME/junction) |

## Failure handling

- **exit 1** (user error): stderr explains — unknown group, `--skill`/`--path` count mismatch, or **conflict** (same skill id from two sources, or a target dir already exists with different content). Mounts are **all-or-nothing**: on conflict nothing was changed. Resolve by renaming one skill or pointing at the same source, then retry.
- **exit 2** (environment error): run `sg doctor` and report its output to the user.
- Mounts are links, not copies — editing skill content should happen in the source folder (or by editing the group definition's source), then `sg sync` to refresh.
