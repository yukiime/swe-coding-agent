# 设计说明：Phase 4 备忘录机制的只读复算 + 事件级打标。零 LLM、零改动其他文件。
# (1) 复用 triage.signals() 在三套已归档轨迹上复算 refetch_after_trim 与官方 resolve；
# (2) McNemar(精确二项)检验 round1→round2 的 refetch 变化；
# (3) 事件级打标：一次 refetch 到底是"为写 old_str 取原文"还是"读了不动手"。
# refetch 判定复用 triage.signals() 里的同一段规则，不另造标准。
#
# 打标口径刻意做成三分类而不是良性/失忆二分：读完之后才改=取原文；之前改过、之后
# 没再改=事后回读，既非取原文也不等于失忆；全程没改过才是唯一能算"读了不动手"的桶。
# 二分类会把"第9轮被强制编辑 → 第14轮回读同一文件"全判成失忆，那是判定规则的产物、
# 不是证据。code_search 的 path 是目录不是文件，与 edit 路径无法对应，单列不进三分类。
# 用法: python -m analysis.recompute_refetch
import json
import math
import sys
from collections import Counter
from pathlib import Path

import config
from analysis.triage import _arg_path, signals

SETS = {  # 全部指向归档目录：logs/trajs 是工作目录，每跑一轮就被覆写
    "baseline": config.LOGS / "trajs_baseline",
    "round1": config.LOGS / "trajs_round1",
    "round2": config.LOGS / "trajs_round2",
}
K = config.CTX_KEEP_TURNS
MANIFEST = config.ROOT / "eval" / "instances_50.json"


def manifest_ids() -> list[str]:
    """只认清单里的50例：归档目录里混过 *.mock.bak.jsonl 之类的杂物，直接 glob
    会让 refetch 的分母(51)和官方 resolve 的分母(50)对不上，同一行两个口径。"""
    return sorted(r["instance_id"] for r in
                  json.loads(MANIFEST.read_text())["instances"])


def load_traj(f: Path) -> list[dict]:
    return [json.loads(l) for l in f.read_text().splitlines() if l.strip()]


def load_report(tag: str) -> dict:
    hits = list((config.LOGS / "reports").glob(f"*.{tag}.json"))
    if len(hits) != 1:
        sys.exit(f"expect exactly 1 report for tag={tag}, got {hits}")
    return json.loads(hits[0].read_text())


def refetch_events(traj: list[dict]) -> list[dict]:
    """每个 refetch 事件 {turn, name, path, kind}；kind 见文件头三分类说明。"""
    calls, edit_turns = [], []
    for ev in traj:
        if ev.get("event") != "turn":
            continue
        t = ev["turn"]
        for tc in ev["tool_calls"]:
            calls.append((t, tc["name"], tc["args"]))
            if tc["name"] == "edit_file":
                edit_turns.append((t, _arg_path(tc["args"])))

    events, seen = [], {}
    for t, name, args in calls:
        if name in ("read_file", "code_search"):
            key = (name, args)
            if key in seen and t - seen[key] >= K:
                events.append({"turn": t, "name": name, "path": _arg_path(args)})
            seen[key] = t

    for e in events:
        if e["name"] != "read_file" or not e["path"]:
            e["kind"] = "unlabelable"          # 目录级/路径解析失败，不参与三分类
            continue
        same = [et for et, ep in edit_turns if ep == e["path"]]
        if any(et > e["turn"] for et in same):
            e["kind"] = "edit_after"           # 重读后才改 → 取 old_str 原文
        elif same:
            e["kind"] = "edit_before_only"     # 只在此之前改过 → 事后回读/验证
        else:
            e["kind"] = "never_edited"         # 全程没改过 → 读了不动手
    return events


def chi2_sf_1df(x: float) -> float:
    return math.erfc(math.sqrt(x / 2.0))


def binom_two_sided(k: int, n: int) -> float:
    """精确二项双侧 p（H0: p=0.5），McNemar 小样本用。"""
    from math import comb
    if n == 0:
        return 1.0
    tail = sum(comb(n, i) for i in range(max(k, n - k), n + 1)) / 2 ** n
    return min(1.0, 2 * tail)


def load_sets() -> dict:
    ids = manifest_ids()
    data = {}
    for tag, d in SETS.items():
        if not d.is_dir():
            sys.exit(f"缺少归档轨迹目录 {d}；请先归档: cp -R {config.TRAJ_DIR} {d}")
        rf, evmap = {}, {}
        for iid in ids:
            f = d / f"{iid}.jsonl"
            if not f.exists():
                sys.exit(f"{tag}: 归档目录缺 {f.name}，分母会对不上，拒绝出数")
            traj = load_traj(f)
            rf[iid] = bool(signals(traj, [])["refetch_after_trim"])
            evmap[iid] = refetch_events(traj)
        rep = load_report(tag)
        data[tag] = {"rf": rf, "ev": evmap,
                     "resolved": set(rep.get("resolved_ids", [])), "rep": rep}
    return data


def main() -> None:
    data = load_sets()

    print("=" * 70, "\n三套轨迹：refetch_after_trim × resolve\n", "=" * 70, sep="")
    for tag in SETS:
        rf, rep = data[tag]["rf"], data[tag]["rep"]
        n, nref = len(rf), sum(rf.values())
        print(f"[{tag:8}] N={n}  refetch={nref}/{n}={nref / n * 100:.1f}%  "
              f"resolve(官方)={rep['resolved_instances']}/{rep['total_instances']}"
              f"={rep['resolved_instances'] / rep['total_instances'] * 100:.1f}%")

    r1, r2 = data["round1"]["rf"], data["round2"]["rf"]
    common = sorted(set(r1) & set(r2))
    b = sum(1 for i in common if r1[i] and not r2[i])
    c = sum(1 for i in common if not r1[i] and r2[i])
    if b + c == 0:
        print("\n配对 McNemar (round1→round2 refetch): 无翻转例(b=c=0)，不可检验")
    else:
        chi2cc = (abs(b - c) - 1) ** 2 / (b + c)
        print(f"\n配对 McNemar (round1→round2 refetch): b(降)={b} c(升)={c}  "
              f"χ²cc={chi2cc:.3f} p(χ²cc)≈{chi2_sf_1df(chi2cc):.3f}  "
              f"p(精确二项)≈{binom_two_sided(max(b, c), b + c):.3f}")

    print("\n" + "=" * 70, "\n事件级打标：round2 的 refetch 事件构成\n", "=" * 70, sep="")
    new_ref = [i for i in common if not r1[i] and r2[i]]
    for label, ids in [("round2 全部 refetch 实例", [i for i in r2 if r2[i]]),
                       ("round2 新增 refetch 实例(round1无→round2有)", new_ref)]:
        kinds, inst_never, res = Counter(), 0, 0
        for i in ids:
            evs = data["round2"]["ev"][i]
            kinds.update(e["kind"] for e in evs)
            inst_never += any(e["kind"] == "never_edited" for e in evs)
            res += i in data["round2"]["resolved"]
        print(f"\n[{label}]  n={len(ids)} 实例, {sum(kinds.values())} 个 refetch 事件")
        for kind, desc in (("edit_after", "重读后才改该文件(取 old_str 原文)"),
                           ("edit_before_only", "此前已改过、之后没再改(事后回读)"),
                           ("never_edited", "全程没改过该文件(读了不动手)"),
                           ("unlabelable", "code_search/路径缺失, 不可归类")):
            print(f"  {kind:<17}{kinds[kind]:>3} 事件  {desc}")
        print(f"  含 never_edited 事件的实例 = {inst_never}/{len(ids)}；"
              f"其中最终 resolve = {res}")
    print("\n[完成] 只读复算，未写入仓库其它文件。")


if __name__ == "__main__":
    main()
