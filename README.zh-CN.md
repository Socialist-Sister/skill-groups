# skill-groups

把 AI agent 技能组织成命名组，按项目挂载 —— 技能界的 dotfiles

把你真正在用的技能收集成命名组（如 `python`、`web`、`sql`），声明项目需要哪些组，一条命令把正确的技能挂载进正确的 agent 目录。零依赖，Python 3.9+。

[English](README.md)

## 为什么需要它

技能库会迅速膨胀。当全局库有 160 个技能、每个新项目又需要不同子集时，你就不该再手动逐个挑选，而会问"哪个项目已经解决过这个问题了？"

skill-groups 用三层结构代替一堆散乱的技能：

| 层 | 位置 | 存放内容 |
|---|---|---|
| **全局** | `SG_HOME`（默认 `~/.sg`） | 你收集过的所有技能，按内容缓存 |
| **组** | `SG_HOME/groups/<name>.json` | 带来源的可复用技能捆绑 |
| **项目** | `.sg.json` + 技能目录 | 本项目挂载哪些组、挂到哪 |

项目保持可移植，因为项目声明的是**组**，而不是裸路径。被挂载的技能目录（`.agents/skills`、`.claude/skills`、……）是 agent 真正识别的路径，因此**每个 agent 零配置**：agent 本来就会扫描该目录。组定义和缓存位于项目之外，项目本身保持干净，挂载可以用一条命令重建。

## 支持的 agent

每个 agent 本来就会读取项目根下的约定技能目录。skill-groups 只是把技能挂载进该目录，所以任何扫描这些路径的 agent 都能用。

| Agent | 技能目录 |
|---|---|
| 标准（Cursor、Gemini CLI、Copilot CLI 等） | `.agents/skills` |
| Claude Code | `.claude/skills` |
| Codex | `.codex/skills` |
| OpenCode | `.opencode/skills` |

用 `sg init --agent claude` 选择目标（可选：`agents`、`claude`、`codex`、`opencode`；默认 `agents`）。

> **Claude Code 用户**：必须执行 `sg init --agent claude` —— Claude Code 目前不扫描 `.agents/skills`（追踪见 [anthropics/claude-code#16345](https://github.com/anthropics/claude-code/issues/16345)）；用默认值会静默地什么都加载不到。另外挂载完成后要**完全重启 agent** —— 技能在进程启动时注册；常驻型 agent（如 OpenCode 的 `/new`）内部新开会话不会重新扫描。

## 安装

这是一个零依赖的 Python 3.9+ CLI，命令名为 `sg`。

```powershell
pip install skill-groups
```

**或直接从 GitHub 安装**（无需 PyPI 发布）：

```powershell
pip install git+https://github.com/Socialist-Sister/skill-groups.git
```

安装后 `sg` 就在 PATH 上，`sg --help` 在任何目录都可用。

**开发者**：clone 本仓库后 `pip install -e .` 可编辑安装（构建后端为 [hatchling](https://hatch.pypa.io/)；离线时用 `pip install -e . --no-build-isolation`）。

**零安装替代**：其实什么都不用装。在仓库根目录，`python -m sg` 直接运行同一个 CLI，因为 `sg` 包可以从当前目录导入。下面的每个示例都同时适用于 `sg ...` 和 `python -m sg ...`。

## 快速上手

在新项目目录里运行。每个命令下方是预期输出。

```powershell
# 1. 把当前目录变成 skill-groups 项目
sg init
# initialized C:\path\to\project
```

这会写入 `.sg.json`，并把 agent 技能目录（默认 `.agents/skills`）加进你的 `.gitignore`。

```powershell
# 2. 创建一个组
sg group create python --description "Python 工具集"
# created group python
```

```powershell
# 3. 从本地文件夹把技能加进组
sg group add python lint --type local --path C:\path\to\lint-skill
# added lint to group python

sg group add python format --type local --path C:\path\to\format-skill
# added format to group python
```

```powershell
# 4. 把组里所有技能挂载进本项目
sg use python
# used: python
```

```powershell
# 4b. 同时挂载一个孤立技能（无需组）
sg use python --skill git-commit-writer --path "C:\path\to\git-commit-writer"
# used: python + git-commit-writer
```

`--skill`/`--path` 可以重复（必须成对出现）。孤立技能记录在 `.sg.json` 的 `skills` 字段下（`sg ls` 中组显示为 `ungrouped`），并完全受 `sg status`/`sg sync`/`sg unuse --skill <id>` 管理。

```powershell
# 5. 查看已挂载内容
sg ls
# format (python, local)
# lint (python, local)
```

```powershell
# 6. 查看挂载状态
sg status
# format (python): ok
# lint (python): ok
```

```powershell
# 7. 移除组（卸载其技能）
sg unuse python
# unused: python
```

```powershell
# 8. 让挂载与声明保持一致
sg sync
# format: unchanged
# lint: unchanged
```

`sg status` 每个已挂载技能输出一行（`skill (group): state`，state 为 `ok`、`missing-link`、`drift`、`conflict` 或 `stale`）；`sg sync` 修复挂载使其与声明一致（动作：`unchanged`、`remounted`、`relinked`、`renewed`、`removed`、`skipped`）。两者退出码均为 0。

## 组定义（schema v1）

组是 `<SG_HOME>/groups/<name>.json` 下的 JSON 文件。可以用 `sg group add` 构建，也可以手写：

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

每次读取都会强制校验的规则：

- `name` 是非空字符串，且不能包含 `/` 或 `\`。
- 组内技能 `id` 必须唯一。
- `local` 源必须有字符串 `path`；`git` 源必须有字符串 `repo`（`path` 和 `rev` 可选）。

用 `sg group list` 和 `sg group show python` 查看你的组。

## 缓存与挂载机制

每个技能只获取一次，进入内容寻址缓存，再挂载进项目。流程是：**组 → 来源 → 缓存 → 挂载**。

- 缓存布局：`<SG_HOME>/cache/<key>/<skill_id>/`
- `<key>` 是来源标识的 sha256 前 10 位十六进制。对 git 源，请求的 `rev` 属于标识的一部分，所以请求新 rev 会得到全新缓存目录，旧缓存永远不会被错误地用于新 rev。
- 本地源首次获取时复制（以 `SKILL.md` 是否存在为门控，重复运行是空操作）。git 源 shallow-clone 一次到 `<skill_dir>/.repo`，后续调用复用。

挂载有三种模式，用 `sg init --mode ...` 设置：

| 模式 | 行为 |
|---|---|
| `auto`（默认） | Windows：junction，回退 symlink，再回退 copy。其他平台：symlink，回退 copy。 |
| `symlink` | 始终使用目录符号链接。 |
| `copy` | 始终完整复制。 |

当 `auto` 回退到 copy 时会向 stderr 打印警告（`warning: fell back to copy for <skill_id>`）。优先 junction/symlink 而不是 copy：缓存技能变化时副本会过期，而且 `sg unuse` 需要删除真实文件而不是一个链接。

`sg use` 是全有或全无的。挂载任何东西之前，它会先解析所有来源并预检所有目标链接；如果两个组以不同来源声明同一个技能 id，或目标路径已存在且内容不同，整个批次中止，什么都不改。

## Windows 说明

- **junction 不需要管理员权限**。`mklink /J` 在普通用户会话中即可工作，这就是为什么 `auto` 在 Windows 上优先尝试 junction。
- **symlink 需要开发者模式**（设置 → 隐私和安全性 → 开发者选项 → 开发者模式）或管理员终端。如果无法创建 symlink，`auto` 会静默回退到复制。
- **含空格的路径是安全的**。所有 git 和挂载命令都用 subprocess 列表形式，绝不使用 shell 字符串拼接，所以 `C:\My Skills\repo` 这样的仓库路径也能正常工作。
- 卸载绝不会触碰目标：junction 作为重解析点被移除（`os.rmdir` / `cmd /c rmdir`），绝不会递归删除。

## 退出码

| 码 | 含义 | 示例 |
|---|---|---|
| `0` | 成功 | 任何正常完成的命令 |
| `1` | 用户错误：输入错误、冲突、未知组 | `error: group already exists: python` |
| `2` | 环境错误或意外失败；也包括 CLI 用法错误 | `error: git not found on PATH`、未知命令 |

用户错误向 stderr 打印 `error: <message>` 且不改动你的状态。环境错误（缺少 git、缓存不可写、配置损坏）同样打印，但表明是环境问题而非输入问题。

## git 源

```powershell
sg group add web serve --type git --repo owner/repo --path skills/web --rev v1
# added serve to group web
```

- `repo` 接受 `owner/repo` 简写（解析为 `https://github.com/<owner/repo>.git`）、完整 URL 或本地仓库路径。
- `path` 选择仓库内的子目录作为技能内容；省略则使用仓库根。
- `rev` 接受**分支名、标签或完整的 40 位 commit sha**。分支/标签使用浅 `--branch` clone；sha 则直接 fetch 并 checkout。
- 锁文件（`sg.lock`）在 `resolved_sha` 中记录**实际解析出的 HEAD**，所以 `sg status` 之后能告诉你挂载的技能是否与声明解析结果一致。

## 故障排查：`sg doctor`

git 源拉不下来或挂载失败时，第一站就是 `sg doctor` 的环境报告：

```
> sg doctor
python: 3.13.3
git: available
sg_home: C:\Users\me\.sg
sg_home_writable: yes
junction: supported
```

| 行 | 健康值 | 如果不健康 |
|---|---|---|
| `git` | `available` | 安装 git 并加入 PATH；没有它 git 源无法获取 |
| `sg_home` | 你期望的主目录 | 设置 `SG_HOME` 重定向全部全局状态，如 PowerShell 里 `$env:SG_HOME="$env:TEMP\sg-test"` |
| `sg_home_writable` | `yes` | 修复 `SG_HOME` 权限；缓存和组都存放在那里 |
| `junction` | Windows 上为 `supported` | 挂载会自动回退到 symlink/copy |

默认 `SG_HOME` 是 `~/.sg`。布局：`config.json`（全局配置）、`groups/`（组定义）、`cache/`（已获取的技能）。

## 开发

```powershell
python -m unittest discover tests -v
```

测试套件共 210 个测试，覆盖 CLI 命令面、组校验、缓存、git 源、挂载、孤立技能与隔离。

## 协议

MIT
