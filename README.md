# tiny-claw

微型编码 Agent（Python 实现），灵感来自 OpenClaw：在受限工作区内调用大模型 + 工具，完成读文件、改代码、执行命令等任务，并通过 **Reporter** 把过程输出到终端或飞书。

> 本项目仍在持续迭代。下文「变更记录」「路线图」会随版本更新，欢迎在对应章节补充说明。

---

## 功能概览

| 能力 | 说明 | 状态 |
| --- | --- | --- |
| ReAct 主循环 | Thinking（可选）→ Action → Observation；Thinking/Action 合并写入 Session | ✅ |
| Session 会话 | 多会话隔离；全量历史在 Session；引擎无状态 | ✅ |
| Working Memory | 每轮从 Session 取最近 N 条（默认 **20**），含孤儿 ToolResult 防线 | ✅ |
| Compactor 压缩 | 超水位线后：远期掩码 + 近期头尾截断；只改发给模型的临时上下文 | ✅ |
| Recovery 自愈 | 工具失败时按报错特征注入「系统救援指南」 | ✅ |
| Plan Mode | 进度外部化到 `PLAN.md` / `TODO.md`，支持断点续传 | ✅ |
| 动态 System Prompt | 按 `session.work_dir` 组装：内核 +（可选 Plan）+ `AGENTS.md` + Skills | ✅ |
| Agent Skills | 扫描 `{work_dir}/.claw/skills/**/SKILL.md` | ✅ |
| 工具集 | `read_file` / `write_file` / `edit_file` / `bash` | ✅ |
| 并发策略 | 只读工具可并行；含写操作或 bash 时整批串行 | ✅ |
| Provider 清洗 | OpenAI 消息链 sanitize，降低智谱 `1214` | ✅ |
| Reporter | 统一输出契约；终端 / 飞书实现 | ✅ |
| CLI / OOM 演示 | `main.py`：读大文件触发压缩；工作区 `./workspace` | ✅ |
| Session 隔离演示 | `main1.py`：记忆截断 + 双会话隔离 | ✅ |
| Plan Mode 演示 | `main2.py`：`--prompt` 长程任务 | ✅ |
| Recovery 演示 | `main3.py`：陷阱指令诱发 `edit_file` 纠偏 | ✅ |
| 飞书接入 | 长连接 / HTTP 代码已注释保留 | 💤 |
| 长期 Memory | `internal/memory` 占位 | 🚧 |
| 多渠道（钉钉 / WebUI 等） | 可基于 Reporter 扩展 | 📋 |

---

## 架构一览

```text
用户（CLI / 飞书）
        │  先 session.append(用户消息)
        ▼
   SessionManager（按 session_id 隔离）
        │
        ▼
   AgentEngine.run(session, reporter)
        │  内置 Compactor + RecoveryManager；无 work_dir
        │
        ├─ PromptComposer(session.work_dir, plan_mode).build()  → System
        ├─ session.get_working_memory(20)
        ├─ context = [System] + Working Memory
        ├─ compacted = Compactor.compact(context)    ← 仅临时上下文
        ├─ Thinking / Action 使用 compacted
        ├─ session.append(合并后的 Assistant + 全量工具结果)
        ├─ 工具 IsError → RecoveryManager.analyze_and_inject
        └─ Observation(s) → session.append(...)
                │
         TerminalReporter / FeishuReporter
                │
         Provider（sanitize 消息链）+ Tools Registry（bound to work_dir）
```

核心约定：

- **Session**：绑定 `work_dir` 与完整对话历史；`work_dir` 跟随会话。
- **Working Memory**：每轮只把「System + 最近 N 条」取出；全量仍在 Session。
- **Compactor**：只压缩「本轮发给模型」的临时列表；Session / 落盘仍是全量原文。
- **Recovery**：工具返回 `IsError` 时增强 stderr，引导 Agent 改主意（如先 `read_file` 再编辑）。
- **Plan Mode**：System Prompt 强制维护 `PLAN.md` / `TODO.md`；进程重启后可凭文件续跑。
- **调用约定**：先 `session.append(用户输入)`，再 `engine.run(session, reporter)`。
- **工具边界**：工具只能访问 `session.work_dir`（CLI 默认 `./workspace`），仓库根目录文件对 Agent 不可见。

---

## 目录结构

```text
tiny-claw/
├── cmd/claw/
│   ├── main.py                   # CLI / OOM 压缩演示（Session + workspace）
│   ├── main1.py                  # Session / Working Memory 隔离演示
│   ├── main2.py                  # Plan Mode：--prompt 长程任务
│   └── main3.py                  # Recovery：edit_file 自愈演示
├── AGENTS.md                     # 规范样例（需放在对应 work_dir 下）
├── .claw/skills/
│   └── git-workflow/SKILL.md
├── workspace/                    # CLI 默认工作区（.gitignore）
│   ├── mock_log.txt              # OOM 演示用大日志（需在此目录）
│   ├── auth.py                   # Recovery 演示目标文件
│   ├── PLAN.md / TODO.md         # Plan Mode 外部化进度（运行后生成）
│   └── …
├── mock_log.txt                  # 可选：根目录样例；演示前请 cp 到 workspace/
├── tmp/
│   ├── project_front/
│   └── project_back/
├── internal/
│   ├── engine/
│   │   ├── loop.py               # AgentEngine：Compactor + Recovery 注入
│   │   ├── session.py
│   │   ├── reporter.py
│   │   └── terminal_reporter.py
│   ├── context/
│   │   ├── composer.py           # 含 Plan Mode 提示词分支
│   │   ├── skill.py
│   │   ├── compactor.py
│   │   └── recovery.py           # 工具失败救援指南
│   ├── feishu/
│   ├── provider/
│   │   └── openai.py             # 消息链 sanitize（防 1214）
│   ├── schema/
│   ├── tools/
│   └── memory/                   # 【预留】
├── .env
└── README.md
```

---

## 环境要求

- Python 3.10+（推荐 3.11 / 3.13）
- 依赖：

```bash
pip install openai python-dotenv lark-oapi flask
```

---

## 配置

```bash
# 大模型（智谱 OpenAI 兼容）
ZHIPU_API_KEY=your_zhipu_api_key

# 飞书（恢复飞书入口时需要）
FEISHU_APP_ID=cli_xxx
FEISHU_APP_SECRET=xxx
FEISHU_ENCRYPT_KEY=xxx
FEISHU_VERIFY_TOKEN=xxx
# FEISHU_EVENT_MODE=websocket  # 或 http
```

---

## 快速启动

### 1. CLI / Compactor（OOM）演示

```bash
cd tiny-claw
# 大日志必须在工作区内，工具才能读到
cp -f mock_log.txt workspace/   # 若已在 workspace/ 可跳过
python cmd/claw/main.py
```

当前 `main.py` 行为：

1. Session id：`test_oom_protection_001`，`work_dir=./workspace`
2. 任务：`echo` → `read_file mock_log.txt` → `date`
3. 读大文件后上下文变长，下一轮 Generate 前会走 `Compactor`（默认水位线 **20000** 字符，保护最近 **6** 条）

让 `AGENTS.md` / Skills 生效时，放到 **work_dir** 下：

```bash
cp AGENTS.md workspace/
mkdir -p workspace/.claw/skills && cp -R .claw/skills/* workspace/.claw/skills/
```

### 2. Session / 记忆截断演示

```bash
python cmd/claw/main1.py
```

| 会话 | 目的 |
| --- | --- |
| Session A（`tmp/project_front`） | 读密钥 → 刷闲聊挤出 Working Memory → 再追问 |
| Session B（`tmp/project_back`） | 验证跨会话隔离 |

### 3. Plan Mode 演示

```bash
python cmd/claw/main2.py --prompt "在 workspace 里搭一个简单的静态页服务"
```

| 项 | 说明 |
| --- | --- |
| Session | `task_web_server_01`，`work_dir=./workspace` |
| 引擎 | `plan_mode=True` |
| 外部化 | 强制维护 `PLAN.md` / `TODO.md`；进程重启后可凭文件断点续传 |

### 4. Recovery 自愈演示

```bash
# 确保 workspace/auth.py 存在（login 仅允许 admin 的初始版本更易触发纠偏）
python cmd/claw/main3.py
```

| 项 | 说明 |
| --- | --- |
| Session | `test_recovery_001`，`work_dir=./workspace` |
| 引擎 | `plan_mode=False`，专注单点纠偏 |
| 陷阱 | 不给读文件机会，直接命令 `edit_file` 替换给定代码块 |
| 预期 | `old_text` 不匹配 → Recovery 注入救援指南 → Agent 先 `read_file` 再重试 |

---

## Compactor 行为摘要

默认：`Compactor(max_chars=20000, retain_last_msgs=6)`。

| 区域 | 消息类型 | 超限后处理 |
| --- | --- | --- |
| 任意 | System | 原样保留 |
| 远期（保护区外） | ToolResult（内容 >200） | 全量掩码，只留长度提示 |
| 近期（保护区内） | ToolResult（内容 >1000） | 头 500 + 尾 500 截断 |
| 远期 | Assistant 长文（>200） | 折叠为简短占位 |
| 任意 | `tool_calls` 字段 | **永不修改**（维系工具链） |

引擎侧流程：`get_working_memory(20)` → `compact(...)` → `Generate`；`session.append` 始终写全量原文。

---

## Recovery 行为摘要

`RecoveryManager.analyze_and_inject(tool_name, raw_error)`：匹配已知报错特征后，在原文后追加 `[系统救援指南]`。

| 工具 | 典型触发 | 救援方向 |
| --- | --- | --- |
| `edit_file` | 未找到 `old_text` / 多处匹配 | 先 `read_file`，或补充上下文保证唯一 |
| `read_file` / `write_file` | 路径不存在 / 权限拒绝 | `ls` / `find` 探路，或检查工作区限制 |
| `bash` | 命令不存在 / 超时 / 语法错误 | 换命令、后台化常驻进程、检查转义 |

未匹配特征时原样返回错误文本。引擎仅在 `result.is_error` 时注入。

---

## Plan Mode 行为摘要

`PromptComposer(..., plan_mode=True)` 会追加强制规范：

1. **Bootstrapping**：先嗅探 `PLAN.md` / `TODO.md`；不存在则创建，已存在则读取续跑（禁止覆盖）。
2. **单步打勾**：完成一步立即把 `TODO.md` 对应项改为 `- [x]`。
3. **迷失自救**：报错或迷失时重新读 `TODO.md` 定位。

---

## 飞书模式（已注释保留）

`main.py` 内飞书 HTTP / 长连接代码已注释。`FeishuBot` 按 `chat_id` 取 Session，先 append 再 `run`。

恢复：取消注释 → 配置开放平台（长连接 + `im.message.receive_v1` + 发版）→ 启动。

HTTP：`FEISHU_EVENT_MODE=http`，`POST :48080/webhook/event`（需公网）。

### 常见问题

| 现象 | 可能原因 |
| --- | --- |
| 找不到 `mock_log.txt` | 文件在仓库根目录，但工具只能读 `workspace/`；请 `cp` 进去 |
| 服务无日志、飞书无回复 | 未选长连接 / 未订阅事件 / 未发版 |
| 收得到、发不出，`21001` | 飞书平台策略拦截取 token |
| Thinking / 请求 `429`、超时 | 智谱限流或网络问题 |
| 工具名为 `None` | 模型偶发畸形 tool_call；Provider 侧会过滤空 name |
| 智谱 `1214` messages 非法 | 工具链断裂或 Thinking/Action 分条写入；现已合并 + sanitize |
| Turn 2「不记得密钥」 | Working Memory 截断的预期效果（`main1.py`） |
| 日志出现 `[Compactor] 内存告警` | 上下文超水位线，压缩已触发（预期） |
| 日志出现 `注入救援指南` | Recovery 已增强工具错误（`main3.py` 预期） |
| `workspace` 嵌套 git 警告 | Agent 在 workspace 内 `git init` |
| macOS 5000 端口占用 | 隔空播放接收器，可改用 8080 |

---

## 核心模块说明

### Session（`internal/engine/session.py`）

| API | 作用 |
| --- | --- |
| `append(*msgs)` | 线程安全追加全量历史 |
| `get_working_memory(limit)` | 最近 N 条深拷贝；丢弃首部孤儿 ToolResult |
| `SessionManager.get_or_create` | 多会话隔离 |
| `global_session_mgr` | 模块级单例 |

### AgentEngine（`internal/engine/loop.py`）

- 构造：`AgentEngine(provider, registry, enable_thinking=..., plan_mode=..., compactor=..., recovery=...)`
- 默认 `Compactor(20000, 6)`、`RecoveryManager()`；**不持有** `work_dir`
- `run(session, reporter)`：`working_memory(20)` → `compact` → Thinking/Action（合并为一条 Assistant）→ 工具失败走 Recovery
- 写入 Session 的始终是全量真实消息

### Compactor（`internal/context/compactor.py`）

见上文「Compactor 行为摘要」。

### RecoveryManager（`internal/context/recovery.py`）

见上文「Recovery 行为摘要」。

### PromptComposer / SkillLoader

| 组件 | 作用 |
| --- | --- |
| `PromptComposer` | 内核 →（可选 Plan Mode）→ 可选 `AGENTS.md` → 可选 Skills |
| `SkillLoader` | 扫描 `{work_dir}/.claw/skills/**/SKILL.md` |

### Provider / Reporter / Tools

- Provider：`OpenAIProvider` 发送前 `_sanitize_openai_messages`，保证 tool 调用链完整
- Reporter：`TerminalReporter` / `FeishuReporter`
- Tools：`read_file` / `write_file` / `edit_file` / `bash`（均受 `work_dir` 约束）

---

## 变更记录

> 新版本请在表格**顶部**追加一行，保持时间倒序。

| 日期 | 版本 / 标签 | 摘要 |
| --- | --- | --- |
| 2026-10-27 | — | Recovery 接入引擎；`main3.py` 自愈演示；Plan Mode + `main2.py`；Compactor 默认 20000；Thinking/Action 合并与消息 sanitize 防 1214 |
| 2026-10-24 | — | Compactor 接入引擎；WM=20；OOM 演示与 `workspace` 边界说明 |
| 2026-10-24 | — | Session / Working Memory；引擎无状态；`main`/`main1` Session 驱动 |
| 2026-10-22 | — | Composer + Skills、CLI/`workspace`、TerminalReporter；飞书注释保留 |
| 2026-10-21 | — | 初版：ReAct、工具、Reporter、飞书 |
| _YYYY-MM-DD_ | _vX.Y_ | _在此填写本次迭代说明_ |

### 迭代备注（自由文本）

```text
# 2026-10-27（Recovery / Plan Mode / 1214）
# - 新增 internal/context/recovery.py；工具 IsError 时注入救援指南
# - AgentEngine 默认 RecoveryManager()；plan_mode 传入 PromptComposer
# - Compactor 默认水位线改为 20000（保护区仍为最近 6 条）
# - Thinking 与 Action 合并为单条 Assistant 写入 Session
# - openai.py：_sanitize_openai_messages，过滤空 name tool_call
# - main2.py：Plan Mode + --prompt；外部化 PLAN.md / TODO.md
# - main3.py：Recovery 演示；陷阱指令改 auth.py

# 2026-10-24（Compactor）
# - 新增 internal/context/compactor.py
# - AgentEngine 默认 Compactor；Generate 前 compact
# - Working Memory 取出条数改为 20；Compact 保护区仍为最近 6 条
# - main.py：OOM 演示读 mock_log.txt（须位于 workspace/）
# - 修复 Action 误用未压缩 context 的问题

# 2026-10-24（Session）
# - 新增 session.py；run(session, reporter)
# - main1.py 双会话演示；FeishuBot 按 chat_id 取 Session

# 2026-10-22
# - composer / skill；CLI + TerminalReporter

（在此继续追加）
```

---

## 路线图

- [ ] `requirements.txt` / `pyproject.toml`
- [ ] CLI / 飞书 / 演示入口用启动参数切换
- [ ] Session 历史 JSONL 落盘
- [ ] Compactor / WM 阈值可配置或按 token 预算
- [x] Recovery：工具失败特征救援（已落地，可继续扩展模式）
- [x] Plan Mode：`PLAN.md` / `TODO.md` 外部化（已落地）
- [ ] `internal/memory` 跨会话长期记忆
- [ ] Provider 429/超时重试
- [ ] 更多 Reporter
- [ ] ……（在此追加）

---

## 开发约定（简要）

- 注释中文，源码 UTF-8
- 改一处功能时尽量不破坏已正确能力
- 密钥只放 `.env`
- 演示用文件放进对应 `work_dir`（默认 `workspace/`），不要假设能读仓库根目录

---

## License

见 [LICENSE](./LICENSE)。
