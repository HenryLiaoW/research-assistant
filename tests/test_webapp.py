"""网页后端的离线测试（假模型，不调用 API、不加载嵌入模型）。python -m unittest tests.test_webapp -v"""

import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import openai

import memory
import tools
import webapp
from tests.fakes import FakeClient, reply

SAVE = ("c1", "save_memory", '{"content": "下次实验用 seed 42"}')


class FlakyClient(FakeClient):
    """第一次调用模型时网络断了，之后正常。"""

    def __init__(self, replies):
        super().__init__(replies)
        self.failed = False

    def _create(self, **kwargs):
        if not self.failed:
            self.failed = True
            raise openai.OpenAIError("网络断了")                    # 所有 SDK 异常的基类，chat.py 和网页都按它处理
        return super()._create(**kwargs)


class WebAppTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.conn = memory.connect(str(Path(tmp.name) / "m.sqlite3"))       # 临时记忆库，不碰 memory.sqlite3
        for p in (patch.object(tools, "_memory_conn", self.conn), patch("agent.log"),
                  patch("tracing.config.TRACE_ENABLED", False)):
            p.start()
            self.addCleanup(p.stop)

    def app(self, replies=(), client=None, confirm_timeout=5):
        return webapp.create_app(client or FakeClient(replies), confirm_timeout=confirm_timeout, warmup=False).test_client()

    def wait(self, http, job_id, until=("done", "error"), timeout=5):
        deadline = time.time() + timeout
        while time.time() < deadline:
            snap = http.get(f"/api/jobs/{job_id}").get_json()
            if snap["status"] in until:
                return snap
            time.sleep(0.02)
        self.fail(f"等待超时，最后状态：{snap}")

    def send(self, http, text, session_id=None):
        r = http.post("/api/chat", json={"message": text, "session_id": session_id})
        self.assertEqual(r.status_code, 202, r.get_json())
        return r.get_json()

    def test_chat_runs_in_background_and_returns_answer(self):
        http = self.app([reply("你好，我是研究助手。")])
        ids = self.send(http, "你好")
        snap = self.wait(http, ids["job_id"])
        self.assertEqual((snap["status"], snap["result"]["answer"]), ("done", "你好，我是研究助手。"))
        self.assertEqual(snap["steps"]["model_calls"][0]["finish_reason"], "stop")       # 执行步骤来自运行记录本

    def test_same_session_keeps_history(self):
        fake = FakeClient([reply("第一轮"), reply("第二轮")])
        http = self.app(client=fake)
        first = self.send(http, "第一句")
        self.wait(http, first["job_id"])
        second = self.send(http, "第二句", first["session_id"])
        self.wait(http, second["job_id"])
        self.assertIn("第一轮", str(fake.sent[1]))                                      # 第二次请求带着第一轮的历史

    def test_write_waits_for_confirmation_in_page(self):
        http = self.app([reply(tool_calls=[SAVE], finish_reason="tool_calls"), reply("已记住")])
        ids = self.send(http, "记住：下次实验用 seed 42")
        snap = self.wait(http, ids["job_id"], until=("waiting_confirm",))
        self.assertIn("下次实验用 seed 42", snap["pending"]["detail"])                   # 页面上显示要写入的内容
        self.assertEqual(memory.list_all(self.conn), [])                                 # 确认之前没有写入
        self.assertEqual(http.post(f"/api/jobs/{ids['job_id']}/confirm", json={"approve": True}).status_code, 200)
        self.assertEqual(self.wait(http, ids["job_id"])["status"], "done")
        self.assertEqual([m["content"] for m in memory.list_all(self.conn)], ["下次实验用 seed 42"])

    def test_refused_write_is_not_executed(self):
        http = self.app([reply(tool_calls=[SAVE], finish_reason="tool_calls"), reply("没有保存")])
        ids = self.send(http, "记住：下次实验用 seed 42")
        self.wait(http, ids["job_id"], until=("waiting_confirm",))
        http.post(f"/api/jobs/{ids['job_id']}/confirm", json={"approve": False})
        self.wait(http, ids["job_id"])
        self.assertEqual(memory.list_all(self.conn), [])

    def test_no_answer_within_timeout_means_no(self):
        http = self.app([reply(tool_calls=[SAVE], finish_reason="tool_calls"), reply("没有保存")], confirm_timeout=0.1)
        ids = self.send(http, "记住：下次实验用 seed 42")
        self.assertEqual(self.wait(http, ids["job_id"])["status"], "done")
        self.assertEqual(memory.list_all(self.conn), [])

    def test_one_message_at_a_time(self):
        http = self.app([reply(tool_calls=[SAVE], finish_reason="tool_calls"), reply("已记住")])
        ids = self.send(http, "记住：下次实验用 seed 42")
        self.wait(http, ids["job_id"], until=("waiting_confirm",))
        self.assertEqual(http.post("/api/chat", json={"message": "再问一句"}).status_code, 409)   # 上一条还没完
        http.post(f"/api/jobs/{ids['job_id']}/confirm", json={"approve": False})
        self.wait(http, ids["job_id"])

    def test_api_error_rolls_back_history(self):
        fake = FlakyClient([reply("这次好了")])
        http = self.app(client=fake)
        ids = self.send(http, "第一次提问")
        self.assertEqual(self.wait(http, ids["job_id"])["status"], "error")
        again = self.send(http, "再试一次", ids["session_id"])                           # 出错后锁已释放，可以继续发
        self.assertEqual(self.wait(http, again["job_id"])["result"]["answer"], "这次好了")
        self.assertNotIn("第一次提问", str(fake.sent[0]))                                 # 失败那一轮已从历史里回滚

    def test_bad_requests(self):
        http = self.app()
        self.assertEqual(http.post("/api/chat", json={"message": "  "}).status_code, 400)
        self.assertEqual(http.get("/api/jobs/nope").status_code, 404)
        self.assertEqual(http.post("/api/jobs/nope/confirm", json={"approve": True}).status_code, 404)

    def test_page_is_served(self):
        http = self.app()
        r = http.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertIn("AI 实验研究助手", r.get_data(as_text=True))
        self.assertEqual(http.get("/static/app.js").status_code, 200)
        self.assertEqual(http.get("/static/../.env").status_code, 404)                # 不能借静态文件路径读到项目里的其他文件

    def test_reset_starts_new_session(self):
        http = self.app([reply("好")])
        ids = self.send(http, "你好")
        self.wait(http, ids["job_id"])
        new_id = http.post("/api/sessions/reset", json={"session_id": ids["session_id"]}).get_json()["session_id"]
        self.assertNotEqual(new_id, ids["session_id"])


if __name__ == "__main__":
    unittest.main()
