"""
ask.py — 阶段 0：一句话 → DeepSeek API → 显示回答。

用法（在项目目录、已激活 .venv 的终端中）：
    python ask.py "用一句话解释什么是总体标准差"
    python ask.py                # 不带参数时会提示你输入

本脚本只验证：Python 环境可用、SDK 安装正确、API 配置正确、请求能成功、常见错误能看懂。
没有工具调用、没有记忆、没有 RAG。

会发给 DeepSeek API 的内容：config.SYSTEM_PROMPT + 你输入的这一句话。不会发送本机任何文件。
"""

import os
import sys
import time

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

    谁调用：main()。
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
    """构造 API 客户端：base_url 指向 DeepSeek，超时和重试次数来自 config。谁调用：main()。"""
    return OpenAI(
        api_key=api_key,
        base_url=config.BASE_URL,
        timeout=config.TIMEOUT_SECONDS,
        max_retries=config.MAX_RETRIES,
    )


def ask_once(client: OpenAI, question: str) -> str:
    """发送一次请求，返回回答文本。

    谁调用：main()。
    输入：客户端 + 用户的一句话。输出：模型回答（字符串）。
    这里不捕获异常，让异常冒到 main() 统一翻译成人话。
    """
    messages = [
        {"role": "system", "content": config.SYSTEM_PROMPT},
        {"role": "user", "content": question},
    ]
    response = client.chat.completions.create(
        model=config.MODEL,
        messages=messages,
        max_tokens=config.MAX_OUTPUT_TOKENS,
        temperature=config.TEMPERATURE,
        # DeepSeek 特有参数，openai SDK 没有对应字段，所以放在 extra_body 里原样透传
        extra_body={"thinking": {"type": config.THINKING}},
    )

    choice = response.choices[0]
    usage = response.usage

    # finish_reason 说明模型为什么停下：stop = 正常结束；length = 撞到 max_tokens 被截断
    print(
        f"[info] model={response.model}  finish_reason={choice.finish_reason}  "
        f"tokens: prompt={usage.prompt_tokens}  completion={usage.completion_tokens}"
    )
    if choice.finish_reason == "length":
        print("[warn] 回答被截断：已达到 MAX_OUTPUT_TOKENS，可在 config.py 中调大。")

    return choice.message.content or ""


def main() -> None:
    question = " ".join(sys.argv[1:]).strip()
    if not question:
        question = input("请输入一句话：").strip()
    if not question:
        sys.exit("错误：输入为空。")

    client = build_client(load_api_key())

    t0 = time.perf_counter()
    try:
        answer = ask_once(client, question)
    except AuthenticationError:
        sys.exit("API 错误 401：密钥无效。检查 .env 里的 DEEPSEEK_API_KEY 是否完整、是否已被删除。")
    except RateLimitError:
        sys.exit("API 错误 429：请求太频繁。稍等几秒再试。")
    except APITimeoutError:
        sys.exit(f"超时：{config.TIMEOUT_SECONDS} 秒内没有收到响应。检查网络，或在 config.py 调大 TIMEOUT_SECONDS。")
    except APIConnectionError as e:
        sys.exit(f"连接失败：无法连到 {config.BASE_URL}。检查网络 / 代理 / DNS。详情：{e}")
    except APIStatusError as e:
        hints = {
            400: "请求格式错误：通常是 messages 或参数写错。",
            402: "余额不足：去 platform.deepseek.com 充值。",
            422: "参数不合法：检查 config.py 里的 MODEL / MAX_OUTPUT_TOKENS 等。",
            500: "DeepSeek 服务端错误：稍后重试。",
            503: "DeepSeek 服务繁忙：稍后重试。",
        }
        sys.exit(f"API 错误 {e.status_code}：{hints.get(e.status_code, '')} 原始信息：{e.message}")
    elapsed = time.perf_counter() - t0

    print(f"[info] 耗时 {elapsed:.2f} 秒\n")
    print(answer)


if __name__ == "__main__":
    main()
