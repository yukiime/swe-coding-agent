# 设计说明：上下文管理宁简勿繁。(1) 工具输出截中间留头尾——报错信息通常在开头
# (traceback头)或结尾(assert行)；(2) 消息滚动窗口永远保留 system+问题原文，正文只保留
# 最近K个assistant轮及其tool结果，截断边界落在assistant消息上（不能拆散tool_calls与
# 其结果，否则API直接报错），并插一条占位消息告知模型历史被省略。
import config


def truncate_tool_output(text: str) -> str:
    if len(text) <= config.TOOL_OUT_MAX_CHARS:
        return text
    cut = len(text) - config.TOOL_OUT_HEAD - config.TOOL_OUT_TAIL
    return (text[:config.TOOL_OUT_HEAD]
            + f"\n...[omitted {cut} chars]...\n"
            + text[-config.TOOL_OUT_TAIL:])


def trim(messages: list) -> list:
    head, rest = messages[:2], messages[2:]   # [0]=system, [1]=问题原文
    starts = [i for i, m in enumerate(rest) if m["role"] == "assistant"]
    if len(starts) <= config.CTX_KEEP_TURNS:
        return messages
    cut_at = starts[-config.CTX_KEEP_TURNS]
    marker = {"role": "user",
              "content": f"[{cut_at} earlier messages omitted to fit the context window]"}
    return head + [marker] + rest[cut_at:]
