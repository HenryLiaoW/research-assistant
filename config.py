"""
config.py — 项目里"最可能想改"的参数都集中在这里。
其他文件只 import config，不各自硬编码模型名或超时。
密钥不放在这里：密钥只从 .env / 环境变量读取（见 ask.py 的 load_api_key）。
"""

# ---- 模型供应商：DeepSeek（OpenAI 兼容接口）----
BASE_URL = "https://api.deepseek.com"

# 当前官方文档的主力模型名（2026-10 查证：deepseek-flash = DeepSeek-V4.1-Flash）。
# 文档将来更名时只改这一行。
MODEL = "deepseek-flash"

# 思考模式：DeepSeek 默认开启（"enabled"）。阶段 0 关闭：更快、更便宜、返回结构更简单。
# 注意：开启后 temperature 无效；带工具的多轮对话还需要回传 reasoning_content（阶段 1 再处理）。
THINKING = "disabled"

# 仅非思考模式下有效。越低越"照本宣科"，越高越发挥。
TEMPERATURE = 0.2

# ---- 调用上限 ----
TIMEOUT_SECONDS = 60       # 单次请求最长等待秒数
MAX_RETRIES = 1            # SDK 对网络错误 / 429 / 5xx 的自动重试次数（0 = 不重试）
MAX_OUTPUT_TOKENS = 800    # 一次回答最多生成多少 token；超过会被截断（finish_reason == "length"）

# ---- 提示词 ----
SYSTEM_PROMPT = "你是一个研究助手。回答简洁、直接；不知道的就说不知道。"
