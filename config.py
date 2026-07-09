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
    # 阿里 DashScope 工作区端点(多厂商聚合: qwen/deepseek/glm/kimi 同一key全覆盖,
    # Phase5 对比可直接换 model)。国内网直连, 无需热点。免费额度期内真实花费=0,
    # 下面单价仅作名义记账+熔断兜底; 真正的约束是token额度, 盯 cost.json 累计token。
    # 省额度可换 qwen3.6-flash; 更强可换 qwen3.7-max。
    "ali": dict(model="qwen3.7-plus",
                base_url="https://ws-xrrutp9e08h229by.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
                api_key_env="ALI_API_KEY",
                price_in_per_m=0.10, price_out_per_m=0.20),
    # OpenRouter 上的腾讯 HY3(免费变体, 按token免费=0/0)。tool calling 已实测✓, 256K上下文。
    # 约束是"每日请求数": 账户未充$10=50次/天(基本跑不动批量), 充>=$10=1000次/天。
    # 当前账户 is_free_tier=True(50/天) → 仅作备用, 别设默认跑批量。
    "hy3": dict(model="tencent/hy3:free", base_url="https://openrouter.ai/api/v1",
                api_key_env="OPENROUTER_API_KEY",
                price_in_per_m=0.0, price_out_per_m=0.0),
    # NVIDIA 托管的 deepseek-v4-flash(免费)。tool calling ✓, 但实测约50%概率返回503
    # (免费层容量throttle) → 依赖链会因重试耗尽而中途失败, 会污染解决率数字。
    # 只适合单例调试/Phase5 DeepSeek系对比取样, 不可用于无人值守批量。
    "nvidia": dict(model="deepseek-ai/deepseek-v4-flash",
                   base_url="https://integrate.api.nvidia.com/v1",
                   api_key_env="NVAPI_KEY",
                   price_in_per_m=0.0, price_out_per_m=0.0),
    # 硅基流动(SiliconFlow, 国内聚合)。付费·按token, key SILICON_KEY, 国内wifi直连。
    # 实测 tool calling✓、可靠性 8/8(远好于nvidia免费层的~50%) → 适合无人值守批量, 当前主力。
    # 这是真花钱: 美元熔断在此才真正护钱包, 单价务必准。DeepSeek-V4-Flash 官价 ¥1/¥2 每M
    "silicon": dict(model="deepseek-ai/DeepSeek-V4-Flash",
                    base_url="https://api.siliconflow.cn/v1",
                    api_key_env="SILICON_KEY",
                    price_in_per_m=0.14, price_out_per_m=0.28),
}
ACTIVE_PROFILE = os.environ.get("SWE_PROFILE", "silicon")
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

# --- 批量 ---
BATCH_CONCURRENCY = 3       # 同时在跑的instance数（每个=1个run_one子进程+1个容器）
BATCH_TIMEOUT_S = 25 * 60   # 单例墙钟上限，超时对进程组SIGTERM→SIGKILL并清理容器

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
