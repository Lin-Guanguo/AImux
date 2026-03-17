# AI 终端远程操控探索 2：Agora 项目分析

Last Updated: 2026-03-17
Topic Summary: 分析 Agora 编排框架 + acpx/ACP 协议生态，评估对 AImux 的参考价值，确认 tmux+JSONL 双通道方案的独特定位

## 背景

[上一篇](AI终端远程操控探索-2026-03-14.md) 确定了 tmux send-keys 方案和 AImux 项目方向。本篇深入分析 [Agora](https://github.com/FairladyZ625/Agora) 项目——一个基于 tmux 的多 Agent 民主编排框架，评估其设计对 AImux 的参考价值。

---

## Agora 项目概览

核心理念："Agents debate, Humans decide, Machines execute"

三层架构：
- **上层**：IM Adapter（Discord / Feishu / Slack）
- **中层**：Agora Core / Orchestrator（状态机 + Gate 审批 + 任务调度）
- **下层**：Runtime Adapter（OpenClaw）+ Craftsman Adapter（Claude Code / Codex / Gemini）

技术栈：TypeScript + SQLite + Fastify + Commander，核心层零外部依赖。

### Agora 做得好的地方

- 三层解耦干净，Core 零外部依赖，换 IM/Runtime/Craftsman 不碰核心代码
- Gate 系统完整，6 种门禁（command / archon_review / approval / quorum / all_subtasks_done / auto_timeout）覆盖从自动到人工审批的全部场景
- Workflow as Data，阶段/门禁/分支全是 JSON 定义，运行时可调
- 执行抽象统一，Claude/Codex/Gemini 在同一套接口下工作
- 审计能力强，每一步状态变迁和操作都有完整日志

---

## 需求场景差异分析

Agora 和 AImux 面向的是两种不同的多 Agent 协作模式：

| 维度 | Agora 场景 | AImux 场景 |
|------|-----------|-----------|
| 编排者 | 人工通过 CLI/REST 驱动 | AI 自主决策 |
| 协作结构 | 民主治理（投票/审批/多角色） | 金字塔委派（老板带员工） |
| 状态管理 | SQLite 持久化 + 状态机 | 无状态，直接观测 tmux |
| tmux 管理 | 框架统一创建和管理 | 灵活接管已有 session，人工随时介入 |
| 任务模式 | 离散阶段，按 Gate 推进 | 长期驻留，持续工作 |

**Agora 的场景下这些设计很合理：** 多 Agent 协作需要正式的治理流程——谁能审批、几票通过、什么阶段该做什么，状态机和 Gate 系统正是为此而生。适合团队协作、正式工作流、需要审计追溯的场景。

**AImux 的场景不同：** 一个 AI 老板带几个 AI 员工做长期任务，决策链路短，需要灵活介入。Agora 的核心能力（Gate、Quorum、模板驱动的 Workflow）在 AImux 场景下用不上，tmux 层也需要更灵活的接管能力。不是因为这些设计不好，而是场景不匹配。

类比：Agora 像一个**正规的项目管理系统**（Jira），AImux 需要的更像**老板直接在工位上指挥**。两种模式各有所长，适用场景不同。

### 是否基于 Agora 开发？

结论：**另起炉灶。** 核心需求和 Agora 的核心假设差异太大，基于 Agora 开发意味着要拆掉状态机、Gate 系统、模板系统、注册表——剩下的基本没什么了。AImux 需要的东西很薄，几十行代码 + 一个 skill 文件就能搭起来。

---

## Agora 的 tmux 执行层

### 统一执行抽象

4 个核心 Port 接口：

| Port | 职责 |
|------|------|
| `CraftsmanAdapter` | 分派任务：把 dispatch request 翻译成具体 CLI 命令 |
| `CraftsmanExecutionProbePort` | 观测状态：轮询判断"跑完没？要输入吗？" |
| `CraftsmanExecutionTailPort` | 读输出：抓最近 N 行 stdout/stderr |
| `CraftsmanInputPort` | 发送输入：给运行中的会话发文字/按键/选择 |

### Adapter 实现示例

每个 Craftsman 只需定义命令规格：

```typescript
// ClaudeCraftsmanAdapter
buildCommand(request) {
  return { command: 'claude', args: ['--dangerously-skip-permissions', '-p', request.prompt] }
}

// CodexCraftsmanAdapter
buildCommand(request) {
  return { command: 'codex', args: ['exec', request.prompt] }
}

// GeminiCraftsmanAdapter
buildCommand(request) {
  return { command: 'gemini', args: ['-p', request.prompt] }
}
```

### 退出标记机制

每条命令被 shell 层包装：

```bash
claude -p "prompt"; status=$?; printf '__AGORA_EXIT__:exec-123:%s\n' "$status"
```

Probe 定期 `tmux capture-pane`，找到 `__AGORA_EXIT__` 标记即知道命令完成和 exit code。标记由 shell 打印，不依赖 AI 输出，可靠性有保障。

### Input 状态机

```
running → needs_input     (等文字输入)
running → awaiting_choice (等选择)
needs_input → running     (收到 sendText 后恢复)
awaiting_choice → running (收到 submitChoice 后恢复)
```

每个输入请求带 transport 类型（text / keys / choice），上层知道该发什么类型的输入。

### 两种运行时模式

| 模式 | 机制 | 默认场景 |
|------|------|----------|
| `tmux` | pane 内执行 + capture-pane 轮询 + send-keys 交互 | CLI |
| `watched` | 独立子进程 + 进程退出后 HTTP POST callback | Server |

tmux 模式支持交互，watched 模式不支持。两者都不适合长期持续通信。

---

## Agora tmux 层的局限

与 AImux 需求对比：

| 维度 | Agora 做法 | AImux 需要 |
|------|-----------|-----------|
| Pane 数量 | 硬编码 3 个（codex/claude/gemini） | 动态数量 |
| 外部 session | 只认自己创建的 pane | 能接管已有 session |
| 完成检测 | 必须有 `__AGORA_EXIT__` 标记 | 不依赖标记，AI 读屏判断 |
| 人工介入 | 未考虑 | 核心需求 |
| 状态感知 | 只知道"跑完没" | 知道"在干什么"（空闲/生成中/等审批/报错） |

Agora 的 tmux 层是**封闭管理型**——假设一切由框架创建和控制。AImux 需要的是**开放接管型**——能操作任何已有 tmux pane。

---

## Agora 上层编排分析

### 任务驱动方式

**全是人工驱动，没有自主决策层。**

```
人类 CLI: agora create "修登录bug" --type coding
  → 任务创建，进入 discuss 阶段

人类 CLI: agora advance OC-123
  → 通过 Gate 检查，推进到 execute 阶段

人类 CLI: agora subtasks create OC-123 --file subtasks.json
  → subtask 写在 JSON 里手动提交，指定谁干、用哪个 craftsman
  → 有 craftsman 配置的 subtask 自动 dispatch

人类 CLI: agora advance OC-123
  → 所有 subtask 完成后推进到下一阶段
```

每一步都需要人工敲命令触发。编排器本质是**确定性规则引擎**：
- 什么时候起 session？没有决策——subtask 创建时立即 dispatch
- 选哪个 craftsman？没有决策——模板里写死
- 失败了怎么办？重试 / 跳过 / 人工介入，没有智能判断

### 场景差异

| 维度 | Agora 场景 | AImux 场景 |
|------|-----------|-----------|
| 编排者 | 人工通过 CLI/REST 驱动 | AI 自主决策 |
| 协作结构 | 民主治理（投票/审批/多角色） | 金字塔委派（老板带员工） |
| 状态管理 | SQLite 持久化 + 状态机 | 无状态，直接观测 tmux |
| 任务模式 | 离散阶段，按 Gate 推进 | 长期驻留，持续工作 |

Agora 面向**多 Agent 协作治理**，需要正式流程（投票、审批、多阶段流转）。AImux 面向**单人带队的工作模式**，决策链路短，需要灵活介入。两种模式各有所长，适用场景不同。

---

## 对 AImux 的启发

### 值得借鉴的概念

1. **退出标记 + probe 轮询** — 解决"怎么知道跑完了"，不依赖 AI 输出
2. **Input 状态机（text/keys/choice）** — 解决"怎么知道该发什么输入"
3. **Session 持久化 + 恢复** — Claude Code `--resume`，Codex `resume --last`
4. **输出归一化** — 不管底层是什么工具，上层收到统一格式的结果

### AImux 的 skill 已覆盖但 Agora 没做到的

AImux 的 `tmux-control.md` skill 已经做到了 Agora 做不到的事：

1. **Claude Code 状态检测**（通过屏幕内容判断）：
   - `❯ ` 在最后一行 → 空闲
   - `⏺` + 文字生成中 → 正在回答
   - `Allow | Deny` → 等权限确认
   - `Ctx:` 在底部状态栏 → 进程还活着

2. **`pane_current_command` 的坑**：Claude Code 版本号显示为进程名，不可靠

3. **capture-pane 带元数据头**：一条命令拿到 pane 元信息 + 屏幕内容

4. **工具特定操作表**：Claude Code 的 `C-c C-c` 退出、`y/n` 审批、Escape 退出 plan mode

### AImux 不需要做的

- Gate / Quorum / Approval 审批系统（场景不需要）
- 状态机 + SQLite 持久化（无状态设计更简单）
- 封闭的 pane 注册管理（需要开放接管）
- 每个工具写一个 Adapter（AI 看屏幕自行判断，零代码适配）

---

## 关键结论

### Skill 即框架

AImux 的核心编排逻辑不需要写成代码框架。老板 AI 的工作本质是"看现状 → 判断 → 发指令"，这些全是 AI 擅长的事。一个 skill 文件描述清楚工具和规则就够了。

Agora 需要几千行代码做编排，是因为编排器是死规则。AImux 的编排器是 AI 本身，只需要告诉它工具和规则。

### 两个信息源：capture-pane vs JSONL 文件

获取 Claude Code 输出有两条路径，各有优劣：

| 维度 | `tmux capture-pane` | Claude Code JSONL session 文件 |
|--------|------|--------|
| 用途 | 判断当前状态（空闲？报错？等输入？） | 了解完整对话历史和具体做了什么 |
| 实时性 | 实时 | 实时写入（已验证） |
| 数据格式 | 纯文本，需要 AI 解析 | 结构化 JSON，字段清晰 |
| 内容范围 | 受 scrollback 限制（默认 2000 行） | 完整，不丢 |
| 包含信息 | 屏幕上当前看到的文字 | 对话、tool_use、token 用量、模型信息 |

**结论：两个都要，互补使用。** capture-pane 看"现在在干什么"，JSONL 看"到底做了什么"。

#### capture-pane 细节

scrollback 默认 2000 行，可通过 `tmux set-option -g history-limit 50000` 调大。但实际使用中不需要抓太多——几千行输出塞进 AI context 浪费 token 且降低判断质量。分层读取更实际：
- 先抓最近 30 行看当前状态
- 需要细节时再抓 200 行看上下文
- 全量 scrollback 几乎不需要

`-J` 参数可以把被 wrap 的行合并回来，AI 更容易解析：
```bash
tmux capture-pane -t %15 -p -S -30 -J
```

关于 pane 尺寸：理论上可以 `tmux resize-pane -x 300 -y 100` 增大 pane 让一屏装更多内容。但 resize 会发 SIGWINCH 信号导致进程重新渲染，对正在运行的 Claude Code 可能造成干扰。考虑到需要人工随时 `tmux attach` 介入，pane 尺寸不宜改太大。

#### JSONL session 文件细节

路径：`~/.claude/projects/<project-dir-encoded>/<session-uuid>.jsonl`

已验证实时性：当前对话的最新用户消息在 JSONL 中立即可见，无延迟。

CyberMnema 的 `fetch-cc-sessions.py` 已实现完整的 JSONL 解析，包括：
- 三层输出（L1 总览 / L2 每 session 对话 / L3 原始 JSONL）
- 消息分类（用户文本 / tool_result / compact_summary / 噪音过滤）
- tool_use 摘要（Read/Edit/Bash/Grep 等工具的参数提取）
- token 用量统计（input/output/cache，按模型分类）
- git commit 追踪（提交次数、增删行数）
- 活跃时长计算（排除 60 分钟以上的空闲间隔）

#### Pane → Session 映射

这是两个信息源配合使用的关键问题。JSONL 文件名是 session UUID，tmux pane 是 `%15`，两者之间没有直接关联。

可行的映射方式：
1. **通过 cwd 关联**（最可靠）：JSONL 里有 `cwd` 字段，tmux pane 有 `pane_current_path`，同一个项目目录基本一对一
2. **通过文件修改时间**：pane 里 Claude Code 刚输出了东西，对应 JSONL 文件修改时间也会更新
3. **通过进程 PID 追溯**：`tmux list-panes` 拿到 PID → 找到 Claude Code 进程 → 从 `lsof` 找到它打开的 JSONL 文件

**映射实现细节（2026-03-17 补充）：**

方案 1 实际上是确定性映射，不需要模糊匹配。Claude Code 的 project 目录编码规则是把路径中的 `/` 替换为 `-`：

```
tmux pane_current_path = /Users/linguanguo/dev/AImux
                              ↓
~/.claude/projects/-Users-linguanguo-dev-AImux/*.jsonl
                              ↓
                    取修改时间最新的 JSONL 文件
```

同一 cwd 下有多个 session 时（比如同一项目目录开了两个 Claude Code pane），用 **最近一条用户消息内容** 交叉验证：刚通过 `send-keys` 发了什么，JSONL 里最新的 user message 应该包含那段文字。这比 `lsof` 追 PID 简单得多，不需要 root 权限。

### 少量代码需求

真正需要写代码的只有：
- 定时触发器（让老板 AI 定期醒来巡检）
- 便利脚本（一键起 N 个 worker pane）
- Pane → Session 映射工具

几十行 shell / Python 即可，不需要一个框架。

---

## acpx / ACP 生态调研（2026-03-17 补充）

### ACP 协议概览

[Agent Client Protocol (ACP)](https://agentclientprotocol.com/) 是新兴的 agent 间通信标准，类似 LSP 对编辑器的意义。由 Zed 和 JetBrains 推动，目标是让任何 agent 接入任何编辑器/客户端。

### acpx 项目

[acpx](https://github.com/openclaw/acpx) 是 ACP 的无头 CLI 客户端（OpenClaw 团队开发，MIT 开源）。核心能力：

- **统一接口**操控 13+ 种 coding agent（Codex、Claude Code、Gemini、Cursor、Copilot 等）
- **结构化 JSON-RPC 通信**，不是 PTY 刮屏
- 持久会话、命名会话、prompt 队列、崩溃恢复

### acpx 的原理

**本质是非交互式运行。** acpx spawn 一个 ACP 适配器子进程，通过 stdin/stdout JSON-RPC 2.0 通信：

```
acpx ──JSON──→ adapter(stdin) ──→ 新的 agent 进程
acpx ←─JSON── adapter(stdout) ←── agent 结构化输出
```

两种接入方式：
1. **原生 ACP 模式** — 工具 CLI 官方内置 `--acp` flag（Gemini、Cursor、Copilot、Kimi、Qwen 等）
2. **SDK 适配器** — Zed 团队写的开源适配器，用官方 SDK 包装：
   - [claude-agent-acp](https://github.com/zed-industries/claude-agent-acp) — 调用 Claude Code Agent SDK（`@anthropic-ai/claude-code`），SDK 的 `query()` spawn 新 CLI 子进程
   - [codex-acp](https://github.com/zed-industries/codex-acp) — 同理包装 Codex SDK

**不是 hack 闭源工具，是用官方 SDK / CLI flag。但正因如此，必须 spawn 新进程，不能 attach 已有 session。**

### Agent SDK resume 的真实机制

Claude Code Agent SDK 的 `resume` 功能容易产生误解：

```typescript
// 不是连接到运行中的进程
// 而是：spawn 新进程 + 从磁盘加载历史对话
query({ prompt: "continue", options: { resume: sessionId } })
```

`session/load` 是从 `~/.claude/sessions/` 读取历史记录重建上下文，不是 IPC 连接到运行中的 Claude Code 进程。

### 为什么官方不开放本地 attach

Remote Control 证明了 Claude Code 进程技术上可以接受外部指令。但 Anthropic 选择只通过云中继暴露：

| 能力 | 是否提供 | 推测原因 |
|------|---------|---------|
| spawn 新进程 | SDK 开放 | 安全可控 |
| 从磁盘恢复历史 | SDK 开放 | 只是重放，不涉及运行态 |
| 连接运行中进程 | 只走 Anthropic 云中继（Remote Control） | 安全审计、商业、流量控制 |
| 本地直连运行中进程 | 不提供 | — |

这是产品决策，不是技术限制。

### 对 AImux 的定位确认

```
         官方路线                    AImux 路线

SDK resume → 新进程+历史恢复      tmux send-keys → 直接操控运行中进程（输入）
ACP/acpx → 新进程+结构化通信     JSONL 文件 → 结构化输出（输出）
Remote Control → 云中继 attach
```

**AImux 用 tmux 解决了 acpx 解决不了的问题（接入已有 session），又用 JSONL 解决了 tmux 的弱点（输出解析）。两个正交通道的组合，比任何单一方案都强。**

这也意味着 AImux 在开源生态中有明确的差异化定位：唯一能本地 attach 已有 session 的自建方案。

---

## 参考

- [Agora](https://github.com/FairladyZ625/Agora) — 多 Agent 民主编排框架
- [上一篇：AI 终端远程操控探索](AI终端远程操控探索-2026-03-14.md) — tmux send-keys 验证与 AImux 设计
- [AImux](https://github.com/Lin-Guanguo/AImux) — AI 驱动的 tmux session 管理器
- [acpx](https://github.com/openclaw/acpx) — ACP 无头 CLI 客户端
- [Agent Client Protocol](https://agentclientprotocol.com/) — ACP 协议官网
- [claude-agent-acp](https://github.com/zed-industries/claude-agent-acp) — Zed 的 Claude Code ACP 适配器
- [codex-acp](https://github.com/zed-industries/codex-acp) — Zed 的 Codex ACP 适配器
- [Claude Code Remote Control 文档](https://code.claude.com/docs/en/remote-control)
