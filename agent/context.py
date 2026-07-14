# 设计说明：上下文管理宁简勿繁。(1) 工具输出截中间留头尾——报错信息通常在开头
# (traceback头)或结尾(assert行)；(2) 消息滚动窗口永远保留 system+问题原文，正文只保留
# 最近K个assistant轮及其tool结果，截断边界落在assistant消息上（不能拆散tool_calls与
# 其结果，否则API直接报错）；(3) Round 2：滚出窗口的轮次不再一句话带过，压成备忘录——
# 保留模型自己的方案陈述和每次工具调用的一行记录(调用+结果首行)，只丢工具输出正文。
# 针对"截断失忆→重读已读文件→更不敢动手"（基线17/30例有此模式，见phase4_candidates.md候选2）。
import json

import config


def truncate_tool_output(text: str) -> str:
    if len(text) <= config.TOOL_OUT_MAX_CHARS:
        return text
    cut = len(text) - config.TOOL_OUT_HEAD - config.TOOL_OUT_TAIL
    return (text[:config.TOOL_OUT_HEAD]
            + f"\n...[omitted {cut} chars]...\n"
            + text[-config.TOOL_OUT_TAIL:])


def _one_line_call(call: dict) -> str:
    """一次工具调用压成一行：工具名+最能定位它的那个参数(路径/命令/正则)。"""
    try:
        args = json.loads(call["function"]["arguments"])
    except (json.JSONDecodeError, TypeError):
        args = {}
    key = args.get("path") or args.get("command") or args.get("pattern") or ""
    return f"{call['function']['name']}({str(key)[:120]})"


def _memo(old: list) -> str:
    """被滚出窗口的轮次→备忘录。每轮=方案陈述(模型自己的结论) + 每次调用一行
    `工具(参数) => 结果首行`（[ok]/[error]/[exit=N]都在首行）。工具输出正文全部丢弃。"""
    lines, turn, call_desc = [], 0, {}
    for m in old:
        if m["role"] == "assistant":
            turn += 1
            note = (m.get("content") or "").strip()
            if note:
                lines.append(f"turn {turn}: {note[:config.CTX_MEMO_NOTE_CHARS]}")
            for c in m.get("tool_calls") or []:
                call_desc[c["id"]] = _one_line_call(c)
        elif m["role"] == "tool":
            first = (m["content"] or "").strip().split("\n", 1)[0]
            lines.append(f"turn {turn}:   {call_desc.get(m.get('tool_call_id'), '?')}"
                         f" => {first[:config.CTX_MEMO_RESULT_CHARS]}")
    return "\n".join(lines)


def trim(messages: list) -> list:
    head, rest = messages[:2], messages[2:]   # [0]=system, [1]=问题原文
    starts = [i for i, m in enumerate(rest) if m["role"] == "assistant"]
    if len(starts) <= config.CTX_KEEP_TURNS:
        return messages
    cut_at = starts[-config.CTX_KEEP_TURNS]
    dropped = len(starts) - config.CTX_KEEP_TURNS
    memo = {"role": "user", "content": (
        f"[Context window: turns 1-{dropped} were compacted into the MEMO below — your own "
        "stated conclusions plus every tool call you made (full outputs are gone). Trust it: "
        "do NOT re-read files just to reconfirm what the MEMO already tells you; re-read only "
        "when you need exact text for an edit_file old_str.]\n" + _memo(rest[:cut_at]))}
    return head + [memo] + rest[cut_at:]
