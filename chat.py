"""
chat.py — 阶段 2（上半）：多轮对话。同一次运行里，模型能看到之前几轮说过什么。

用法（项目目录、已激活 .venv）：
    python chat.py

命令：
    /new       清空会话历史，重新开始（只保留系统提示）
    /history   显示当前历史里有多少条消息、多少个回合
    /memories  列出当前课题（作用域）里的有效记忆（直接读数据库，不经过模型）
    /scope     显示当前课题和各课题的记忆数；/scope <名字> 切换课题（之后的记忆写入只进这个课题，检索返回这个课题 + 全局）
               /scope global 切到全局：在这里"记住"的事所有课题都能查到（如论文截止日期）
    /quit      退出（Ctrl-D 或 Ctrl-C 也可以）

两种记忆：会话历史只在内存里、按回合裁剪、程序退出即失；长期记忆在 SQLite 文件里，
只在用户明确要求"记住"时由模型调用 save_memory，而且写入和删除前都要用户在终端里输入 y 确认，跨运行保留。

会发给 API 的内容：AGENT_SYSTEM_PROMPT、本次会话中保留的历史消息（最多 config.MAX_HISTORY_TURNS 个回合）、
工具菜单、工具请求与结果。工具结果里可能包含：CSV 的统计汇总（不含原始行）、长期记忆的内容、
search_documents 检索到的资料原文片段（每次最多 config.MAX_SEARCH_K 段，带文件名和页码）。
不会发送整篇 PDF 或其他本机文件。
"""

import time

from openai import OpenAIError

import config
import memory
import tools
import tracing
from agent import log, new_history, rollback_last_turn, run_agent
from llm_client import build_client, describe_api_error, load_api_key


def confirm_write(name: str, args_json: str) -> bool:
    """写长期记忆前问用户。谁调用：run_agent（通过 confirm 参数）。只有输入 y 才执行。
    在提示处按 Ctrl-C / Ctrl-D 当作拒绝，而不是让整个程序崩溃退出。"""
    try:
        answer = input(f"[确认] 助手想要{tools.describe_write(name, args_json)}。确认吗？(y/N) ")
    except (EOFError, KeyboardInterrupt):
        print()
        return False
    return answer.strip().lower() in ("y", "yes")


def describe_history(history: list) -> str:
    turns = sum(1 for m in history if m["role"] == "user")
    return f"{len(history)} 条消息，{turns} 个回合（上限 {config.MAX_HISTORY_TURNS} 回合）"


def main() -> None:
    client = build_client(load_api_key())
    history = new_history()
    session_id = tracing.new_session_id()                # 同一段对话的请求在日志里共用这个编号
    memory_conn = memory.connect(config.MEMORY_DB_PATH)  # 只给 /memories 用；模型走 tools.py 里的连接
    print("多轮对话已开始。输入 /new 重开，/history 查看历史，/memories 看长期记忆，/scope 看或切换课题，/quit 退出。")
    print(f"当前课题（记忆作用域）：{tools.memory_scope()}")
    if config.TRACE_ENABLED:
        print(f"运行日志写在 {config.TRACE_DIR}/（只在本机，不进 git）。")

    while True:
        try:
            text = input("\n你：").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n再见。")
            break

        if not text:
            continue
        if text == "/quit":
            print("再见。")
            break
        if text == "/new":
            history = new_history()
            session_id = tracing.new_session_id()
            print("[history] 已清空，开始新会话。")
            continue
        if text == "/history":
            print(f"[history] {describe_history(history)}")
            continue
        if text == "/memories":
            scope = tools.memory_scope()
            rows = memory.list_all(memory_conn, scope)
            others = sum(s["count"] for s in memory.scopes(memory_conn) if s["scope"] != scope)
            if not rows:
                print(f"[memory] 课题「{scope}」的长期记忆为空。")
            for r in rows:
                print(f"[memory] #{r['id']}  {r['created_at']}  {r['content']}")
            if others:
                print(f"[memory] 其他课题另有 {others} 条，/scope 可查看并切换。")
            continue
        if text == "/scope" or text.startswith("/scope "):
            name = text[len("/scope"):].strip()
            if name:
                r = tools.set_memory_scope(name)
                if not r["ok"]:
                    print(f"[memory] {r['error']}")
                elif r["scope"] == memory.GLOBAL_SCOPE:
                    print("[memory] 已切换到全局（global）：这里的记忆在所有课题下都能查到。")
                elif not memory.list_all(memory_conn, r["scope"]):
                    print(f"[memory] 已切换到课题「{r['scope']}」（目前没有记忆，是个新课题；打错名字的话请重新 /scope）。")
                else:
                    print(f"[memory] 已切换到课题「{r['scope']}」。")
            else:
                print(f"[memory] 当前课题：{tools.memory_scope()}")
                for s in memory.scopes(memory_conn):
                    print(f"[memory]   {s['scope']}：{s['count']} 条")
            continue

        history.append({"role": "user", "content": text})
        trace = tracing.start(session_id, text)           # 这条请求的记录本，run_agent 边做边记
        t0 = time.perf_counter()
        try:
            result = run_agent(client, history, confirm=confirm_write, trace=trace)
        except OpenAIError as e:
            # 这一轮失败：把刚追加的 user 消息和半截产物都撤掉，历史恢复到提问前，下一轮才不会带着残缺协议再报错
            rollback_last_turn(history)
            tracing.write(tracing.finish(trace, time.perf_counter() - t0, error=type(e).__name__))  # 出错也留记录
            print(describe_api_error(e))
            continue
        total = time.perf_counter() - t0
        tracing.write(tracing.finish(trace, total, result))

        elapsed = total - result.wait_seconds   # 不含等你确认的时间
        waited = f"（另有等待确认 {result.wait_seconds:.1f} 秒）" if result.wait_seconds else ""
        log("agent", f"请求 {trace['request_id']}：状态={result.status}，模型调用 {result.model_calls} 次，"
                     f"工具调用 {result.tool_calls} 次，耗时 {elapsed:.2f} 秒{waited}，"
                     f"历史 {describe_history(history)}")
        if result.status == "truncated":
            print("[warn] 回答被截断：已达到 MAX_OUTPUT_TOKENS，可在 config.py 中调大。")
        if result.invalid_citations:
            print(f"[warn] 回答引用了不存在的证据编号：{'、'.join(result.invalid_citations)}")
        if result.uncited:
            print("[warn] 这次检索了资料，但回答没有标注证据编号")
        print(f"\n助手：{result.answer}")


if __name__ == "__main__":
    main()
