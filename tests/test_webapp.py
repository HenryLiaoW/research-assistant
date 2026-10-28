"""网页后端的离线测试（假模型，不调用 API、不加载嵌入模型）。python -m unittest tests.test_webapp -v"""

import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import openai

import documents
import memory
import retrieval
import tools
import webapp
from tests.fakes import FakeClient, reply

SAVE = ("c1", "save_memory", '{"content": "下次实验用 seed 42"}')
SEARCH = ("s1", "search_documents", '{"query": "pretraining epochs", "k": 2}')
CHUNKS = [{"chunk_id": "d.pdf#p22#3", "file": "d.pdf", "page": 22, "label": "14", "section": "3.3.3 Pretraining",
           "text": "Pretraining runs for 50 epochs.", "score": 0.83},
          {"chunk_id": "d.pdf#p23#0", "file": "d.pdf", "page": 23, "label": "15", "section": "3.3.4 Fine-tuning",
           "text": "Fine-tuning uses a smaller learning rate.", "score": 0.71}]


class FakeSearcher:
    manifest = {"model": "fake", "built_at": "2026-10-26T00:00:00+00:00"}    # 运行日志会读索引版本
    files = sorted({c["file"] for c in CHUNKS})

    def search(self, query, k, file=None):
        return [dict(c) for c in CHUNKS[:k]]


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
                  patch("tracing.config.TRACE_ENABLED", False), patch.object(tools, "_searcher", FakeSearcher())):
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

    def test_answer_carries_passage_text(self):
        http = self.app([reply(tool_calls=[SEARCH], finish_reason="tool_calls"), reply("预训练 50 轮 [E1]。")])
        res = self.wait(http, self.send(http, "预训练多少轮？")["job_id"])["result"]
        self.assertEqual([c["id"] for c in res["citations"]], ["E1"])
        ev = {e["id"]: e for e in res["evidence"]}
        self.assertEqual(sorted(ev), ["E1", "E2"])                                   # 检索到的两段都在，侧边栏能看
        self.assertEqual((ev["E1"]["cited"], ev["E2"]["cited"]), (True, False))      # 区分"引用了"和"检索到但没引用"
        self.assertIn("50 epochs", ev["E1"]["text"])
        self.assertEqual((ev["E1"]["page"], ev["E1"]["query"]), (22, "pretraining epochs"))

    def test_citing_earlier_evidence_still_has_text(self):
        http = self.app([reply(tool_calls=[SEARCH], finish_reason="tool_calls"), reply("50 轮 [E1]。"),
                         reply("刚才那段说的是 50 轮 [E1]。")])
        first = self.send(http, "预训练多少轮？")
        self.wait(http, first["job_id"])
        res = self.wait(http, self.send(http, "再说一遍", first["session_id"])["job_id"])["result"]
        self.assertEqual([(e["id"], e["cited"]) for e in res["evidence"]], [("E1", True)])   # 这一轮没检索，只带引用的那段
        self.assertIn("50 epochs", res["evidence"][0]["text"])

    def test_memory_panel_lists_and_deletes(self):
        http = self.app()
        saved = memory.save(self.conn, "下次实验用 seed 42")
        self.assertEqual([m["content"] for m in http.get("/api/memories").get_json()["memories"]], ["下次实验用 seed 42"])
        self.assertEqual(http.delete(f"/api/memories/{saved['id']}").status_code, 200)
        self.assertEqual(http.get("/api/memories").get_json()["memories"], [])
        self.assertEqual(http.delete(f"/api/memories/{saved['id']}").status_code, 404)    # 已经删过了
        self.assertEqual(http.get(f"/api/memories/{saved['id']}").status_code, 405)       # 只能用 DELETE，链接点不出删除

    def test_memory_delete_waits_for_running_message(self):
        http = self.app([reply(tool_calls=[SAVE], finish_reason="tool_calls"), reply("没有保存")])
        saved = memory.save(self.conn, "旧记忆")
        ids = self.send(http, "记住：下次实验用 seed 42")
        self.wait(http, ids["job_id"], until=("waiting_confirm",))
        self.assertEqual(http.delete(f"/api/memories/{saved['id']}").status_code, 409)    # 不和正在跑的 Agent 同时改库
        http.post(f"/api/jobs/{ids['job_id']}/confirm", json={"approve": False})
        self.wait(http, ids["job_id"])
        self.assertEqual(http.delete(f"/api/memories/{saved['id']}").status_code, 200)

    def test_sources_panel_reports_stale_index(self):
        tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        docs, index, data = tmp / "docs", tmp / "index", tmp / "data"
        for d in (docs, data):
            d.mkdir()
        (docs / "d.pdf").write_bytes(b"fake pdf")
        (data / "r.csv").write_text("a,b\n1,2\n", encoding="utf-8")
        retrieval.save_index(index, CHUNKS, np.zeros((2, 4), dtype=np.float32),
                             {"model": "m", "chunk_size": 500, "sources": retrieval.source_fingerprints(documents.pdf_files(docs))})
        (docs / "new.pdf").write_bytes(b"added later")
        with patch("webapp.config.DOCS_DIR", str(docs)), patch("webapp.config.INDEX_DIR", str(index)), \
                patch("webapp.config.DATA_DIR", str(data)):
            s = self.app().get("/api/sources").get_json()
        self.assertEqual([(d["name"], d["chunks"]) for d in s["docs"]], [("d.pdf", 2), ("new.pdf", 0)])
        self.assertEqual(s["index"]["stale"], ["新增了 new.pdf"])
        self.assertEqual([f["name"] for f in s["data_files"]], ["r.csv"])

    def test_sources_panel_without_index(self):
        tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        with patch("webapp.config.DOCS_DIR", str(tmp)), patch("webapp.config.INDEX_DIR", str(tmp / "none")), \
                patch("webapp.config.DATA_DIR", str(tmp)):
            s = self.app().get("/api/sources").get_json()
        self.assertFalse(s["index"]["exists"])
        self.assertIn("build_index.py", s["index"]["stale"][0])

    def test_other_host_names_are_rejected(self):
        http = self.app()
        self.assertEqual(http.get("/api/status", headers={"Host": "127.0.0.1:8000"}).status_code, 200)
        self.assertEqual(http.get("/api/memories", headers={"Host": "evil.example:8000"}).status_code, 403)   # DNS 重绑定

    def test_reset_starts_new_session(self):
        http = self.app([reply("好")])
        ids = self.send(http, "你好")
        self.wait(http, ids["job_id"])
        new_id = http.post("/api/sessions/reset", json={"session_id": ids["session_id"]}).get_json()["session_id"]
        self.assertNotEqual(new_id, ids["session_id"])


if __name__ == "__main__":
    unittest.main()
