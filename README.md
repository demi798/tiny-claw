# tiny-claw

微型编码 Agent（Python 实现），灵感来自 OpenClaw：在受限工作区内调用大模型 + 工具，完成读文件、改代码、执行命令等任务，并通过 **Reporter** 把过程输出到终端或飞书。

> 下文「变更记录」「路线图」会随版本更新，欢迎在对应章节补充说明。

---

## 功能概览

| 能力 | 说明 | 状态 |
| --- | --- | --- |
| ReAct 主循环 | Thinking（可选慢思考）→ Action → Observation | ✅ |
| 工具集 | `read_file` / `write_file` / `edit_file` / `bash` | ✅ |
| 并发策略 | 只读工具可并行；含写操作或 bash 时整批串行 | ✅ |
| Reporter | 统一输出契约；终端 / 飞书实现 | ✅ |
| 飞书接入 | WebSocket 长连接（默认）或 HTTP Webhook | ✅ |
| 上下文压缩 / Memory | 目录占位，待实现 | 🚧 |
| 多渠道（钉钉 / WebUI 等） | 可基于 Reporter 扩展 | 📋 |

---

## 架构一览

```text
用户（飞书 / CLI）
        │
        ▼
   FeishuBot / 入口
        │  Reporter（进度回传）
        ▼
   AgentEngine（ReAct 循环）
        │
   ┌────┴────┐
   ▼         ▼
 Provider   Tools Registry
 (智谱等)   read / write / edit / bash
```

核心约定：

- **work_dir**：工具操作的物理边界，默认取进程当前工作目录。
- **Reporter**：引擎不直接 `print` / 调飞书 API，只通过接口上报事件，便于切换展现层。
- **Provider**：`LLMProvider` 抽象，当前以 OpenAI 兼容接口对接智谱。

---

## 目录结构

```text
tiny-claw/
├── cmd/claw/main.py          # 入口：组装引擎 + 启动飞书服务
├── internal/
│   ├── engine/               # Agent 主循环与 Reporter
│   │   ├── loop.py
│   │   ├── reporter.py
│   │   └── terminal_reporter.py
│   ├── feishu/               # 飞书 Bot + FeishuReporter
│   │   └── bot.py
│   ├── provider/             # 大模型适配（OpenAI / Claude 占位）
│   ├── schema/               # Message / ToolCall 等协议
│   ├── tools/                # 工具实现与 Registry
│   ├── context/              # 【预留】上下文管理 / 压缩
│   └── memory/               # 【预留】长期记忆
├── .env                      # 本地密钥（勿提交）
└── README.md
```

---

## 环境要求

- Python 3.10+（推荐 3.11 / 3.13）
- 依赖（按需安装）：

```bash
pip install openai python-dotenv lark-oapi flask
```

> 后续可补充 `requirements.txt` / `pyproject.toml`，见路线图。

---

## 配置

在项目根目录创建 `.env`（可参考下列键名，**不要把真实密钥写进文档或提交到 Git**）：

```bash
# 大模型（智谱 OpenAI 兼容）
ZHIPU_API_KEY=your_zhipu_api_key

# 飞书应用
FEISHU_APP_ID=cli_xxx
FEISHU_APP_SECRET=xxx
FEISHU_ENCRYPT_KEY=xxx          # HTTP Webhook 模式需要
FEISHU_VERIFY_TOKEN=xxx         # HTTP Webhook 模式需要

# 可选：事件模式，默认 websocket
# FEISHU_EVENT_MODE=websocket   # 长连接（本地推荐）
# FEISHU_EVENT_MODE=http        # Webhook，需公网 URL
```

入口也会尝试加载 `internal/provider/.env`。

---

## 快速启动

```bash
cd tiny-claw
python cmd/claw/main.py
```

默认以 **WebSocket 长连接** 连接飞书，无需公网 IP。

HTTP 模式（需公网可达，监听 `:48080`）：

```bash
FEISHU_EVENT_MODE=http python cmd/claw/main.py
# 回调路径：POST /webhook/event
```

---

## 飞书开放平台配置清单

使用长连接时：

1. **事件配置** → 订阅方式选「使用长连接接收事件」并保存  
2. 添加事件：`接收消息`（`im.message.receive_v1`）  
3. 权限至少开通：  
   - `im:message.p2p_msg:readonly`（收单聊）  
   - `im:message:send_as_bot`（机器人发消息）  
4. **版本管理与发布** → 新建版本并**发布**（改配置后必须发版才生效）  
5. 开启应用「机器人」能力，用该机器人发起**纯文本**单聊测试  

常见问题简述：

| 现象 | 可能原因 |
| --- | --- |
| 服务无日志、飞书无回复 | 未选长连接 / 未订阅事件 / 未发版 |
| 收得到、发不出，`21001` | 飞书平台策略拦截取 token（账号/应用侧），非本仓库逻辑错误 |
| Thinking 失败，`429` / `1305` | 智谱模型限流，稍后重试或换模型 |

---

## 核心模块说明

### AgentEngine（`internal/engine/loop.py`）

- 可选 Phase 1 慢思考（不挂载工具）  
- Phase 2 行动（挂载工具定义）  
- 无 `tool_calls` 则结束；否则执行工具并把 observation 写回上下文  
- 通过 `Reporter` 上报：`on_thinking` / `on_message` / `on_tool_call` / `on_tool_result`  
- Reporter 展示侧工具输出默认截断（完整结果仍写入模型上下文）

### Reporter

- 抽象：`internal/engine/reporter.py`  
- 终端：`TerminalReporter`  
- 飞书：`FeishuReporter`（成功工具结果只回状态，避免刷屏）

### Tools

| 工具名 | 作用 |
| --- | --- |
| `read_file` | 读取工作区内文件（可并行） |
| `write_file` | 写入文件 |
| `edit_file` | 按片段替换编辑 |
| `bash` | 在工作区边界内执行命令 |

---

## 变更记录

> 新版本请在表格**顶部**追加一行，保持时间倒序。

| 日期 | 版本 / 标签 | 摘要 |
| --- | --- | --- |
| 2026-10-21 | — | 初版可用：ReAct 引擎、四件套工具、Reporter、飞书长连接 / HTTP 双模式入口 |
| _YYYY-MM-DD_ | _vX.Y_ | _在此填写本次迭代说明_ |
| _YYYY-MM-DD_ | _vX.Y_ | _在此填写本次迭代说明_ |

### 迭代备注（自由文本）

```text
# 示例：
# 2026-10-22
# - 调整默认模型为 xxx
# - 修复飞书发送失败时的二次异常淹没日志

（在此继续追加）
```

---

## 路线图

已规划、待落地（可勾选 / 改写）：

- [ ] 补充 `requirements.txt` 或 `pyproject.toml`，锁定依赖版本  
- [ ] `internal/context`：上下文窗口管理与压缩策略  
- [ ] `internal/memory`：跨会话记忆  
- [ ] CLI 单次任务模式与飞书服务模式并存（子命令或启动参数）  
- [ ] Provider 侧 429 重试 / 退避  
- [ ] 更多 Reporter（钉钉、WebUI 等）  
- [ ] ……（在此追加计划）

已完成可挪到「变更记录」，避免路线图无限膨胀。

---

## 开发约定（简要）

- 注释使用中文，源码 UTF-8  
- 改某一功能时尽量不破坏已正确运行的其他能力  
- 密钥只放 `.env`，勿写入 README 或提交仓库  

---

## License

见仓库根目录 [LICENSE](./LICENSE)。
