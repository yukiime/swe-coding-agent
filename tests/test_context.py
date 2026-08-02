import json

import config
from agent import context


def _turn(n, note):
    """一轮 = assistant(带一次工具调用) + 对应的 tool 结果。"""
    return [
        {"role": "assistant", "content": note,
         "tool_calls": [{"id": f"c{n}", "type": "function",
                         "function": {"name": "read_file",
                                      "arguments": json.dumps({"path": "a.py"})}}]},
        {"role": "tool", "tool_call_id": f"c{n}", "content": f"[ok] result {n}\nbody line"},
    ]


def _msgs(n_turns):
    head = [{"role": "system", "content": "SYS"},
            {"role": "user", "content": "PROBLEM"}]
    return head + [m for i in range(1, n_turns + 1) for m in _turn(i, f"note {i}")]


def test_truncate_short_output_unchanged(monkeypatch):
    monkeypatch.setattr(config, "TOOL_OUT_MAX_CHARS", 100)
    assert context.truncate_tool_output("abc") == "abc"


def test_truncate_keeps_head_and_tail(monkeypatch):
    monkeypatch.setattr(config, "TOOL_OUT_MAX_CHARS", 100)
    monkeypatch.setattr(config, "TOOL_OUT_HEAD", 10)
    monkeypatch.setattr(config, "TOOL_OUT_TAIL", 5)
    out = context.truncate_tool_output("H" * 10 + "M" * 200 + "T" * 5)
    assert out.startswith("H" * 10)
    assert out.endswith("T" * 5)
    assert "omitted 200 chars" in out


def test_trim_under_window_returns_input_unchanged(monkeypatch):
    monkeypatch.setattr(config, "CTX_KEEP_TURNS", 10)
    msgs = _msgs(3)
    assert context.trim(msgs) is msgs


def test_trim_keeps_system_and_problem(monkeypatch):
    monkeypatch.setattr(config, "CTX_KEEP_TURNS", 2)
    out = context.trim(_msgs(6))
    assert out[0]["content"] == "SYS"
    assert out[1]["content"] == "PROBLEM"


def test_trim_never_orphans_a_tool_message(monkeypatch):
    """切点必须落在 assistant 上：以 tool 消息开头的历史会让 API 直接报 400。"""
    monkeypatch.setattr(config, "CTX_KEEP_TURNS", 2)
    out = context.trim(_msgs(6))
    assert out[2]["role"] == "user"        # 备忘录
    assert out[3]["role"] == "assistant"   # 保留段的第一条
    kept_ids = {c["id"] for m in out if m["role"] == "assistant"
                for c in m.get("tool_calls") or []}
    orphans = [m for m in out if m["role"] == "tool" and m["tool_call_id"] not in kept_ids]
    assert orphans == []


def test_trim_keeps_exactly_k_recent_turns(monkeypatch):
    monkeypatch.setattr(config, "CTX_KEEP_TURNS", 2)
    out = context.trim(_msgs(6))
    assert [m["content"] for m in out if m["role"] == "assistant"] == ["note 5", "note 6"]


def test_memo_records_dropped_calls_and_first_result_line(monkeypatch):
    monkeypatch.setattr(config, "CTX_KEEP_TURNS", 2)
    memo = context.trim(_msgs(6))[2]["content"]
    assert "turns 1-4 were compacted" in memo
    assert "note 1" in memo                    # 模型自己的结论保留
    assert "read_file(a.py) => [ok] result 1" in memo
    assert "body line" not in memo             # 工具输出正文丢弃


def test_memo_survives_malformed_tool_arguments(monkeypatch):
    """模型偶尔吐出非法 JSON 参数；备忘录不能因此炸掉整轮。"""
    monkeypatch.setattr(config, "CTX_KEEP_TURNS", 1)
    msgs = _msgs(3)
    msgs[2]["tool_calls"][0]["function"]["arguments"] = "{not json"
    memo = context.trim(msgs)[2]["content"]
    assert "read_file()" in memo
