# skill-groups

Organize AI-agent skills into named groups and mount them per project — the dotfiles of the AI skill world

Collect the skills you actually use into named groups (e.g. `python`, `web`, `sql`), declare which groups a project needs, and let one command mount the right skills into the right agent directory. Zero dependencies, works on Python 3.9+.

## Why

Skill libraries grow fast. When your global store has 160 skills and every new project needs a different subset, you stop re-picking them by hand and start asking "which project already solved this?"

skill-groups gives you three layers instead of one pile:

| Layer | Where it lives | What it holds |
|---|---|---|
| **Global** | `SG_HOME` (default `~/.sg`) | every skill you've ever collected, cached by content |
| **Group** | `SG_HOME/groups/<name>.json` | a named, reusable bundle of skills with sources |
| **Project** | `.sg.json` + a skills dir | which groups this project mounts, and where |

Projects stay portable because a project declares *groups*, not raw paths. The skills dir that gets mounted (`.agents/skills`, `.claude/skills`, ...) is a real agent-recognized location, so there is zero per-agent configuration: the agent already scans that directory. The group definitions and the cache live outside the project, so the project itself stays clean, and the mounts are re-creatable with one command.

## Supported agents

Each agent already reads a conventional skills directory inside the project root. skill-groups just mounts skills into that directory, so it works with any agent that scans these paths.

| Agent | Skills directory |
|---|---|
| Standard (Cursor, Gemini CLI, Copilot CLI, others) | `.agents/skills` |
| Claude Code | `.claude/skills` |
| Codex | `.codex/skills` |
| OpenCode | `.opencode/skills` |

Pick the target with `sg init --agent claude` (choices: `agents`, `claude`, `codex`, `opencode`; default `agents`).

> **Claude Code users**: you MUST run `sg init --agent claude` — Claude Code does not scan `.agents/skills` yet (tracked in [anthropics/claude-code#16345](https://github.com/anthropics/claude-code/issues/16345)); the default would silently load nothing. And after mounting, **fully restart your agent** — skills are registered at process start; a new session inside a long-running agent (e.g. `/new` in OpenCode) does not re-scan.

## Install

The package is a zero-dependency Python 3.9+ CLI named `sg`.

```powershell
# from the repo root
pip install -e .
```

The build backend is [hatchling](https://hatch.pypa.io/). If you install offline, skip the build-isolation step that would otherwise try to fetch it:

```powershell
pip install -e . --no-build-isolation
```

After install, `sg` is on your PATH and `sg --help` works from any directory.

**Zero-install alternative:** you don't have to install anything. From the repo root, `python -m sg` runs the same CLI directly, because the `sg` package is importable from the current directory. Every example below works with either `sg ...` or `python -m sg ...`.

## Quickstart

Run these in a new project directory. Expected output is shown below each command.

```powershell
# 1. turn the current directory into a skill-groups project
sg init
# initialized C:\path\to\project
```

This writes `.sg.json` and adds the agent skills dir (`.agents/skills` by default) to your `.gitignore`.

```powershell
# 2. create a group
sg group create python --description "Python tooling"
# created group python
```

```powershell
# 3. add skills to the group from local folders
sg group add python lint --type local --path C:\path\to\lint-skill
# added lint to group python

sg group add python format --type local --path C:\path\to\format-skill
# added format to group python
```

```powershell
# 4. mount every skill in the group into this project
sg use python
# used: python
```

```powershell
# 4b. mount a standalone skill too (no group required)
sg use python --skill git-commit-writer --path "C:\path\to\git-commit-writer"
# used: python
# used skill: git-commit-writer
```

`--skill`/`--path` can be repeated (they must come in pairs). Standalone skills are recorded in `.sg.json` under `skills` (group is `ungrouped` in `sg ls`) and are fully covered by `sg status`/`sg sync`/`sg unuse --skill <id>`.

```powershell
# 5. see what's mounted
sg ls
# format (python, local)
# lint (python, local)
```

```powershell
# 6. check status while the skills are mounted
sg status
# format (python): ok
# lint (python): ok
```

```powershell
# 7. drop the group again (unmounts its skills)
sg unuse python
# unused: python
```

```powershell
# 8. repair mounts to match the declaration
sg sync
# format: unchanged
# lint: unchanged
```

`sg status` prints one line per mounted skill (`skill (group): state`, where state is `ok`, `missing-link`, `drift`, `conflict`, or `stale`), and `sg sync` repairs mounts to match the declaration (actions: `unchanged`, `remounted`, `relinked`, `renewed`, `removed`, `skipped`). Both exit 0.

## Group definitions (schema v1)

A group is a JSON file at `<SG_HOME>/groups/<name>.json`. You can build it with `sg group add`, or write it by hand:

```json
{
  "name": "python",
  "description": "Python tooling",
  "skills": [
    {
      "id": "lint",
      "source": { "type": "local", "path": "C:/skills/lint" }
    },
    {
      "id": "pytest",
      "source": {
        "type": "git",
        "repo": "owner/repo",
        "path": "skills/pytest",
        "rev": "main"
      }
    }
  ]
}
```

Rules enforced on every read:

- `name` is a non-empty string and may not contain `/` or `\`.
- Skill `id` must be unique within the group.
- `local` sources require a string `path`; `git` sources require a string `repo` (`path` and `rev` are optional).

Inspect your groups with `sg group list` and `sg group show python`.

## Cache and mount mechanism

Every skill is fetched once into a content-addressed cache, then mounted into the project. The flow is: **group → source → cache → mount**.

- Cache layout: `<SG_HOME>/cache/<key>/<skill_id>/`
- `<key>` is a 10-hex sha256 of the source identity. For git sources the requested `rev` is part of the identity, so asking for a new rev gets a fresh cache directory and a stale cache can never be served for the wrong rev.
- Local sources are copied on first fetch (gated by the presence of `SKILL.md`, so re-running is a no-op). Git sources are shallow-cloned once into `<skill_dir>/.repo` and reused on later calls.

Mounting happens in one of three modes, set with `sg init --mode ...`:

| Mode | Behavior |
|---|---|
| `auto` (default) | Windows: junction, falling back to symlink, then copy. Other platforms: symlink, falling back to copy. |
| `symlink` | Always a directory symlink. |
| `copy` | Always a full copy. |

When `auto` falls back to copy, it prints a warning to stderr (`warning: fell back to copy for <skill_id>`). Prefer junction/symlink over copy: copies go stale when the cached skill changes, and `sg unuse` has to delete real files instead of one link.

`sg use` is all-or-nothing. Before any mount it resolves every source and pre-flights every target link; if the same skill id is declared by two groups with different sources, or a target path already exists with different content, the whole batch aborts and nothing is changed.

## Windows notes

- **Junctions need no admin rights.** `mklink /J` works in a normal user session, which is why `auto` tries junctions first on Windows.
- **Symlinks need Developer Mode** (Settings → Privacy & Security → For developers → Developer Mode) or an elevated shell. If a symlink can't be created, `auto` silently falls back to a copy.
- **Paths with spaces are safe.** All git and mount commands use subprocess list form, never shell string concatenation, so a repo path like `C:\My Skills\repo` works.
- Unmounting never touches the target: junctions are removed as reparse points (`os.rmdir` / `cmd /c rmdir`), never with a recursive delete.

## Exit codes

| Code | Meaning | Example |
|---|---|---|
| `0` | Success | any command that completes |
| `1` | User error: bad input, conflicts, unknown group | `error: group already exists: python` |
| `2` | Environment error or unexpected failure; also CLI usage errors | `error: git not found on PATH`, unknown command |

User errors print `error: <message>` to stderr and leave your state untouched. Environment errors (missing git, unwritable cache, corrupt config) print the same way but signal a setup problem, not an input problem.

## Git sources

```powershell
sg group add web serve --type git --repo owner/repo --path skills/web --rev v1
# added serve to group web
```

- `repo` accepts an `owner/repo` shorthand (resolved to `https://github.com/<owner/repo>.git`), a full URL, or a local repository path.
- `path` selects a subdirectory of the repo as the skill content; omit it to use the repo root.
- `rev` accepts a **branch name, a tag, or a full 40-character commit sha**. A branch/tag becomes a shallow `--branch` clone; a sha is fetched and checked out directly.
- The lock (`sg.lock`) records the **actual resolved HEAD** in `resolved_sha`, so `sg status` can later tell you whether the mounted skill matches what the declaration resolved to.

## Troubleshooting: `sg doctor`

`sg doctor` reports the environment, which is the first stop when a git source won't fetch or a mount fails:

```
> sg doctor
python: 3.13.3
git: available
sg_home: C:\Users\me\.sg
sg_home_writable: yes
junction: supported
```

| Line | Healthy | If not |
|---|---|---|
| `git` | `available` | install git and put it on PATH; git sources cannot fetch without it |
| `sg_home` | your expected home path | set `SG_HOME` to redirect all global state, e.g. `$env:SG_HOME="$env:TEMP\sg-test"` in PowerShell |
| `sg_home_writable` | `yes` | fix permissions on `SG_HOME`; the cache and groups live there |
| `junction` | `supported` on Windows | mounts fall back to symlink/copy automatically |

The default `SG_HOME` is `~/.sg`. Its layout: `config.json` (global config), `groups/` (group definitions), `cache/` (fetched skills).

## Development

```powershell
python -m unittest discover tests -v
```

The suite runs 195 tests covering the CLI surface, group validation, caching, git sources, mounting, and isolation.

## License

MIT
