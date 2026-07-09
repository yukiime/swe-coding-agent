# 设计说明：批量编排层，无智能。每个instance = 一个run_one子进程（独立进程组）：
# 墙钟超时对整组killpg硬杀（线程杀不死docker exec调用链），一例崩溃零传染，
# 断点续跑=已有preds的直接跳过（超时/崩溃例不产preds，重跑同命令自动补）。
# 镜像默认用完即删：x86 eval镜像约3GB/例，50例全留约150GB会爆盘（判卷时harness自会重拉）。
import argparse
import json
import os
import signal
import subprocess
import sys
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

import config
from agent import tools
from runner.run_one import load_env

BATCH_LOG_DIR = config.LOGS / "batch"


def _kill_group(p: subprocess.Popen) -> None:
    for sig, grace in ((signal.SIGTERM, 10), (signal.SIGKILL, 5)):
        try:
            os.killpg(p.pid, sig)
        except ProcessLookupError:
            return
        try:
            p.wait(grace)
            return
        except subprocess.TimeoutExpired:
            continue


def run_instance(iid: str, timeout: int, keep_image: bool, procs: dict,
                 mock: bool = False) -> str:
    """跑一例并善后。返回状态 ok/timeout/crash；子进程输出落 logs/batch/<iid>.log。"""
    cmd = [sys.executable, "-m", "runner.run_one", "--instance", iid] + (["--mock"] if mock else [])
    # 删旧轨迹再spawn：保证轨迹必是本次所写，否则超时/崩溃时describe会播报上次的陈旧结果
    (config.TRAJ_DIR / f"{iid}.jsonl").unlink(missing_ok=True)
    with (BATCH_LOG_DIR / f"{iid}.log").open("w") as lf:
        p = subprocess.Popen(cmd, stdout=lf, stderr=subprocess.STDOUT,
                             cwd=config.ROOT, start_new_session=True)
        procs[iid] = p
        try:
            status = "ok" if p.wait(timeout) == 0 else "crash"
        except subprocess.TimeoutExpired:
            _kill_group(p)
            status = "timeout"
        finally:
            procs.pop(iid, None)
    # 兜底清容器：SIGKILL不会走run_one的finally，容器会残留
    subprocess.run(["docker", "rm", "-f", tools.container_name(iid)], capture_output=True)
    if not keep_image:
        subprocess.run(["docker", "rmi", tools.image_of(iid)], capture_output=True)
    return status


def describe(iid: str) -> str:
    """从轨迹末行提取一句话结果，仅供控制台播报，不参与任何判定。"""
    try:
        last = json.loads((config.TRAJ_DIR / f"{iid}.jsonl").read_text().splitlines()[-1])
    except (OSError, IndexError, json.JSONDecodeError):
        return "no-traj"
    if last.get("event") != "end":
        return f"interrupted@{last.get('event', '?')}:turn={last.get('turn', '?')}"
    cost = _total_cost()["instances"].get(iid, {}).get("usd", 0.0)
    return (f"stop={last['stop_reason']} turns={last['turns']} "
            f"patch={last['patch_chars']}B ${cost:.4f}")


def _total_cost() -> dict:
    return (json.loads(config.COST_PATH.read_text()) if config.COST_PATH.exists()
            else {"total_usd": 0.0, "instances": {}})


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", required=True, help="本批标签：合并预测文件名+judge的run_id")
    ap.add_argument("--smoke", action="store_true", help="只跑清单里的冒烟5例")
    ap.add_argument("--ids", nargs="*", help="显式指定instance子集（优先于--smoke）")
    ap.add_argument("--concurrency", type=int, default=config.BATCH_CONCURRENCY)
    ap.add_argument("--timeout", type=int, default=config.BATCH_TIMEOUT_S)
    ap.add_argument("--keep-images", action="store_true", help="跑完不删eval镜像（默认删，防爆盘）")
    ap.add_argument("--mock", action="store_true", help="透传run_one --mock，零API费联调编排层")
    args = ap.parse_args()
    load_env()

    manifest = json.loads((config.ROOT / "eval" / "instances_50.json").read_text())
    ids = args.ids or (manifest["smoke"] if args.smoke
                       else [r["instance_id"] for r in manifest["instances"]])
    todo = [i for i in ids if not (config.PRED_DIR / f"{i}.jsonl").exists()]
    print(f"total={len(ids)} skip(done)={len(ids) - len(todo)} todo={len(todo)} "
          f"concurrency={args.concurrency} timeout={args.timeout}s "
          f"profile={config.ACTIVE_PROFILE} model={config.MODEL}", flush=True)

    BATCH_LOG_DIR.mkdir(parents=True, exist_ok=True)
    cost0, t0 = _total_cost()["total_usd"], time.time()
    procs, statuses, lock = {}, {}, threading.Lock()

    def worker(iid: str) -> None:
        with lock:
            print(f"[start] {iid}", flush=True)
        st = run_instance(iid, args.timeout, args.keep_images, procs, args.mock)
        with lock:
            statuses[iid] = st
            print(f"[{len(statuses)}/{len(todo)}] {iid} {st} {describe(iid)}", flush=True)

    ex = ThreadPoolExecutor(max_workers=args.concurrency)
    futures = [ex.submit(worker, i) for i in todo]
    try:
        for f in futures:
            f.result()
        ex.shutdown()
    except KeyboardInterrupt:  # 不留孤儿进程/容器；已完成的例不受影响，重跑续接
        print("\ninterrupted: killing children & cleaning containers ...", flush=True)
        for iid, p in list(procs.items()):
            _kill_group(p)
            subprocess.run(["docker", "rm", "-f", tools.container_name(iid)], capture_output=True)
        ex.shutdown(wait=False, cancel_futures=True)
        sys.exit(130)

    # 合并本id集合的全部预测为单文件，直接喂judge.sh
    rows = [(config.PRED_DIR / f"{i}.jsonl").read_text().strip()
            for i in ids if (config.PRED_DIR / f"{i}.jsonl").exists()]
    merged = config.LOGS / f"predictions_{args.tag}.jsonl"
    merged.write_text("\n".join(rows) + "\n")

    print(f"\n==== batch done in {(time.time() - t0) / 60:.1f}min ====")
    print(f"statuses: {dict(Counter(statuses.values()))} (skipped-as-done: {len(ids) - len(todo)})")
    print(f"batch cost: ${_total_cost()['total_usd'] - cost0:.4f}  "
          f"project total: ${_total_cost()['total_usd']:.4f}")
    print(f"predictions ({len(rows)}/{len(ids)} rows): {merged}")
    print(f"next: bash eval/judge.sh {merged.relative_to(config.ROOT)} {args.tag}")


if __name__ == "__main__":
    main()
