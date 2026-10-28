# tiny-claw

微型编码 Agent（Python 实现），灵感来自 OpenClaw：在受限工作区内调用大模型 + 工具，完成读文件、改代码、执行命令等任务，并通过 **Reporter** 把过程输出到终端或飞书。

> 本项目仍在持续迭代。下文「变更记录」「路线图」会随版本更新，欢迎在对应章节补充说明。

---

## 功能概览

| 能力 | 说明 | 状态 |
| --- | --- | --- |
| ReAct 主循环 | Thinking（可选）→ Action → Observation；Thinking/Action 合并写入 Session | ✅ |
| Session 会话 | 多会话隔离；全量历史在 Session；引擎无状态；支持 `clear()` | ✅ |
| Working Memory | 每轮从 Session 取最近 N 条（默认 **20**），含孤儿 ToolResult 防线 | ✅ |
| Compactor 压缩 | 超水位线后：远期掩码 + 近期头尾截断；只改发给模型的临时上下文 | ✅ |
| Recovery 自愈 | 工具失败时按报错特征注入「系统救援指南」 | ✅ |
| Reminder 死循环打断 | 同一工具指纹连续失败 ≥3 次，注入 `Role.USER` 强力 Reminder | ✅ |
| Plan Mode | 进度外部化到 `PLAN.md` / `TODO.md`，支持断点续传 | ✅ |
| Approval Gate | System Prompt 要求原样调工具，安全交给 Middleware + 人类 | ✅ |
| Registry Middleware | `registry.use(mw)`：执行前拦截；可挂人工审批 | ✅ |
| 飞书人工审批 | 高危命令发交互卡片（通过/拒绝）；`card.action.trigger` + 文本口令兜底 | ✅ |
| 动态 System Prompt | 内核 +（可选 Approval Gate / Plan）+ `AGENTS.md` + Skills | ✅ |
| Agent Skills | 扫描 `{work_dir}/.claw/skills/**/SKILL.md` | ✅ |
| 工具集 | `read_file` / `write_file` / `edit_file` / `bash` / `spawn_subagent` | ✅ |
| Subagent 探路者 | 主 Agent 委派只读子循环；摘要回流，防污染主上下文 | ✅ |
| 并发策略 | 只读工具可并行；含写操作或 bash 时整批串行 | ✅ |
| Provider 清洗 | OpenAI 消息链 sanitize，降低智谱 `1214` | ✅ |
| Reporter | 统一输出契约；终端 / 飞书实现 | ✅ |
| CLI / OOM 演示 | `main.py`：压缩 / 死循环干预等 | ✅ |
| Session 隔离演示 | `main1.py`：记忆截断 + 双会话隔离 | ✅ |
| Plan Mode 演示 | `main2.py`：`--prompt` 长程任务 | ✅ |
| Recovery 演示 | `main3.py`：陷阱指令诱发 `edit_file` 纠偏 | ✅ |
| 飞书审批演示 | `main4.py`：长连接 + Middleware 审批卡片 | ✅ |
| Subagent 演示 | `main5.py`：委派探路者找密码 → 主 Agent 写 `answer.txt` | ✅ |
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
        │  Compactor + Recovery + Reminder；可选 approval_gate
        │
        ├─ PromptComposer(work_dir, plan_mode, approval_gate).build()
        ├─ session.get_working_memory(20)
        ├─ compacted = Compactor.compact([System]+WM)
        ├─ Thinking / Action → 合并 Assistant 写入 Session
        ├─ Registry.Execute
        │     ├─ Middleware 链（如高危 → ApprovalManager 阻塞等待）
        │     ├─ 工具执行（含 spawn_subagent → engine.run_sub 只读子循环）
        │     └─ IsError → RecoveryManager.analyze_and_inject
        ├─ session.append(Observation…)
        └─ ReminderInjector.check_and_inject → 必要时再 append User Reminder
                │
         TerminalReporter / FeishuReporter（文本 / 交互卡片）
                │
         Provider（sanitize）+ Tools Registry（bound to work_dir）
```

核心约定：

- **Session**：绑定 `work_dir` 与完整对话历史；`work_dir` 跟随会话。
- **Working Memory**：每轮只把「System + 最近 N 条」取出；全量仍在 Session。
- **Compactor**：只压缩「本轮发给模型」的临时列表；Session 仍是全量原文。
- **Recovery**：工具 `IsError` 时增强 stderr，引导改主意。
- **Reminder**：同一调用指纹连续失败 ≥3 次，追加 User 级打断消息。
- **Middleware**：工具真正执行前可拦截；高危命令可挂起等待人类审批。
- **Approval Gate**：提示模型勿口头拒答，安全由中间件 + 人类把关。
- **Subagent**：主 Agent 通过 `spawn_subagent` 委派；子循环不写主 Session，只回摘要。
- **Plan Mode**：强制维护 `PLAN.md` / `TODO.md`。
- **调用约定**：先 `session.append(用户输入)`，再 `engine.run(session, reporter)`。
- **工具边界**：只能访问 `session.work_dir`（默认 `./workspace`）。

---

## 目录结构

```text
tiny-claw/
├── cmd/claw/
│   ├── main.py                   # CLI 演示（Compactor / Reminder 等）
│   ├── main1.py                  # Session / Working Memory 隔离
│   ├── main2.py                  # Plan Mode：--prompt
│   ├── main3.py                  # Recovery：edit_file 自愈
│   ├── main4.py                  # 飞书长连接 + 高危审批卡片
│   └── main5.py                  # Subagent：委派探路者 + 主写 answer.txt
├── AGENTS.md
├── .claw/skills/
│   └── git-workflow/SKILL.md
├── workspace/                    # 默认工作区（.gitignore）
│   ├── mock_log.txt
│   ├── auth.py                   # Recovery 演示目标
│   ├── config.txt                # Subagent 演示：待查找的「核心密码」（需自备）
│   ├── answer.txt                # Subagent 演示：主 Agent 写入结果
│   ├── PLAN.md / TODO.md
│   └── …
├── mock_log.txt
├── tmp/
│   ├── project_front/
│   └── project_back/
├── internal/
│   ├── engine/
│   │   ├── loop.py               # run + run_sub；Compactor / Recovery / Reminder
│   │   ├── session.py
│   │   ├── reminder.py           # 死循环打断
│   │   ├── reporter.py
│   │   └── terminal_reporter.py
│   ├── context/
│   │   ├── composer.py           # Plan Mode + Approval Gate
│   │   ├── skill.py
│   │   ├── compactor.py
│   │   └── recovery.py
│   ├── feishu/
│   │   ├── bot.py                # 长连接事件 + card.action.trigger
│   │   └── approval.py           # ApprovalManager + 审批卡片 JSON
│   ├── provider/
│   │   └── openai.py
│   ├── schema/
│   ├── tools/
│   │   ├── registry.py           # Middleware 挂载与执行前拦截
│   │   └── subagent.py           # spawn_subagent → AgentRunner.run_sub
│   └── memory/
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

# 飞书（main4 / 飞书 Bot 需要）
FEISHU_APP_ID=cli_xxx
FEISHU_APP_SECRET=xxx
FEISHU_ENCRYPT_KEY=xxx          # 仅 HTTP 回调需要；长连接可空
FEISHU_VERIFY_TOKEN=xxx
```

---

## 快速启动

### 1. CLI / Compactor（OOM）演示

```bash
cd tiny-claw
cp -f mock_log.txt workspace/   # 若已在 workspace/ 可跳过
python cmd/claw/main.py
```

当前 `main.py` 还可切换为死循环干预等演示任务（见源码内 prompt）。

让 `AGENTS.md` / Skills 生效时放到 **work_dir**：

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
| Session | `task_web_server_01` |
| 引擎 | `plan_mode=True` |
| 外部化 | `PLAN.md` / `TODO.md` 断点续传 |

### 4. Recovery 自愈演示

```bash
python cmd/claw/main3.py
```

| 项 | 说明 |
| --- | --- |
| Session | `test_recovery_001` |
| 陷阱 | 直接 `edit_file` 改 `auth.py`，诱发 `old_text` 不匹配 |
| 预期 | Recovery 注入指南 → 先 `read_file` 再重试 |

### 5. 飞书高危审批演示（长连接）

```bash
python cmd/claw/main4.py
```

| 项 | 说明 |
| --- | --- |
| Session | `test_command_intercept_001`（启动时 `clear()`） |
| 引擎 | `approval_gate=True` |
| Middleware | `is_dangerous_command` 命中后 `wait_for_approval` |
| 交互 | 飞书发「通过 / 拒绝」卡片；也可用 `approve <id>` / `reject <id>` |

开放平台请确认：

1. 事件：长连接 + `im.message.receive_v1`
2. 回调：长连接接收回调 + **卡片回传交互** `card.action.trigger`
3. 权限含发消息；改配置后**发版**

### 6. Subagent 多智能体协同演示

```bash
# 请先在 workspace 某路径下放好 config.txt（内含「核心密码」）
python cmd/claw/main5.py
```

| 项 | 说明 |
| --- | --- |
| Session | `test_subagent_001` |
| 只读沙箱 | 子智能体仅 `read_file` + `bash` |
| 主线工具 | 四大工具 + `spawn_subagent`（注入 `engine` + 只读 Registry） |
| 任务 | 委派子智能体找 `config.txt` → 主 Agent `write_file` 写 `answer.txt` |
| 预期 | 终端可见 `[Subagent]` 工具轨迹；主上下文只收到探索报告 |

---

## Compactor 行为摘要

默认：`Compactor(max_chars=20000, retain_last_msgs=6)`。

| 区域 | 消息类型 | 超限后处理 |
| --- | --- | --- |
| 任意 | System | 原样保留 |
| 远期（保护区外） | ToolResult（内容 >200） | 全量掩码 |
| 近期（保护区内） | ToolResult（内容 >1000） | 头 500 + 尾 500 |
| 远期 | Assistant 长文（>200） | 折叠占位 |
| 任意 | `tool_calls` | **永不修改** |

---

## Recovery 行为摘要

`RecoveryManager.analyze_and_inject(tool_name, raw_error)`：匹配特征后追加 `[系统救援指南]`。

| 工具 | 典型触发 | 救援方向 |
| --- | --- | --- |
| `edit_file` | 未找到 `old_text` / 多处匹配 | 先 `read_file` 或加上下文 |
| `read_file` / `write_file` | 路径不存在 / 权限 | `ls` / `find` |
| `bash` | 命令不存在 / 超时 / 语法错 | 换命令、后台化、查转义 |

---

## Reminder 行为摘要

`ReminderInjector.check_and_inject(tool_call, tool_result)`：

- 成功：清空全部失败计数
- 失败：按 `md5(tool_name + args)` 累加；**≥3** 次注入 User Reminder，要求改策略或向人求助
- 引擎在 Observation 写入 Session 后调用；简化策略取本轮**第一个**工具结果分析

---

## Middleware / 人工审批摘要

`Registry.use(mw)` → `Execute` 前依次调用；`allowed=False` 时返回 `is_error=True` 及原因。

`ApprovalManager`（`global_approval_mgr`）：

1. `wait_for_approval`：登记 TaskID 队列 → 发交互卡片（独立线程发送，避免堵死事件循环）→ `Queue.get()` 阻塞
2. `resolve_approval`：卡片按钮或文本口令唤醒
3. `is_dangerous_command`：对 `bash` 匹配 `rm -r` / `sudo` / `drop` / 重定向覆盖 `.py` 等

`approval_gate=True` 时 System Prompt 要求模型**原样调工具**，勿口头拒答（否则中间件进不去）。

---

## Plan Mode 行为摘要

`PromptComposer(..., plan_mode=True)`：

1. Bootstrapping：嗅探 / 创建或续读 `PLAN.md`、`TODO.md`
2. 单步打勾：完成一步立即 `- [x]`
3. 迷失时重读 `TODO.md`

---

## Subagent 行为摘要

主 Agent 把深度探索外包出去，避免大段文件内容污染主 Session。

```text
engine.run（主线）
  └─ spawn_subagent
        └─ SubagentTool.execute(task_prompt)
              └─ AgentEngine.run_sub（临时上下文，最多 10 轮）
                    ├─ 仅用只读 Registry（read_file / bash）
                    ├─ 强制关闭慢思考；Reporter 带 [Subagent] 前缀
                    └─ 不再调工具时 → 纯文本摘要返回主线
```

要点：

- `AgentRunner` Protocol 打破 `tools` ↔ `engine` 循环依赖；`main5` 把主引擎注入为 runner
- 子循环**不依赖**外部 Session，打完即弃；失败时把错误文案当 Observation 交回主 Agent
- 成功返回形如 `【子智能体探索报告】:\n...`

---

## 飞书模式

- **`main4.py`**：推荐入口；WebSocket 长连接 + 审批卡片
- **`main.py`**：飞书 HTTP/WS 代码仍注释保留，可按需恢复
- `FeishuBot`：可绑定固定 Session；处理文本审批口令与 `card.action.trigger`

### 常见问题

| 现象 | 可能原因 |
| --- | --- |
| 找不到 `mock_log.txt` | 文件不在 `workspace/` |
| 飞书无消息日志 | 未选长连接 / 未订阅事件 / 未发版 |
| 卡片按钮无反应 | 未添加 `card.action.trigger` 回调或未发版 |
| 收得到、发不出，`21001` | 飞书取 token 策略拦截 |
| 高危指令只有口头拒绝、无审批卡片 | 模型未调工具；确认 `approval_gate=True` 并重启清历史 |
| 审批日志有、飞书无卡片 | 旧版在事件循环里 `create_task` 后阻塞；现已独立线程发送 |
| `429` / 超时 | 智谱限流或网络 |
| 智谱 `1214` | 已合并 Thinking/Action + sanitize |
| Turn 2「不记得密钥」 | Working Memory 截断（`main1` 预期） |
| `[Compactor] 内存告警` | 超水位线（预期） |
| `注入救援指南` | Recovery 触发（`main3` 预期） |
| Reminder 死循环干预 | 同指纹失败 ≥3（`main.py` 死循环演示预期） |
| 子智能体找不到密码 | `workspace` 下缺少 `config.txt`，或未走 `spawn_subagent` |
| 子智能体超 10 轮被召回 | 任务指令过宽；请主 Agent 给出更明确的 `task_prompt` |
| `git push` 连不上 github.com | 本机网络/代理问题，与仓库无关 |

---

## 核心模块说明

### Session（`internal/engine/session.py`）

| API | 作用 |
| --- | --- |
| `append(*msgs)` | 追加全量历史 |
| `clear()` | 清空历史 |
| `get_working_memory(limit)` | 最近 N 条；丢弃孤儿 ToolResult |
| `global_session_mgr` | 模块级单例 |

### AgentEngine（`internal/engine/loop.py`）

- 构造：`enable_thinking` / `plan_mode` / `approval_gate` / `compactor` / `recovery` / `injector`
- 默认：`Compactor(20000, 6)`、`RecoveryManager()`、`ReminderInjector()`
- `run`：compact → Thinking/Action 合并 → 工具（Recovery）→ Reminder
- `run_sub`：一次性只读子循环（供 `spawn_subagent`）；最多 10 轮；不写主 Session

### ReminderInjector（`internal/engine/reminder.py`）

见上文「Reminder 行为摘要」。

### Registry（`internal/tools/registry.py`）

- `register` / `use` / `get_available_tools` / `execute`
- Middleware 拦截必须 `is_error=True`，强制模型阅读拒绝理由

### SubagentTool（`internal/tools/subagent.py`）

- 工具名：`spawn_subagent`；参数：`task_prompt`
- 注入 `AgentRunner`（通常即主引擎）+ 只读 Registry + 可选 Reporter
- 见上文「Subagent 行为摘要」

### ApprovalManager（`internal/feishu/approval.py`）

见上文「Middleware / 人工审批摘要」。`build_approval_card` 生成通过/拒绝按钮卡片。

### PromptComposer / SkillLoader

| 组件 | 作用 |
| --- | --- |
| `PromptComposer` | 内核 → Approval Gate → Plan Mode → `AGENTS.md` → Skills |
| `SkillLoader` | `{work_dir}/.claw/skills/**/SKILL.md` |

### Provider / Reporter / Tools

- Provider：`_sanitize_openai_messages`
- Reporter：`TerminalReporter` / `FeishuReporter`（含 `send_approval_card`）
- Tools：四大基础工具 + 可选 `spawn_subagent`，均受 `work_dir` 约束

---

## 变更记录

> 新版本请在表格**顶部**追加一行，保持时间倒序。

| 日期 | 版本 / 标签 | 摘要 |
| --- | --- | --- |
| 2026-10-28 | — | Subagent：`spawn_subagent` + `AgentEngine.run_sub` 只读探路者；`main5.py` 多智能体协同演示 |
| 2026-10-27 | — | Reminder 死循环打断；Registry Middleware；飞书审批卡片 + `card.action.trigger`；`approval_gate`；`main4.py` 长连接演示 |
| 2026-10-27 | — | Recovery；`main3.py`；Plan Mode + `main2.py`；Compactor 默认 20000；Thinking/Action 合并与 sanitize 防 1214 |
| 2026-10-24 | — | Compactor；WM=20；OOM 与 `workspace` 边界 |
| 2026-10-24 | — | Session / Working Memory；引擎无状态 |
| 2026-10-22 | — | Composer + Skills、CLI/`workspace`、TerminalReporter |
| 2026-10-21 | — | 初版：ReAct、工具、Reporter、飞书 |
| _YYYY-MM-DD_ | _vX.Y_ | _在此填写本次迭代说明_ |

### 迭代备注（自由文本）

```text
# 2026-10-28（Subagent）
# - subagent.py：spawn_subagent；AgentRunner Protocol 打破循环依赖
# - loop.py：run_sub 只读子循环（最多 10 轮）；Reporter 前缀 [Subagent]
# - main5.py：只读沙箱 + 主线全工具；委派找 config.txt → 写 answer.txt

# 2026-10-27（Reminder / Middleware / 飞书审批）
# - reminder.py：同指纹连续失败 ≥3 注入 User Reminder
# - registry.py：MiddlewareFunc + use()；Execute 前拦截
# - approval.py：ApprovalManager、交互卡片 JSON、独立线程发卡片
# - bot.py：card.action.trigger；approve/reject 文本兜底；send_approval_card
# - composer/loop：approval_gate；main4 长连接 + 审批演示
# - session.clear()：演示入口清历史，避免口头拒答污染

# 2026-10-27（Recovery / Plan Mode / 1214）
# - recovery.py；plan_mode；Compactor 20000
# - Thinking/Action 合并；openai sanitize
# - main2 / main3

# 2026-10-24（Compactor / Session）
# - compactor.py；session.py；main / main1

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
- [x] Recovery：工具失败特征救援
- [x] Plan Mode：`PLAN.md` / `TODO.md`
- [x] Reminder：连续失败死循环打断
- [x] Middleware + 飞书人工审批卡片
- [x] Subagent：只读探路者 + 摘要回流
- [ ] `internal/memory` 跨会话长期记忆
- [ ] Provider 429/超时重试
- [ ] 更多 Reporter
- [ ] ……（在此追加）

---

## 开发约定（简要）

- 注释中文，源码 UTF-8
- 改一处功能时尽量不破坏已正确能力
- 密钥只放 `.env`
- 演示用文件放进对应 `work_dir`（默认 `workspace/`）

---

## License

见 [LICENSE](./LICENSE)。
