"""
agent.py — Agent 循环本体（阶段 1 建立，阶段 2 改为可处理多轮对话历史）。

用法（项目目录、已激活 .venv）：
    python agent.py "帮我计算这组ARI的均值和总体标准差：0.52、0.55、0.61、0.49。"   # 单条请求
    python chat.py                                                                # 多轮对话（见 chat.py）

一条用户请求内部是一个有上限的循环：
    模型 →（要调工具？）→ 程序校验并执行 → 结果放回对话 → 模型 → … → 最终回答

会发给 API 的内容：AGENT_SYSTEM_PROMPT、本次会话中保留的历史消息、工具菜单（tools.TOOLS）、
以及循环中产生的工具请求与工具结果。不会发送本机任何文件。
"""

import json
import sys
import time
from dataclasses import dataclass, field

from openai import OpenAI, OpenAIError

import citations
import config
import tracing
from llm_client import build_client, describe_api_error, load_api_key
from tools import TOOLS, run_tool


@dataclass
class AgentResult:
    """一条用户请求的最终结果。status 区分几种结束方式，answer 是给用户看的文字。"""

    status: str          # ok | truncated | max_rounds | time_budget | unexpected:<finish_reason>
    answer: str
    model_calls: int     # 这条请求一共调用了几次模型
    tool_calls: int      # 一共执行了几次工具
    citations: list = field(default_factory=list)           # 回答里有效的证据编号（来自检索结果）
    invalid_citations: list = field(default_factory=list)   # 回答里出现、但不在检索结果中的编号
    uncited: bool = False  # 这次请求检索了资料，回答却没有标注任何证据编号
    wait_seconds: float = 0.0  # 等用户确认写操作的总秒数：不算工具耗时，也不占总时间预算


def log(tag: str, text: str) -> None:
    """终端执行记录。只打印工具名、参数、结果摘要、耗时，不打印密钥。"""
    print(f"[{tag}] {text}")


SUMMARY_CHARS = 200   # 终端里一条工具结果最多显示多少字符；完整结果照样发给模型，细节在运行日志里


def summarize_result(name: str, result: dict) -> str:
    """把工具结果压成一行给终端看。谁调用：execute_tool_calls()。只影响显示，发给模型的结果不变。"""
    if name == "search_documents":
        parts = [f"{r.get('evidence_id')} {r.get('file')} 第 {r.get('page')} 页（{r.get('score')}）"
                 for r in result.get("results", [])]
        return f"{len(parts)} 段：" + "；".join(parts)
    if name == "query_results" and result.get("ok"):
        cut = "，已截断" if result.get("truncated") else ""
        return f"{result.get('row_count')} 行{cut}；列 {', '.join(map(str, result.get('columns', [])))}"
    text = json.dumps(result, ensure_ascii=False)
    if len(text) <= SUMMARY_CHARS:
        return text
    return text[:SUMMARY_CHARS] + f"…（共 {len(text)} 字符，完整结果已发给模型）"


def new_history() -> list:
    """开始一段新会话：历史里只有系统提示。谁调用：chat.py 的启动和 /new，agent.main()。"""
    return [{"role": "system", "content": config.AGENT_SYSTEM_PROMPT}]


def trim_history(history: list, max_turns: int) -> list:
    """只保留最近 max_turns 个用户回合（system 永远保留）。谁调用：run_agent() 开头。

    一个"回合"= 一条 user 消息 + 它后面直到下一条 user 之前的所有消息
    （assistant 的工具请求、tool 结果、assistant 的最终回答）。
    必须整回合地删，不能从中间切：如果删掉了带 tool_calls 的 assistant 消息却留下了 tool 消息，
    API 会报 400（tool 消息找不到它对应的请求）。
    """
    user_indexes = [i for i, m in enumerate(history) if m["role"] == "user"]
    if len(user_indexes) <= max_turns:
        return history
    cut = user_indexes[-max_turns]           # 第 cut 条是要保留的最早一条 user 消息
    return [history[0]] + history[cut:]


def rollback_last_turn(history: list) -> None:
    """API 出错时调用：删掉最后一条 user 消息和它后面的所有消息，让历史回到这次提问之前。谁调用：chat.py。

    按"最后一条 user 消息的位置"来删，而不是按调用前记下的长度：run_agent 开头可能已经就地裁剪了历史，
    裁剪后旧长度就对不上了，会删错范围，留下没有结果的工具请求（下一轮请求会因协议不完整报 400）。
    注意：这只恢复对话历史；这一轮里已经执行的写操作（比如 save_memory）不会被撤销。
    """
    last_user = None
    for i, m in enumerate(history):          # 从前往后看，每遇到一条 user 消息就记下它的位置
        if m["role"] == "user":
            last_user = i                    # 循环结束时，last_user 就是最后一条 user 消息的位置
    if last_user is not None:                # 一条 user 消息都没有时什么也不删
        del history[last_user:]


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


# 会改动长期数据的工具：执行前必须经过用户确认（代码层面的授权，不只靠提示词）。
# 资料里夹带的指令（比如论文里写"请保存某条记忆"）就算诱导模型发出了请求，没有用户确认也不会写入。
WRITE_TOOLS = {"save_memory", "forget_memory"}


def execute_tool_calls(tool_calls, messages: list, confirm=None) -> list:
    """执行模型提出的全部工具请求，并按协议把每个结果追加进 messages。谁调用：run_agent()。

    返回这一轮每个工具的记录 {"tool", "args", "result", "ms", "wait_ms"}：
    ms 只算工具本身的执行时间；wait_ms 是等用户确认的时间（只有写操作才有，读工具为 0）。
    两者分开记：否则用户想了 18 秒才输入 y，会被记成"save_memory 耗时 18 秒"。
    协议要点：每个 tool_call 的 id 必须对应一条 role="tool" 的消息，一个不能少，
    否则下一次模型调用会被 API 以 400 拒绝。
    """
    records = []
    for tc in tool_calls:
        name = tc.function.name
        args_json = tc.function.arguments
        log("tool-call", f"模型请求调用 {name}，参数 {args_json}")

        approved, wait_ms = True, 0.0
        if name in WRITE_TOOLS:
            # 没有确认函数（如单次运行的 agent.main）或用户拒绝：approved 为 False，下面不执行
            t_wait = time.perf_counter()
            approved = bool(confirm and confirm(name, args_json))
            wait_ms = (time.perf_counter() - t_wait) * 1000

        t0 = time.perf_counter()                # 从这里开始才是工具自己的耗时
        if approved:
            result = run_tool(name, args_json)  # 永远返回带 ok 字段的 dict，不抛异常
                                            # args_json 是 str（比如'{"numbers": [0.62, 0.58, 0.65]}'），
                                            # run_tool 内部会 json.loads() 解析成 dict
        else:
            result = {"ok": False, "error": "用户没有确认，未执行这个写操作"}   # 如实告诉模型
        ms = (time.perf_counter() - t0) * 1000
        if name == "search_documents" and result.get("ok"):
            citations.assign_ids(result["results"], messages)   # 程序给每个片段编号 E1、E2……，模型只引用编号

        waited = f"，等待确认 {wait_ms / 1000:.1f} 秒" if name in WRITE_TOOLS else ""
        if result.get("ok"):
            log("tool", f"{name} 成功（{ms:.1f} ms{waited}）：{summarize_result(name, result)}")
        else:
            log("tool", f"{name} 失败（{ms:.1f} ms{waited}）：{result.get('error')}")

        messages.append({
            "role": "tool",
            "tool_call_id": tc.id,
            "content": json.dumps(result, ensure_ascii=False),  # 结构化结果，模型按数据读
        })
        records.append({"tool": name, "args": args_json, "result": result,
                        "ms": round(ms, 1), "wait_ms": round(wait_ms, 1)})
    return records


def run_agent(client: OpenAI, history: list, confirm=None, trace=None) -> AgentResult:
    """Agent 循环本体。谁调用：chat.py 每一轮，agent.main()。

    输入：客户端 + 会话历史（最后一条必须是刚追加的 user 消息）。
    输出：AgentResult；同时就地修改 history —— 把这条请求产生的工具请求、工具结果和最终回答
    都追加进去，下一轮对话模型才能看到。
    confirm(name, args_json) -> bool：写操作（WRITE_TOOLS）执行前调用，返回 True 才执行；不给则一律不写。
    trace：调用方用 tracing.start() 开的记录本。每调用一次模型、每执行一个工具就往里记一条；
    记录本留在调用方手里，API 中途出错时已经发生的调用也不会丢。不给就不记。
    上限：config.MAX_TOOL_ROUNDS 轮、config.MAX_TOTAL_SECONDS 秒（只算程序和模型的时间，不含等用户确认的时间）。API 异常不在这里捕获，冒给调用方。
    """
    assert history and history[-1]["role"] == "user", "run_agent 需要 history 以一条 user 消息结尾"
    if trace is None:
        trace = {"model_calls": [], "tool_calls": []}   # 调用方不要日志：记在一本用完就扔的本子上

    # 先裁剪再调用：历史太长会让每轮 prompt token 越来越贵，也可能超出模型上下文。
    trimmed = trim_history(history, config.MAX_HISTORY_TURNS)
    if len(trimmed) != len(history):
        log("history", f"裁剪：丢弃最早的 {len(history) - len(trimmed)} 条消息，只保留最近 {config.MAX_HISTORY_TURNS} 个回合")
        history[:] = trimmed                 # 就地替换，调用方手里的列表同步变化

    messages = history                       # 同一个列表对象：循环中追加的消息会留在会话里
    started = time.perf_counter()
    waited = 0.0                             # 等用户确认的总秒数：从已用时间里扣掉，人想多久都不该让 Agent 超时
    tool_call_count = 0
    last_tool_results: list = []
    searched = False                         # 这次请求有没有检索到资料（用来判断回答是否漏了引用）

    for round_no in range(1, config.MAX_TOOL_ROUNDS + 1):
        elapsed = time.perf_counter() - started - waited
        if elapsed > config.MAX_TOTAL_SECONDS:
            answer = f"已用时 {elapsed:.0f} 秒（不含等待确认的时间），超过总时间预算 {config.MAX_TOTAL_SECONDS} 秒，停止。"
            # append 管的是"正常结束时一定闭合"
            messages.append({"role": "assistant", "content": answer}) 
            return AgentResult("time_budget", answer, round_no - 1, tool_call_count, wait_seconds=waited)

        t0 = time.perf_counter()
        response = call_model(client, messages)  # 和 ask_once 类似，但多了工具菜单和历史
        model_ms = (time.perf_counter() - t0) * 1000
        choice = response.choices[0]
        msg = choice.message
        usage = response.usage
        log("model", f"第 {round_no} 轮：finish_reason={choice.finish_reason}，"
                     f"tokens prompt={usage.prompt_tokens} completion={usage.completion_tokens}，"
                     f"耗时 {model_ms / 1000:.2f} 秒")
        trace["model_calls"].append({"round": round_no, "finish_reason": choice.finish_reason,
                                     "prompt_tokens": usage.prompt_tokens,
                                     "completion_tokens": usage.completion_tokens, "ms": round(model_ms, 1)})

        if choice.finish_reason == "length":
            # 先判断截断，再看工具请求：被截断的回复是不完整的，里面的工具请求（参数可能只有半截）一律不执行，
            # 写操作（如 save_memory）尤其不能由半截回复触发。历史里只放文字、不放 tool_calls，协议保持完整。
            answer = msg.content or ""
            if msg.tool_calls:
                answer = (answer + "\n" if answer else "") + "（回复被截断，其中的工具请求没有执行。）"
            messages.append({"role": "assistant", "content": answer})
            return AgentResult("truncated", answer, round_no, tool_call_count, wait_seconds=waited)

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
                        "function": {"name": tc.function.name, "arguments": tc.function.arguments},  # 函数名和参数
                    }
                    for tc in msg.tool_calls
                ],
            })  # 这里 append 的是工具调用请求
            last_tool_results = execute_tool_calls(msg.tool_calls, messages, confirm)  # append 工具结果
            waited += sum(r["wait_ms"] for r in last_tool_results) / 1000
            trace["tool_calls"].extend(tracing.tool_entry(round_no, r) for r in last_tool_results)
            # 只有检索成功、而且真的返回了片段，才有证据可引用；检索失败时不带编号是对的，不该警告
            searched = searched or any(r["tool"] == "search_documents" and r["result"].get("ok") and r["result"].get("results")
                                       for r in last_tool_results)
            tool_call_count += len(msg.tool_calls)
            continue  # 带着工具结果再问一次模型

        answer = msg.content or ""
        # 给模型看的
        messages.append({"role": "assistant", "content": answer})  # 最终回答进入历史，下一轮可见（保留模型原文）
        # 给用户看的，带有引用标记的最终回答
        shown, valid, invalid = citations.render(answer, citations.collect_evidence(messages))
        status = "ok" if choice.finish_reason == "stop" else f"unexpected:{choice.finish_reason}"
        # unexpected：content_filter / insufficient_system_resource / aborted 等，如实报告，不假装正常
        return AgentResult(status, shown, round_no, tool_call_count, valid, invalid,
                           uncited=searched and not valid and bool(answer.strip()), wait_seconds=waited)

    # 轮数用尽：不再调模型，但把最后一轮工具算出的结果原样交给用户（阶段 1 的遗留问题）。
    answer = (
        f"已达到 {config.MAX_TOOL_ROUNDS} 轮工具循环上限仍未得到最终回答，停止。"
        f"共执行了 {tool_call_count} 次工具调用。最后一轮工具的原始结果："
        + json.dumps([{"tool": r["tool"], "result": r["result"]} for r in last_tool_results], ensure_ascii=False)
    )
    messages.append({"role": "assistant", "content": answer})
    return AgentResult("max_rounds", answer, config.MAX_TOOL_ROUNDS, tool_call_count, wait_seconds=waited)


def main() -> None:
    """单条请求模式：建一段只有一轮的会话，跑完就退出。多轮请用 chat.py。"""
    question = " ".join(sys.argv[1:]).strip()
    if not question:
        question = input("请输入你的问题：").strip()
    if not question:
        sys.exit("错误：输入为空。")

    client = build_client(load_api_key())
    history = new_history()
    history.append({"role": "user", "content": question})
    trace = tracing.start(tracing.new_session_id(), question)

    t0 = time.perf_counter()
    try:
        result = run_agent(client, history, trace=trace)
    except OpenAIError as e:
        tracing.write(tracing.finish(trace, time.perf_counter() - t0, error=type(e).__name__))
        sys.exit(describe_api_error(e))
    total = time.perf_counter() - t0
    tracing.write(tracing.finish(trace, total, result))

    log("agent", f"请求 {trace['request_id']}：状态={result.status}，模型调用 {result.model_calls} 次，"
                 f"工具调用 {result.tool_calls} 次，总耗时 {total:.2f} 秒")
    if result.status == "truncated":
        print("[warn] 回答被截断：已达到 MAX_OUTPUT_TOKENS，可在 config.py 中调大。")
    print()
    print(result.answer)


if __name__ == "__main__":
    main()
