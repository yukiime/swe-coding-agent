# 设计说明：唯一接触LLM网络的模块，做三件事：带指数退避的调用、token→美元记账并
# 原子落盘（进程重启不清零）、两级成本熔断（单例$0.40/全项目$40）超限抛异常。
# MockLLM 与 LLM 同接口，回放写死的剧本，让四工具+主循环可以零API费联调。
import fcntl
import json
import os
import time

from openai import (APIConnectionError, APITimeoutError, InternalServerError,
                    OpenAI, RateLimitError)

import config


class CostLimitExceeded(Exception):
    pass


RETRIABLE = (RateLimitError, APITimeoutError, APIConnectionError, InternalServerError)


class CostLedger:
    """logs/cost.json: {"total_usd", "instances": {iid: {usd, prompt_tokens, completion_tokens, calls}}}

    批量并发时多个run_one进程共写同一账本：记账在文件独占锁内"重读→累加→原子写回"，
    否则各进程的内存副本互相覆盖会把账记少——记少=总额熔断可能被绕过。"""

    def __init__(self, path=config.COST_PATH):
        self.path = path
        self.data = self._load()

    def _load(self) -> dict:
        return (json.loads(self.path.read_text()) if self.path.exists()
                else {"total_usd": 0.0, "instances": {}})

    def add(self, iid: str, prompt_tokens: int, completion_tokens: int) -> float:
        usd = (prompt_tokens * config.PRICE_IN_PER_M
               + completion_tokens * config.PRICE_OUT_PER_M) / 1e6
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path.with_suffix(".lock"), "w") as lockf:
            fcntl.flock(lockf, fcntl.LOCK_EX)
            self.data = self._load()  # 锁内重读：别的进程可能刚记过账，内存副本已过期
            inst = self.data["instances"].setdefault(
                iid, {"usd": 0.0, "prompt_tokens": 0, "completion_tokens": 0, "calls": 0})
            inst["usd"] += usd
            inst["prompt_tokens"] += prompt_tokens
            inst["completion_tokens"] += completion_tokens
            inst["calls"] += 1
            self.data["total_usd"] += usd
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data, indent=2))
            tmp.replace(self.path)  # 先记账再判限：熔断时这笔钱已经花了，必须入账
        if inst["usd"] > config.COST_LIMIT_INSTANCE_USD:
            raise CostLimitExceeded(
                f"instance {iid} spent ${inst['usd']:.4f} > ${config.COST_LIMIT_INSTANCE_USD}")
        if self.data["total_usd"] > config.COST_LIMIT_TOTAL_USD:
            raise CostLimitExceeded(
                f"project total ${self.data['total_usd']:.2f} > ${config.COST_LIMIT_TOTAL_USD}")
        return usd


class LLM:
    def __init__(self, instance_id: str, ledger: CostLedger | None = None):
        self.iid = instance_id
        self.ledger = ledger or CostLedger()
        self.client = OpenAI(api_key=os.environ[config.API_KEY_ENV],
                             base_url=config.BASE_URL, timeout=config.LLM_TIMEOUT_S)

    def chat(self, messages: list, tools: list) -> tuple[dict, dict]:
        """返回 (标准化的assistant消息dict, usage dict)。重试仅针对网络/限流类错误。"""
        for attempt in range(config.LLM_RETRIES):
            try:
                resp = self.client.chat.completions.create(
                    model=config.MODEL, messages=messages, tools=tools,
                    temperature=config.TEMPERATURE,
                    max_tokens=config.MAX_COMPLETION_TOKENS)
                break
            except RETRIABLE:
                if attempt == config.LLM_RETRIES - 1:
                    raise
                time.sleep(2 ** attempt)
        m = resp.choices[0].message
        usage = {"prompt_tokens": resp.usage.prompt_tokens,
                 "completion_tokens": resp.usage.completion_tokens}
        usage["usd"] = self.ledger.add(self.iid, **usage)
        msg: dict = {"role": "assistant", "content": m.content or ""}
        if m.tool_calls:
            msg["tool_calls"] = [
                {"id": c.id, "type": "function",
                 "function": {"name": c.function.name, "arguments": c.function.arguments}}
                for c in m.tool_calls]
        return msg, usage


class MockLLM:
    """剧本 = [(content, [(tool_name, args_dict), ...]), ...]，每次chat回放一条。"""

    def __init__(self, script: list):
        self.script = list(script)
        self.i = 0

    def chat(self, messages: list, tools: list) -> tuple[dict, dict]:
        content, calls = self.script[self.i]
        self.i += 1
        msg: dict = {"role": "assistant", "content": content}
        if calls:
            msg["tool_calls"] = [
                {"id": f"mock-{self.i}-{j}", "type": "function",
                 "function": {"name": name, "arguments": json.dumps(args)}}
                for j, (name, args) in enumerate(calls)]
        return msg, {"prompt_tokens": 0, "completion_tokens": 0, "usd": 0.0}
