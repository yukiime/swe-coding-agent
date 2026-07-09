# 设计说明：全部可调参数集中于此，其他模块禁止出现魔法数字——Phase 5 换模型对比时
# 只改这个文件。供应商以"档案"共存，切换=改 ACTIVE_PROFILE 一行(或设环境变量
# SWE_PROFILE)，价格随档案走：记账/熔断依赖单价，换端点忘改价格会把账记错。
# 价格按 cache-miss 单价保守计费：宁可高估成本提前熔断，不可少算超支。
import os
from pathlib import Path

ROOT = Path(__file__).parent

# --- LLM 供应商档案（任何 OpenAI 兼容端点都能当一个档案）---
PROFILES = {
    "deepseek": dict(model="deepseek-chat", base_url="https://api.deepseek.com",
                     api_key_env="DEEPSEEK_API_KEY",
                     price_in_per_m=0.28, price_out_per_m=0.42),
    # 中转商：base_url/模型名/单价按服务商页面填(单价务必填，记账靠它)，key放.env
    "relay": dict(model="deepseek-chat", base_url="https://REPLACE-ME.example.com/v1",
                  api_key_env="RELAY_API_KEY",
                  price_in_per_m=None, price_out_per_m=None),
    # 其他示例: ollama本地 base_url="http://localhost:11434/v1" (价格填0)
    #           gemini    base_url="https://generativelanguage.googleapis.com/v1beta/openai/"
}
ACTIVE_PROFILE = os.environ.get("SWE_PROFILE", "deepseek")
_p = PROFILES[ACTIVE_PROFILE]
MODEL = _p["model"]
BASE_URL = _p["base_url"]
API_KEY_ENV = _p["api_key_env"]
PRICE_IN_PER_M = _p["price_in_per_m"]
PRICE_OUT_PER_M = _p["price_out_per_m"]
assert PRICE_IN_PER_M is not None and PRICE_OUT_PER_M is not None, \
    f"档案 {ACTIVE_PROFILE} 未填单价, 记账会失真, 拒绝启动"

# --- LLM 通用参数 ---
TEMPERATURE = 0.0
MAX_COMPLETION_TOKENS = 4096
LLM_RETRIES = 5             # 指数退避: 第n次失败后睡 2^n 秒
LLM_TIMEOUT_S = 120

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
