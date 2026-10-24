"""tiny-claw 入口：CLI 单次任务模式（终端 Reporter）。

历史：飞书长连接 / HTTP Webhook 服务逻辑已注释保留，便于后续切换。
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path
from textwrap import dedent

# import lark_oapi as lark
from dotenv import load_dotenv
# from flask import Flask
# from lark_oapi.adapter.flask import parse_req, parse_resp

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

load_dotenv(_ROOT / ".env")
load_dotenv()

from internal.engine.loop import AgentEngine
from internal.engine.session import global_session_mgr
from internal.engine.terminal_reporter import TerminalReporter
# from internal.feishu.bot import FeishuBot
from internal.provider.openai import OpenAIProvider
from internal.schema.message import Message, Role
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

# # 飞书事件回调监听端口（HTTP 模式）
# _PORT = 48080


def _build_engine() -> AgentEngine:
    """初始化 Provider、工具注册表与 Agent 引擎。"""
    if not os.getenv("ZHIPU_API_KEY"):
        raise SystemExit("请先设置 ZHIPU_API_KEY 环境变量")

    work_dir = str(Path.cwd() / "workspace")
    Path(work_dir).mkdir(parents=True, exist_ok=True)

    llm_provider = OpenAIProvider.from_zhipu("glm-4.6v")
    registry = new_registry()

    # 挂载工具全家桶（操作边界锁定在 workspace）
    registry.register(ReadFileTool(work_dir))
    registry.register(WriteFileTool(work_dir))
    registry.register(BashTool(work_dir))
    registry.register(EditFileTool(work_dir))

    # work_dir / System Prompt 跟随 Session，不再挂在 Engine 上
    return AgentEngine(
        llm_provider,
        registry,
        enable_thinking=False,
    )


# def _run_http_server(bot: FeishuBot) -> None:
#     """HTTP Webhook 模式：飞书云端必须能访问到公网 URL。"""
#     event_handler = bot.get_event_dispatcher(use_long_connection=False)
#     app = Flask(__name__)
#
#     @app.route("/webhook/event", methods=["POST"])
#     def webhook_event():
#         """接收飞书事件回调（含 URL 校验与业务事件）。"""
#         resp = event_handler.do(parse_req())
#         return parse_resp(resp)
#
#     logger.info("🚀 tiny-claw HTTP 模式已启动，监听 :%s", _PORT)
#     logger.warning(
#         "飞书开放平台无法访问局域网地址（如 192.168.x.x）。"
#         "请填写公网可访问地址（ngrok/cpolar 等），"
#         "或改用长连接：不设 FEISHU_EVENT_MODE / 设为 websocket"
#     )
#     # threaded=True：允许多个飞书回调并发进入，Agent 任务由 bot 内部后台线程执行
#     app.run(host="0.0.0.0", port=_PORT, threaded=True)
#
#
# def _run_websocket(bot: FeishuBot) -> None:
#     """WebSocket 长连接模式：本机主动连飞书，无需公网 IP / URL 校验。"""
#     event_handler = bot.get_event_dispatcher(use_long_connection=True)
#     ws_client = lark.ws.Client(
#         bot.app_id,
#         bot.app_secret,
#         event_handler=event_handler,
#         log_level=lark.LogLevel.INFO,
#     )
#     logger.info("🚀 tiny-claw 长连接模式已启动（WS 已准备连接飞书）")
#     logger.info(
#         "若聊天无日志，请逐项确认飞书开放平台：\n"
#         "  1) 事件配置 → 订阅方式 =「使用长连接接收事件」并保存\n"
#         "  2) 添加事件「接收消息」im.message.receive_v1\n"
#         "  3) 权限：im:message.p2p_msg:readonly + im:message:send_as_bot\n"
#         "  4) 版本管理与发布 → 新建版本并发布（改配置后必须发版才生效）\n"
#         "  5) 应用能力已开启「机器人」，并用该机器人发起单聊测试"
#     )
#     ws_client.start()


async def _async_main() -> None:
    """CLI：组装引擎，用 TerminalReporter 跑一次任务。"""
    work_dir = str(Path.cwd() / "workspace")
    Path(work_dir).mkdir(parents=True, exist_ok=True)

    engine = _build_engine()
    # 【注入终端输出器】
    reporter = TerminalReporter()


    # prompt = dedent(
    #     """
    #     我需要在当前目录下新建一个 ping.py，提供一个简单的 http ping 接口。
    #     写完之后，帮我把代码用 git 提交一下。
    #     """
    # ).strip()

    # 会话承载历史；用户输入先写入 Session，再唤醒引擎
    sessionID = "test_oom_protection_001"
    session = global_session_mgr.get_or_create(sessionID, work_dir)

    # 发起一个会导致读取大文件的恶意任务 
    prompt = dedent(
        """
        请帮我执行以下三个步骤： 
        1. 使用 bash 执行 echo "开始排查日志" 
        2. 使用 read_file 工具读取当前目录下的巨大文件 mock_log.txt 
        3. 使用 bash 执行 date 命令获取当前时间，并告诉我任务全部完成。 
        """
    ).strip()

    session.append(Message(role=Role.USER, content=prompt))
    await engine.run(session, reporter)

def main() -> None:
    """启动 CLI 单次任务。"""
    # ---------- 原飞书服务入口（已注释保留）----------
    # # 1. 初始化引擎依赖
    # engine = _build_engine()
    #
    # # 2. 初始化飞书 Bot
    # bot = FeishuBot(engine)
    #
    # # 3. 按模式启动（默认 websocket，便于本地开发）
    # mode = os.getenv("FEISHU_EVENT_MODE", "websocket").strip().lower()
    # if mode in {"http", "webhook"}:
    #     _run_http_server(bot)
    # else:
    #     _run_websocket(bot)
    # ---------- /原飞书服务入口 ----------

    try:
        asyncio.run(_async_main())
    except Exception:
        logger.exception("引擎运行崩溃")
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
