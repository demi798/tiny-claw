"""Compactor：水位线、掩码与不污染原 Message。"""

from __future__ import annotations

from internal.context.compactor import Compactor
from internal.schema.message import Message, Role


def test_under_watermark_returns_same_list() -> None:
    msgs = [
        Message(role=Role.SYSTEM, content="sys"),
        Message(role=Role.USER, content="hi"),
    ]
    c = Compactor(max_chars=10_000, retain_last_msgs=6)
    out = c.compact(msgs)
    assert out is msgs


def test_far_tool_result_is_fully_masked() -> None:
    long_tool = "X" * 500
    msgs = [
        Message(role=Role.SYSTEM, content="sys"),
        Message(role=Role.USER, content=long_tool, tool_call_id="t1"),
        Message(role=Role.USER, content="近期1"),
        Message(role=Role.USER, content="近期2"),
    ]
    # retain_last_msgs=2 → 索引 2,3 为保护区；远期 tool 在索引 1
    c = Compactor(max_chars=100, retain_last_msgs=2)
    out = c.compact(msgs)

    assert "早期的工具输出已被系统强制清理" in out[1].content
    assert out[1].tool_call_id == "t1"
    # 原消息未被污染
    assert msgs[1].content == long_tool


def test_near_tool_result_is_head_tail_truncated() -> None:
    long_tool = "A" * 600 + "MID" + "B" * 600
    msgs = [
        Message(role=Role.SYSTEM, content="sys"),
        Message(role=Role.USER, content=long_tool, tool_call_id="t1"),
    ]
    # retain 覆盖全部非 system，走短期头尾截断
    c = Compactor(max_chars=50, retain_last_msgs=10)
    out = c.compact(msgs)

    assert "内容过长，中间" in out[1].content
    assert out[1].content.startswith("A" * 100)  # 头 500 的前缀仍在
    assert out[1].content.endswith("B" * 100)
    assert msgs[1].content == long_tool


def test_far_assistant_long_content_is_folded() -> None:
    msgs = [
        Message(role=Role.SYSTEM, content="sys"),
        Message(role=Role.ASSISTANT, content="T" * 300),
        Message(role=Role.USER, content="u1"),
        Message(role=Role.USER, content="u2"),
    ]
    c = Compactor(max_chars=50, retain_last_msgs=2)
    out = c.compact(msgs)
    assert out[1].content == "...[早期的推理思考过程已折叠]..."
    assert msgs[1].content == "T" * 300


def test_system_message_never_modified() -> None:
    sys_content = "S" * 500
    msgs = [
        Message(role=Role.SYSTEM, content=sys_content),
        Message(role=Role.USER, content="U" * 500),
    ]
    c = Compactor(max_chars=10, retain_last_msgs=0)
    out = c.compact(msgs)
    assert out[0].content == sys_content
    assert out[0] is msgs[0]
