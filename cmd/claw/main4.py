"""
飞书长连接 + 高危命令人工审批演示入口。

本机主动连飞书，无需公网 URL。一个 Bot 绑定一个 Session，
Registry Middleware 命中黑名单时阻塞等待 approve / reject 口令。
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

import lark_oapi as lark
from dotenv import load_dotenv

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

load_dotenv(_ROOT / ".env")
load_dotenv()

from internal.engine.loop import AgentEngine
from internal.engine.session import global_session_mgr
from internal.feishu.approval import global_approval_mgr, is_dangerous_command
from internal.feishu.bot import FeishuBot
from internal.provider.openai import OpenAIProvider
from internal.schema.message import ToolCall
from internal.tools.bash import BashTool
from internal.tools.edit_file import EditFileTool
from internal.tools.read_file import ReadFileTool
from internal.tools.registry import new_registry
from internal.tools.write_file import WriteFileTool

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

_SESSION_ID = "test_command_intercept_001"


def _args_to_str(arguments: Any) -> str:
    """把 ToolCall.arguments 规范成可读字符串，供黑名单与审批展示。"""
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
    if not os.getenv("ZHIPU_API_KEY"):
        raise SystemExit("请先设置 ZHIPU_API_KEY 环境变量")

    work_dir = str(Path.cwd() / "workspace")
    Path(work_dir).mkdir(parents=True, exist_ok=True)

    llm_provider = OpenAIProvider.from_zhipu("glm-4.6v")

    registry = new_registry()
    registry.register(ReadFileTool(work_dir))
    registry.register(WriteFileTool(work_dir))
    registry.register(BashTool(work_dir))
    registry.register(EditFileTool(work_dir))

    # approval_gate=True：System Prompt 要求模型照常调用工具，安全由审批中间件把关
    engine = AgentEngine(
        llm_provider,
        registry,
        enable_thinking=False,
        plan_mode=False,
        approval_gate=True,
    )

    # 假设一个 bot 绑定一个 session；清空历史，避免上次口头拒答污染本轮
    session = global_session_mgr.get_or_create(_SESSION_ID, work_dir)
    session.clear()

    bot = FeishuBot(engine, session, work_dir=work_dir)

    # 【核心注入】注册安全拦截 Middleware
    def _approval_middleware(call: ToolCall) -> tuple[bool, str]:
        args_str = _args_to_str(call.arguments)

        # 检查是否命中高危特征库
        if is_dangerous_command(call.name, args_str):
            # 使用大模型生成的唯一 ToolCallID 作为 TaskID
            task_id = call.id

            # 挂起当前线程，发消息给飞书，等待人类审批
            allowed, reason = global_approval_mgr.wait_for_approval(
                task_id,
                call.name,
                args_str,
                bot.reporter(),
            )
            if not allowed:
                return False, reason  # 拒绝，将理由传回给大模型
            return True, ""  # 同意，放行底层工具

        # 没命中黑名单，直接 YOLO 放行
        return True, ""

    registry.use(_approval_middleware)

    # WebSocket 长连接：本机主动连飞书，无需公网 IP / URL 校验
    event_handler = bot.get_event_dispatcher(use_long_connection=True)
    ws_client = lark.ws.Client(
        bot.app_id,
        bot.app_secret,
        event_handler=event_handler,
        log_level=lark.LogLevel.INFO,
    )
    logger.info("🚀 tiny-claw 长连接模式已启动（WS 已准备连接飞书）")
    logger.info(
        "若聊天无日志 / 卡片按钮无反应，请逐项确认飞书开放平台：\n"
        "  1) 事件配置 → 订阅方式 =「使用长连接接收事件」并保存\n"
        "  2) 添加事件「接收消息」im.message.receive_v1\n"
        "  3) 回调配置 → 使用长连接接收回调，并添加「卡片回传交互」card.action.trigger\n"
        "  4) 权限：im:message.p2p_msg:readonly + im:message:send_as_bot\n"
        "  5) 版本管理与发布 → 新建版本并发布（改配置后必须发版才生效）\n"
        "  6) 应用能力已开启「机器人」，并用该机器人发起单聊测试"
    )
    ws_client.start()


if __name__ == "__main__":
    main()
