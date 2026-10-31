"""
AgentOps 飞书服务端：WebSocket 长连接 + 高危审批 Middleware + 按 Session 工厂组装引擎。
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

import lark_oapi as lark
from dotenv import load_dotenv

from internal.engine.loop import AgentEngine
from internal.engine.session import Session, create_session_manager
from internal.feishu.approval import create_approval_manager, is_dangerous_command
from internal.feishu.bot import FeishuBot, reporter_from_context
from internal.observability.tracker import CostTracker
from internal.provider.openai import OpenAIProvider
from internal.schema.message import ToolCall
from internal.tools.bash import BashTool
from internal.tools.edit_file import EditFileTool
from internal.tools.read_file import ReadFileTool
from internal.tools.registry import ToolRegistry
from internal.tools.write_file import WriteFileTool

# 从仓库根目录加载 .env（需先 pip install -e .）
_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(_ROOT / ".env")
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

_MODEL = "glm-4.6v"


def _args_to_str(arguments: Any) -> str:
    """把 ToolCall.arguments 规范成字符串，供黑名单与审批卡片展示。"""
    if isinstance(arguments, (bytes, bytearray)):
        return arguments.decode("utf-8", errors="replace")
    if isinstance(arguments, str):
        return arguments
    if arguments is None:
        return ""
    try:
        return json.dumps(arguments, ensure_ascii=False)
    except TypeError:
        return str(arguments)


def main() -> None:
    logger.info("🚀 正在启动 tiny-claw AgentOps 飞书服务端...")

    if not os.getenv("ZHIPU_API_KEY") or not os.getenv("FEISHU_APP_ID"):
        raise SystemExit("❌ 请先导出 ZHIPU_API_KEY 和 飞书相关的环境变量")

    # 1. 设定监控的物理工作区
    work_dir = str(Path.cwd() / "workspace")
    Path(work_dir).mkdir(parents=True, exist_ok=True)

    # 2. 初始化底层大脑与注册表（入口显式构造，注入 Bot / Middleware）
    llm_provider = OpenAIProvider.from_zhipu(_MODEL)
    session_mgr = create_session_manager()
    approval_mgr = create_approval_manager()
    registry = ToolRegistry()
    registry.register(ReadFileTool(work_dir))
    registry.register(WriteFileTool(work_dir))
    registry.register(EditFileTool(work_dir))
    registry.register(BashTool(work_dir))  # 必备的运维工具

    # 3. 【核心防御】：注入安全拦截 Middleware（与 Bot 共享同一 approval_mgr）
    def _approval_middleware(call: ToolCall) -> tuple[bool, str]:
        args_str = _args_to_str(call.arguments)

        # 检查是否命中危险命令黑名单
        if is_dangerous_command(call.name, args_str):
            task_id = call.id
            logger.info(
                "[Middleware] 拦截到高危操作: %s，触发飞书审批挂起...",
                call.name,
            )

            # 【驾驭魔术】：从 Context 取出专属于发起该请求群聊的 Reporter
            current_reporter = reporter_from_context()

            # 当前线程挂起，向飞书发送卡片，等待人类决定
            allowed, reason = approval_mgr.wait_for_approval(
                task_id,
                call.name,
                args_str,
                current_reporter,
            )

            if not allowed:
                return False, reason  # 拒绝，将理由作为 ToolResult 喂回大模型
            return True, ""  # 同意，放行底层物理执行

        # 普通读取命令，YOLO 放行
        return True, ""

    registry.use(_approval_middleware)
    logger.info("🛡️ 安全防御 Middleware 已挂载。")

    # 4. 动态 Factory：保证高并发物理独立与账单准确追踪
    def engine_factory(session: Session) -> AgentEngine:
        # Tracker 绑定当前特定用户的 Session 账本
        tracked_provider = CostTracker(llm_provider, _MODEL, session)
        # 关闭慢思考与 Plan Mode
        return AgentEngine(
            tracked_provider,
            registry,
            enable_thinking=False,
            plan_mode=False,
        )

    # 5. 初始化飞书 Bot 调度中心（长连接模式：encrypt_key / verify_token 传空）
    bot = FeishuBot(
        engine_factory,
        work_dir=work_dir,
        session_mgr=session_mgr,
        approval_mgr=approval_mgr,
    )
    event_handler = bot.get_event_dispatcher(use_long_connection=True)

    # 6. 启动 WebSocket 长连接（阻塞主线程；无需 ngrok / HTTP 公网回调）
    ws_client = lark.ws.Client(
        bot.app_id,
        bot.app_secret,
        event_handler=event_handler,
        log_level=lark.LogLevel.INFO,
    )
    logger.info("📡 飞书长连接已启动，等待消息与审批卡片交互...")
    ws_client.start()


if __name__ == "__main__":
    main()
