# 设计说明：从 SWE-bench Verified(500例) 按 repo 分层比例抽样50例，seed固定=42，
# 结果固化到 eval/instances_50.json（无时间戳，重跑必须逐字节一致）。
# 配额用"最大余数法"分配保证总数恰好50；冒烟5例取样本中配额最大的5个repo各1例，
# 且优先避开 harness 标记为必须x86的instance（USE_X86），以保留arm64本地构建的可能性。
# 只固化 instance_id/repo，完整字段评测时由 harness 自行从HF数据集读取，避免仓库存大文件。
import json
import random
from collections import Counter
from math import floor
from pathlib import Path

from datasets import load_dataset
from swebench.harness.constants import USE_X86

DATASET = "princeton-nlp/SWE-bench_Verified"
SEED = 42
N_TOTAL = 50
N_SMOKE = 5
OUT_PATH = Path(__file__).parent / "instances_50.json"


def stratified_sample() -> dict:
    ds = load_dataset(DATASET, split="test")
    by_repo: dict[str, list[str]] = {}
    for row in ds:
        by_repo.setdefault(row["repo"], []).append(row["instance_id"])
    counts = Counter({repo: len(ids) for repo, ids in by_repo.items()})
    total = sum(counts.values())

    # 最大余数法：先给floor配额，再按小数部分从大到小补齐到50
    quotas: dict[str, int] = {}
    remainders: list[tuple[float, str]] = []
    for repo in sorted(counts):
        exact = N_TOTAL * counts[repo] / total
        quotas[repo] = floor(exact)
        remainders.append((exact - floor(exact), repo))
    shortfall = N_TOTAL - sum(quotas.values())
    for _, repo in sorted(remainders, key=lambda x: (-x[0], x[1]))[:shortfall]:
        quotas[repo] += 1

    rng = random.Random(SEED)
    sampled: list[dict] = []
    for repo in sorted(quotas):
        picked = rng.sample(sorted(by_repo[repo]), quotas[repo])
        sampled.extend({"instance_id": iid, "repo": repo} for iid in sorted(picked))

    # 冒烟子集：配额最大的5个repo（并列按字母序），每repo取首个非强制x86的样本
    top_repos = sorted(quotas, key=lambda r: (-quotas[r], r))[:N_SMOKE]
    smoke: list[str] = []
    for repo in top_repos:
        ids = [s["instance_id"] for s in sampled if s["repo"] == repo]
        smoke.append(next((i for i in ids if i not in USE_X86), ids[0]))

    return {
        "dataset": DATASET,
        "seed": SEED,
        "total": len(sampled),
        "smoke": sorted(smoke),
        "instances": sampled,
    }


if __name__ == "__main__":
    result = stratified_sample()
    OUT_PATH.write_text(json.dumps(result, indent=2) + "\n")
    print(f"wrote {OUT_PATH}: {result['total']} instances, smoke={result['smoke']}")
