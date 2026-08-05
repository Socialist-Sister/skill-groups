# skill-groups 项目状态档案

> 更新时间：2026-08-06 · 供新会话快速续接，替代长对话上下文

## 一句话

**skill-groups v0.1.0** —— 把 AI agent 技能组织成命名"组"、按项目挂载到 `.agents/skills` 等目录的 CLI（零依赖 Python 3.9+，命令名 `sg`）。"技能界的 dotfiles"。

## 已发布状态 ✅

| 渠道 | 位置 | 状态 |
|---|---|---|
| GitHub | https://github.com/Socialist-Sister/skill-groups | 公开，26+ 提交 |
| PyPI | https://pypi.org/project/skill-groups/ | v0.1.0，`pip install skill-groups` 可用 |
| CI | GitHub Actions `.github/workflows/ci.yml` | 7 平台全绿（Ubuntu 3.9–3.13 + Windows 3.9/3.13） |
| 测试 | 217 个 unittest | 全绿 |
| 引导 skill | `guides/sg-skill/` | 已装 `~/.claude/skills` + `~/.agents/skills` |
| 文档 | README.md + README.zh-CN.md | 中英双语，命令示例全部实测 |

## 本机环境

```
项目路径:   D:\opencode\skill-groups
sg 命令:    C:\Users\ZengYiming\AppData\Local\Programs\Python\Python313\Scripts\sg.exe（editable 安装）
技能源:     D:\opencode\demo-skills（6 个技能，local 源）
组定义:     ~/.sg/groups/ → python（python-docstring/test-runner）、docs（markdown-tidy/json-validator）
孤立技能:   git-commit-writer、sql-formatter（用 sg use --skill 挂载）
虚拟环境:   .venv/（打包验证用）
```

## 进行中/待办

### 1. opencode 云端部署排查（搁置中，随时可继续）
- `.github/workflows/opencode.yml` 已推送（触发：issue/PR 评论含 `/oc` 或 `/opencode`）
- 失败症状：`Failed to parse JSON` + `undefined is not an object (evaluating 'p.rest')`，checkout 成功、Run opencode 步骤 4 秒内失败
- 已排除：API key 有效性（本地 opencode 正常用同一模型）、模型名 `deepseek/deepseek-v4-flash`（用户确认官方支持 v4-flash/v4-pro）
- **下一步方向**：查 `anomalyco/opencode/github` action 文档；日志显示 `USE_GITHUB_TOKEN: false` 且 `OIDC_BASE_URL` 为空，重点怀疑 GitHub 认证/评论创建环节（`p.rest` 崩溃点）
- 提醒：确认仓库 Settings → Secrets → Actions 里已有 `DEEPSEEK_API_KEY`

### 2. PyPI token 轮换（安全待办）
- 开发过程中整个账号 scope 的 token 曾在对话中明文出现 → **已暴露，建议删除重建**
- 操作：pypi.org → Account settings → API tokens → 删除旧 token → 新建 `Project: skill-groups` 专用 token（现在项目已发布，Project scope 可选了）

### 3. v0.2 候选（未开始，按需推进）
- `sg init --agent auto` 自动检测 agent
- `sg doctor` 增加项目 agent 匹配检测
- 组定义注册表/`sg publish`（技能组可分享）

## 常用命令

```powershell
python -m unittest discover tests        # 全量测试（217）
python -m build                           # 构建产物 → dist/
# 发新版本：pyproject.toml 改 version → build → twine upload dist/*
# 推代码后 CI 自动跑 7 平台验证
```

## 续接指南

- 新会话：先读本文件 + README.md（项目全貌）
- 上一篇完整开发历程对话（从调研到发布）在旧目录 `D:\opencode\skills test` 启动的 opencode 会话里，需要考古时回那里
- 项目全部历史决策已沉淀在 git 提交历史（26+ 原子提交，Conventional Commits）
