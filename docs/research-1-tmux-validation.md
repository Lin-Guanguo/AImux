# AI 终端远程操控探索

Last Updated: 2026-03-14
Topic Summary: 探索 Claude Code 远程接入方案，验证 tmux send-keys 可行性，确定新开源项目设计方向

## 背景

日常在 tmux 里开多个 Claude Code session 工作，需要一种零介入的远程接入方式，能从手机或其他设备操作本地正在运行的 Claude Code。

---

## Claude Code 远程方案对比

| 方案 | 能接入已有 session？ | Subscription？ | 自建？ |
|------|---------------------|---------------|--------|
| Remote Control (`/remote-control`) | 能 | 能 | 不能，走 Anthropic 中继 |
| SSH + tmux attach | 能 | 能 | 能 |
| 社区 Web UI（claude-code-web 等） | 不能，新建 | 能 | 能 |
| Agent SDK | 不能，新建 | 不能（第三方禁用） | 能 |

### Remote Control

- 协议：outbound HTTPS 轮询 Anthropic API，私有协议，无法自建中继
- 启动方式：`/remote-control <name>` 或 `/config` 全局开启
- 连接方式：claude.ai/code Web 界面 或 Claude 手机 App 扫码
- 限制：依赖 Anthropic 网络，断网 ~10 分钟超时

### 社区 Web UI 项目

| 项目 | 架构 | 核心机制 |
|------|------|----------|
| **claude-code-web** (vultuk) | Express + WebSocket + xterm.js | `node-pty` spawn CLI，PTY 套壳 |
| **claude-code-webui** (sugyan) | React + Hono + SSE | SDK `query()` spawn CLI，结构化 JSON |
| **CloudCLI** (siteboon) | 插件系统 | 读写 `~/.claude` 配置 |

关键发现：
- PTY 方案（claude-code-web）不需要 API key，用本地 CLI 登录态，subscription 可用
- SDK 方案底层也是 spawn CLI 子进程，JSON over stdin/stdout 通信
- **所有社区方案都是新建 session，无法接入已有 session**

## tmux send-keys 方案验证

### 核心发现

tmux `send-keys` 可以完美模拟人类在 Claude Code CLI 中的所有操作：

```bash
# 发送文本 + 回车
tmux send-keys -t %15 "你好" Enter

# 发送 Ctrl-C 中断
tmux send-keys -t %15 C-c

# 发送 Escape
tmux send-keys -t %15 Escape

# 读取输出
tmux capture-pane -t %15 -p -S -30
```

### 实际验证结果

1. **发送消息** — `send-keys "你好" Enter` → Claude Code 正常接收并回复
2. **中断响应** — 发送消息后 0.5 秒 `send-keys C-c` → 成功中断，显示 `Interrupted`
3. **从另一个 Claude Code session 操控** — 在 pane %13 的 Claude Code 里通过 Bash 工具操控 pane %15 的 Claude Code，完全可行

### 窗口管理

tmux 支持给窗口和 pane 打标注，AI 终端可以用来识别每个 pane 的用途：

```bash
tmux rename-window -t 0:1 "cyber-mnema"
tmux select-pane -t %15 -T "claude-tmp"
tmux list-panes -t 0:1 -F "#{pane_index} #{pane_id} #{pane_title} #{pane_current_command}"
```

## 架构设想：AI 终端

```
手机/浏览器 → Web 服务（自建）→ tmux send-keys → Claude Code CLI
                   ↑
          tmux capture-pane ← 读取输出
```

优势：
- **能接入已有 session** — 这是唯一能做到这点的自建方案
- **零依赖** — 不需要 SDK、不需要 API key、不需要 Anthropic 中继
- **Subscription 可用** — 用的就是本地 CLI 登录态
- **人类随时介入** — tmux attach 即可接管
- **多 session 管理** — 通过 pane ID + 标题管理多个 Claude Code 实例

待解决：
- Web 前端如何渲染 ANSI 输出（xterm.js？）
- 输出轮询频率与实时性权衡
- 认证与安全（Tailscale / nginx 反代 + auth）
- 会话状态感知（Claude Code 是在等输入还是在执行？）

## 新项目设计方向

### 与 TermSupervisor 的关系

TermSupervisor 的问题：
- 为支持 iTerm2 + tmux 双适配器引入了大量抽象（TerminalAdapter protocol）
- 每个 pane 建状态机，硬编码状态规则，新工具（如 Codex）没有统一的状态信号，维护困难
- 定位是"监控/展示"，不是"操控"

新项目是**全新项目**，不是重写：
- 只绑定 tmux，不做终端抽象
- 核心能力是 AI 操控，不是监控展示
- 不做状态机，状态判断交给 AI（通过 capture-pane 读屏幕内容）

### 架构设计

两层结构，极简：

1. **API 层** — 封装 tmux 操作原语
   - `send_keys(pane_id, text)` / `send_key(pane_id, key)`
   - `capture_pane(pane_id, lines)`
   - `list_panes()` / `rename_window()` / `rename_pane()`
   - `new_window()` / `split_pane()`

2. **AI 层** — 调用 API 层完成任务
   - AI 通过 capture-pane 读取屏幕内容，自行判断状态
   - AI 自己写 Python 脚本调用 API 层执行操作
   - 状态判断可用低成本模型（如 Haiku），不进入主上下文

**不做硬编码的状态逻辑。** 加什么新工具都不需要改代码，AI 看一眼屏幕就知道状态。

### 交互模式

- 浏览器/手机提供 dashboard + 聊天窗口
- 用户通过聊天告诉 AI 要做什么
- AI 翻译成 tmux 操作执行
- 人类随时可以 tmux attach 直接接管

### 开源定位

- 计划开源，内容不含个人信息
- 目标受众：tmux 重度用户 + AI 编程工具用户
- 目前缺少一个好的"AI 操控 tmux"的开源方案

### 候选名称

- **AImux** — AI + tmux，短小好记
- **TmuxSupervisor** — 延续 TermSupervisor 血统
- **tmux-pilot** — AI 副驾驶隐喻

## 下一步

单独起项目，开源开发。

## 参考

- [claude-code-web](https://github.com/vultuk/claude-code-web) — PTY 套壳方案参考
- [claude-code-webui](https://github.com/sugyan/claude-code-webui) — SDK 方案参考
- [Claude Code Remote Control 文档](https://code.claude.com/docs/en/remote-control)
- [Claude Agent SDK](https://platform.claude.com/docs/en/agent-sdk/overview)
