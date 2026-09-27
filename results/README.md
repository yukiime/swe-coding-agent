# 50 例评测证据

这里保存三轮提交给 SWE-bench 官方 harness 的 predictions 与原始 report。它们从本地 `logs/` 中原样复制，文件名简化为 `predictions_<版本>.jsonl` 和 `report_<版本>.json`。`logs/` 中的完整轨迹、个人过程记录及成本账本不公开。

| 版本 | predictions | 官方 report | `resolved_instances / total_instances` | `empty_patch_instances` | `error_instances` |
|---|---|---|---:|---:|---:|
| baseline | [文件](predictions_baseline.jsonl) | [文件](report_baseline.json) | 20/50 | 25 | 0 |
| round 1 | [文件](predictions_round1.jsonl) | [文件](report_round1.json) | 29/50 | 5 | 0 |
| round 2 | [文件](predictions_round2.jsonl) | [文件](report_round2.json) | 30/50 | 3 | 0 |

复判某轮时，从项目根目录运行：

```bash
bash eval/judge.sh results/predictions_round2.jsonl rejudge-round2
```

这会使用 `swebench.harness.run_evaluation`，需要 Docker、SWE-bench 数据集和评测镜像，但不调用模型 API。运行 ID 取新名字，避免覆盖既有报告。固定预测文件的复判与重新运行 Agent 是两件事；模型再次生成的 patch 可能不同。

脚本 [`eval/judge.sh`](../eval/judge.sh) 只包装官方判卷。`analysis/triage.py` 的失败归因和 `eval/summarize.py` 的汇总均不修改官方 `resolved` 判定。
