"""tiny-claw 

各阶段函数保留完整核心逻辑，可直接调用。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from pathlib import Path
from textwrap import dedent
from typing import Any

from dotenv import load_dotenv

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

load_dotenv(_ROOT / ".env")
load_dotenv()

from internal.engine.loop import AgentEngine
from internal.engine.session import Session, global_session_mgr
from internal.engine.terminal_reporter import TerminalReporter
from internal.observability.tracker import CostTracker
from internal.provider.interface import LLMProvider
from internal.provider.openai import OpenAIProvider
from internal.schema.message import Message, Role, ToolCall
from internal.tools.bash import BashTool
from internal.tools.edit_file import EditFileTool
from internal.tools.read_file import ReadFileTool
from internal.tools.registry import Registry, new_registry
from internal.tools.subagent import SubagentTool
from internal.tools.write_file import WriteFileTool

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

_MODEL = "glm-4.6v"
_FRONT_DIR = _ROOT / "tmp" / "project_front"
_BACK_DIR = _ROOT / "tmp" / "project_back"


# ---------------------------------------------------------------------------
# 公共装配（去重）
# ---------------------------------------------------------------------------

def _require_api_key() -> None:
    if not os.getenv("ZHIPU_API_KEY"):
        raise SystemExit("请先设置 ZHIPU_API_KEY 环境变量")


def _workspace_dir() -> str:
    work_dir = str(Path.cwd() / "workspace")
    Path(work_dir).mkdir(parents=True, exist_ok=True)
    return work_dir


def _new_provider() -> OpenAIProvider:
    return OpenAIProvider.from_zhipu(_MODEL)


def _register_basic_tools(registry: Registry, work_dir: str) -> None:
    """挂载四大基础工具。"""
    registry.register(ReadFileTool(work_dir))
    registry.register(WriteFileTool(work_dir))
    registry.register(BashTool(work_dir))
    registry.register(EditFileTool(work_dir))


def _new_engine(
    provider: LLMProvider,
    registry: Registry,
    *,
    plan_mode: bool = False,
    approval_gate: bool = False,
) -> AgentEngine:
    return AgentEngine(
        provider,
        registry,
        enable_thinking=False,
        plan_mode=plan_mode,
        approval_gate=approval_gate,
    )


async def _run_once(
    engine: AgentEngine,
    session: Session,
    prompt: str,
    reporter: TerminalReporter | None = None,
) -> None:
    """往 Session 追加用户消息并跑一轮引擎。"""
    reporter = reporter or TerminalReporter()
    session.append(Message(role=Role.USER, content=prompt))
    await engine.run(session, reporter)


def _args_to_str(arguments: Any) -> str:
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


# ---------------------------------------------------------------------------
# 阶段 1 — CLI（OOM / Reminder 死循环等）
# ---------------------------------------------------------------------------

async def demo_cli() -> None:
    """CLI 单次任务。默认跑 Reminder 死循环干预；其它 prompt 见函数内注释。"""
    _require_api_key()
    work_dir = _workspace_dir()
    registry = new_registry()
    _register_basic_tools(registry, work_dir)
    engine = _new_engine(_new_provider(), registry)
    reporter = TerminalReporter()

    # # OOM / Compactor
    # session_id = "test_oom_protection_001"
    # prompt = dedent("""
    #     请帮我执行以下三个步骤：
    #     1. 使用 bash 执行 echo "开始排查日志"
    #     2. 使用 read_file 工具读取当前目录下的巨大文件 mock_log.txt
    #     3. 使用 bash 执行 date 命令获取当前时间，并告诉我任务全部完成。
    # """).strip()

    # # 新建 ping.py + git
    # session_id = "cli_default"
    # prompt = dedent("""
    #     我需要在当前目录下新建一个 ping.py，提供一个简单的 http ping 接口。
    #     写完之后，帮我把代码用 git 提交一下。
    # """).strip()

    session_id = "test_doom_loop_001"
    prompt = dedent(
        """
        帮我读取当前目录下的 secret_key.txt。
        注意：我们的文件系统现在非常不稳定，经常报 File Not Found。
        如果报错了，请你【千万不要改变参数】，直接原样再次调用 read_file 尝试，直到成功或连续重试 5 次为止。
        """
    ).strip()

    session = global_session_mgr.get_or_create(session_id, work_dir)
    logger.info("🚀 [main] CLI 演示 session=%s", session_id)
    await _run_once(engine, session, prompt, reporter)


# ---------------------------------------------------------------------------
# 阶段 2 — 双 Session + Working Memory 截断
# ---------------------------------------------------------------------------

async def demo_session_isolation() -> None:
    """Session A 刷记忆后追问密钥；Session B 验证跨会话隔离。"""
    _require_api_key()
    _FRONT_DIR.mkdir(parents=True, exist_ok=True)
    _BACK_DIR.mkdir(parents=True, exist_ok=True)

    registry = new_registry()
    registry.register(ReadFileTool(str(_FRONT_DIR)))
    engine = _new_engine(_new_provider(), registry)
    reporter = TerminalReporter()

    async def _session_a() -> None:
        session_a = global_session_mgr.get_or_create("chat_front_001", str(_FRONT_DIR))
        logger.info("\n>>> [Session A / T1] 查 README 密钥")
        await _run_once(
            engine,
            session_a,
            "帮我看看 README.md 里记录了什么密钥？",
            reporter,
        )
        for _ in range(6):
            session_a.append(Message(role=Role.USER, content="这只是一句闲聊占位符。"))
            session_a.append(Message(role=Role.ASSISTANT, content="好的，收到闲聊。"))
        logger.info("\n>>> [Session A / T2] 追问密钥（应已挤出 WM）")
        await _run_once(
            engine,
            session_a,
            "请直接告诉我，刚才第一轮你查到的那个密钥是什么？不准调用工具！",
            reporter,
        )

    async def _session_b() -> None:
        await asyncio.sleep(1)
        session_b = global_session_mgr.get_or_create("chat_back_002", str(_BACK_DIR))
        logger.info("\n>>> [Session B] 跨会话隔离追问")
        await _run_once(
            engine,
            session_b,
            "别人查到了一个密钥，你这里能看到吗？不准调用工具！",
            reporter,
        )

    await asyncio.gather(_session_a(), _session_b())


# ---------------------------------------------------------------------------
# 阶段 3 — Plan Mode（执行：python main.py -p "任务描述"）
# ---------------------------------------------------------------------------

# 固定 SessionID：多次运行可复用；进程重启后靠 PLAN.md / TODO.md 断点续传
_PLAN_SESSION_ID = "task_web_server_001"


def _parse_plan_args() -> argparse.Namespace:
    """与 main2.py 的 _parse_args 一致。"""
    parser = argparse.ArgumentParser(
        description="tiny-claw Plan Mode：把长程任务进度外部化到 PLAN.md / TODO.md",
    )
    parser.add_argument(
        "--prompt",
        "-p",
        required=True,
        help="要交给 Agent 执行的任务描述",
    )
    return parser.parse_args()


async def demo_plan_mode(prompt: str) -> None:
    """与 main2.py 的 _async_main 逻辑保持一致。"""
    if not os.getenv("ZHIPU_API_KEY"):
        raise SystemExit("请先设置 ZHIPU_API_KEY 环境变量")

    work_dir = str(Path.cwd() / "workspace")
    Path(work_dir).mkdir(parents=True, exist_ok=True)

    llm_provider = OpenAIProvider.from_zhipu("glm-4.6v")
    registry = new_registry()

    # 挂载四大基础工具
    registry.register(ReadFileTool(work_dir))
    registry.register(WriteFileTool(work_dir))
    registry.register(BashTool(work_dir))
    registry.register(EditFileTool(work_dir))

    # 关闭慢思考，开启计划模式
    engine = AgentEngine(
        llm_provider,
        registry,
        enable_thinking=False,
        plan_mode=True,
    )
    reporter = TerminalReporter()

    session = global_session_mgr.get_or_create(_PLAN_SESSION_ID, work_dir)

    logger.info("\n>>> 🚀 收到指令: %s", prompt)
    session.append(Message(role=Role.USER, content=prompt))
    await engine.run(session, reporter)


# ---------------------------------------------------------------------------
# 阶段 4 — Recovery 自愈
# ---------------------------------------------------------------------------

async def demo_recovery() -> None:
    """陷阱指令直接 edit_file，诱发 old_text 不匹配后 Recovery 纠偏。"""
    _require_api_key()
    work_dir = _workspace_dir()
    registry = new_registry()
    _register_basic_tools(registry, work_dir)
    engine = _new_engine(_new_provider(), registry)
    session = global_session_mgr.get_or_create("test_recovery_001", work_dir)
    prompt = dedent(
        """
        我当前目录下有一个 auth.py 文件。
        请修改 auth.py 中的 login 函数。
        请直接使用 edit_file 工具替换下面的代码块，将判断条件改为同时允许"admin"、"root"和"guest"三种用户登录：

        # 鉴权入口函数
        def login(user: str) -> bool:
            # 检查用户名
            if user == "admin":
                return True
            return False
        """
    ).strip()
    logger.info("\n>>> 🚀 [main3] Recovery 自愈测试")
    await _run_once(engine, session, prompt)


# ---------------------------------------------------------------------------
# 阶段 5 — 飞书长连接 + 高危审批（阻塞服务，与 CLI 互斥）
# ---------------------------------------------------------------------------

def demo_feishu_approval() -> None:
    """飞书 WS + Middleware 审批卡片。阻塞主线程，勿与其它 asyncio 演示同开。"""
    import lark_oapi as lark
    from internal.feishu.approval import global_approval_mgr, is_dangerous_command
    from internal.feishu.bot import FeishuBot

    _require_api_key()
    work_dir = _workspace_dir()
    registry = new_registry()
    _register_basic_tools(registry, work_dir)
    engine = _new_engine(_new_provider(), registry, approval_gate=True)
    session = global_session_mgr.get_or_create("test_command_intercept_001", work_dir)
    session.clear()
    bot = FeishuBot(engine, session, work_dir=work_dir)

    def _approval_middleware(call: ToolCall) -> tuple[bool, str]:
        args_str = _args_to_str(call.arguments)
        if is_dangerous_command(call.name, args_str):
            allowed, reason = global_approval_mgr.wait_for_approval(
                call.id,
                call.name,
                args_str,
                bot.reporter(),
            )
            return (True, "") if allowed else (False, reason)
        return True, ""

    registry.use(_approval_middleware)
    event_handler = bot.get_event_dispatcher(use_long_connection=True)
    ws_client = lark.ws.Client(
        bot.app_id,
        bot.app_secret,
        event_handler=event_handler,
        log_level=lark.LogLevel.INFO,
    )
    logger.info("🚀 [main4] 飞书长连接已启动（审批卡片）")
    ws_client.start()


# ---------------------------------------------------------------------------
# 阶段 6 — Subagent 多智能体
# ---------------------------------------------------------------------------

async def demo_subagent() -> None:
    """只读探路者找 config.txt；主 Agent 写 answer.txt。"""
    _require_api_key()
    work_dir = _workspace_dir()
    reporter = TerminalReporter()

    read_only = new_registry()
    read_only.register(ReadFileTool(work_dir))
    read_only.register(BashTool(work_dir))

    main_registry = new_registry()
    _register_basic_tools(main_registry, work_dir)
    engine = _new_engine(_new_provider(), main_registry)
    main_registry.register(SubagentTool(engine, read_only, reporter))

    session = global_session_mgr.get_or_create("test_subagent_001", work_dir)
    prompt = dedent(
        """
        我需要你在这个遗留项目里，找到那个“核心密码”。
        为了防止污染主上下文，请你务必派出子智能体（spawn_subagent）去执行探索任务。
        你可以让子智能体使用 bash 去查找当前目录（及其所有子目录）下名为 config.txt 的文件。
        子智能体拿到密码向你汇报后，请你亲自使用 write_file 工具，将密码写在根目录的 answer.txt 里。
        """
    ).strip()
    logger.info("\n>>> 🚀 [main5] 多智能体协同测试")
    await _run_once(engine, session, prompt, reporter)


# ---------------------------------------------------------------------------
# 阶段 7 — CostTracker 可观测性 【默认运行】
# ---------------------------------------------------------------------------

async def demo_observability() -> None:
    """Tracker 包装 Provider；bash date；打印会话财务报表。"""
    _require_api_key()
    work_dir = str(Path.cwd())  # 仅 date，不必进 workspace

    real_provider = _new_provider()
    session = global_session_mgr.get_or_create("test_observability_001", work_dir)
    tracked = CostTracker(real_provider, _MODEL, session)

    registry = new_registry()
    registry.register(BashTool(work_dir))
    engine = _new_engine(tracked, registry)

    logger.info("\n>>> 🚀 [main6] 可观测性 / 账单仪表盘测试")
    await _run_once(
        engine,
        session,
        "请用 bash 帮我用 date 命令查一下现在的时间。",
    )

    logger.info("\n================ 财务报表 ================")
    logger.info("会话 ID: %s", session.id)
    logger.info("总消耗 Input Tokens: %d", session.total_prompt_tokens)
    logger.info("总消耗 Output Tokens: %d", session.total_completion_tokens)
    logger.info("总计费用 (CNY): ¥%.6f", session.total_cost_cny)
    logger.info("==========================================")


# ---------------------------------------------------------------------------
# 入口：一次只启用一个演示（互斥处用注释）
# ---------------------------------------------------------------------------

def main() -> None:
    try:
        # --- 互斥：一次只打开一个 ---
        # asyncio.run(demo_cli())                # main.py   CLI / Reminder
        # asyncio.run(demo_session_isolation())  # main1.py  双会话 WM

        # Plan Mode（执行：python main.py -p "任务描述"）
        args = _parse_plan_args()
        asyncio.run(demo_plan_mode(args.prompt))

        # asyncio.run(demo_recovery())           # main3.py  Recovery
        # demo_feishu_approval()                 # main4.py  飞书阻塞（勿包 asyncio.run）
        # asyncio.run(demo_subagent())           # main5.py  Subagent
        # asyncio.run(demo_observability())      # main6.py  账单仪表盘
    except Exception:
        logger.exception("引擎运行崩溃")
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
