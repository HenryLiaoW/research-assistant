"""
chat.py — 阶段 2（上半）：多轮对话。同一次运行里，模型能看到之前几轮说过什么。

用法（项目目录、已激活 .venv）：
    python chat.py

命令：
    /new       清空会话历史，重新开始（只保留系统提示）
    /history   显示当前历史里有多少条消息、多少个回合
    /memories  列出长期记忆库里的全部有效记忆（直接读数据库，不经过模型）
    /quit      退出（Ctrl-D 或 Ctrl-C 也可以）

两种记忆：会话历史只在内存里、按回合裁剪、程序退出即失；长期记忆在 SQLite 文件里，
只在用户明确要求"记住"时由模型调用 save_memory 写入，跨运行保留。

会发给 API 的内容：AGENT_SYSTEM_PROMPT、本次会话中保留的历史消息（最多 config.MAX_HISTORY_TURNS 个回合）、
工具菜单、工具请求与结果。不会发送本机任何文件。
"""

import time

from openai import OpenAIError

import config
import memory
from agent import log, new_history, run_agent
from llm_client import build_client, describe_api_error, load_api_key


def describe_history(history: list) -> str:
    turns = sum(1 for m in history if m["role"] == "user")
    return f"{len(history)} 条消息，{turns} 个回合（上限 {config.MAX_HISTORY_TURNS} 回合）"


def main() -> None:
    client = build_client(load_api_key())
    history = new_history()
    memory_conn = memory.connect(config.MEMORY_DB_PATH)  # 只给 /memories 用；模型走 tools.py 里的连接
    print("多轮对话已开始。输入 /new 重开，/history 查看历史，/memories 看长期记忆，/quit 退出。")

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
            print("[history] 已清空，开始新会话。")
            continue
        if text == "/history":
            print(f"[history] {describe_history(history)}")
            continue
        if text == "/memories":
            rows = memory.list_all(memory_conn)
            if not rows:
                print("[memory] 长期记忆为空。")
            for r in rows:
                print(f"[memory] #{r['id']}  {r['created_at']}  {r['content']}")
            continue

        history.append({"role": "user", "content": text})
        before = len(history)
        t0 = time.perf_counter()
        try:
            result = run_agent(client, history)
        except OpenAIError as e:
            # 这一轮失败：把刚追加的 user 消息和半截产物都撤掉，历史恢复到提问前，下一轮才不会带着残缺协议再报错
            del history[before - 1:]
            print(describe_api_error(e))
            continue

        log("agent", f"状态={result.status}，模型调用 {result.model_calls} 次，"
                     f"工具调用 {result.tool_calls} 次，耗时 {time.perf_counter() - t0:.2f} 秒，"
                     f"历史 {describe_history(history)}")
        if result.status == "truncated":
            print("[warn] 回答被截断：已达到 MAX_OUTPUT_TOKENS，可在 config.py 中调大。")
        print(f"\n助手：{result.answer}")


if __name__ == "__main__":
    main()
