import json

import pytest

import config
from agent.llm import CostLedger, CostLimitExceeded, MockLLM


def test_default_path_follows_config(tmp_path):
    """path 若在导入期定死，测试沙箱与将来换路径都改不动它。"""
    assert CostLedger().path == tmp_path / "cost.json"


def test_add_prices_tokens_with_the_active_profile_rates(tmp_path, monkeypatch):
    # 各 100 万 token 恰好 $0.70，越过 $0.40 的单例熔断线；这里只想验单价，先把线抬开
    monkeypatch.setattr(config, "COST_LIMIT_INSTANCE_USD", 100.0)
    led = CostLedger(tmp_path / "c.json")
    usd = led.add("iid", prompt_tokens=1_000_000, completion_tokens=1_000_000)
    assert usd == pytest.approx(0.28 + 0.42)


def test_add_persists_atomically_and_accumulates(tmp_path):
    p = tmp_path / "c.json"
    led = CostLedger(p)
    led.add("iid", 1000, 1000)
    led.add("iid", 1000, 1000)
    data = json.loads(p.read_text())
    assert data["instances"]["iid"]["calls"] == 2
    assert data["instances"]["iid"]["prompt_tokens"] == 2000
    assert data["total_usd"] == pytest.approx(2 * (1000 * 0.28 + 1000 * 0.42) / 1e6)
    assert not (tmp_path / "c.tmp").exists()      # 临时文件已 replace 掉


def test_two_ledgers_sharing_a_file_sum_instead_of_clobbering(tmp_path):
    """并发跑批时每个 run_one 进程各有一份内存副本；不在锁内重读就会互相覆盖，
    账记少 = 总额熔断被绕过。"""
    p = tmp_path / "c.json"
    a, b = CostLedger(p), CostLedger(p)
    a.add("i1", 1000, 0)
    b.add("i2", 1000, 0)
    data = json.loads(p.read_text())
    assert set(data["instances"]) == {"i1", "i2"}
    assert data["total_usd"] == pytest.approx(2 * 1000 * 0.28 / 1e6)


def test_instance_limit_raises_but_records_the_spend_first(tmp_path, monkeypatch):
    """熔断时这笔钱已经花掉了，必须先入账再抛，否则账本比真实支出少一笔。"""
    monkeypatch.setattr(config, "COST_LIMIT_INSTANCE_USD", 0.01)
    p = tmp_path / "c.json"
    with pytest.raises(CostLimitExceeded, match="instance iid"):
        CostLedger(p).add("iid", 1_000_000, 0)
    assert json.loads(p.read_text())["instances"]["iid"]["usd"] == pytest.approx(0.28)


def test_total_limit_raises_even_when_each_instance_is_cheap(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "COST_LIMIT_INSTANCE_USD", 100.0)
    monkeypatch.setattr(config, "COST_LIMIT_TOTAL_USD", 0.5)
    led = CostLedger(tmp_path / "c.json")
    led.add("i1", 1_000_000, 0)
    with pytest.raises(CostLimitExceeded, match="project total"):
        led.add("i2", 1_000_000, 0)


def test_mock_llm_replays_script_in_openai_shape():
    llm = MockLLM([("thinking", [("read_file", {"path": "a.py"})]), ("done", [])])
    msg, usage = llm.chat([], [])
    assert msg["role"] == "assistant" and msg["content"] == "thinking"
    assert msg["tool_calls"][0]["function"]["name"] == "read_file"
    assert json.loads(msg["tool_calls"][0]["function"]["arguments"]) == {"path": "a.py"}
    assert usage == {"prompt_tokens": 0, "completion_tokens": 0, "usd": 0.0}
    last, _ = llm.chat([], [])
    assert "tool_calls" not in last
