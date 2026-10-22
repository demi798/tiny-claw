"""终端（CLI）场景下的 Reporter 实现。"""

from __future__ import annotations

from internal.engine.reporter import Reporter

# 终端参数展示最大长度，过长则截断以保持清爽
_ARGS_DISPLAY_LIMIT = 150


class TerminalReporter(Reporter):
    """在终端直观打印 Agent 状态。"""

    async def on_thinking(self) -> None:
        """模型开始慢思考时提示。"""
        print("\n[🤔 思考中] 模型正在推理...", flush=True)

    async def on_tool_call(self, tool_name: str, args: str) -> None:
        """即将执行工具时打印调用信息（参数过长会截断）。"""
        print(f"[🛠️ 调用工具] {tool_name}", flush=True)
        display_args = args.replace("\n", "\\n").replace("\r", "\\r")
        if len(display_args) > _ARGS_DISPLAY_LIMIT:
            display_args = display_args[:_ARGS_DISPLAY_LIMIT] + "... (已截断)"
        print(f"   参数: {display_args}", flush=True)

    async def on_tool_result(
        self,
        tool_name: str,
        result: str,
        *,
        is_error: bool,
    ) -> None:
        """工具执行完毕后打印成功/失败状态。"""
        if is_error:
            print(f"[❌ 执行失败] {tool_name}", flush=True)
            if result:
                print(f"   错误: {result}", flush=True)
        else:
            print(f"[✅ 执行成功] {tool_name}", flush=True)

    async def on_message(self, content: str) -> None:
        """输出模型的阶段性总结或最终回复。"""
        if not content:
            return
        print(f"\n🤖 Agent 回复:\n{content}\n", flush=True)
