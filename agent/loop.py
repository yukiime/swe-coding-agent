# 设计说明：ReAct主循环。终止条件只有三个且全部显式记录在轨迹里：模型不再调用工具
# (自认完成) / 轮数上限 / 成本熔断。无论哪种终止都导出 git diff——半成品patch也是
# Phase 3 失败归因的证据，不白跑。
import json
import time

import config
from agent import context, tools
from agent.llm import CostLimitExceeded

SYSTEM_PROMPT = (config.ROOT / "agent" / "system_prompt.txt").read_text()


def run(instance: dict, llm, ctr: tools.Container, traj) -> dict:
    """跑一个instance的完整agent会话。traj(dict)用于逐事件写轨迹。"""
    msgs = [{"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": instance["problem_statement"]}]
    stop, turns = "max_turns", 0
    for turn in range(1, config.MAX_TURNS + 1):
        turns = turn
        t0 = time.time()
        try:
            msg, usage = llm.chat(context.trim(msgs), tools.TOOLS)
        except CostLimitExceeded as e:
            stop = "cost_limit"
            traj({"event": "cost_limit", "turn": turn, "detail": str(e)})
            break
        msgs.append(msg)
        record = {"event": "turn", "turn": turn, "content": (msg["content"] or "")[:500],
                  "tool_calls": [], "tool_results": [], "usage": usage}
        for call in msg.get("tool_calls") or []:
            fn = call["function"]
            try:
                args = json.loads(fn["arguments"])
            except json.JSONDecodeError:
                args, out = None, "[error] tool arguments are not valid JSON"
            if args is not None:
                out = context.truncate_tool_output(tools.run_tool(ctr, fn["name"], args))
            msgs.append({"role": "tool", "tool_call_id": call["id"], "content": out})
            record["tool_calls"].append({"name": fn["name"], "args": fn["arguments"][:300]})
            record["tool_results"].append(out[:500])
        record["elapsed_s"] = round(time.time() - t0, 1)
        traj(record)
        if not msg.get("tool_calls"):
            stop = "model_done"
            break
    _, patch, _ = ctr.exec("git diff")
    traj({"event": "end", "stop_reason": stop, "turns": turns, "patch_chars": len(patch)})
    return {"stop_reason": stop, "turns": turns, "patch": patch}
