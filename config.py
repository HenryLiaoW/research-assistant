"""
config.py — 项目里"最可能想改"的参数都集中在这里。
其他文件只 import config，不各自硬编码模型名或超时。
密钥不放在这里：密钥只从 .env / 环境变量读取（见 llm_client.load_api_key）。
"""

# ---- 模型供应商：DeepSeek（OpenAI 兼容接口）----
BASE_URL = "https://api.deepseek.com"

# 当前官方文档的主力模型名（2026-10 查证：deepseek-flash = DeepSeek-V4.1-Flash）。
# 文档将来更名时只改这一行。
MODEL = "deepseek-flash"

# 思考模式：DeepSeek 默认开启（"enabled"）。本项目关闭：更快、更便宜、返回结构更简单。
# 注意：开启后 temperature 无效；而且带工具的多轮对话必须把每轮的 reasoning_content 原样回传，
# 否则 API 返回 400。agent.py 的循环目前没有处理 reasoning_content，所以请保持 "disabled"。
THINKING = "disabled"

# 仅非思考模式下有效。越低越"照本宣科"，越高越发挥。工具选择希望稳定，所以偏低。
TEMPERATURE = 0.2

# ---- 单次调用上限 ----
TIMEOUT_SECONDS = 60       # 单次请求最长等待秒数
MAX_RETRIES = 1            # SDK 对网络错误 / 429 / 5xx 的自动重试次数（0 = 不重试）
MAX_OUTPUT_TOKENS = 800    # 一次回答最多生成多少 token；超过会被截断（finish_reason == "length"）

# ---- Agent 循环上限（阶段 1）----
MAX_TOOL_ROUNDS = 5        # 每条用户请求最多几轮"模型 → 工具 → 模型"，到了就停下解释
MAX_TOTAL_SECONDS = 120    # 一条用户请求的总时间预算；超过就不再发起新一轮模型调用

# ---- 提示词 ----
SYSTEM_PROMPT = "你是一个研究助手。回答简洁、直接；不知道的就说不知道。"

AGENT_SYSTEM_PROMPT = """你是一个研究助手，回答简洁、直接。

规则：
1. 需要对具体数字做统计（均值、最小值、最大值、标准差等）时，必须调用 calculate_stats 工具，不要心算或估算。
2. 回答中的数字一律引用工具返回的结果，可以四舍五入到合理位数，但不要改变数值。
3. 工具返回 ok=false 时，如实告诉用户失败原因，不要编造结果，也不要假装计算成功。
4. 普通聊天或不需要计算的问题直接回答，不要调用工具。
5. 提到标准差时说明用的是总体标准差（分母为 N）。"""
