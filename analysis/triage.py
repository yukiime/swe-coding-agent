# 设计说明：失败轨迹初分类，纯规则、零LLM调用——可复现、零API费、辅助人工归因而非替代。
# 主类别按"最具体的信号优先"裁决：有patch的看官方judge事实(应用失败/回归/没修对)，
# 空patch的看行为信号(是否读到gold文件/是否动过手/是否无视报错重复)。gold patch来自
# HF离线缓存，只用于归因对照，绝不参与判卷(判卷只认官方harness)。
# --render 把轨迹渲染成markdown供人工抽查：先轨迹后"答案钥匙"(gold patch)，方便对照。
import argparse
import csv
import json
import os
import re
import sys
from collections import Counter
from pathlib import Path

import config

RENDER_DIR = config.LOGS / "renders"
TRIAGE_DIR = config.LOGS / "triage"

# 类别：PLAN五类起步 + 按数据实况建议的新类别(标*)。key进csv，zh用于展示。
CATS = {
    "wrong_location":         "定位失败：20轮从未读到gold patch要改的文件",
    "located_but_no_edit":    "*读到了该改的文件但从未尝试edit（探索瘫痪细分）",
    "edit_failure_loop":      "*反复尝试edit但old_str始终匹配失败，patch为空",
    "ignored_error_or_repeat": "未读报错就放弃或重复同样修改",
    "broke_other_tests":      "patch破坏其他测试（PASS_TO_PASS回归）",
    "wrong_fix":              "*patch可应用且无回归，但没修复目标测试",
    "patch_apply_failed":     "*patch无法被harness应用",
    "context_loss":           "上下文截断导致信息丢失（规则仅置旗标，主因需人工确认）",
    "budget_exhausted":       "纯轮数/成本耗尽（行为正常但没做完）",
    "other":                  "其他",
}


# ---------- 数据装载 ----------

def load_dataset_map(ids: set[str]) -> dict[str, dict]:
    """HF离线缓存 -> {iid: 数据集行}。不可用时降级：定位规则失效、渲染缺问题原文。"""
    os.environ.setdefault("HF_DATASETS_OFFLINE", "1")
    try:
        from datasets import load_dataset
        ds = load_dataset(config.DATASET, split="test")
    except Exception as e:
        print(f"[warn] HF数据集不可用({type(e).__name__}); 定位类规则与问题原文降级", file=sys.stderr)
        return {}
    return {r["instance_id"]: r for r in ds if r["instance_id"] in ids}


def gold_files(row: dict | None) -> list[str]:
    if not row:
        return []
    return re.findall(r"^diff --git a/\S+ b/(\S+)", row["patch"], re.M)


def traj_dir(tag: str) -> Path:
    """归档目录 logs/trajs_<tag>。找不到就退出，绝不回退到工作目录 logs/trajs：
    runner 每跑一次都覆写 logs/trajs，静默回退会拿最新一轮的轨迹去归因旧 tag，
    错得毫无痕迹。跑完一轮请先 `cp -R logs/trajs logs/trajs_<tag>` 归档。"""
    d = config.LOGS / f"trajs_{tag}"
    if not d.is_dir():
        sys.exit(f"缺少归档轨迹目录 {d}；请先归档: cp -R {config.TRAJ_DIR} {d}")
    return d


def load_traj(iid: str, tag: str) -> list[dict]:
    p = traj_dir(tag) / f"{iid}.jsonl"
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()]


def load_preds(tag: str) -> dict[str, str]:
    p = config.LOGS / f"predictions_{tag}.jsonl"
    return {r["instance_id"]: r.get("model_patch") or ""
            for r in map(json.loads, p.read_text().splitlines())}


def load_report(tag: str) -> dict:
    hits = list((config.LOGS / "reports").glob(f"*.{tag}.json"))
    if len(hits) != 1:
        sys.exit(f"expect exactly 1 report for tag={tag}, got {hits}")
    return json.loads(hits[0].read_text())


def inst_report(tag: str, iid: str) -> dict | None:
    """官方harness的per-instance report（只有非空patch被判卷的才有）。"""
    hits = list((config.LOGS / "run_evaluation" / tag).glob(f"*/{iid}/report.json"))
    if not hits:
        return None
    return json.loads(hits[0].read_text())[iid]


# ---------- 行为信号提取 ----------

def _relpath(p: str) -> str:
    p = p.strip()
    return p[len(config.TESTBED) + 1:] if p.startswith(config.TESTBED + "/") else p


def _arg_path(args_str: str) -> str:
    """args被轨迹截断到300字符，JSON可能不完整——先json后regex双保险。"""
    try:
        return _relpath(json.loads(args_str).get("path", ""))
    except (json.JSONDecodeError, AttributeError):
        m = re.search(r'"path"\s*:\s*"([^"]*)"', args_str)
        return _relpath(m.group(1)) if m else ""


def signals(traj: list[dict], gold: list[str]) -> dict:
    """一次遍历轨迹，抽出分类所需的全部行为信号。"""
    reads, edits, calls = [], [], []          # (turn,path) / (turn,path,args,ok) / (turn,name,args)
    n_search = n_tests = tests_failed = 0
    read_gold_turn = saw_gold_turn = 0
    for ev in traj:
        if ev.get("event") != "turn":
            continue
        t = ev["turn"]
        blob = " ".join(ev["tool_results"])
        for i, tc in enumerate(ev["tool_calls"]):
            name, args = tc["name"], tc["args"]
            res = ev["tool_results"][i] if i < len(ev["tool_results"]) else ""
            calls.append((t, name, args))
            path = _arg_path(args)
            if name == "read_file":
                reads.append((t, path))
                if not read_gold_turn and path in gold:
                    read_gold_turn = t
            elif name == "edit_file":
                edits.append((t, path, args, res.startswith("[ok]")))
                if not read_gold_turn and path in gold:
                    read_gold_turn = t
            elif name == "code_search":
                n_search += 1
            elif name == "run_tests":
                n_tests += 1
                m = re.search(r"\[exit=(-?\d+)", res)
                tests_failed += bool(m and m.group(1) != "0")
        if not saw_gold_turn and any(g in blob or g in json.dumps([c["args"] for c in ev["tool_calls"]]) for g in gold):
            saw_gold_turn = t

    # 顽固重复：同一edit args失败>=2次，且两次之间没有re-read同一文件
    stubborn = False
    by_args = Counter(a for _, _, a, ok in edits if not ok)
    for args_str, n in by_args.items():
        if n < 2:
            continue
        ts = [t for t, _, a, ok in edits if a == args_str and not ok]
        path = _arg_path(args_str)
        reread = any(ts[0] < rt < ts[-1] and rp == path for rt, rp in reads)
        if not reread:
            stubborn = True
    # 截断后重取：同一read/search在相隔>=CTX_KEEP_TURNS轮后原样重发（早先结果已被滚出窗口）
    refetch = False
    seen: dict[tuple, int] = {}
    for t, name, args in calls:
        if name in ("read_file", "code_search"):
            key = (name, args)
            if key in seen and t - seen[key] >= config.CTX_KEEP_TURNS:
                refetch = True
            seen[key] = t
    rep = Counter((n, a) for _, n, a in calls)
    return dict(
        n_search=n_search, n_read=len(reads), n_read_files=len({p for _, p in reads}),
        n_edit_ok=sum(ok for *_, ok in edits), n_edit_err=sum(not ok for *_, ok in edits),
        n_tests=n_tests, tests_failed=tests_failed,
        read_gold_turn=read_gold_turn, saw_gold_turn=saw_gold_turn,
        stubborn_edit=stubborn, refetch_after_trim=refetch,
        max_repeat=max(rep.values()) if rep else 0,
    )


# ---------- 主类别裁决 ----------

def classify(sig: dict, patch_chars: int, judged: dict | None, has_gold: bool) -> tuple[str, str]:
    """返回(类别key, 一句话证据)。有patch信官方judge事实，空patch看行为信号。"""
    if patch_chars > 0 and judged:
        # tests_status[*]["success"/"failure"] 是测试名列表, 不是计数, 取len才是例数
        p2p_fail = len(judged["tests_status"]["PASS_TO_PASS"]["failure"])
        f2p_fail = len(judged["tests_status"]["FAIL_TO_PASS"]["failure"])
        if not judged["patch_successfully_applied"]:
            return "patch_apply_failed", "harness: patch应用失败"
        if p2p_fail:
            return "broke_other_tests", f"harness: 破坏{p2p_fail}个已过测试, 目标测试fail={f2p_fail}"
        return "wrong_fix", f"harness: patch已应用无回归, 但目标测试仍fail={f2p_fail}"
    if patch_chars > 0:
        return "other", "有patch但找不到per-instance judge报告"
    # ---- 空patch ----
    n_edit = sig["n_edit_ok"] + sig["n_edit_err"]
    if n_edit == 0:
        if sig["read_gold_turn"]:
            return "located_but_no_edit", f"第{sig['read_gold_turn']}轮已读到gold文件, 但20轮0次edit"
        if not has_gold:
            return "other", "无gold对照(数据集不可用), 0次edit"
        hint = f"(搜索结果第{sig['saw_gold_turn']}轮出现过gold路径但未读)" if sig["saw_gold_turn"] else ""
        return "wrong_location", f"0次edit, 从未读到gold文件{hint}"
    if sig["stubborn_edit"]:
        return "ignored_error_or_repeat", "同一old_str失败后未重读文件即原样重试"
    if sig["n_edit_ok"] == 0:
        return "edit_failure_loop", f"{sig['n_edit_err']}次edit全部失败(old_str不匹配), 有调整但没成功"
    return "other", (f"{sig['n_edit_ok']}次edit成功但git diff为空"
                     "(疑似新建文件: git diff不含untracked文件, loop.py导出丢patch)")


# ---------- 归因主流程 ----------

def cmd_triage(tag: str) -> None:
    report = load_report(tag)
    preds = load_preds(tag)
    unresolved = [i for i in report["submitted_ids"] if i not in set(report["resolved_ids"])]
    data = load_dataset_map(set(unresolved))

    rows = []
    for iid in unresolved:
        gold = gold_files(data.get(iid))
        sig = signals(load_traj(iid, tag), gold)
        cat, why = classify(sig, len(preds[iid]), inst_report(tag, iid), bool(gold))
        rows.append({"instance_id": iid, "category": cat, "patch_chars": len(preds[iid]),
                     "evidence": why, **sig, "gold_files": ";".join(gold)})

    TRIAGE_DIR.mkdir(parents=True, exist_ok=True)
    out = TRIAGE_DIR / f"triage_{tag}.csv"
    with out.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    print(f"{out}  ({len(rows)} rows, 未resolved例)\n")
    print(f"{'类别':<26}{'数量':>4}  典型案例")
    by_cat = Counter(r["category"] for r in rows)
    for cat, n in by_cat.most_common():
        ex = [r["instance_id"] for r in rows if r["category"] == cat][:3]
        print(f"{cat:<26}{n:>4}  {', '.join(ex)}")
    print(f"\n旗标: refetch_after_trim(上下文截断后重取)="
          f"{sum(r['refetch_after_trim'] for r in rows)}例  "
          f"tests从未运行={sum(r['n_tests'] == 0 for r in rows)}例")
    print("\n类别说明:")
    for k, v in CATS.items():
        if k in by_cat:
            print(f"  {k}: {v}")


# ---------- 轨迹渲染 ----------

def _fence(text: str, lang: str = "") -> str:
    return f"````{lang}\n{text.rstrip()}\n````"


def render_one(iid: str, tag: str, preds: dict, data: dict, resolved_ids: set) -> Path:
    traj = load_traj(iid, tag)
    end = traj[-1] if traj[-1].get("event") == "end" else {}
    row = data.get(iid)
    judged = inst_report(tag, iid)
    status = "✅ resolved" if iid in resolved_ids else "❌ unresolved"

    md = [f"# {iid}  ({status})\n",
          f"- run: `{tag}`  停止: `{end.get('stop_reason')}`  轮数: {end.get('turns')}  "
          f"patch: {end.get('patch_chars')} chars"]
    if judged:
        ts = judged["tests_status"]
        md.append(f"- harness: applied={judged['patch_successfully_applied']}  "
                  f"FAIL_TO_PASS {len(ts['FAIL_TO_PASS']['success'])}✓/"
                  f"{len(ts['FAIL_TO_PASS']['failure'])}✗  "
                  f"PASS_TO_PASS {len(ts['PASS_TO_PASS']['success'])}✓/"
                  f"{len(ts['PASS_TO_PASS']['failure'])}✗")
    md.append("\n## 问题原文 (issue)\n")
    md.append(_fence(row["problem_statement"]) if row else "_(HF数据集不可用，缺问题原文)_")
    md.append("\n## 轨迹\n")
    for ev in traj:
        if ev.get("event") == "cost_limit":
            md.append(f"**💸 第{ev['turn']}轮触发成本熔断**: {ev.get('detail', '')}\n")
        if ev.get("event") != "turn":
            continue
        u = ev.get("usage", {})
        md.append(f"### 第 {ev['turn']} 轮  "
                  f"(prompt {u.get('prompt_tokens', 0) / 1000:.1f}k tok, {ev.get('elapsed_s', '?')}s)\n")
        if ev["content"]:
            md.append(f"**模型说**: {ev['content']}\n")
        for i, tc in enumerate(ev["tool_calls"]):
            res = ev["tool_results"][i] if i < len(ev["tool_results"]) else ""
            md.append(f"🔧 **{tc['name']}** `{tc['args']}`\n")
            md.append(_fence(res) + "\n")
        if not ev["tool_calls"]:
            md.append("_(未调用工具 → 会话结束 model_done)_\n")
    md.append("\n---\n\n## 最终patch (模型产出)\n")
    md.append(_fence(preds.get(iid) or "(空)", "diff"))
    md.append("\n## 🔑 答案钥匙：gold patch（人工归因对照用；建议先读完轨迹再看）\n")
    md.append(_fence(row["patch"], "diff") if row else "_(HF数据集不可用)_")

    out_dir = RENDER_DIR / tag
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{iid}.md"
    out.write_text("\n".join(md))
    return out


def cmd_render(tag: str, iid: str | None, render_all: bool) -> None:
    report = load_report(tag)
    preds = load_preds(tag)
    ids = list(preds) if render_all else [iid]
    data = load_dataset_map(set(ids))
    for i in ids:
        print(render_one(i, tag, preds, data, set(report["resolved_ids"])))


def main() -> None:
    ap = argparse.ArgumentParser(description="失败轨迹初分类 + 轨迹渲染(人工抽查视图)")
    ap.add_argument("--tag", default="baseline")
    ap.add_argument("--render", metavar="INSTANCE_ID", help="渲染单个instance为markdown")
    ap.add_argument("--render-all", action="store_true", help="渲染该tag下全部instance")
    a = ap.parse_args()
    if a.render or a.render_all:
        cmd_render(a.tag, a.render, a.render_all)
    else:
        cmd_triage(a.tag)


if __name__ == "__main__":
    main()
