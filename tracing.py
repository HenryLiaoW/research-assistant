"""
tracing.py — 运行日志（阶段 4e）：每条用户请求在 logs/trace-<日期>.jsonl 末尾追加一行 JSON。

为什么要有：终端输出看完就没了。日志留下每次请求"发生了什么"：调了几次模型、用了哪些工具、
花了多少 token 和时间、检索到哪些 chunk、引用了哪些证据。4f 统计延迟和成本、排查答错的题都靠它。

谁来记：chat.py（或 agent.main）用 start() 开一本记录本，交给 run_agent；run_agent 每调用一次模型、
每执行一个工具就往里记一条；结束后调用方用 finish() 补上汇总，再用 write() 写进文件。
记录本留在调用方手里（和 history 一样），所以 API 中途出错时，已经发生的调用照样能写进日志。

记录：问题和最终回答原文；每次模型调用的 finish_reason、token、耗时；每次工具调用的名称、参数、成败、
耗时、等待确认的时间；检索结果的证据编号、chunk_id 和分数；索引的嵌入模型和建立时间。
不记录：API 密钥；检索段落的原文、SQL 查询结果的行、记忆检索结果的内容（要看原文时按 chunk_id 回索引里找）。
日志只写在本机 config.TRACE_DIR（已在 .gitignore 里），不会发给任何服务；config.TRACE_ENABLED = False 可关闭。
"""

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

import config
import tools


def new_session_id() -> str:
    """一段会话的编号：chat.py 启动和 /new 时各生成一个，用来把同一段对话的请求归到一起。"""
    return uuid.uuid4().hex[:8]


def start(session_id: str, question: str) -> dict:
    """一条请求开始时调用，返回一本空记录本。谁调用：chat.py、agent.main()。"""
    return {
        "request_id": uuid.uuid4().hex[:12],
        "session_id": session_id,
        "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "model": config.MODEL,
        "memory_scope": tools.memory_scope(),   # 这条请求在哪个课题下：记忆题答错时先看作用域对不对
        "question": question,
        "model_calls": [],   # run_agent 每调用一次模型追加一条
        "tool_calls": [],    # run_agent 每执行一个工具追加一条（经 tool_entry 整理）
    }


def tool_entry(round_no: int, record: dict) -> dict:
    """把 execute_tool_calls 返回的一条记录整理成日志条目：留排查需要的字段，不留结果正文。谁调用：run_agent。"""
    result = record["result"]
    entry = {"round": round_no, "name": record["tool"], "args": _parse_args(record["args"]),
             "ok": bool(result.get("ok")), "ms": record["ms"]}
    if record["wait_ms"]:
        entry["wait_ms"] = record["wait_ms"]
    if not result.get("ok"):
        entry["error"] = result.get("error")
    elif record["tool"] == "query_results":
        # 记 SQL 和规模，不记结果行（结果里是实验数字，要看时用同一条 SQL 重查）
        entry["sql"] = result.get("sql")
        entry["row_count"] = result.get("row_count")
        entry["truncated"] = result.get("truncated")
    elif record["tool"] == "search_documents":
        # 只记编号和分数，段落原文不进日志
        entry["results"] = [{"evidence_id": r.get("evidence_id"), "chunk_id": r.get("chunk_id"), "score": r.get("score")}
                            for r in result.get("results", [])]
    return entry


def finish(trace: dict, total_seconds: float, result=None, error=None) -> dict:
    """请求结束时补上汇总。谁调用：chat.py、agent.main()。

    result 是 AgentResult；API 出错时 result 为 None，error 是错误类型名（如 "APIConnectionError"）。
    total_ms 不含等用户确认的时间（和终端显示同一口径），等待时间单独记在 wait_ms。
    """
    wait_ms = sum(t.get("wait_ms", 0) for t in trace["tool_calls"])
    trace["status"] = result.status if result else "api_error"
    if error:
        trace["error"] = error
    trace["tokens"] = {
        "prompt": sum(m["prompt_tokens"] for m in trace["model_calls"]),
        "completion": sum(m["completion_tokens"] for m in trace["model_calls"]),
    }
    trace["total_ms"] = round(total_seconds * 1000 - wait_ms, 1)
    trace["wait_ms"] = round(wait_ms, 1)
    if any(t["name"] == "search_documents" for t in trace["tool_calls"]):
        trace["index"] = tools.index_info()    # 索引重建后同一个 chunk_id 可能指向别的内容，所以记下版本
    if result:
        trace["answer"] = result.answer
        trace["citations"] = result.citations
        trace["invalid_citations"] = result.invalid_citations
        trace["uncited"] = result.uncited
    return trace


def write(trace: dict, log_dir=None):
    """在 <log_dir>/trace-<本地日期>.jsonl 末尾追加一行，返回文件路径。谁调用：chat.py、agent.main()。

    写失败只打印警告、返回 None：日志是旁路，不能因为写不了日志就让对话中断。
    """
    if not config.TRACE_ENABLED:
        return None
    try:
        d = Path(log_dir or config.TRACE_DIR)
        d.mkdir(parents=True, exist_ok=True)
        path = d / f"trace-{datetime.now().strftime('%Y-%m-%d')}.jsonl"
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(trace, ensure_ascii=False) + "\n")
        return path
    except OSError as e:
        print(f"[warn] 运行日志没有写入：{e}")
        return None


def _parse_args(args_json):
    """工具参数是 JSON 字符串；解析失败（模型写了坏 JSON）就原样记下字符串。"""
    try:
        return json.loads(args_json)
    except (TypeError, json.JSONDecodeError):
        return args_json
