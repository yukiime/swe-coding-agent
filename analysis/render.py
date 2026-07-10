# 设计说明：把一条轨迹jsonl渲染成人工归因用的markdown——除了逐轮对话，还并排放
# 问题描述/官方gold patch/我们的patch，因为"定位错文件"这类判断必须对着正确答案看。
# 只做展示不做判断。用法: python -m analysis.render <instance_id> [tag]
import json
import sys

from datasets import load_dataset

import config


def load_row(iid: str) -> dict:
    ds = load_dataset(config.DATASET, split="test")
    row = next((r for r in ds if r["instance_id"] == iid), None)
    return row or sys.exit(f"{iid} not in {config.DATASET}")


def verdict(iid: str, tag: str) -> str:
    reports = list((config.LOGS / "reports").glob(f"*.{tag}.json"))
    if not reports:
        return "（无判卷报告）"
    d = json.loads(reports[0].read_text())
    if iid in d.get("resolved_ids", []):
        return "✅ resolved"
    if iid in d.get("error_ids", []):
        return "⚠️ judge error"
    return "❌ unresolved"


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit("usage: python -m analysis.render <instance_id> [tag=baseline]")
    iid, tag = sys.argv[1], (sys.argv[2] if len(sys.argv) > 2 else "baseline")
    row = load_row(iid)
    pred_path = config.PRED_DIR / f"{iid}.jsonl"
    patch = json.loads(pred_path.read_text())["model_patch"] if pred_path.exists() else ""

    out = [f"# {iid} — {verdict(iid, tag)}\n"]
    out.append(f"## 问题描述\n\n{row['problem_statement']}\n")
    out.append(f"## 官方gold patch（正确答案，归因对照用）\n\n```diff\n{row['patch']}\n```\n")
    out.append(f"## 我们的patch\n\n```diff\n{patch or '（空）'}\n```\n")
    out.append("## 逐轮轨迹\n")

    for line in (config.TRAJ_DIR / f"{iid}.jsonl").read_text().splitlines():
        e = json.loads(line)
        if e["event"] == "start":
            out.append(f"- 模型: `{e['model']}`\n")
        elif e["event"] == "turn":
            u = e.get("usage", {})
            out.append(f"### Turn {e['turn']}  "
                       f"(prompt {u.get('prompt_tokens', '?')} tok, {e.get('elapsed_s', '?')}s)\n")
            if e["content"]:
                out.append(f"{e['content']}\n")
            for call, result in zip(e["tool_calls"], e["tool_results"]):
                out.append(f"**→ {call['name']}** `{call['args']}`\n")
                out.append(f"```\n{result}\n```\n")
        elif e["event"] == "end":
            out.append(f"### 终止：{e['stop_reason']}，{e['turns']}轮，patch {e['patch_chars']}B\n")
        elif e["event"] == "cost_limit":
            out.append(f"### ⚠️ 成本熔断 @turn{e['turn']}: {e['detail']}\n")

    view_dir = config.LOGS / "views"
    view_dir.mkdir(parents=True, exist_ok=True)
    path = view_dir / f"{iid}.md"
    path.write_text("\n".join(out))
    print(path)


if __name__ == "__main__":
    main()
