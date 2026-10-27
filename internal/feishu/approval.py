"""高危操作人工审批：在 Middleware 与飞书回调之间共享等待状态。"""

from __future__ import annotations

import asyncio
import logging
import re
import threading
from dataclasses import dataclass
from queue import Queue
from typing import Any, Protocol

logger = logging.getLogger(__name__)


class _ApprovalNotifier(Protocol):
    """审批通知发送契约（FeishuReporter 满足此接口）。"""

    async def send_approval_card(
        self,
        task_id: str,
        tool_name: str,
        args: str,
    ) -> None: ...


@dataclass
class ApprovalResult:
    """审批结果包。"""

    allowed: bool
    reason: str


class ApprovalManager:
    """统一管理当前正在等待人类审批的任务。"""

    def __init__(self) -> None:
        self._mu = threading.RLock()
        # Key 为审批唯一 TaskID，Value 为接收审批结果的队列（容量 1，防止死锁）
        self._pending_tasks: dict[str, Queue[ApprovalResult]] = {}

    def wait_for_approval(
        self,
        task_id: str,
        tool_name: str,
        args: str,
        reporter: _ApprovalNotifier | None = None,
    ) -> tuple[bool, str]:
        """发送飞书审批卡片，并阻塞当前线程等待回调结果。

        返回 (是否放行, 原因说明)。
        """
        # 1. 创建用于阻塞当前引擎协程/线程的队列
        ch: Queue[ApprovalResult] = Queue(maxsize=1)

        with self._mu:
            self._pending_tasks[task_id] = ch

        # 2. 终端始终打印一份，避免飞书发送失败时用户完全看不到 TaskID
        print(
            f"\n\033[31m[需要审批 TaskID: {task_id}]\033[0m "
            f"工具={tool_name} 参数={args}\n"
            f"请在飞书卡片点击「通过/拒绝」，"
            f'或回复 "approve {task_id}" / "reject {task_id}"\n'
        )

        # 3. 发送带按钮的交互卡片
        if reporter is not None:
            _dispatch_approval_card(reporter, task_id, tool_name, args)

        logger.info(
            "[Approval] 已发送审批请求 (TaskID: %s)，协程挂起等待...",
            task_id,
        )

        # 4. 【驾驭核心】：阻塞等待飞书卡片回调 / 文本口令唤醒
        result = ch.get()

        # 5. 拿到结果后清理内存
        with self._mu:
            self._pending_tasks.pop(task_id, None)

        return result.allowed, result.reason

    def resolve_approval(self, task_id: str, allowed: bool, reason: str) -> None:
        """由飞书卡片回调或文本口令触发，向队列发送信号解开阻塞。"""
        with self._mu:
            ch = self._pending_tasks.get(task_id)

        if ch is not None:
            logger.info(
                "[Approval] 收到审批结果 (TaskID: %s, Allowed: %s)",
                task_id,
                allowed,
            )
            ch.put(ApprovalResult(allowed=allowed, reason=reason))
        else:
            logger.info(
                "[Approval] 找不到对应的 TaskID: %s，可能已超时或处理完毕",
                task_id,
            )


def build_approval_card(
    task_id: str,
    tool_name: str,
    args: str,
    *,
    resolved: bool = False,
    allowed: bool | None = None,
) -> dict[str, Any]:
    """构造审批交互卡片 JSON（msg_type=interactive）。

    resolved=False：展示「通过 / 拒绝」按钮；
    resolved=True：展示最终结果，去掉按钮避免重复点击。
    """
    display_args = args if len(args) <= 800 else args[:800] + "...(已截断)"
    body_md = (
        f"Agent 试图执行以下动作:\n"
        f"- **工具**: `{tool_name}`\n"
        f"- **参数**: `{display_args}`\n"
        f"- **任务 ID**: `{task_id}`"
    )

    header_template = "red"
    header_title = "⚠️ 高危操作审批请求"
    if resolved:
        if allowed:
            header_template = "green"
            header_title = "✅ 已批准该高危操作"
            body_md += "\n\n**状态**: 人类管理员已点击「通过」"
        else:
            header_template = "orange"
            header_title = "🚫 已拒绝该高危操作"
            body_md += "\n\n**状态**: 人类管理员已点击「拒绝」"

    elements: list[dict[str, Any]] = [
        {
            "tag": "div",
            "text": {"tag": "lark_md", "content": body_md},
        },
    ]

    if not resolved:
        elements.append(
            {
                "tag": "action",
                "actions": [
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "✅ 通过"},
                        "type": "primary",
                        "value": {
                            "action": "approve",
                            "task_id": task_id,
                            "tool_name": tool_name,
                            "args": display_args,
                        },
                    },
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "🚫 拒绝"},
                        "type": "danger",
                        "value": {
                            "action": "reject",
                            "task_id": task_id,
                            "tool_name": tool_name,
                            "args": display_args,
                        },
                    },
                ],
            }
        )
        elements.append(
            {
                "tag": "note",
                "elements": [
                    {
                        "tag": "plain_text",
                        "content": (
                            f"也可手动回复：approve {task_id} / reject {task_id}"
                        ),
                    }
                ],
            }
        )

    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": header_template,
            "title": {"tag": "plain_text", "content": header_title},
        },
        "elements": elements,
    }


def is_dangerous_command(tool_name: str, args: str) -> bool:
    """简单正则黑名单：判断该工具调用是否需要审批。"""
    # 纯读取类工具：YOLO 模式，全部放行
    if tool_name not in {"bash", "write_file", "edit_file"}:
        return False

    # 针对 bash 的高危模式匹配
    if tool_name == "bash":
        dangerous_patterns = [
            r"rm\s+-r",  # 级联删除
            r"sudo\s+",  # 提权
            r"drop\s+",  # 数据库删除
            r">.*\.py",  # 恶意覆盖源代码
        ]
        for pattern in dangerous_patterns:
            if re.search(pattern, args):
                return True

    return False


def _dispatch_approval_card(
    reporter: _ApprovalNotifier,
    task_id: str,
    tool_name: str,
    args: str,
) -> None:
    """在独立线程中发完飞书审批卡片，再返回。

    不能用当前事件循环的 create_task：随后 wait_for_approval 会 ch.get()
    阻塞同一线程，导致发送协程永远得不到调度。
    """
    error_box: list[BaseException] = []

    def _run() -> None:
        try:
            asyncio.run(reporter.send_approval_card(task_id, tool_name, args))
        except BaseException as err:  # noqa: BLE001 — 收集后由主线程记录
            error_box.append(err)

    worker = threading.Thread(
        target=_run,
        name="feishu-approval-card",
        daemon=True,
    )
    worker.start()
    worker.join(timeout=30)

    if worker.is_alive():
        logger.error("[Approval] 飞书审批卡片发送超时（30s），请查看终端打印的口令")
    elif error_box:
        logger.error(
            "[Approval] 飞书审批卡片发送失败: %s",
            error_box[0],
            exc_info=error_box[0],
        )
    else:
        logger.info("[Approval] 飞书审批卡片已发送")


# 全局单例，方便在 Registry Middleware 与 Feishu Webhook 之间共享状态
global_approval_mgr = ApprovalManager()
