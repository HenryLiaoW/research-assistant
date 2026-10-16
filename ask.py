"""
ask.py — 阶段 0：一句话 → DeepSeek API → 显示回答。

用法（在项目目录、已激活 .venv 的终端中）：
    python ask.py "用一句话解释什么是总体标准差"
    python ask.py                # 不带参数时会提示你输入

本脚本只验证：Python 环境可用、SDK 安装正确、API 配置正确、请求能成功、常见错误能看懂。
没有工具调用、没有记忆、没有 RAG。阶段 1 之后它保留为最简单的"连通性测试"。

会发给 DeepSeek API 的内容：config.SYSTEM_PROMPT + 你输入的这一句话。不会发送本机任何文件。
"""

import sys
import time

from openai import OpenAI, OpenAIError

import config
from llm_client import build_client, describe_api_error, load_api_key


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
    except OpenAIError as e:
        sys.exit(describe_api_error(e))
    elapsed = time.perf_counter() - t0

    print(f"[info] 耗时 {elapsed:.2f} 秒\n")
    print(answer)


if __name__ == "__main__":
    main()
