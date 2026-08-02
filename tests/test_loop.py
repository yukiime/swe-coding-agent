import pytest

import config
from agent import loop
from agent.llm import CostLimitExceeded
from tests.conftest import FakeContainer, ScriptedLLM

INSTANCE = {"problem_statement": "PROBLEM"}


@pytest.fixture
def traj():
    """返回 (事件列表, 写事件的回调) —— loop.run 期望一个单参可调用。"""
    events = []
    return events, events.append


def _read_turn(note="looking"):
    return (note, [("read_file", {"path": "a.py"})])


def _edit_turn(note="fixing"):
    return (note, [("edit_file", {"path": "a.py", "old_str": "x", "new_str": "y"})])


def test_model_done_when_no_tool_calls(traj):
    events, sink = traj
    result = loop.run(INSTANCE, ScriptedLLM([_read_turn(), ("all set", [])]),
                      FakeContainer(), sink)
    assert result["stop_reason"] == "model_done"
    assert result["turns"] == 2
    assert events[-1]["event"] == "end"
    assert events[-1]["stop_reason"] == "model_done"


def test_max_turns_stops_the_loop(monkeypatch, traj):
    monkeypatch.setattr(config, "MAX_TURNS", 3)
    monkeypatch.setattr(config, "FORCE_FIRST_EDIT_TURN", 99)
    events, sink = traj
    result = loop.run(INSTANCE, ScriptedLLM([_read_turn()] * 3), FakeContainer(), sink)
    assert result["stop_reason"] == "max_turns"
    assert result["turns"] == 3


def test_cost_limit_stops_and_is_recorded_in_the_trajectory(traj):
    class Broke:
        def chat(self, messages, tools, tool_choice=None):
            raise CostLimitExceeded("instance iid spent $0.99 > $0.40")

    events, sink = traj
    result = loop.run(INSTANCE, Broke(), FakeContainer(), sink)
    assert result["stop_reason"] == "cost_limit"
    assert events[0]["event"] == "cost_limit"
    assert "$0.99" in events[0]["detail"]


def test_patch_is_exported_with_untracked_files_staged(traj):
    """裸 git diff 看不见模型新建的文件（astropy-13398 曾因此丢掉整个修复）。"""
    events, sink = traj
    ctr = FakeContainer([(0, "DIFF-BODY", "")])
    result = loop.run(INSTANCE, ScriptedLLM([("done", [])]), ctr, sink)
    assert ctr.calls[-1][0] == "git add -A && git diff --cached"
    assert result["patch"] == "DIFF-BODY"
    assert events[-1]["patch_chars"] == len("DIFF-BODY")


def test_watchdog_nudges_after_n_read_only_turns(monkeypatch, traj):
    monkeypatch.setattr(config, "MAX_TURNS", 6)
    monkeypatch.setattr(config, "WATCHDOG_NO_EDIT_TURNS", 2)
    monkeypatch.setattr(config, "FORCE_FIRST_EDIT_TURN", 99)
    events, sink = traj
    llm = ScriptedLLM([_read_turn()] * 6)
    loop.run(INSTANCE, llm, FakeContainer(), sink)
    # 条件是 turn - last_edit_turn > 阈值，last_edit_turn=0 → 第 3 轮起开始催
    turns = [e for e in events if e["event"] == "turn"]
    assert [t.get("nudged", False) for t in turns] == [False, False, True, True, True, True]
    assert "ACTION REQUIRED" in llm.seen[2]["messages"][-1]["content"]
    assert "in the last 2 turns" in llm.seen[2]["messages"][-1]["content"]


def test_force_uses_tool_choice_when_deadline_passes_with_zero_edits(monkeypatch, traj):
    """文字提醒会被无视（round1 冒烟：16 次 nudge 零转化），到期改用 tool_choice 机械强制。"""
    monkeypatch.setattr(config, "MAX_TURNS", 3)
    monkeypatch.setattr(config, "WATCHDOG_NO_EDIT_TURNS", 99)
    monkeypatch.setattr(config, "FORCE_FIRST_EDIT_TURN", 3)
    events, sink = traj
    llm = ScriptedLLM([_read_turn(), _read_turn(), _edit_turn()])
    loop.run(INSTANCE, llm, FakeContainer(), sink)
    assert [c["tool_choice"] for c in llm.seen] == [
        None, None, {"type": "function", "function": {"name": "edit_file"}}]
    turns = [e for e in events if e["event"] == "turn"]
    assert turns[2]["forced_edit"] is True
    assert "mandatory THIS turn" in llm.seen[2]["messages"][-1]["content"]


def test_no_force_once_an_edit_was_attempted(monkeypatch, traj):
    """计的是"尝试过 edit"而非"edit 成功"——正在试错的模型不该被继续催。"""
    monkeypatch.setattr(config, "MAX_TURNS", 3)
    monkeypatch.setattr(config, "WATCHDOG_NO_EDIT_TURNS", 99)
    monkeypatch.setattr(config, "FORCE_FIRST_EDIT_TURN", 3)
    events, sink = traj
    ctr = FakeContainer([(0, "no match here", "")])   # 让首轮 edit_file 报 old_str not found
    llm = ScriptedLLM([_edit_turn(), _read_turn(), _read_turn()])
    loop.run(INSTANCE, llm, ctr, sink)
    assert [c["tool_choice"] for c in llm.seen] == [None, None, None]


def test_banner_is_not_appended_to_history(monkeypatch, traj):
    """轮数横幅是临时消息：进了 msgs 会随历史累积，且模型会看到过期的轮数。"""
    monkeypatch.setattr(config, "MAX_TURNS", 3)
    monkeypatch.setattr(config, "FORCE_FIRST_EDIT_TURN", 99)
    events, sink = traj
    llm = ScriptedLLM([_read_turn()] * 3)
    loop.run(INSTANCE, llm, FakeContainer(), sink)
    stale = [m for m in llm.seen[-1]["messages"][:-1]
             if m["role"] == "user" and m["content"].startswith("[turn ")]
    assert stale == []
    assert llm.seen[-1]["messages"][-1]["content"].startswith("[turn 3/3]")


def test_malformed_tool_arguments_do_not_crash_the_loop(traj):
    class BadJSON:
        def __init__(self):
            self.n = 0

        def chat(self, messages, tools, tool_choice=None):
            self.n += 1
            usage = {"prompt_tokens": 0, "completion_tokens": 0}
            if self.n == 1:
                return ({"role": "assistant", "content": "",
                         "tool_calls": [{"id": "c1", "type": "function",
                                         "function": {"name": "read_file",
                                                      "arguments": "{not json"}}]}, usage)
            return ({"role": "assistant", "content": "giving up"}, usage)

    events, sink = traj
    result = loop.run(INSTANCE, BadJSON(), FakeContainer(), sink)
    assert result["stop_reason"] == "model_done"
    assert events[0]["tool_results"][0] == "[error] tool arguments are not valid JSON"
