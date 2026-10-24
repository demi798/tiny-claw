"""飞书机器人：将飞书消息桥接到 Agent 引擎，并通过 Reporter 回传进度。"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any

import lark_oapi as lark
from lark_oapi.api.im.v1 import (
    CreateMessageRequest,
    CreateMessageRequestBody,
    P2ImMessageMessageReadV1,
    P2ImMessageReceiveV1,
)
from lark_oapi.event.dispatcher_handler import EventDispatcherHandler
from dotenv import load_dotenv

from internal.engine.reporter import Reporter
from internal.engine.session import global_session_mgr
from internal.schema.message import Message, Role

load_dotenv()

if TYPE_CHECKING:
    from internal.engine.loop import AgentEngine

logger = logging.getLogger(__name__)

# 飞书群聊 @ 机器人时常见的占位符，例如 @_user_1
_MENTION_PATTERN = re.compile(r"@_user_\d+")


class FeishuBot:
    """封装飞书机器人配置与核心业务流。"""

    def __init__(self, engine: AgentEngine, *, work_dir: str | None = None) -> None:
        app_id = os.getenv("FEISHU_APP_ID", "").strip()
        app_secret = os.getenv("FEISHU_APP_SECRET", "").strip()
        if not app_id or not app_secret:
            raise SystemExit("请设置 FEISHU_APP_ID 和 FEISHU_APP_SECRET")

        self.app_id = app_id
        self.app_secret = app_secret
        self.engine = engine
        # 会话工作区：默认 cwd/workspace，与 CLI 约定一致
        self.work_dir = work_dir or str(Path.cwd() / "workspace")
        Path(self.work_dir).mkdir(parents=True, exist_ok=True)
        # 实例化飞书官方客户端，用于主动发消息
        self.client = (
            lark.Client.builder()
            .app_id(app_id)
            .app_secret(app_secret)
            .build()
        )

    def get_event_dispatcher(
        self,
        *,
        use_long_connection: bool = False,
    ) -> EventDispatcherHandler:
        """构建事件调度器。

        use_long_connection=True 时（WebSocket 长连接），encrypt_key / verify_token
        必须传空字符串，由飞书开放平台长连接通道自行鉴权。
        HTTP 回调模式则读取环境变量中的 Encrypt Key 与 Verification Token。
        """
        if use_long_connection:
            encrypt_key = ""
            verify_token = ""
        else:
            encrypt_key = os.getenv("FEISHU_ENCRYPT_KEY", "")
            verify_token = os.getenv("FEISHU_VERIFY_TOKEN", "")

        # Python SDK builder 参数顺序为 (encrypt_key, verification_token)
        return (
            lark.EventDispatcherHandler.builder(encrypt_key, verify_token)
            .register_p2_im_message_receive_v1(self._on_message_receive)
            .register_p2_im_message_message_read_v1(self._on_message_read)
            .build()
        )

    def _on_message_receive(self, event: P2ImMessageReceiveV1) -> None:
        """处理「接收消息」事件：解析文本后异步拉起 Agent，不阻塞回调。"""
        # 先打日志，便于确认事件是否到达（配置未生效时这里不会触发）
        logger.info("[Feishu] 收到原始事件: %s", lark.JSON.marshal(event))

        message = event.event.message
        sender = event.event.sender
        # 忽略机器人自己发出的消息，避免循环
        if sender is not None and getattr(sender, "sender_type", None) == "bot":
            logger.info("[Feishu] 忽略机器人自身消息")
            return

        content_str = _extract_text(message.content)
        chat_id = message.chat_id
        if not content_str:
            logger.warning("[Feishu] 会话 %s 文本为空，跳过（请发送纯文本）", chat_id)
            return

        logger.info("[Feishu] 收到会话 %s 消息: %s", chat_id, content_str)

        # 【驾驭并发】：绝不能阻塞事件回调，后台独立线程跑 Agent
        self._spawn_agent_run(chat_id, content_str)

    def _on_message_read(self, event: P2ImMessageMessageReadV1) -> None:
        """消息已读事件：静默忽略，避免日志干扰。"""
        return None

    def _spawn_agent_run(self, chat_id: str, prompt: str) -> None:
        """在独立线程中启动 Agent，避免占用飞书长连接的事件循环。"""
        threading.Thread(
            target=lambda: asyncio.run(self._handle_agent_run(chat_id, prompt)),
            daemon=True,
            name=f"feishu-agent-{chat_id[:8]}",
        ).start()

    async def _handle_agent_run(self, chat_id: str, prompt: str) -> None:
        """连接飞书与底层引擎的桥梁。"""
        reporter = FeishuReporter(client=self.client, chat_id=chat_id)
        # 以 chat_id 隔离会话；用户输入先写入 Session 再唤醒引擎
        session = global_session_mgr.get_or_create(chat_id, self.work_dir)
        session.append(Message(role=Role.USER, content=prompt))
        try:
            await self.engine.run(session, reporter)
        except Exception as err:
            logger.exception("[Feishu] Agent 运行崩溃 chat_id=%s", chat_id)
            try:
                await reporter.send_msg(f"❌ Agent 运行崩溃: {err}")
            except Exception:
                # 发消息本身也可能失败（如 21001 无法取 token），避免二次抛错淹没日志
                logger.exception("[Feishu] 崩溃通知发送也失败 chat_id=%s", chat_id)


class FeishuReporter(Reporter):
    """将引擎输出格式化后发到飞书会话。"""

    def __init__(self, client: lark.Client, chat_id: str) -> None:
        self._client = client
        self._chat_id = chat_id

    async def send_msg(self, text: str) -> None:
        """调用飞书 OpenAPI 发送文本消息。"""
        content_str = json.dumps({"text": text}, ensure_ascii=False)
        request = (
            CreateMessageRequest.builder()
            .receive_id_type("chat_id")
            .request_body(
                CreateMessageRequestBody.builder()
                .receive_id(self._chat_id)
                .msg_type("text")
                .content(content_str)
                .build()
            )
            .build()
        )
        # SDK 为同步调用，放到线程池避免长时间阻塞事件循环
        response = await asyncio.to_thread(self._client.im.v1.message.create, request)
        if not response.success():
            logger.error(
                "[Feishu] 发送消息失败 code=%s msg=%s log_id=%s",
                response.code,
                response.msg,
                response.get_log_id(),
            )

    async def on_thinking(self) -> None:
        """仅发轻量提示，避免飞书刷屏。"""
        await self.send_msg("🤔 模型正在慢思考 (Thinking)...")

    async def on_tool_call(self, tool_name: str, args: str) -> None:
        """汇报即将执行的工具。"""
        await self.send_msg(f"🛠️ **正在执行工具**：`{tool_name}`\n参数：`{args}`")

    async def on_tool_result(
        self,
        tool_name: str,
        result: str,
        *,
        is_error: bool,
    ) -> None:
        """汇报工具执行结果；成功时不刷全量日志。"""
        if is_error:
            await self.send_msg(f"⚠️ **执行报错** ({tool_name})：\n{result}")
        else:
            await self.send_msg(f"✅ **执行成功** ({tool_name})")

    async def on_message(self, content: str) -> None:
        """将模型阶段性总结或最终纯文本回答发给用户。"""
        await self.send_msg(content)


def _extract_text(raw_content: Any) -> str:
    """从飞书消息 content JSON 中提取纯文本，并去掉 @ 机器人占位符。"""
    if raw_content is None:
        return ""

    text = ""
    if isinstance(raw_content, dict):
        text = str(raw_content.get("text", ""))
    else:
        content_str = str(raw_content)
        try:
            payload = json.loads(content_str)
            text = str(payload.get("text", "")) if isinstance(payload, dict) else content_str
        except json.JSONDecodeError:
            # 兼容粗粒度裁剪：去掉 {"text":"..."} 外壳
            text = content_str.removeprefix('{"text":"').removesuffix('"}')

    return _MENTION_PATTERN.sub("", text).strip()
