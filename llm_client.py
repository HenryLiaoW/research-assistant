"""
llm_client.py — 与模型供应商打交道的公共部分：读密钥、建客户端、把 API 异常翻译成人话。

ask.py（阶段 0）和 agent.py（阶段 1）都从这里 import，避免同样的代码存两份。
"""

import os
import sys

from dotenv import load_dotenv
from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    OpenAI,
    RateLimitError,
)

import config


def load_api_key() -> str:
    """从 .env（或已有的环境变量）读取密钥。

    谁调用：各脚本的 main()。
    输入：无。输出：密钥字符串。找不到时直接退出，并给出可操作的提示。
    """
    load_dotenv()  # 读取项目目录下的 .env 并写入环境变量；已存在的同名环境变量不会被覆盖
    key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if not key or key == "sk-REPLACE_ME":
        sys.exit(
            "错误：没有找到可用的 DEEPSEEK_API_KEY。\n"
            "请先执行  cp .env.example .env ，再把 .env 里的占位符换成你在 platform.deepseek.com 创建的密钥。"
        )
    return key


def build_client(api_key: str) -> OpenAI:
    """构造 API 客户端：base_url 指向 DeepSeek，超时和重试次数来自 config。"""
    return OpenAI(
        api_key=api_key,
        base_url=config.BASE_URL,
        timeout=config.TIMEOUT_SECONDS,
        max_retries=config.MAX_RETRIES,
    )


def describe_api_error(e: Exception) -> str:
    """把 openai SDK 抛出的异常翻译成可操作的中文提示。

    谁调用：各脚本的 main() 的 except 分支。
    注意判断顺序：AuthenticationError / RateLimitError 是 APIStatusError 的子类，
    APITimeoutError 是 APIConnectionError 的子类，所以具体的要放在前面。
    """
    if isinstance(e, AuthenticationError):
        return "API 错误 401：密钥无效。检查 .env 里的 DEEPSEEK_API_KEY 是否完整、是否已被删除。"
    if isinstance(e, RateLimitError):
        return "API 错误 429：请求太频繁。稍等几秒再试。"
    if isinstance(e, APITimeoutError):
        return f"超时：{config.TIMEOUT_SECONDS} 秒内没有收到响应。检查网络，或在 config.py 调大 TIMEOUT_SECONDS。"
    if isinstance(e, APIConnectionError):
        return f"连接失败：无法连到 {config.BASE_URL}。检查网络 / 代理 / DNS。详情：{e}"
    if isinstance(e, APIStatusError):
        hints = {
            400: "请求格式错误：通常是 messages 或参数写错（工具调用阶段常见于历史消息顺序不符合协议）。",
            402: "余额不足：去 platform.deepseek.com 充值。",
            422: "参数不合法：检查 config.py 里的 MODEL / MAX_OUTPUT_TOKENS 等。",
            500: "DeepSeek 服务端错误：稍后重试。",
            503: "DeepSeek 服务繁忙：稍后重试。",
        }
        return f"API 错误 {e.status_code}：{hints.get(e.status_code, '')} 原始信息：{e.message}"
    return f"未预期的错误：{type(e).__name__}: {e}"
