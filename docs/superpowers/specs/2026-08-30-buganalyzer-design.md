# BugAnalyzer —— AI 辅助问题定位工具 设计文档

日期：2026-08-30
状态：已确认（设计评审通过，待用户审阅本文档）

## 1. 背景与目标

团队需要一套**基于 Claude Code 的 AI 辅助问题定位工具**。目标定位工程师在接到问题时，能借助 AI 完成：分析本地代码 → 连接远程环境查日志 → 用诊断工具（如 Arthas）深挖 → 定位根因 → 沉淀案例供后续复用。且不同业务组能低成本适配到自己的代码库与知识库。

### 核心需求（来自用户）

1. AI 基于本地代码分析问题
2. 通过 SSH 连接环境查看服务日志
3. 能使用诊断工具（如 Arthas）
4. 定位过程能总结生成案例，形成知识库，后续类似问题可复用
5. 不同业务组可自行适配自己的代码库与知识库

## 2. 已确认的约束与决策

| 维度 | 结论 |
|---|---|
| 使用形态 | **MCP Server + Skill 工作流**（能力包），本仓库为可安装的 Claude Code 能力包 |
| 目标技术栈 | Java 为主，Python/Shell 为辅；Linux 环境（虚机 + 容器） |
| 环境接入 | 各环境独立 SSH 配置（本地 `~/.ssh/config`，团队维护清单） |
| 知识库形态 | **Markdown 文件仓库**（git 版本化、人可读） |
| 历史问题单 | 无 API，只能导出文件（CSV/Excel）→ 半自动导入建库 |
| 执行边界 | **按环境分级授权**（测试/预发可自主，生产收紧） |
| 定位入口 | 粘贴错误/症状为主，预留 AI 自助上环境查证能力 |
| MCP Server 语言 | **Python（uv 管理）** |

## 3. 方案选型

三个候选方案：

- **A. MCP Server + Skill 工作流（能力包）** —— 本仓库即能力包，MCP 封装远程能力，Skill 编排定位流程，配置化适配多业务组。✅ **选定**
- B. 纯 Skill + Bash —— 最轻，但能力无封装边界、Arthas 集成难、权限控制粗糙。
- C. 独立平台/Web 应用 —— 统一体验但工程量巨大，且丢失 Claude Code 读取本地代码的人机交互优势。

选 A 的理由：贴合「基于 Claude Code」；MCP 是官方标准，工具/权限/类型化天然支持；多业务组适配即「clone + 改配置」；可分阶段落地，风险可控。

## 4. 总体架构

核心思路：**BugAnalyzer = 一个可复制的 Claude Code 能力包**。业务组 clone 下来 → 改配置 → 装进自己的 Claude Code → 在自己业务仓库里直接使用。不改动业务代码，只作为「定位工具」存在。

### 三个关键边界

| 层 | 职责 | 谁关心 |
|---|---|---|
| **MCP Server** | 「手」——能对环境做什么（SSH、日志、Arthas、检索） | 平台方维护 |
| **Skill** | 「脑」——怎么定位（流程编排、假设-验证） | 平台方维护，业务组可调 |
| **配置 + 知识库** | 「数据」——环境在哪、踩过什么坑 | 业务组自己的 |

### 目录结构

```
BugAnalyzer/
├── .claude/
│   ├── settings.json              # 按环境分级授权的 Permission 规则
│   └── skills/
│       └── analyze-issue/         # 问题定位主工作流（SKILL.md + 脚本）
├── mcp/                           # MCP Server（远程环境能力层，Python/uv）
│   ├── pyproject.toml
│   └── src/buganalyzer_mcp/
│       ├── server.py              # MCP 入口，注册工具
│       ├── env_registry.py        # 解析环境清单
│       ├── ssh_client.py          # SSH / 跳板机执行
│       ├── log_tools.py           # 拉取/搜索日志
│       ├── arthas_tools.py        # Arthas 诊断
│       └── kb_tools.py            # 知识库检索
├── tools/
│   └── kb_import/                 # 问题单导出文件 → 案例 Markdown 转换器
├── config/
│   ├── envs.example.toml          # 环境清单模板（业务组适配点①，复制为 envs.toml 使用）
│   └── policy.example.toml        # 环境分级权限策略（业务组适配点②，复制为 policy.toml 使用）
├── knowledge-base/                # 案例知识库（业务组适配点③）
│   ├── cases/
│   ├── templates/case-template.md
│   └── INDEX.md
├── docs/
│   ├── quickstart.md              # 业务组接入 3 步
│   └── architecture.md
└── README.md
```

## 5. MCP Server（远程环境能力层）

「手」，把所有远程操作封装成类型化的工具暴露给 Claude。

技术选型：**Python（uv 管理）**。理由：SSH 库（asyncssh/paramiko）与运维脚本生态最成熟，和 Arthas 命令行集成顺手；Java 团队维护 Python 门槛低于 TS。

### 工具清单（完整列表；`arthas_run` 属 Phase 2 落地）

| 工具 | 作用 | 示例 |
|---|---|---|
| `list_environments()` | 读环境清单，列出可用环境及分级 | 环境=prod-order, tier=prod |
| `list_services(env)` | 列出环境上的服务（进程/容器） | order-service, payment-service |
| `ssh_run(env, cmd)` | 在目标环境执行命令 | `jps`, `cat /etc/hosts` |
| `fetch_logs(env, service, pattern, since, tail)` | 拉取/搜索服务日志 | 最近 10 分钟内的 ERROR |
| `arthas_run(env, service, command)` | 对 Java 服务跑 Arthas 命令 | `thread -n 3`, `watch ...` |
| `kb_search(query)` | 检索知识库案例 | 输入症状/关键词 → 返回相关案例 |

### 设计决策

- **SSH 复用本地配置**：直接用 `~/.ssh/config`，不单独存凭据。容器/虚机通过环境清单里的 `mode`（`vm`/`docker`/`k8s`）决定连接方式（如 docker exec）。
- **Arthas 集成方式**：先 SSH 找目标 JVM PID → 用 `arthas-boot.jar` attach（或已有 Arthas server）→ 执行诊断命令。封装成幂等工具，带超时保护。
- **命令带风险等级**：每条命令关联风险级别，是否允许由「策略配置 + Claude Code 权限系统」共同决定（见第 7 节）。

## 6. analyze-issue 定位工作流（Skill 层）

「脑」，把一次问题定位编排成可复用的标准流程。**核心：三层信息源（用户提示 / 历史案例 / 本地代码）交叉印证，假设驱动，每一步留痕。**

```
用户粘贴错误/症状（或：指定环境 + 关键词）
   │
① 理解输入 ──────────── 解析异常堆栈/症状，提取实体（服务、错误类型、关键词）
   │                    ↳ 症状有歧义 → 主动追问 1-2 个关键问题（哪个环境/何时开始/最近改动）
   │
② 检索知识库 ────────── kb_search：有没有类似案例？（含历史导入的问题单）
   │                    ↳ 命中 → 参考当时结论和修复 MR，直接进④验证
   │                    ↳ 未命中 → 参考「排查模式」（同类问题查什么、什么顺序）
   │
③ 本地代码分析 ──────── 在业务仓库里搜相关代码路径、异常处理、配置；看最近 git 改动
   │                    （MR 链接 → 找到具体改动文件）
   │
④ 形成假设 ──────────── 假设来源 = 用户提示线索 × 案例模式 × 代码现状，按可能性排序
   │
⑤ 环境查证（分级授权）─ list_environments → list_services → fetch_logs 找异常
   │                    →（Java 需要时）arthas 看线程/JVM/方法执行 → 验证/排除假设
   │
⑥ 收敛根因 ──────────── 用证据链排除假设，定位根因
   │
⑦ 输出报告 ──────────── 根因 + 证据链（日志片段/命令输出摘要）+ 建议修复 + 参考 MR
   │
⑧ 沉淀案例（可选）──── 用户确认后，按模板生成案例 Markdown 入库 → 回填问题单
```

要点：
- **①②③ 不碰环境**：先靠「知识库 + 本地代码」低成本收敛，命中就直接验证，避免无谓 SSH 翻日志。
- **⑤ 带着问题去查证**：用假设驱动的精确检索（`pattern + since`），输出精简、证据可追溯。
- **⑦ 每一步留痕**：报告附证据链，⑧ 生成的案例天然带证据，不是 AI 脑补。
- **⑧ 回填问题单**：按问题单导出的同格式回填「定位结论 + MR」，人工 review 后提交。

## 7. 环境分级授权与安全

授权不按「工具」维度，按「环境」维度。同一个 `ssh_run` 在测试环境和生产环境风险完全不同。

### 环境清单分级（`config/envs.toml`）

```toml
[env.test-order]
tier = "test"
ssh = "user@10.20.x.x"
mode = "docker"           # vm / docker / k8s
services = ["order-service"]

[env.prod-order]
tier = "prod"
ssh = "user@10.10.x.x"
mode = "vm"
services = ["order-service", "payment-service"]
```

### 权限策略（`config/policy.toml`，业务组可调）

```toml
[tier.test]
allow_auto = ["list_*", "fetch_logs", "ssh_run:readonly:*", "arthas:readonly:*"]
allow_confirm = ["ssh_run:*", "arthas:*"]

[tier.prod]
allow_auto = ["list_*"]
allow_confirm = ["fetch_logs", "ssh_run:readonly:*", "arthas:readonly:*"]
deny = ["*write*", "restart", "kill", "rm"]
```

### 命令风险分级（MCP Server 内部硬约束）

| 级别 | 例子 | 默认策略 |
|---|---|---|
| 只读 | `tail`、`grep`、`jps`、`arthas thread/watch` | 低风险环境自动放行 |
| 可观察影响 | `arthas redefine/trace`、`jstack` 造成停顿 | 需确认 |
| 写/危险 | 重启、`kill`、改配置、删文件 | 默认拒绝，除非策略显式放行 |

### 双重校验（关键设计）

- **MCP Server 侧（硬约束）**：每个工具执行前检查 `env.tier` + 命令风险等级，不符合策略直接拒绝并说明原因。Server 是最后防线——就算 Claude Code 权限配置错了，生产上的写操作也过不了 Server。
- **Claude Code 侧（交互确认）**：settings.json 把需要确认的操作配置成弹窗确认，让人工在关键时刻把关。

### 安全增强（Phase 2 可加）

- **日志脱敏**：`fetch_logs` 返回前对密钥/token/手机号脱敏（可配置）
- **审计日志**：MCP Server 记录每次调用（环境、命令、结果摘要、时间）
- **超时与沙箱**：所有远程命令强制 timeout，非交互模式执行

## 8. 知识库与多业务组适配

### 案例结构（knowledge-base/）

```
knowledge-base/
├── cases/
│   ├── 2026-08/
│   │   ├── order-service-oom-killed.md          # 手工/定位沉淀
│   │   └── imported-TKT-4821-conn-timeout.md    # 历史问题单导入
│   └── ...
├── templates/case-template.md
└── INDEX.md                # 检索索引
```

案例模板（frontmatter 是检索的关键）：

```markdown
---
title: OrderService 连接超时导致 502
tags: [java, http, timeout, netty]
symptom: 高峰期 502，日志有 ConnectException
root_cause: 连接池耗尽
service: order-service
env: prod
severity: P1
created: 2026-08-30
source: ticket        # ticket(问题单导入) / ai(定位沉淀) / manual
related_mr: https://gitlab.../order-service/-/merge_requests/123
---
## 现象
## 定位过程（证据链）
## 根因
## 修复方案（含 MR）
## 验证方法
## 复用提示
```

### 知识库构建链路（历史问题单导入）

问题单系统**无 API，只能导出文件**，因此采用「半自动导入」：

```
问题单系统（无 API）
   │ 导出 CSV/Excel（含：标题、症状、定位结论、修复MR、标签）
   ▼
tools/kb_import/          # 校验字段 → 按模板生成 Markdown → 归入 cases/
   ▼
knowledge-base/cases/*.md
```

- 每个历史问题单 = 一个标准案例，`related_mr` 让 AI 能顺藤摸瓜找到修改代码。
- 转换产出人可读 Markdown，业务组 review 后 commit 入库，非黑盒写入。

### 检索方式（`kb_search`）

- 基于 **ripgrep 在 frontmatter + 正文全文搜索**，支持 `tags:` 精确过滤 + 症状关键词模糊匹配。
- 返回 Top-K 相关案例（标题 + tags + 摘要 + 文件路径），AI 决定深入读哪个。
- MVP 不引入向量库；案例量 >几百条后再升级检索层。

### 多业务组适配 = 「改 4 处」清单

| 适配点 | 业务组要做的 |
|---|---|
| ① `config/envs.toml` | 填自己环境清单 + 分级 |
| ② `config/policy.toml` | 按自己安全要求调权限 |
| ③ `knowledge-base/cases/` | 用自己的案例（可从问题单导出导入） |
| ④ 代码库 | 在自己业务仓库里启动 Claude Code + 注册本能力包 |

**业务组不需要动的**：MCP Server 代码、analyze-issue 工作流逻辑。平台方维护能力，业务组只填数据。

### 知识库增长闭环

```
问题单导出 ──→ kb_import ──→ cases/ (历史积累)
                                 ▲
新问题定位 ──→ analyze-issue ──→ 生成案例 ──→ 人工 review ──┘
                                  （含 MR 链接）
后续类似问题 ──→ kb_search 命中 ──→ 复用上次结论，快速定位
```

## 9. 分阶段落地

### Phase 1 —— MVP（单业务组跑通闭环）

目标：一个 Java 团队能用它定位真实问题，覆盖「粘贴问题 → 分析 → 查日志 → 出报告 → 沉淀案例」全流程。

| 交付物 | 内容 |
|---|---|
| MCP Server | `list_environments`、`list_services`、`ssh_run`、`fetch_logs`、`kb_search`（暂不含 Arthas） |
| analyze-issue Skill | 完整 8 步工作流（含主动追问、案例检索、排查模式参考） |
| 知识库 | 案例模板 + `kb_import` 导入工具 + 基础检索 |
| 配置 | `envs.toml` + `policy.toml` 示例 |
| 文档 | README、业务组接入 3 步（quickstart） |

验证方式：挑 2-3 个历史问题单用 kb_import 导入建库，再拿 1 个相似真实问题走 analyze-issue，看能否命中并给出正确方向。

### Phase 2 —— 深度诊断

- Arthas 集成：attach 诊断（线程、JVM、方法级 watch/trace）
- 环境分级授权落地：policy.toml 硬约束 + Claude Code 确认弹窗
- 安全增强：日志脱敏、审计日志、命令超时/沙箱
- 检索增强：案例多了之后 kb_search 加权重排序

### Phase 3 —— 多团队推广

- 多业务组打包分发流程、适配文档
- 问题单回填闭环（结论 + MR 写回导出格式，人工提交）
- 可选：案例库升级向量检索、对接告警/工单

## 10. 测试策略

| 层级 | 方式 |
|---|---|
| MCP Server 单元测试 | mock SSH，测 env 解析、policy 判断、日志解析 |
| 流程测试 | 本地「模拟环境」沙箱，skill 全流程对着它跑（不碰真实生产） |
| 端到端冒烟 | 挑低风险测试环境，真实走一遍 analyze-issue |
| 安全测试 | 验证 prod 环境危险命令被策略拒绝（不管 Claude 怎么请求） |

## 11. 未决事项（后续阶段处理）

- 问题单系统的具体导出格式（CSV 字段映射）需拿到真实样例后确定
- Arthas 是否已有公司内统一部署方式（arthas-boot vs server）待确认
- 日志脱敏规则需业务组提供敏感字段清单
