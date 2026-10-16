"""
agent.py — 阶段 1：单 Agent + 一个真实工具（calculate_stats）。

用法（项目目录、已激活 .venv）：
    python agent.py "帮我计算这组ARI的均值和总体标准差：0.52、0.55、0.61、0.49。"
    python agent.py "你好"            # 普通聊天：模型直接回答，不调工具

一次运行处理一条用户请求。请求内部是一个有上限的循环：
    模型 →（要调工具？）→ 程序校验并执行 → 结果放回对话 → 模型 → … → 最终回答

会发给 API 的内容：AGENT_SYSTEM_PROMPT、你的问题、工具菜单（tools.TOOLS）、
以及循环中产生的工具请求与工具结果。不会发送本机任何文件。
"""

import json
import sys
import time
from dataclasses import dataclass

from openai import OpenAI, OpenAIError

import config
from llm_client import build_client, describe_api_error, load_api_key
from tools import TOOLS, run_tool


@dataclass
class AgentResult:
    """一条用户请求的最终结果。status 区分几种结束方式，answer 是给用户看的文字。"""

    status: str          # ok | truncated | max_rounds | time_budget | unexpected:<finish_reason>
    answer: str
    model_calls: int     # 这条请求一共调用了几次模型
    tool_calls: int      # 一共执行了几次工具


def log(tag: str, text: str) -> None:
    """终端执行记录。只打印工具名、参数、结果、耗时，不打印密钥。"""
    print(f"[{tag}] {text}")


def call_model(client: OpenAI, messages: list):
    """一次模型调用，带上工具菜单。谁调用：run_agent()。

    输入：完整的 messages。输出：原始 response（由调用方解读 finish_reason / tool_calls）。
    tool_choice="auto"：模型自己决定答还是调工具，程序不替它做关键词判断。
    """
    return client.chat.completions.create(
        model=config.MODEL,
        messages=messages,
        tools=TOOLS,
        tool_choice="auto",
        max_tokens=config.MAX_OUTPUT_TOKENS,
        temperature=config.TEMPERATURE,
        extra_body={"thinking": {"type": config.THINKING}},
    )


def execute_tool_calls(tool_calls, messages: list) -> None:
    """执行模型提出的全部工具请求，并按协议把每个结果追加进 messages。谁调用：run_agent()。

    协议要点：每个 tool_call 的 id 必须对应一条 role="tool" 的消息，一个不能少，
    否则下一次模型调用会被 API 以 400 拒绝。
    """
    for tc in tool_calls:
        name = tc.function.name
        args_json = tc.function.arguments
        log("tool", f"模型请求调用 {name}，参数 {args_json}")

        t0 = time.perf_counter()
        result = run_tool(name, args_json)  # 永远返回带 ok 字段的 dict，不抛异常
        ms = (time.perf_counter() - t0) * 1000

        if result.get("ok"):
            log("tool", f"{name} 成功（{ms:.1f} ms）：{json.dumps(result, ensure_ascii=False)}")
        else:
            log("tool", f"{name} 失败（{ms:.1f} ms）：{result.get('error')}")

        messages.append({
            "role": "tool",
            "tool_call_id": tc.id,
            "content": json.dumps(result, ensure_ascii=False),  # 结构化结果，模型按数据读
        })


def run_agent(client: OpenAI, question: str) -> AgentResult:
    """Agent 循环本体。谁调用：main()。

    输入：客户端 + 用户的一条请求。输出：AgentResult。
    每一轮：调模型 → 看它是要工具还是给了最终文本 → 要工具就执行并继续，否则结束。
    上限：config.MAX_TOOL_ROUNDS 轮、config.MAX_TOTAL_SECONDS 秒。API 异常不在这里捕获，冒给 main()。
    """
    messages = [
        {"role": "system", "content": config.AGENT_SYSTEM_PROMPT},
        {"role": "user", "content": question},
    ]
    started = time.perf_counter()
    tool_call_count = 0

    for round_no in range(1, config.MAX_TOOL_ROUNDS + 1):
        elapsed = time.perf_counter() - started
        if elapsed > config.MAX_TOTAL_SECONDS:
            return AgentResult(
                "time_budget",
                f"已用时 {elapsed:.0f} 秒，超过总时间预算 {config.MAX_TOTAL_SECONDS} 秒，停止。",
                round_no - 1, tool_call_count,
            )

        t0 = time.perf_counter()
        response = call_model(client, messages)
        choice = response.choices[0]
        msg = choice.message
        usage = response.usage
        log("model", f"第 {round_no} 轮：finish_reason={choice.finish_reason}，"
                     f"tokens prompt={usage.prompt_tokens} completion={usage.completion_tokens}，"
                     f"耗时 {time.perf_counter() - t0:.2f} 秒")

        if msg.tool_calls:
            # 协议要求：先把"带 tool_calls 的 assistant 消息"原样放回历史，再放每个工具的结果。
            # 这里手动拼 dict 而不是直接 append SDK 对象，为的是让你看清协议里到底有哪些字段。
            messages.append({
                "role": "assistant",
                "content": msg.content,
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                    }
                    for tc in msg.tool_calls
                ],
            })
            execute_tool_calls(msg.tool_calls, messages)
            tool_call_count += len(msg.tool_calls)
            continue  # 带着工具结果再问一次模型

        if choice.finish_reason == "length":
            return AgentResult("truncated", msg.content or "", round_no, tool_call_count)
        if choice.finish_reason == "stop":
            return AgentResult("ok", msg.content or "", round_no, tool_call_count)
        # content_filter / insufficient_system_resource / aborted 等：如实报告，不假装正常
        return AgentResult(f"unexpected:{choice.finish_reason}", msg.content or "", round_no, tool_call_count)

    return AgentResult(
        "max_rounds",
        f"已达到 {config.MAX_TOOL_ROUNDS} 轮工具循环上限仍未得到最终回答，停止。"
        f"共执行了 {tool_call_count} 次工具调用，每次的参数和结果见上方日志。",
        config.MAX_TOOL_ROUNDS, tool_call_count,
    )


def main() -> None:
    question = " ".join(sys.argv[1:]).strip()
    if not question:
        question = input("请输入你的问题：").strip()
    if not question:
        sys.exit("错误：输入为空。")

    client = build_client(load_api_key())

    t0 = time.perf_counter()
    try:
        result = run_agent(client, question)
    except OpenAIError as e:
        sys.exit(describe_api_error(e))

    log("agent", f"状态={result.status}，模型调用 {result.model_calls} 次，"
                 f"工具调用 {result.tool_calls} 次，总耗时 {time.perf_counter() - t0:.2f} 秒")
    if result.status == "truncated":
        print("[warn] 回答被截断：已达到 MAX_OUTPUT_TOKENS，可在 config.py 中调大。")
    print()
    print(result.answer)


if __name__ == "__main__":
    main()
