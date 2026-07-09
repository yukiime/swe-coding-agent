# 设计说明：把三处落盘数据拼成每例一行的汇总csv——resolved只信官方report json，
# 轮数/终止原因/耗时来自轨迹end事件，token/费用来自cost.json（注意：那是该instance
# 的累计账，含历次重跑；这正是熔断的口径，所以如实照搬不拆分）。
# 用法: python -m eval.summarize <tag>   # 读 logs/predictions_<tag>.jsonl + reports/*.{tag}.json
import csv
import glob
import json
import sys
from collections import Counter

import config


def traj_end(iid: str) -> dict:
    """轨迹末行的end事件；缺轨迹/被截断则返回空dict（列留空，不编数字）。"""
    p = config.TRAJ_DIR / f"{iid}.jsonl"
    if not p.exists():
        return {}
    last = json.loads(p.read_text().splitlines()[-1])
    return last if last.get("event") == "end" else {}


def main() -> None:
    tag = sys.argv[1] if len(sys.argv) > 1 else sys.exit("usage: python -m eval.summarize <tag>")
    preds = [json.loads(l) for l in
             (config.LOGS / f"predictions_{tag}.jsonl").read_text().splitlines() if l.strip()]
    reports = glob.glob(str(config.LOGS / "reports" / f"*.{tag}.json"))
    if len(reports) != 1:
        sys.exit(f"expect exactly 1 report for tag={tag}, got {reports}")
    report = json.loads(open(reports[0]).read())
    resolved_ids = set(report.get("resolved_ids", []))
    error_ids = set(report.get("error_ids", []))
    costs = json.loads(config.COST_PATH.read_text())["instances"]

    rows = []
    for pred in preds:
        iid = pred["instance_id"]
        end, cost = traj_end(iid), costs.get(iid, {})
        rows.append({
            "instance_id": iid,
            "resolved": iid in resolved_ids,
            "judge_error": iid in error_ids,
            "stop_reason": end.get("stop_reason", ""),
            "turns": end.get("turns", ""),
            "patch_chars": len(pred.get("model_patch") or ""),
            "prompt_tokens": cost.get("prompt_tokens", ""),
            "completion_tokens": cost.get("completion_tokens", ""),
            "usd": round(cost["usd"], 4) if cost else "",
            "elapsed_s": end.get("ts", ""),
        })

    out = config.LOGS / f"summary_{tag}.csv"
    with out.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    n, nres = len(rows), sum(r["resolved"] for r in rows)
    usd = sum(r["usd"] or 0 for r in rows)
    turns = [r["turns"] for r in rows if r["turns"] != ""]
    print(f"{out}  ({n} rows)")
    print(f"resolved: {nres}/{n} = {nres / n:.0%}   total_usd(累计口径): ${usd:.4f}   "
          f"avg_turns: {sum(turns) / len(turns):.1f}")
    print(f"stop_reason: {dict(Counter(r['stop_reason'] for r in rows))}")
    print(f"resolved_by_stop: "
          f"{dict(Counter(r['stop_reason'] for r in rows if r['resolved']))}")


if __name__ == "__main__":
    main()
