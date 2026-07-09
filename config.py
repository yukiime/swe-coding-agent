# 设计说明：全部可调参数集中于此，其他模块禁止出现魔法数字——Phase 5 换模型对比时
# 只改这个文件。价格按 cache-miss 单价保守计费：宁可高估成本提前熔断，不可少算超支。
from pathlib import Path

ROOT = Path(__file__).parent

# --- LLM ---
MODEL = "deepseek-chat"
BASE_URL = "https://api.deepseek.com"
API_KEY_ENV = "DEEPSEEK_API_KEY"
TEMPERATURE = 0.0
MAX_COMPLETION_TOKENS = 4096
LLM_RETRIES = 5             # 指数退避: 第n次失败后睡 2^n 秒
LLM_TIMEOUT_S = 120

# 单价 USD / 1M tokens（deepseek-chat；跑批前对照 platform.deepseek.com 价目表核对）
PRICE_IN_PER_M = 0.28
PRICE_OUT_PER_M = 0.42

# --- 成本熔断 ---
COST_LIMIT_INSTANCE_USD = 0.40
COST_LIMIT_TOTAL_USD = 40.0

# --- 主循环 ---
MAX_TURNS = 20

# --- 上下文管理 ---
CTX_KEEP_TURNS = 10         # 滚动窗口保留的最近 assistant 轮数（system+问题原文永远保留）
TOOL_OUT_MAX_CHARS = 4000   # 单条工具输出超过此长度则截中间留头尾
TOOL_OUT_HEAD = 2800
TOOL_OUT_TAIL = 1000

# --- 容器与工具 ---
TESTBED = "/testbed"
IMAGE_PREFIX = "swebench/sweb.eval.x86_64."   # + instance_id('__'→'_1776_') + ':latest'
CONDA_ACTIVATE = "source /opt/miniconda3/bin/activate testbed"
TOOL_TIMEOUT_S = 60
RUN_TESTS_TIMEOUT_S = 600

# --- 数据与路径 ---
DATASET = "princeton-nlp/SWE-bench_Verified"
LOGS = ROOT / "logs"
TRAJ_DIR = LOGS / "trajs"
PRED_DIR = LOGS / "preds"
COST_PATH = LOGS / "cost.json"
