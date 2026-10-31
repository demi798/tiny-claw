# tiny-claw 代码规范整改 Todo

> 依据代码审查结论整理，优先级从高到低。  
> 完成一项请将 `[ ]` 改为 `[x]`。

---

## P0 — 高优先级

### 1. 补自动化测试

- [x] 新建 `tests/` 目录，接入 `pytest`
- [x] 为 `Session.get_working_memory`（回合裁剪 / User 首条）补单测
- [x] 为 `Compactor.compact`（水位线 / 掩码 / 不污染原 Message）补单测
- [x] 为 `ReminderInjector`（失败 ≥3 / 成功 ≥8）补单测
- [x] 为 Provider `_sanitize_openai_messages` / `_ensure_openai_user_first` 补单测
- [x] （可选）为 `edit_file.fuzzy_replace`、Recovery 特征匹配补单测

### 2. 包工程化：去掉 `sys.path` hack

- [x] 新增 `pyproject.toml`（或完善安装元数据），声明可安装包与依赖
- [x] 补齐缺失的 `__init__.py`：`internal/`、`internal/eval/`、`internal/observability/`
- [x] 支持 `pip install -e .` 后直接运行入口
- [x] 移除各入口中的 `sys.path.insert`：
  - [x] `cmd/claw/main.py`
  - [x] `cmd/claw/main_case.py`
  - [x] `cmd/agentops/main.py`
  - [x] `cmd/bench/main.py`
- [x] 确认 `mypy` 不再因 duplicate module / 包映射失败

### 3. 修复 mypy 类型错误

- [x] `internal/schema/message.py`：`to_dict` 中 `data` 标注为 `dict[str, Any]`
- [x] `internal/engine/loop.py`：并行工具结果解包前过滤 / 断言非 `None`
- [x] `internal/provider/openai.py`：`tool_call_id` 为 `None` 时勿加入 `set[str]`
- [x] 跑通 `mypy internal --explicit-package-bases`（或项目约定命令）零错误

### 4. 修正错误的包导出

- [x] 重写 `internal/tools/__init__.py`（去掉误写的「schema 包」docstring 与 schema re-export）
- [x] 按需导出 `BaseTool` / `Registry` / `new_registry`（或保持空 `__init__.py`）

---

## P1 — 中优先级

### 5. 收敛 Go 风格工厂 API

- [x] 评估并以类构造 / `@classmethod` 作为主 API：
  - [x] `new_registry` → `ToolRegistry()`
  - [x] `new_benchmark_runner` → `BenchmarkRunner(...)` / `from_model`
  - [x] `new_cost_tracker` → `CostTracker(...)`
  - [x] `new_reminder_injector` → `ReminderInjector()`
  - [x] `new_subagent_tool` → `SubagentTool(...)`
- [x] 若需兼容旧调用，保留薄包装别名并标注废弃（或文档说明）

### 6. 弱化模块级全局单例

- [x] `global_session_mgr`：改为入口构造并注入，或提供显式工厂
- [x] `global_approval_mgr`：同上，避免测试串扰
- [x] 更新 `cmd/claw`、`cmd/agentops`、`feishu/bot` 等调用方

### 7. 减少过度抽象

- [x] 评估去掉仅一实现的 `Registry` ABC，保留 `ToolRegistry`（或改为 `Protocol`）
- [x] 同步调整类型注解与导入

### 8. 入口 import 置顶（E402）

- [x] 打包完成后，将 `cmd/*` 中业务 import 移回文件顶部
- [x] 过渡期对「dotenv 之后的 import」统一 `# noqa: E402`（若仍需要）
- [x] `ruff check cmd` 无 E402（或仅保留已标注豁免）

### 9. 异常链不要随意掐断

- [x] `cmd/claw/main.py`：`raise SystemExit(1) from None` → 先 `logger.exception`，再 `from err`
- [x] `cmd/claw/main_case.py`：同上
- [x] `cmd/bench/main.py`：同上

### 10. 统一类型注解风格

- [x] `Optional[X]` 统一改为 `X | None`（如 `message.py`）
- [x] 裸 `-> dict` / 无注解 `to_dict` 改为 `-> dict[str, Any]`
- [x] `ToolResult.to_dict` 补全返回类型

---

## P2 — 低优先级

> 说明：文档无独立「P3」章节；若口头说的 P3 即本段低优先级收尾，按下列项完成。

### 11. 清理 lint 小问题

- [x] 删除 `message.py` 未使用的 `asdict` 导入（F401）（P1 已顺带清理）
- [x] 清理空白行尾随空格（W293），可用 `ruff check --fix`

### 12. 库代码日志规范

- [x] `internal/feishu/approval.py` 中调试用 `print` 改为 `logging`
- [x] 确认 CLI / `TerminalReporter` 的 `print` 保留（面向用户输出）

### 13. 固化静态检查配置

- [x] 在 `pyproject.toml`（或 `ruff.toml` / `mypy.ini`）写入统一规则
- [x] 设定 `target-version`、`line-length` 等
- [x] 对中文注释场景忽略 RUF001–003（全角标点误报）
- [x] 文档或脚本中写明推荐命令：`ruff check` / `mypy` / `pytest`

### 14. 精简 ABC 样板

- [x] `@abstractmethod` 方法体改为 `...`，去掉多余的 `raise NotImplementedError`
  - [x] `internal/tools/registry.py`
  - [x] `internal/provider/interface.py`
  - [x] 其他同类抽象基类（`internal/engine/reporter.py`）

---

## 建议落地顺序

1. P0-3（mypy）+ P0-4（`tools/__init__.py`）— 改动小、风险低  
2. P0-2（`pyproject.toml` / 包结构）— 解锁规范 import 与工具链  
3. P0-1（测试）— 为后续重构护航  
4. P1 各项按依赖注入 → API 收敛 → 入口/异常/注解统一  
5. P2 收尾 lint 与配置固化  

---

## 明确不在本次范围

- 不改动已正确的业务行为（ReAct / WM / Compactor / Reminder / 飞书审批等）
- 不为整改单独扩写功能说明 md（本文件除外）
- `workspace/test.py` 为演示用并发示例，不纳入产品测试套件除非另有说明
