"""终端（CLI）场景下的 Reporter 实现。"""

from __future__ import annotations
from internal.engine.reporter import Reporter

class TerminalReporter(Reporter):
    """将引擎事件打印到标准输出，便于本地调试与观察。"""

    async def on_thinking(self) -> None:
        """模型开始慢思考时提示。"""
        print("🧠 [内部思考 Trace]: 正在规划...", flush=True)

    async def on_tool_call(self, tool_name: str, args: str) -> None:
        """即将执行工具时打印调用信息。"""
        print(f"🔧 [工具调用]: {tool_name} args={args}", flush=True)

    async def on_tool_result(
        self,
        tool_name: str,
        result: str,
        *,
        is_error: bool,
    ) -> None:
        """工具执行完毕后打印结果（可能已被引擎截断）。"""
        status = "失败" if is_error else "成功"
        print(f"📦 [工具结果/{status}]: {tool_name} -> {result}", flush=True)

    async def on_message(self, content: str) -> None:
        """输出模型的阶段性总结或最终回复。"""
        print(f"🤖 [对外回复]: {content}", flush=True)
