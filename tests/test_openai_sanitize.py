"""Provider 消息清洗：tool 链完整性与 system 后 User 首条。"""

from __future__ import annotations

from internal.provider.openai import (
    _ensure_openai_user_first,
    _sanitize_openai_messages,
)


def test_orphan_tool_is_dropped() -> None:
    msgs = [
        {"role": "user", "content": "hi"},
        {"role": "tool", "tool_call_id": "orphan", "content": "x"},
    ]
    out = _sanitize_openai_messages(msgs)
    assert len(out) == 1
    assert out[0]["role"] == "user"


def test_complete_tool_chain_is_kept() -> None:
    msgs = [
        {"role": "user", "content": "run"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "c1",
                    "type": "function",
                    "function": {"name": "bash", "arguments": "{}"},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "c1", "content": "ok"},
    ]
    out = _sanitize_openai_messages(msgs)
    assert len(out) == 3
    assert out[1]["role"] == "assistant"
    assert out[1]["tool_calls"][0]["id"] == "c1"
    assert out[2]["role"] == "tool"


def test_incomplete_tool_calls_degrades_to_text_assistant() -> None:
    msgs = [
        {"role": "user", "content": "run"},
        {
            "role": "assistant",
            "content": "我先想想",
            "tool_calls": [
                {
                    "id": "c1",
                    "type": "function",
                    "function": {"name": "bash", "arguments": "{}"},
                }
            ],
        },
        # 缺少对应 tool 结果
    ]
    out = _sanitize_openai_messages(msgs)
    assert len(out) == 2
    assert out[1] == {"role": "assistant", "content": "我先想想"}
    assert "tool_calls" not in out[1]


def test_ensure_user_first_after_system() -> None:
    msgs = [
        {"role": "system", "content": "sys"},
        {"role": "assistant", "content": "先说话"},
    ]
    out = _ensure_openai_user_first(msgs)
    assert out[0]["role"] == "system"
    assert out[1]["role"] == "user"
    assert out[2]["role"] == "assistant"


def test_sanitize_inserts_placeholder_user_after_system() -> None:
    """sanitize 末尾会调用 _ensure_openai_user_first。"""
    msgs = [
        {"role": "system", "content": "sys"},
        {"role": "assistant", "content": "hello"},
    ]
    out = _sanitize_openai_messages(msgs)
    assert out[0]["role"] == "system"
    assert out[1]["role"] == "user"
    assert out[2]["role"] == "assistant"
