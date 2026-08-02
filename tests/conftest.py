# 设计说明：测试只替换"外部世界"（容器、LLM、产物目录），不替换任何被测逻辑。
# FakeContainer 不模拟 grep/cat 的语义——那等于把实现写两遍、真假一起错；它只回放
# 脚本化的退出码与输出，让断言落在"我们的分支选择"和"我们拼的命令串"上。
import os

os.environ["SWE_PROFILE"] = "deepseek"   # 必须在 import config 之前：档案在导入期求值

import pytest

import config
from agent.llm import MockLLM


@pytest.fixture(autouse=True)
def _sandbox_logs(tmp_path, monkeypatch):
    """把一切会落盘的路径改指 tmp_path，杜绝测试写坏真实的 logs/cost.json。"""
    monkeypatch.setattr(config, "LOGS", tmp_path)
    monkeypatch.setattr(config, "COST_PATH", tmp_path / "cost.json")
    monkeypatch.setattr(config, "TRAJ_DIR", tmp_path / "trajs")
    monkeypatch.setattr(config, "PRED_DIR", tmp_path / "preds")


class FakeContainer:
    """按脚本回放 exec 结果，并记录收到的命令串与 stdin。脚本耗尽后返回 (0, "", "")。"""

    def __init__(self, responses=None):
        self.responses = list(responses or [])
        self.calls = []

    def exec(self, cmd, timeout=None, stdin=None):
        self.calls.append((cmd, stdin))
        return self.responses.pop(0) if self.responses else (0, "", "")


class ScriptedLLM(MockLLM):
    """MockLLM + 记录每次调用收到的 messages 与 tool_choice（用于验证强制编辑）。"""

    def __init__(self, script):
        super().__init__(script)
        self.seen = []

    def chat(self, messages, tools, tool_choice=None):
        self.seen.append({"messages": messages, "tool_choice": tool_choice})
        return super().chat(messages, tools, tool_choice)


@pytest.fixture
def ctr():
    return FakeContainer()
