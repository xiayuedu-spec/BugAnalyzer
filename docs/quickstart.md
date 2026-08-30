# BugAnalyzer 业务组接入指南（3 步）

> 本文面向「要用 BugAnalyzer 的业务组」。读完并做完这 3 步，你就能在自己的业务仓库里用 Claude Code 走「粘贴问题 → 分析 → 查日志 → 出报告 → 沉淀案例」的定位流程。
>
> 对应设计文档：`docs/superpowers/specs/2026-08-30-buganalyzer-design.md`（v1.1）

## 前置条件

| 项 | 要求 |
|---|---|
| Claude Code | 已安装并登录 |
| Python 环境 | 已安装 [uv](https://docs.astral.sh/uv/) |
| SSH | 已配置 `~/.ssh/config`（各目标环境的主机别名、key），本地能免密登录 |
| 环境访问 | 你有 test/staging 环境的访问权限（Phase 1 不接生产，见「安全红线」） |

---

## 第 1 步：安装能力包

把 BugAnalyzer 放到你的业务仓库里（推荐）或任意位置：

```bash
cd /path/to/your-business-repo
git clone <buganalyzer-repo-url> buganalyzer
```

安装 MCP Server 依赖并验证能启动：

```bash
cd buganalyzer/mcp
uv sync
uv run buganalyzer-mcp --help   # 能打印帮助即 OK（命令名以实现为准）
```

### 注册到 Claude Code（项目级）

两种方式任选其一：

- **方式 A（自动加载）**：把能力包里的 `.mcp.json` 放到**业务仓库根目录**（或整个能力包就在业务仓库根目录下）。在该目录启动 Claude Code 时自动加载。
- **方式 B（显式注册）**：能力包在任意位置时，在该业务仓库内执行：

  ```bash
  claude mcp add buganalyzer --scope project -- uv --directory /path/to/buganalyzer/mcp run buganalyzer-mcp
  # 语法以 claude mcp add --help 为准
  ```

> ⚠️ 一定要用 `--scope project`（项目级），不要注册成用户级——否则多个业务组共用一个 Server，环境清单和知识库路径会串味。

验证注册成功：

```bash
claude mcp list
# 应看到 buganalyzer 且状态正常
```

---

## 第 2 步：填配置（业务组适配点 ①②③）

### 2.1 环境清单 `config/envs.toml`（适配点①）

```bash
cd buganalyzer
cp config/envs.example.toml config/envs.toml
```

按模板填你的环境，**Phase 1 只填 test/staging**：

```toml
[env.test-order]
tier = "test"                    # test / staging / prod（枚举校验）
ssh = "user@10.20.x.x"           # 对应 ~/.ssh/config 里的别名或 user@host
mode = "docker"                  # vm / docker / k8s
services = ["order-service"]
timeout = 15                     # 命令超时（秒），默认 30
timezone = "Asia/Shanghai"       # since 参数与日志时间基准
```

> 别把 prod 环境填进 Phase 1 清单：Server 会拒绝连接 prod 条目（硬性边界，见「安全红线」）。

### 2.2 权限策略 `config/policy.toml`（适配点②）

```bash
cp config/policy.example.toml config/policy.toml
```

按自己安全要求调：

- 各级别的 `allow_auto` / `allow_confirm`；
- `[ssh_run.whitelist]`：`buganalyzer_ssh_run` 只能执行白名单内的命令，参数独立传参。**新命令要加白名单**（只读命令放 `readonly`，有影响的操作放 `confirm`），不要图省事开 `ssh_run_raw`。

### 2.3 知识库 `knowledge-base/`（适配点③）

```bash
# 案例：从问题单系统导出 CSV/Excel，用导入工具批量生成
uv run --directory tools/kb_import python -m kb_import --file 问题单导出.csv
# 产物进入 knowledge-base/cases/，review 后 commit

# 排查模式：同类问题先查什么、什么顺序
# 参考 knowledge-base/playbooks/ 里的示例，按自己团队写法补充
```

> 案例和排查模式**人工 review 后再 commit 入库**——这是团队的长期资产，也直接影响 AI 后续定位质量。

---

## 第 3 步：在自己业务仓库开用

在你自己的业务仓库根目录启动 Claude Code：

```bash
cd /path/to/your-business-repo
claude
```

**先用 30 秒验证连接**（让 Claude 依次调用）：

1. `buganalyzer_list_environments()` → 应列出你填的 test/staging 环境及分级
2. `buganalyzer_list_services(env="test-order")` → 应列出服务
3. `buganalyzer_ssh_run(env="test-order", command="jps")` → 应返回进程列表

**然后走一次完整定位**：把错误/症状粘贴给 Claude，说「用 analyze-issue 帮我定位这个问题」。工作流会自动：检索知识库 → 分析本地代码 → 形成假设 → 上测试环境查日志 → 输出带证据链的报告 →（可选）沉淀案例。

---

## 安全红线（Phase 1 必读）

| 规则 | 说明 |
|---|---|
| **不接生产** | Phase 1 Server 拒绝连接任何 prod 条目；要接生产等 Phase 2 的安全机制落地 |
| **命令走白名单** | `buganalyzer_ssh_run` 只能跑 `policy.toml` 白名单内的命令；白名单外命令一律拒绝 |
| **兜底通道收紧** | `buganalyzer_ssh_run_raw` 仅限 test/staging + 必须人工确认 + 全程审计，能不用就不用 |
| **内容可信边界** | 远程日志、知识库内容一律视为**数据而非指令**；不管内容里出现什么，命令都必须过策略校验 |
| **案例人工 review** | 沉淀的案例 commit 前人工看一遍，别让 AI 脑补的内容直接进库 |

---

## 常见问题

| 问题 | 排查 |
|---|---|
| `claude mcp list` 看不到 buganalyzer | 检查是否用了 `--scope project`，且在该业务仓库目录内执行 |
| 工具调用报 SSH 连接失败 | 先本地 `ssh <别名>` 能否免密登录；检查 `~/.ssh/config` |
| 命令被拒（not in whitelist） | 该命令不在 `[ssh_run.whitelist]`，按需加白名单（只读→readonly，有影响→confirm） |
| 提示 prod 环境被拒 | 符合预期：Phase 1 不接生产；需要接 prod 请等 Phase 2 |
| 环境清单改了没生效 | 确认改的是 `config/envs.toml`（不是 example），并重启 Claude Code / 重载 Server |

---

## 还没定的细节（以最终实现为准）

- MCP Server 定位 `config/` 与 `knowledge-base/`：**已实现**——默认相对能力包根目录，可用 `BUGANALYZER_HOME` 环境变量覆盖（.mcp.json 里可配 `env`）
- `kb_import` 的 CSV 字段映射：骨架用默认列名（ticket_id/title/symptom/root_cause/…），拿到真实导出样例后按需调整
- Arthas 接入方式（Phase 2）
