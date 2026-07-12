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
    stop, turns, last_edit_turn = "max_turns", 0, 0
    for turn in range(1, config.MAX_TURNS + 1):
        turns = turn
        t0 = time.time()
        # 行动强制三级递进：轮数横幅（常开）→ 连续只读时的文字提醒 → 到期仍0次edit则
        # tool_choice机械强制（冒烟实证：文字提醒会被无视）。横幅是临时消息不进msgs，
        # 既不随历史累积占上下文，也保证模型看到的永远是当前轮的数字。
        banner = f"[turn {turn}/{config.MAX_TURNS}]"
        forced = last_edit_turn == 0 and turn >= config.FORCE_FIRST_EDIT_TURN
        nudged = not forced and turn - last_edit_turn > config.WATCHDOG_NO_EDIT_TURNS
        if forced:
            banner += (" You have made ZERO edits and the deadline has passed: edit_file is"
                       " mandatory THIS turn. Apply the smallest fix consistent with what you"
                       " have read so far; you can refine it in later turns.")
        elif nudged:
            banner += (f" ACTION REQUIRED: no edit_file call in the last {turn - 1 - last_edit_turn}"
                       " turns. If you know the fix, apply it NOW with the smallest edit_file change;"
                       " if a previous edit failed, re-read just enough to retry. Do not spend this"
                       " turn only reading or searching.")
        try:
            msg, usage = llm.chat(context.trim(msgs) + [{"role": "user", "content": banner}],
                                  tools.TOOLS,
                                  tool_choice={"type": "function", "function": {"name": "edit_file"}}
                                  if forced else None)
        except CostLimitExceeded as e:
            stop = "cost_limit"
            traj({"event": "cost_limit", "turn": turn, "detail": str(e)})
            break
        msgs.append(msg)
        record = {"event": "turn", "turn": turn, "content": (msg["content"] or "")[:500],
                  "tool_calls": [], "tool_results": [], "usage": usage}
        if nudged:
            record["nudged"] = True
        if forced:
            record["forced_edit"] = True
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
        if any(c["name"] == "edit_file" for c in record["tool_calls"]):
            last_edit_turn = turn  # 计"尝试过edit"而非"edit成功"：在试错的模型不该被催
        if not msg.get("tool_calls"):
            stop = "model_done"
            break
    # add -A：模型新建的文件是untracked，裸git diff看不见——astropy-13398曾因此丢整个修复
    _, patch, _ = ctr.exec("git add -A && git diff --cached")
    traj({"event": "end", "stop_reason": stop, "turns": turns, "patch_chars": len(patch)})
    return {"stop_reason": stop, "turns": turns, "patch": patch}
