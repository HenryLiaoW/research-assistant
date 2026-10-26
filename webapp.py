"""
webapp.py — 网页界面的后端（阶段 5）：把 run_agent 包成几个 HTTP 接口，页面放在 static/ 里。

用法（项目目录、已激活 .venv）：
    python webapp.py        然后用浏览器打开 http://127.0.0.1:8000

接口：
    POST /api/chat                  发一条消息 {"message", "session_id"?} → {"session_id", "job_id"}
    GET  /api/jobs/<job_id>         查进度：状态、已完成的步骤、待确认的写操作、最终回答
    POST /api/jobs/<job_id>/confirm 对待确认的写操作作答 {"approve": true / false}
    POST /api/sessions/reset        开始新对话 {"session_id"?} → {"session_id"}
    GET  /api/status                检索模型是否加载好、现在是否在忙

怎么运行一条消息：接口立刻返回 job_id，run_agent 在后台线程里跑，页面每隔半秒查一次进度。
- 执行步骤：run_agent 边做边往记录本（trace）里记模型调用和工具调用，查进度时直接读它，agent.py 不用为网页改。
- 写操作确认：传给 run_agent 的确认函数会挂起等待，直到页面点了确认或取消；超过 config.WEB_CONFIRM_TIMEOUT 秒按拒绝。
  和 chat.py 用的是同一个回调接口，只是"怎么问用户"从终端 input 换成了网页弹窗。
- 同一时间只处理一条消息（本机单用户演示），也避免多个线程同时用嵌入模型和数据库。启动时后台预热检索模型。

安全：只监听 127.0.0.1；API 密钥只在服务端读取，不出现在任何接口返回里；不开调试模式。
会话历史只在内存里，重启即清空（和 chat.py 一样）。发给模型 API 的内容和 chat.py 相同；运行日志照常写进 logs/。
"""

import threading
import time
import traceback
import uuid
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory
from openai import OpenAIError

import agent
import citations
import config
import tools
import tracing
from llm_client import build_client, describe_api_error, load_api_key

STATIC_DIR = Path(__file__).parent / "static"
MAX_JOBS = 50           # 最多保留多少条消息的处理记录（超过就丢掉最早的）


class Session:
    """一段对话。历史只存在服务端内存里。"""

    def __init__(self):
        self.id = tracing.new_session_id()
        self.history = agent.new_history()


class Job:
    """一条消息的处理过程：后台线程写，接口线程读。"""

    def __init__(self, session: Session, text: str, confirm_timeout: float):
        self.id = uuid.uuid4().hex[:12]
        self.session = session
        self.status = "running"              # running | waiting_confirm | done | error
        self.trace = tracing.start(session.id, text)
        self.pending = None                  # 等用户确认的写操作 {"tool", "detail"}
        self.result = None
        self.error = None
        self._decision = None
        self._cond = threading.Condition()
        self._timeout = confirm_timeout

    def confirm(self, name: str, args_json: str) -> bool:
        """传给 run_agent 的确认函数：挂起等页面作答，超时按拒绝。谁调用：agent.execute_tool_calls()。"""
        with self._cond:
            self.pending = {"tool": name, "detail": tools.describe_write(name, args_json)}
            self.status = "waiting_confirm"
            answered = self._cond.wait_for(lambda: self._decision is not None, timeout=self._timeout)
            approved = bool(self._decision) if answered else False
            self._decision, self.pending, self.status = None, None, "running"
        return approved

    def answer(self, approve: bool) -> bool:
        """页面点了确认或取消。没有待确认的写操作时返回 False。谁调用：/api/jobs/<id>/confirm。"""
        with self._cond:
            if self.pending is None:
                return False
            self._decision = bool(approve)
            self._cond.notify_all()
            return True

    def snapshot(self) -> dict:
        return {"job_id": self.id, "session_id": self.session.id, "status": self.status,
                "steps": {"model_calls": list(self.trace["model_calls"]), "tool_calls": list(self.trace["tool_calls"])},
                "pending": self.pending, "result": self.result, "error": self.error}


def create_app(client, confirm_timeout=None, warmup=True) -> Flask:
    """client 是模型客户端（测试时传假客户端）；warmup=True 时后台预热检索模型。"""
    app = Flask(__name__, static_folder=None)
    state = {"sessions": {}, "jobs": {}, "busy": threading.Lock(),
             "search_ready": False, "search_error": None,
             "confirm_timeout": config.WEB_CONFIRM_TIMEOUT if confirm_timeout is None else confirm_timeout}

    def new_session() -> Session:
        s = Session()
        state["sessions"][s.id] = s
        return s

    def remember(job: Job) -> None:
        state["jobs"][job.id] = job
        while len(state["jobs"]) > MAX_JOBS:
            state["jobs"].pop(next(iter(state["jobs"])))

    def run_job(job: Job) -> None:
        """后台线程：跑 run_agent，结束后写运行日志、整理结果。无论成败都释放忙碌锁。"""
        history = job.session.history
        t0 = time.perf_counter()
        try:
            result = agent.run_agent(client, history, confirm=job.confirm, trace=job.trace)
            tracing.write(tracing.finish(job.trace, time.perf_counter() - t0, result))
            evidence = citations.collect_evidence(history)
            job.result = {"status": result.status, "answer": result.answer, "answer_raw": history[-1]["content"],
                          "citations": [{"id": e, **evidence[e]} for e in result.citations],
                          "invalid_citations": result.invalid_citations, "uncited": result.uncited}
            job.status = "done"
        except OpenAIError as e:
            agent.rollback_last_turn(history)
            tracing.write(tracing.finish(job.trace, time.perf_counter() - t0, error=type(e).__name__))
            job.error, job.status = describe_api_error(e), "error"
        except Exception as e:           # 兜底：线程里的异常不会自己报到页面上，不处理的话页面会一直转圈
            agent.rollback_last_turn(history)
            traceback.print_exc()
            job.error, job.status = f"内部错误（{type(e).__name__}），详情见运行 webapp.py 的终端", "error"
        finally:
            state["busy"].release()

    def warm() -> None:
        """预热检索模型（约 10 秒），期间不接新消息；同时检查索引是否过期。"""
        with state["busy"]:
            r = tools.search_documents("warm up")
        state["search_ready"], state["search_error"] = r["ok"], (None if r["ok"] else r["error"])

    @app.get("/")
    def index():
        if not (STATIC_DIR / "index.html").exists():
            return "页面还没做（阶段 5b）。接口已可用：POST /api/chat", 200, {"Content-Type": "text/plain; charset=utf-8"}
        return send_from_directory(STATIC_DIR, "index.html")

    @app.get("/static/<path:name>")
    def static_files(name):
        return send_from_directory(STATIC_DIR, name)       # 会拒绝跳出 static/ 的路径

    @app.get("/api/status")
    def status():
        return jsonify(search_ready=state["search_ready"], search_error=state["search_error"],
                       busy=state["busy"].locked())

    @app.post("/api/chat")
    def chat():
        body = request.get_json(silent=True) or {}
        text = body.get("message")
        if not isinstance(text, str) or not text.strip():
            return jsonify(error="消息不能为空"), 400
        text = text.strip()
        if len(text) > config.WEB_MAX_MESSAGE_CHARS:
            return jsonify(error=f"消息太长，最多 {config.WEB_MAX_MESSAGE_CHARS} 字"), 400
        if not state["busy"].acquire(blocking=False):
            return jsonify(error="正在忙（加载检索模型或处理上一条消息），请稍等"), 409
        try:
            session = state["sessions"].get(body.get("session_id")) or new_session()
            session.history.append({"role": "user", "content": text})
            job = Job(session, text, state["confirm_timeout"])
            remember(job)
            threading.Thread(target=run_job, args=(job,), daemon=True).start()
        except Exception:
            state["busy"].release()
            raise
        return jsonify(session_id=session.id, job_id=job.id), 202

    @app.get("/api/jobs/<job_id>")
    def job_status(job_id):
        job = state["jobs"].get(job_id)
        if job is None:
            return jsonify(error="没有这条消息的处理记录"), 404
        return jsonify(job.snapshot())

    @app.post("/api/jobs/<job_id>/confirm")
    def job_confirm(job_id):
        job = state["jobs"].get(job_id)
        if job is None:
            return jsonify(error="没有这条消息的处理记录"), 404
        approve = (request.get_json(silent=True) or {}).get("approve")
        if not isinstance(approve, bool):
            return jsonify(error="approve 必须是 true 或 false"), 400
        if not job.answer(approve):
            return jsonify(error="现在没有等待确认的写操作"), 409
        return jsonify(ok=True)

    @app.post("/api/sessions/reset")
    def reset():
        if state["busy"].locked():
            return jsonify(error="正在处理消息，稍后再开始新对话"), 409
        state["sessions"].pop((request.get_json(silent=True) or {}).get("session_id"), None)
        return jsonify(session_id=new_session().id)

    if warmup:
        threading.Thread(target=warm, daemon=True).start()
    return app


def main() -> None:
    app = create_app(build_client(load_api_key()))
    print(f"打开 http://{config.WEB_HOST}:{config.WEB_PORT} （只在本机可访问；Ctrl-C 退出）")
    app.run(host=config.WEB_HOST, port=config.WEB_PORT, threaded=True, debug=False)


if __name__ == "__main__":
    main()
