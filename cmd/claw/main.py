"""
tiny-claw CLI 入口：解析参数，装配 Tracker / Tracing，启动 Main Loop。
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

load_dotenv(_ROOT / ".env")
load_dotenv()

from internal.engine.loop import AgentEngine
from internal.engine.session import global_session_mgr
from internal.engine.terminal_reporter import TerminalReporter
from internal.observability.trace import export_trace_to_file, start_span
from internal.observability.tracker import CostTracker
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

_MODEL = "glm-4.6v"


def _parse_args() -> argparse.Namespace:
    """解析命令行参数。"""
    parser = argparse.ArgumentParser(
        description="tiny-claw CLI：在指定工作区执行 Agent 任务",
    )
    parser.add_argument(
        "--prompt",
        "-p",
        required=True,
        help="要交给 Agent 执行的任务描述",
    )
    parser.add_argument(
        "--dir",
        "-d",
        default=".",
        help="Agent 运行的工作区目录路径（默认为当前目录）",
    )
    parser.add_argument(
        "--session",
        "-s",
        default="cli_default_session",
        help="指定会话 ID，支持断点续传",
    )
    return parser.parse_args()


async def _async_main(prompt: str, work_dir: str, session_id: str) -> None:
    if not os.getenv("ZHIPU_API_KEY"):
        raise SystemExit("请先设置 ZHIPU_API_KEY 环境变量")

    print("==================================================")
    print("🚀 启动 tiny-claw CLI 引擎...")
    print(f"📁 锁定工作区: {work_dir}")
    print("==================================================")

    # 2. 初始化核心基础服务
    real_provider = OpenAIProvider.from_zhipu(_MODEL)
    sess = global_session_mgr.get_or_create(session_id, work_dir)

    # 【全息监控装配】：用 CostTracker 包裹真实 Provider
    tracked_provider = CostTracker(real_provider, _MODEL, sess)

    # 3. 初始化工具与执行层
    registry = new_registry()
    registry.register(ReadFileTool(work_dir))
    registry.register(WriteFileTool(work_dir))
    registry.register(BashTool(work_dir))
    registry.register(EditFileTool(work_dir))
    # CLI 默认 YOLO：全权信任本地执行，暂不挂载飞书审批 Middleware

    # 4. 核心引擎（内部自动装配 Composer / Compactor / Recovery / Reminder）
    # enable_thinking=False，plan_mode=True（与 Go NewAgentEngine(..., false, true) 对齐）
    engine = AgentEngine(
        tracked_provider,
        registry,
        enable_thinking=False,
        plan_mode=True,
    )

    # 【全息追踪装配】：CLI 层 Root Span（引擎内还会挂 Agent.Run 子树）
    root_span = start_span("CLI.TaskRun")
    root_span.add_attribute("Prompt", prompt)
    try:
        # 5. 彩色终端输出器
        reporter = TerminalReporter()

        print(f"\n🎯 收到任务: {prompt}\n")
        sess.append(Message(role=Role.USER, content=prompt))

        # 6. 启动 Main Loop
        await engine.run(sess, reporter)
    finally:
        root_span.end_span()
        export_trace_to_file(root_span, work_dir, sess.id)

    elapsed = datetime.now() - root_span.start_time
    print("\n==================================================")
    print(f"✨ 任务圆满结束。总耗时: {elapsed}")
    print(
        f"💰 Session 累计消耗: ¥{sess.total_cost_cny:.6f} | "
        f"Token: Input {sess.total_prompt_tokens}, "
        f"Output {sess.total_completion_tokens}"
    )
    print("==================================================")


def main() -> None:
    args = _parse_args()
    work_dir = str(Path(args.dir).expanduser().resolve())
    Path(work_dir).mkdir(parents=True, exist_ok=True)

    try:
        asyncio.run(_async_main(args.prompt, work_dir, args.session))
    except Exception:
        logger.exception("引擎运行崩溃")
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
