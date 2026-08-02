import json

import config
from runner import run_batch


def _write_traj(iid, events):
    config.TRAJ_DIR.mkdir(parents=True, exist_ok=True)
    (config.TRAJ_DIR / f"{iid}.jsonl").write_text(
        "".join(json.dumps(e) + "\n" for e in events))


def test_describe_missing_trajectory():
    assert run_batch.describe("nope") == "no-traj"


def test_describe_interrupted_run_names_the_last_event():
    """超时/崩溃例的轨迹停在半路；播报必须看得出它没跑完，而不是装作有结果。"""
    _write_traj("iid", [{"event": "start"}, {"event": "turn", "turn": 7}])
    assert run_batch.describe("iid") == "interrupted@turn:turn=7"


def test_describe_finished_run_reports_stop_turns_patch_and_cost():
    _write_traj("iid", [{"event": "end", "stop_reason": "model_done",
                         "turns": 5, "patch_chars": 321}])
    config.COST_PATH.write_text(json.dumps(
        {"total_usd": 0.1234, "instances": {"iid": {"usd": 0.1234}}}))
    assert run_batch.describe("iid") == "stop=model_done turns=5 patch=321B $0.1234"
