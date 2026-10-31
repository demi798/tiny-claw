"""Session.get_working_memory：回合裁剪与 User 首条保证。"""

from __future__ import annotations

from internal.engine.session import Session
from internal.schema.message import Message, Role, ToolCall


def _user(content: str) -> Message:
    return Message(role=Role.USER, content=content)


def _assistant_text(content: str) -> Message:
    return Message(role=Role.ASSISTANT, content=content)


def _assistant_tools(*calls: ToolCall) -> Message:
    return Message(role=Role.ASSISTANT, content="", tool_calls=list(calls))


def _tool_result(call_id: str, content: str) -> Message:
    return Message(role=Role.USER, content=content, tool_call_id=call_id)


def test_empty_history_returns_empty() -> None:
    sess = Session(id="s1", work_dir="/tmp")
    assert sess.get_working_memory(20) == []


def test_under_limit_returns_deepcopy() -> None:
    sess = Session(id="s1", work_dir="/tmp")
    u = _user("你好")
    a = _assistant_text("世界")
    sess.append(u, a)

    wm = sess.get_working_memory(20)
    assert len(wm) == 2
    assert wm[0].content == "你好"
    # 深拷贝：改窗口不影响 Session 全量历史
    wm[0].content = "被污染"
    assert sess.get_working_memory(20)[0].content == "你好"


def test_turn_based_trim_keeps_tool_chain_intact() -> None:
    """按完整回合裁剪，不会把 Assistant(tool_calls) 与 ToolResult 拆开。"""
    sess = Session(id="s1", work_dir="/tmp")
    tc = ToolCall(id="c1", name="bash", arguments={"command": "ls"})
    # 回合1：User + Assistant 文本
    # 回合2：User + Assistant(tool) + ToolResult（共 3 条，整组保留）
    sess.append(
        _user("旧任务"),
        _assistant_text("旧回复"),
        _user("新任务"),
        _assistant_tools(tc),
        _tool_result("c1", "ok"),
    )

    # limit=3 只能放下最后一整轮（3 条），不应切出半截 tool 链
    wm = sess.get_working_memory(3)
    assert len(wm) == 3
    assert wm[0].content == "新任务"
    assert wm[1].tool_calls and wm[1].tool_calls[0].id == "c1"
    assert wm[2].tool_call_id == "c1"


def test_window_not_starting_with_user_gets_synthetic_prefix() -> None:
    """窗口若以 Assistant 开头，应插入合成 User，防智谱 1214。"""
    sess = Session(id="s1", work_dir="/tmp")
    sess.append(_assistant_text("只有助手消息"))

    wm = sess.get_working_memory(20)
    assert len(wm) == 2
    assert wm[0].role == Role.USER
    assert "早期对话上下文已压缩" in wm[0].content
    assert wm[1].content == "只有助手消息"


def test_adjacent_user_turn_is_backfilled_when_needed() -> None:
    """裁剪后不以 User 开头时，优先补紧邻的上一条普通 User 回合。"""
    sess = Session(id="s1", work_dir="/tmp")
    sess.append(
        _user("任务A"),
        _assistant_text("回复A"),
        _user("任务B"),
        _assistant_text("回复B"),
    )

    # limit=1 先只保住尾部 Assistant，再回补紧邻 User「任务B」
    wm = sess.get_working_memory(1)
    assert len(wm) == 2
    assert wm[0].role == Role.USER
    assert wm[0].content == "任务B"
    assert wm[1].content == "回复B"
