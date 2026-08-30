# BugAnalyzer

**基于 Claude Code 的 AI 辅助问题定位能力包**：MCP Server 封装远程环境能力（SSH / 日志 / Arthas / 知识库检索），Skill 编排定位流程，业务组 clone 后改配置即可适配自己的代码库与知识库。

## 文档入口

| 文档 | 用途 |
|---|---|
| [docs/quickstart.md](docs/quickstart.md) | **业务组接入 3 步**（先看这个） |
| [docs/superpowers/specs/2026-08-30-buganalyzer-design.md](docs/superpowers/specs/2026-08-30-buganalyzer-design.md) | 设计文档（v1.2：ssh_run 白名单化、Phase 1 仅 test/staging、SSH 执行层等） |

## 工作原理（三层）

| 层 | 职责 | 谁维护 |
|---|---|---|
| **MCP Server**（`mcp/`） | 「手」——对环境做什么：SSH 执行、日志检索、知识库检索 | 平台方 |
| **analyze-issue Skill**（`.claude/skills/`） | 「脑」——怎么定位：8 步工作流（理解输入 → 检索案例 → 分析代码 → 假设 → 环境查证 → 根因 → 报告 → 沉淀案例） | 平台方 |
| **配置 + 知识库**（`config/` + `knowledge-base/`） | 「数据」——环境在哪、踩过什么坑、排查模式 | 业务组 |

## 业务组要做的（改 4 处）

1. `config/envs.toml` —— 环境清单 + 分级（Phase 1 仅 test/staging）
2. `config/policy.toml` —— 权限策略 + ssh_run 命令白名单
3. `knowledge-base/` —— 自己的案例（cases/）+ 排查模式（playbooks/）
4. 在自己业务仓库里启动 Claude Code，项目级注册本能力包

详见 [docs/quickstart.md](docs/quickstart.md)。

## 目录速览

```
BugAnalyzer/
├── .mcp.json            # MCP Server 注册（项目级安装入口）
├── .claude/skills/analyze-issue/   # 定位工作流
├── mcp/                 # MCP Server（Python/uv）
├── tools/kb_import/     # 问题单导出文件 → 案例 Markdown
├── config/              # envs.toml + policy.toml（示例见 *.example.toml）
└── knowledge-base/      # cases/ + playbooks/ + templates/ + INDEX.md
```

## 状态

- 设计已评审确认（v1.2）
- **Phase 1 骨架已落地**：MCP Server（5 个 `buganalyzer_*` 工具）+ 配置解析 + ssh_run 白名单校验 + kb_import 导入工具 + analyze-issue Skill + 知识库骨架，单元测试 14/14 通过
- 待办：真实环境端到端验证（test/staging）、Arthas 与 docker/k8s 命令包装（Phase 2）

## 开发与验证

```bash
# MCP Server（mcp/）
cd mcp && uv sync                 # 或 pip install -e .
uv run buganalyzer-mcp            # 启动（stdio 传输，供 Claude Code 调用）
python -m tests                   # 单元测试（纯逻辑 + 假 SSH 池，无需 paramiko）

# 知识库导入工具（tools/kb_import/）
cd tools/kb_import && uv sync
kb-import rebuild-index                          # 重建 INDEX.md
kb-import import --file 问题单导出.csv            # 导入历史问题单（幂等）
```

## Phase 1 骨架的已知边界（后续阶段处理）

| 边界 | 说明 |
|---|---|
| 不接生产 | Phase 1 硬边界，Server 拒绝 tier=prod 的连接（已实现） |
| 命令白名单 | `buganalyzer_ssh_run` 只跑 `policy.toml` 白名单命令（已实现）；docker/k8s 的 exec 包装与实例选择是 Phase 2 |
| 确认弹窗 | `allow_confirm` 已解析并返回 `decision=confirm`；交互确认由 Claude Code 权限层（settings.json）把关，属 Phase 2 |
| Arthas | Phase 2（工具占位 `buganalyzer_arthas_run`，未实现） |
| `ssh_run_raw` 兜底 | 设计中有，Phase 1 未实现；`.claude/settings.json` 已默认 deny |
| 日志脱敏 / 审计 / 沙箱 | Phase 2 |
| docker/k8s mode | 骨架阶段 ssh 命令直接在 SSH 主机执行，容器内/实例级定位是 Phase 2 |
