# tiny-claw

微型编码 Agent（Python 实现），灵感来自 OpenClaw：在受限工作区内调用大模型 + 工具，完成读文件、改代码、执行命令等任务，并通过 **Reporter** 把过程输出到终端或飞书。

> 本项目仍在持续迭代。下文「变更记录」「路线图」会随版本更新，欢迎在对应章节补充说明。

---

## 功能概览

| 能力 | 说明 | 状态 |
| --- | --- | --- |
| ReAct 主循环 | Thinking（可选）→ Action → Observation；Thinking/Action 合并写入 Session | ✅ |
| Session 会话 | 多会话隔离；全量历史在 Session；引擎无状态；支持 `clear()` | ✅ |
| Working Memory | 按**完整回合**裁剪（默认约 **20** 条）；保证窗口以 User 开头，防智谱 `1214` | ✅ |
| Compactor 压缩 | 超水位线后：远期掩码 + 近期头尾截断；只改发给模型的临时上下文 | ✅ |
| Recovery 自愈 | 工具失败时按报错特征注入「系统救援指南」 | ✅ |
| Reminder 死循环打断 | 同指纹连续**失败**或连续**成功** ≥3 次，注入 `Role.USER` 强力 Reminder | ✅ |
| CostTracker 账单 | Provider 装饰器提取 Usage，累加 Session Token / 费用 | ✅ |
| Tracing 链路追踪 | `contextvars` 挂载 Span；`Agent.Run` → `Turn-N` → `LLM.*` / `Tool.Execute`；导出 JSON | ✅ |
| Harness Benchmark | 隔离沙箱 + Setup/Task/Validate；`CostTracker` 记账；汇总通过率与成本 | ✅ |
| Plan Mode | 进度外部化到 `PLAN.md` / `TODO.md`，支持断点续传 | ✅ |
| Approval Gate | System Prompt 要求原样调工具，安全交给 Middleware + 人类 | ✅ |
| Registry Middleware | `registry.use(mw)`：执行前拦截；可挂人工审批 | ✅ |
| 飞书人工审批 | 高危命令发交互卡片（通过/拒绝）；`card.action.trigger` + 文本口令兜底 | ✅ |
| 动态 System Prompt | 内核 +（可选 Approval Gate / Plan）+ `AGENTS.md` + Skills | ✅ |
| Agent Skills | 扫描 `{work_dir}/.claw/skills/**/SKILL.md` | ✅ |
| 工具集 | `read_file` / `write_file` / `edit_file` / `bash` / `spawn_subagent` | ✅ |
| Subagent 探路者 | 主 Agent 委派只读子循环；摘要回流，防污染主上下文 | ✅ |
| 并发策略 | 只读工具可并行；含写操作或 bash 时整批串行 | ✅ |
| Provider 清洗 | 消息链 sanitize + system 后强制 User 首条，降低智谱 `1214` | ✅ |
| Reporter | 统一输出契约；终端 / 飞书实现 | ✅ |
| 统一演示入口 | `cmd/claw/main.py`：CLI / Session / Plan / Recovery / 飞书 / Subagent / 账单 / Tracing（注释互斥切换） | ✅ |
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
        │  Compactor + Recovery + Reminder + Tracing；可选 approval_gate
        │
        ├─ start_span("Agent.Run") … finally ExportTraceToFile
        ├─ PromptComposer(work_dir, plan_mode, approval_gate).build()
        ├─ session.get_working_memory(20)   # 按完整回合裁剪；必要时合成 User
        ├─ compacted = Compactor.compact([System]+WM)
        ├─ Turn-N：LLM.Thinking? / LLM.Action → 合并 Assistant 写入 Session
        ├─ Registry.Execute → Tool.Execute Span
        │     ├─ Middleware 链（如高危 → ApprovalManager 阻塞等待）
        │     ├─ 工具执行（含 spawn_subagent → engine.run_sub 只读子循环）
        │     └─ IsError → RecoveryManager.analyze_and_inject
        ├─ session.append(Observation…)
        └─ ReminderInjector.check_and_inject → 失败/成功死循环时 append User Reminder
                │
         TerminalReporter / FeishuReporter（文本 / 交互卡片）
                │
         Provider（sanitize + User 首条）+ Tools Registry（bound to work_dir）
                │
         {work_dir}/.claw/traces/trace_*.json
```

核心约定：

- **Session**：绑定 `work_dir` 与完整对话历史；`work_dir` 跟随会话；可 `record_usage` 累加账单。
- **Working Memory**：按「完整回合」从尾部裁剪（非裸条数乱切）；窗口须以普通 User 开头，否则补紧邻 User 或插入合成 User。
- **Compactor**：只压缩「本轮发给模型」的临时列表；Session 仍是全量原文。
- **Recovery**：工具 `IsError` 时增强 stderr，引导改主意。
- **Reminder**：同指纹连续失败 **或** 连续成功 ≥3 次，追加 User 级打断消息。
- **CostTracker**：装饰 LLMProvider，按模型单价把 Usage 写入 Session。
- **Tracing**：用 `contextvars` 模拟 Go `context.Context` 挂载 Span；父子自动绑定；结束导出 JSON。
- **Benchmark**：每用例独立 `workspace/{id}_{ts}/` 沙箱；Setup → Agent → Validate（exit 0 算过）；账单记在独立 Session。
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
├── cmd/
│   ├── claw/
│   │   └── main.py               # 统一演示入口（注释切换各 demo_*）
│   └── bench/
│       └── main.py               # Harness Benchmark 跑分入口
├── AGENTS.md
├── .claw/skills/
│   └── git-workflow/SKILL.md
├── workspace/                    # 默认工作区（.gitignore）
│   ├── mock_log.txt
│   ├── auth.py                   # Recovery 演示目标
│   ├── config.txt                # Subagent 演示：待查找的「核心密码」（需自备）
│   ├── answer.txt                # Subagent 演示：主 Agent 写入结果
│   ├── PLAN.md / TODO.md
│   ├── trace_test.md             # Tracing 演示写入产物
│   ├── .claw/traces/             # Trace JSON 导出目录
│   ├── test_001_edit_*/          # Benchmark 隔离沙箱（按用例生成）
│   ├── test_002_code_gen_*/
│   └── …
├── mock_log.txt
├── tmp/
│   ├── project_front/
│   └── project_back/
├── internal/
│   ├── engine/
│   │   ├── loop.py               # run + run_sub；Compactor / Recovery / Reminder / Tracing
│   │   ├── session.py            # WM 回合裁剪 + record_usage
│   │   ├── reminder.py           # 失败/成功死循环打断
│   │   ├── reporter.py
│   │   └── terminal_reporter.py
│   ├── context/
│   │   ├── composer.py           # Plan Mode + Approval Gate
│   │   ├── skill.py
│   │   ├── compactor.py
│   │   └── recovery.py
│   ├── eval/
│   │   └── benchmark.py          # TestCase / TestResult / BenchmarkRunner
│   ├── observability/
│   │   ├── tracker.py            # CostTracker 账单装饰器
│   │   └── trace.py              # Span + contextvars + 导出 JSON
│   ├── feishu/
│   │   ├── bot.py                # 长连接事件 + card.action.trigger
│   │   └── approval.py           # ApprovalManager + 审批卡片 JSON
│   ├── provider/
│   │   └── openai.py             # Usage 提取 + sanitize / User 首条
│   ├── schema/                   # Message.usage / Usage
│   ├── tools/
│   │   ├── registry.py           # Middleware + Tool.Execute Span
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

# 飞书（demo_feishu_approval / 飞书 Bot 需要）
FEISHU_APP_ID=cli_xxx
FEISHU_APP_SECRET=xxx
FEISHU_ENCRYPT_KEY=xxx          # 仅 HTTP 回调需要；长连接可空
FEISHU_VERIFY_TOKEN=xxx
```

---

## 快速启动

所有演示已合并到 **`cmd/claw/main.py`**。在 `main()` 里**一次只取消注释一个** `demo_*`（互斥）。

让 `AGENTS.md` / Skills 生效时放到 **work_dir**：

```bash
cp AGENTS.md workspace/
mkdir -p workspace/.claw/skills && cp -R .claw/skills/* workspace/.claw/skills/
```

### 1. CLI / Compactor（OOM） / Reminder

启用 `demo_cli()`：

```bash
cd tiny-claw
cp -f mock_log.txt workspace/   # 若已在 workspace/ 可跳过
python cmd/claw/main.py
```

源码内 prompt 可切换压缩 / 死循环干预等任务。

### 2. Session / 记忆截断演示

启用 `demo_session_isolation()`：

```bash
python cmd/claw/main.py
```

| 会话 | 目的 |
| --- | --- |
| Session A（`tmp/project_front`） | 读密钥 → 刷闲聊挤出 Working Memory → 再追问 |
| Session B（`tmp/project_back`） | 验证跨会话隔离 |

### 3. Plan Mode 演示（当前默认常开）

启用 `demo_plan_mode`（需 `-p`）：

```bash
python cmd/claw/main.py -p "在 workspace 里搭一个简单的静态页服务"
```

| 项 | 说明 |
| --- | --- |
| Session | `task_web_server_001` |
| 引擎 | `plan_mode=True` |
| 外部化 | `PLAN.md` / `TODO.md` 断点续传 |

### 4. Recovery 自愈演示

启用 `demo_recovery()`：

```bash
python cmd/claw/main.py
```

| 项 | 说明 |
| --- | --- |
| Session | `test_recovery_001` |
| 陷阱 | 直接 `edit_file` 改 `auth.py`，诱发 `old_text` 不匹配 |
| 预期 | Recovery 注入指南 → 先 `read_file` 再重试 |

### 5. 飞书高危审批演示（长连接）

启用 `demo_feishu_approval()`（**不要**包 `asyncio.run`）：

```bash
python cmd/claw/main.py
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

启用 `demo_subagent()`：

```bash
# 请先在 workspace 某路径下放好 config.txt（内含「核心密码」）
python cmd/claw/main.py
```

| 项 | 说明 |
| --- | --- |
| Session | `test_subagent_001` |
| 只读沙箱 | 子智能体仅 `read_file` + `bash` |
| 主线工具 | 四大工具 + `spawn_subagent`（注入 `engine` + 只读 Registry） |
| 任务 | 委派子智能体找 `config.txt` → 主 Agent `write_file` 写 `answer.txt` |
| 预期 | 终端可见 `[Subagent]` 工具轨迹；主上下文只收到探索报告 |

### 7. CostTracker 账单演示

启用 `demo_observability()`：

```bash
python cmd/claw/main.py
```

| 项 | 说明 |
| --- | --- |
| 装饰 | `CostTracker(provider, model, session)` |
| 预期 | 跑完打印 Session 累计 Input/Output Tokens 与 CNY 费用 |

### 8. Tracing 链路追踪演示

启用 `demo_trace()`：

```bash
python cmd/claw/main.py
```

| 项 | 说明 |
| --- | --- |
| Session | `test_trace_001` |
| 工具 | 仅 `bash` + `write_file` |
| 任务 | 一轮内分别调用 sleep 检查与写入 `trace_test.md` |
| 预期 | `{work_dir}/.claw/traces/trace_*.json`；树形含 `Agent.Run` / `Turn-N` / `LLM.Action` / `Tool.Execute` |

> 引擎对含 `bash` / 写操作会整批串行；Trace 仍会记录多个平级（或顺序）的 `Tool.Execute`。

### 9. Harness Benchmark 跑分

独立入口（不走 `cmd/claw/main.py` 注释切换）：

```bash
python cmd/bench/main.py
```

| 用例 | 靶机 Setup | 任务 | 判卷 |
| --- | --- | --- | --- |
| `test_001_edit` | 写出 `config.json`（version=v1.0.0） | `edit_file` 改为 v2.0.0 | `grep` 检查版本号 |
| `test_002_code_gen` | 写出 `mathutil.py`（`multiply`） | 编写 `test_mathutil.py`（unittest） | `python -m unittest …` |

| 项 | 说明 |
| --- | --- |
| 模型 | 默认 `glm-4.6v`（见 `cmd/bench/main.py` 内 `_BENCH_MODEL`） |
| 沙箱 | `workspace/{case_id}_{unix_ts}/`，用例间物理隔离 |
| 账单 | 每用例独立 `Session` + `CostTracker` |
| 报告 | 终端打印通过数 / 成功率 / 累计成本（CNY） |

---

## Working Memory 行为摘要

`Session.get_working_memory(limit)`（默认 limit=20）：

1. 将历史打包为**完整回合**：普通 User / 纯文本 Assistant / `Assistant(tool_calls)+匹配的 ToolResult`
2. 从尾部按回合累加，避免按裸条数切开 tool 链
3. 窗口不以普通 User 开头时：优先补**紧邻**的上一条 User；仍缺则插入合成 User（防智谱 `1214`）
4. 返回深拷贝；Session 全量历史不变

> 裸条数截断曾导致 `system` 后直接跟 `assistant`，易触发智谱 `1214`；回合裁剪 + User 首条即针对此。

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

| 结果 | 计数行为 | 打断条件 |
| --- | --- | --- |
| 成功 | 清空失败计数；同指纹成功次数 +1（换指纹则从 1 重计） | 连续成功 **≥3** → 注入「停止无意义重复 / 推进或收尾」 |
| 失败 | 清空成功计数；同指纹失败次数 +1 | 连续失败 **≥3** → 注入「改策略或向人求助」（原逻辑） |

- 指纹：`md5(tool_name + args)`
- 引擎在 Observation 写入 Session 后调用；简化策略取本轮**第一个**工具结果分析

> 仅防「失败死循环」时，同参反复成功（如反复 `curl` 同一 URL）不会触发；现已覆盖。

---

## CostTracker 行为摘要

`CostTracker(next_provider, model_name, session)`（`internal/observability/tracker.py`）：

1. 装饰 `LLMProvider.generate`，透传调用
2. 从返回 `Message.usage` 读取 prompt / completion tokens
3. 按内置单价表（如 `glm-4.5-air` / `glm-4.6v`）估算 CNY，调用 `session.record_usage`

---

## Tracing 行为摘要

对应 Go 的 `context.Context` 挂 Span：Python 用 `contextvars.ContextVar` 透传当前 Span。

| API | 作用 |
| --- | --- |
| `start_span(name)` | 创建 Span，挂到当前父节点，写入 ContextVar |
| `span.end_span()` | 计算耗时，并将 ContextVar 复位为父 Span |
| `span.add_attribute(k, v)` | 记录元数据 |
| `export_trace_to_file(root, work_dir, session_id)` | 写入 `{work_dir}/.claw/traces/trace_{session}_{ts}.json` |

引擎埋点（`loop.run`）：

```text
Agent.Run
├── Turn-1
│   ├── LLM.Thinking   (可选)
│   ├── LLM.Action
│   └── Tool.Execute   (Registry.execute；含 tool_name / arguments / output_preview)
├── Turn-2
│   └── …
└── finally：End + Export
```

- `Turn-N` 带 `context_message_count`
- `Tool.Execute`：Middleware 拦截时记 `intercepted` / `reject_reason`；失败记 `error`；成功记 `output_preview`（截断 100 字符）
- `run` 退出（含异常）时仍导出 Trace

---

## Benchmark 行为摘要

`BenchmarkRunner`（`internal/eval/benchmark.py`）：

1. `run_suite(testcases)`：串行执行用例，汇总 `TestResult` 并打印终极报告
2. 单用例流程：`mkdir` 沙箱 → 可选 `setup_script` → `CostTracker` 包装引擎（四工具）→ `engine.run(session, reporter=None)` → `validate_script`（exit 0 通过）
3. `TestCase`：`id` / `name` / `task_prompt` / `validate_script` / 可选 `setup_script` / `max_turns`（字段已留，限轮尚未接入引擎）
4. `TestResult`：`passed` / `total_cost_cny` / `duration_ms` / `error_msg`

入口：`cmd/bench/main.py` 组装微型评测集并调用 `new_benchmark_runner(model).run_suite(...)`。

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

- `AgentRunner` Protocol 打破 `tools` ↔ `engine` 循环依赖；`demo_subagent` 把主引擎注入为 runner
- 子循环**不依赖**外部 Session，打完即弃；失败时把错误文案当 Observation 交回主 Agent
- 成功返回形如 `【子智能体探索报告】:\n...`

---

## 飞书模式

- 在 `main.py` 启用 `demo_feishu_approval()`：WebSocket 长连接 + 审批卡片
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
| 智谱 `1214` | Thinking/Action 合并 + WM 回合裁剪/User 首条 + sanitize |
| Turn 2「不记得密钥」 | Working Memory 截断（Session 隔离演示预期） |
| `[Compactor] 内存告警` | 超水位线（预期） |
| `注入救援指南` | Recovery 触发（Recovery 演示预期） |
| Reminder 死循环干预 | 同指纹失败或成功 ≥3（日志含「成功死循环干预」） |
| `.claw/traces` 无文件 | 未跑完 `engine.run`（未进 finally）；或 `work_dir` 不对 |
| Trace 里 Turn 互相嵌套 | 旧版未每轮 `end_span`；现已 `try/finally` 按轮结束 |
| Benchmark「靶机 Setup 失败」 | `setup_script` 在沙箱 cwd 执行失败；检查 bash / 引号 |
| Benchmark 验证脚本失败 | Agent 未改对文件，或 unittest/grep 断言不匹配；看沙箱目录产物 |
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
| `record_usage(...)` | 累加 Token / 费用 |
| `get_working_memory(limit)` | 按完整回合裁剪；保证 User 开头 |
| `global_session_mgr` | 模块级单例 |

### AgentEngine（`internal/engine/loop.py`）

- 构造：`enable_thinking` / `plan_mode` / `approval_gate` / `compactor` / `recovery` / `injector`
- 默认：`Compactor(20000, 6)`、`RecoveryManager()`、`ReminderInjector()`
- `run`：Root Span → WM → compact → Turn（Thinking/Action + 工具）→ Reminder；finally 导出 Trace
- `run_sub`：一次性只读子循环（供 `spawn_subagent`）；最多 10 轮；不写主 Session

### ReminderInjector（`internal/engine/reminder.py`）

见上文「Reminder 行为摘要」。

### CostTracker（`internal/observability/tracker.py`）

见上文「CostTracker 行为摘要」。

### Trace（`internal/observability/trace.py`）

见上文「Tracing 行为摘要」。

### Benchmark（`internal/eval/benchmark.py`）

见上文「Benchmark 行为摘要」。入口：`cmd/bench/main.py`。

### Registry（`internal/tools/registry.py`）

- `register` / `use` / `get_available_tools` / `execute`
- Middleware 拦截必须 `is_error=True`，强制模型阅读拒绝理由
- `execute` 内开启 `Tool.Execute` Span（`try/finally` 结束）

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

- Provider：提取 `Usage`；`_sanitize_openai_messages` + system 后强制 User 首条
- Reporter：`TerminalReporter` / `FeishuReporter`（含 `send_approval_card`）
- Tools：四大基础工具 + 可选 `spawn_subagent`，均受 `work_dir` 约束

---

## 变更记录

> 新版本请在表格**顶部**追加一行，保持时间倒序。

| 日期 | 版本 / 标签 | 摘要 |
| --- | --- | --- |
| 2026-10-29 | — | Harness Benchmark：`eval/benchmark.py` + `cmd/bench/main.py`；隔离沙箱 Setup/Validate；Python 用例 |
| 2026-10-29 | — | Tracing：`trace.py` + loop/registry 埋点；导出 `.claw/traces`；`demo_trace` |
| 2026-10-29 | — | WM 按回合裁剪 + User 首条防 1214；Reminder 覆盖「成功死循环」；CostTracker / Usage；演示入口合并为 `main.py` |
| 2026-10-28 | — | Subagent：`spawn_subagent` + `AgentEngine.run_sub` 只读探路者 |
| 2026-10-27 | — | Reminder 失败死循环；Registry Middleware；飞书审批卡片 + `card.action.trigger`；`approval_gate` |
| 2026-10-27 | — | Recovery；Plan Mode；Compactor 默认 20000；Thinking/Action 合并与 sanitize 防 1214 |
| 2026-10-24 | — | Compactor；WM=20；OOM 与 `workspace` 边界 |
| 2026-10-24 | — | Session / Working Memory；引擎无状态 |
| 2026-10-22 | — | Composer + Skills、CLI/`workspace`、TerminalReporter |
| 2026-10-21 | — | 初版：ReAct、工具、Reporter、飞书 |
| _YYYY-MM-DD_ | _vX.Y_ | _在此填写本次迭代说明_ |

### 迭代备注（自由文本）

```text
# 2026-10-29（Harness Benchmark）
# - eval/benchmark.py：TestCase / TestResult / BenchmarkRunner
# - 隔离沙箱 + Setup → Agent(+CostTracker) → Validate；汇总通过率/成本
# - cmd/bench/main.py：test_001_edit（edit_file）+ test_002_code_gen（unittest）

# 2026-10-29（Tracing）
# - observability/trace.py：Span + contextvars + export_trace_to_file
# - loop.run：Agent.Run / Turn-N / LLM.Thinking|Action；finally 导出
# - registry.execute：Tool.Execute（arguments / output_preview / 拦截与错误属性）
# - main.demo_trace：test_trace_001；bash + write_file

# 2026-10-29（WM / Reminder 成功循环 / 账单 / 统一入口）
# - session.get_working_memory：按完整回合裁剪；紧邻 User / 合成 User
# - openai：sanitize 后 system 后非 user 则插占位 user
# - reminder：同指纹连续成功 ≥3 亦注入打断（保留失败 ≥3）
# - Usage + Session.record_usage；CostTracker 装饰 Provider
# - cmd/claw/main.py：合并原 main～main6 为 demo_*，注释互斥切换

# 2026-10-28（Subagent）
# - subagent.py：spawn_subagent；AgentRunner Protocol
# - loop.py：run_sub 只读子循环（最多 10 轮）；Reporter 前缀 [Subagent]

# 2026-10-27（Reminder / Middleware / 飞书审批）
# - reminder.py：同指纹连续失败 ≥3 注入 User Reminder
# - registry Middleware；approval 交互卡片；approval_gate

# 2026-10-27（Recovery / Plan Mode / 1214）
# - recovery.py；plan_mode；Compactor 20000
# - Thinking/Action 合并；openai sanitize

# 2026-10-24（Compactor / Session）
# - compactor.py；session.py

# 2026-10-22
# - composer / skill；CLI + TerminalReporter

（在此继续追加）
```

---

## 路线图

- [ ] `requirements.txt` / `pyproject.toml`
- [ ] CLI / 飞书 / 演示入口用启动参数切换（当前靠 `main()` 注释互斥）
- [ ] Session 历史 JSONL 落盘
- [ ] Compactor / WM 阈值可配置或按 token 预算
- [x] Recovery：工具失败特征救援
- [x] Plan Mode：`PLAN.md` / `TODO.md`
- [x] Reminder：连续失败死循环打断
- [x] Reminder：连续成功死循环打断
- [x] WM 回合裁剪 + User 首条（防 1214）
- [x] CostTracker / Usage 账单
- [x] Tracing：contextvars Span + JSON 导出
- [x] Harness Benchmark：隔离沙箱 + Setup/Validate 跑分
- [x] Middleware + 飞书人工审批卡片
- [x] Subagent：只读探路者 + 摘要回流
- [ ] Benchmark `max_turns` 接入引擎限轮
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
