#!/usr/bin/env bash
# 设计说明：官方harness判卷的唯一入口——输入 predictions.jsonl(或字面量'gold') + run_id，
# 输出官方判卷报告到 logs/reports/。本脚本只做参数拼装和结果摘要打印，
# "是否resolved"的判定100%来自 swebench.harness.run_evaluation，不自建任何判定逻辑。
# 用法: bash eval/judge.sh <predictions.jsonl|gold> <run_id> [instance_id ...]
#   不给instance_id时：gold→用eval/instances_50.json里的冒烟5例；文件→用文件内全部instance。
set -euo pipefail
cd "$(dirname "$0")/.."

PREDS="${1:?usage: judge.sh <predictions.jsonl|gold> <run_id> [instance_id ...]}"
RUN_ID="${2:?usage: judge.sh <predictions.jsonl|gold> <run_id> [instance_id ...]}"
shift 2
IDS=("$@")

PY=.venv/bin/python
DATASET="princeton-nlp/SWE-bench_Verified"

if [ ${#IDS[@]} -eq 0 ]; then
  if [ "$PREDS" = "gold" ]; then
    IDS=($($PY -c "import json; print(' '.join(json.load(open('eval/instances_50.json'))['smoke']))"))
  else
    IDS=($($PY -c "
import json, sys
print(' '.join(json.loads(l)['instance_id'] for l in open(sys.argv[1]) if l.strip()))" "$PREDS"))
  fi
fi

mkdir -p logs/reports
$PY -m swebench.harness.run_evaluation \
  --dataset_name "$DATASET" \
  --predictions_path "$PREDS" \
  --run_id "$RUN_ID" \
  --instance_ids "${IDS[@]}" \
  --max_workers "${JUDGE_WORKERS:-2}" \
  --timeout "${JUDGE_TIMEOUT:-1800}" \
  --cache_level "${JUDGE_CACHE:-env}"

# swebench 4.1.0 把报告写在CWD（--report_dir只在rewrite_reports模式生效），归档到logs/reports/
mv -f ./*."$RUN_ID".json logs/reports/

echo "==== judge summary (run_id=$RUN_ID) ===="
$PY - "$RUN_ID" <<'PYEOF'
import glob, json, sys
run_id = sys.argv[1]
reports = glob.glob(f"logs/reports/*.{run_id}.json")
if not reports:
    sys.exit(f"no report found for run_id={run_id}")
for p in sorted(reports):
    d = json.load(open(p))
    print(f"report: {p}")
    print(f"  submitted={d.get('submitted_instances')} completed={d.get('completed_instances')} "
          f"resolved={d.get('resolved_instances')} unresolved={d.get('unresolved_instances')} "
          f"error={d.get('error_instances')}")
    print(f"  resolved_ids={sorted(d.get('resolved_ids', []))}")
    print(f"  unresolved_ids={sorted(d.get('unresolved_ids', []))}")
    print(f"  error_ids={sorted(d.get('error_ids', []))}")
PYEOF
