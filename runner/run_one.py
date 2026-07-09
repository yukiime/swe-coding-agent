# 设计说明：单例调试入口，职责=资源编排而非智能：加载instance→保证镜像→起容器→
# 跑agent.loop→逐事件写jsonl轨迹→输出SWE-bench predictions格式。--mock 用固定剧本
# 代替真LLM（零API费），剧本对django__django-11066复刻gold修复，因此mock产物可直接
# 用judge.sh判卷来验证"工具链→patch→判卷"全链路正确。
import argparse
import json
import os
import subprocess
import sys
import time

from datasets import load_dataset

import config
from agent import loop, tools
from agent.llm import LLM, CostLedger, MockLLM

MOCK_INSTANCE = "django__django-11066"
MOCK_SCRIPT = [
    ("Locating where the content type is saved during rename.",
     [("code_search", {"pattern": r"content_type\.save",
                       "path": "django/contrib/contenttypes"})]),
    ("Reading the management module.",
     [("read_file", {"path": "django/contrib/contenttypes/management/__init__.py"})]),
    ("The save() call ignores the target database alias; adding using=db.",
     [("edit_file", {"path": "django/contrib/contenttypes/management/__init__.py",
                     "old_str": "content_type.save(update_fields={'model'})",
                     "new_str": "content_type.save(using=db, update_fields={'model'})"})]),
    ("Running the related tests.",
     [("run_tests", {"command": "python tests/runtests.py --settings=test_sqlite "
                                "--parallel 1 contenttypes_tests.test_operations"})]),
    ("Fixed: RenameContentType._rename() now saves on the correct database.", []),
]


def load_env() -> None:
    p = config.ROOT / ".env"
    if p.exists():
        for line in p.read_text().splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


def ensure_image(instance_id: str) -> None:
    img = tools.image_of(instance_id)
    if subprocess.run(["docker", "image", "inspect", img], capture_output=True).returncode:
        print(f"pulling {img} ...")
        subprocess.run(["docker", "pull", img], check=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--instance", required=True)
    ap.add_argument("--mock", action="store_true", help="固定剧本代替真LLM，零API费")
    args = ap.parse_args()

    if args.mock and args.instance != MOCK_INSTANCE:
        sys.exit(f"--mock 剧本只适配 {MOCK_INSTANCE}")
    load_env()

    ds = load_dataset(config.DATASET, split="test")
    row = next((r for r in ds if r["instance_id"] == args.instance), None)
    if row is None:
        sys.exit(f"instance {args.instance} not in {config.DATASET}")

    ensure_image(args.instance)
    config.TRAJ_DIR.mkdir(parents=True, exist_ok=True)
    config.PRED_DIR.mkdir(parents=True, exist_ok=True)
    traj_path = config.TRAJ_DIR / f"{args.instance}.jsonl"
    t0 = time.time()

    llm = MockLLM(MOCK_SCRIPT) if args.mock else LLM(args.instance, CostLedger())
    ctr = tools.start_container(args.instance)
    try:
        with traj_path.open("w") as tf:
            def traj(event: dict) -> None:
                event["ts"] = round(time.time() - t0, 1)
                tf.write(json.dumps(event, ensure_ascii=False) + "\n")
                tf.flush()

            traj({"event": "start", "instance": args.instance,
                  "model": "mock" if args.mock else config.MODEL})
            result = loop.run(row, llm, ctr, traj)
    finally:
        tools.remove_container(ctr)

    pred_path = config.PRED_DIR / f"{args.instance}.jsonl"
    pred_path.write_text(json.dumps({
        "instance_id": args.instance,
        "model_name_or_path": "mock" if args.mock else config.MODEL,
        "model_patch": result["patch"]}) + "\n")

    print(f"stop_reason={result['stop_reason']} turns={result['turns']} "
          f"patch_chars={len(result['patch'])} elapsed={time.time() - t0:.0f}s")
    print(f"trajectory: {traj_path}\npredictions: {pred_path}")
    if not args.mock:
        spent = CostLedger().data["instances"].get(args.instance, {}).get("usd", 0.0)
        print(f"instance cost so far: ${spent:.4f}")


if __name__ == "__main__":
    main()
