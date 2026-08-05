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
   `pip install skill-groups` (or `pip install git+https://github.com/Socialist-Sister/skill-groups.git`). If pip is not usable, tell the user how to install.
2. **Identify your own agent FIRST**: you (the assistant) know which agent you are running inside. This decides the mount directory — see "Agent-specific notes" below. Pick the right `--agent` value for `sg init` accordingly.
3. **See what groups exist**: `sg group list`, then `sg group show <name>` for details (skills + sources).
4. **Decide what to mount** — ask the user, or suggest from the project's files (e.g. `pyproject.toml`/`requirements.txt` → a python group; lots of `*.md` → a docs group). Confirm group names AND any standalone skills before executing.
5. **Initialize** (only if `.sg.json` is absent in the project root): `sg init --agent <your agent>` — writes `.sg.json` (declaration) and gitignores the mount dir.
6. **Mount**: `sg use <group> [<group>...] [--skill <id> --path <dir>]...`
   - `--skill`/`--path` come in pairs and may be repeated for standalone skills. Standalone sources are local directories containing `SKILL.md`.
   - Inside a group, sources may be `local` (a path) or `git` (repo/path/rev); standalone skills are local only.
7. **Verify**: `sg ls` (mounted list) and `sg status` (per-skill state `ok`/`missing-link`/`drift`/`conflict`/`stale`). Tell the user to **fully restart their agent** — skills are registered at process start, and a new session inside a long-running agent (e.g. `/new` in OpenCode, a new window in Cursor) does NOT re-scan. If you are Claude Code, this warning is MANDATORY (Claude Code has no reload command).
8. **Cleanup when asked**: `sg unuse <group>... [--skill <id>...]` unmounts only what the user names; shared skills stay mounted while any remaining group references them. `sg sync` repairs mounts to match the declaration.

## Agent-specific notes (READ THIS BEFORE sg init)

You know which agent you are. Map yourself:

| If you are running inside… | Use `sg init --agent …` | Mount dir | Notes |
|---|---|---|---|
| **Claude Code** | `claude` | `.claude/skills` | **MANDATORY**: Claude Code does NOT scan `.agents/skills` yet (tracked in anthropics/claude-code#16345). Using the default would silently fail — the skills would never load. Also: no in-session reload — always tell the user to restart the agent after mounting. |
| Codex CLI | `codex` | `.codex/skills` | |
| OpenCode | `opencode` | `.opencode/skills` | also reads `.agents/skills` |
| Cursor / Gemini CLI / Copilot CLI / other | `agents` (default) | `.agents/skills` | the cross-agent standard dir |

If the project was already initialized with a different agent dir (check `.sg.json` → `agent` field), do NOT re-init — the declaration is the source of truth; `sg use` mounts into the declared dir. Changing agent requires `sg init --agent <x> --force` (or edit `.sg.json`).

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
