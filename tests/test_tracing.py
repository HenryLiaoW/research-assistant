"""运行日志的离线测试。python -m unittest tests.test_tracing -v"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import tracing
from agent import AgentResult, run_agent
from tests.fakes import FakeClient, ask, reply

CHUNK_TEXT = "Pretraining runs for 50 epochs."
SEARCH = ("c1", "search_documents", '{"query": "pretraining epochs"}')


def search_result(name, args_json):
    """假的检索结果；每次新建，因为 assign_ids 会就地改写 results。"""
    return {"ok": True, "query": "pretraining epochs", "results": [
        {"chunk_id": "a.pdf#p22#0", "file": "a.pdf", "page": 22, "label": "14", "section": "3.3.3 Pretraining",
         "score": 0.83, "text": CHUNK_TEXT}]}


@patch("agent.log")
class RunAgentTraceTest(unittest.TestCase):
    def test_records_model_and_tool_calls(self, _log):
        client = FakeClient([reply(tool_calls=[SEARCH], finish_reason="tool_calls"), reply("50 个 epoch [E1]")])
        trace = tracing.start("s1", "预训练多少轮？")
        with patch("agent.run_tool", side_effect=search_result):
            run_agent(client, ask("预训练多少轮？"), trace=trace)
        self.assertEqual([m["finish_reason"] for m in trace["model_calls"]], ["tool_calls", "stop"])
        call = trace["tool_calls"][0]
        self.assertEqual((call["round"], call["name"], call["args"], call["ok"]),
                         (1, "search_documents", {"query": "pretraining epochs"}, True))
        self.assertEqual(call["results"], [{"evidence_id": "E1", "chunk_id": "a.pdf#p22#0", "score": 0.83}])
        self.assertNotIn(CHUNK_TEXT, json.dumps(trace, ensure_ascii=False))     # 段落原文不进日志

    def test_partial_record_survives_api_error(self, _log):
        class FailingClient(FakeClient):
            def _create(self, **kwargs):
                if self.sent:                                    # 第二次调用模型时"断网"
                    raise ConnectionError("网络断了")
                return super()._create(**kwargs)

        client = FailingClient([reply(tool_calls=[SEARCH], finish_reason="tool_calls")])
        trace = tracing.start("s1", "预训练多少轮？")
        with patch("agent.run_tool", side_effect=search_result), self.assertRaises(ConnectionError):
            run_agent(client, ask("预训练多少轮？"), trace=trace)
        self.assertEqual(len(trace["model_calls"]), 1)          # 出错前发生的调用还在记录本里
        self.assertEqual(trace["tool_calls"][0]["name"], "search_documents")
        record = tracing.finish(trace, 1.0, error="APIConnectionError")
        self.assertEqual((record["status"], record["error"]), ("api_error", "APIConnectionError"))
        self.assertNotIn("answer", record)


class FinishTest(unittest.TestCase):
    def test_sums_tokens_and_separates_wait(self):
        trace = tracing.start("s1", "记住：seed 42")
        trace["model_calls"] = [
            {"round": 1, "finish_reason": "tool_calls", "prompt_tokens": 3000, "completion_tokens": 40, "ms": 900.0},
            {"round": 2, "finish_reason": "stop", "prompt_tokens": 3100, "completion_tokens": 10, "ms": 700.0}]
        trace["tool_calls"] = [{"round": 1, "name": "save_memory", "args": {}, "ok": True, "ms": 3.0, "wait_ms": 18000.0}]
        record = tracing.finish(trace, total_seconds=20.0, result=AgentResult("ok", "已记住", 2, 1))
        self.assertEqual(record["tokens"], {"prompt": 6100, "completion": 50})
        self.assertEqual((record["total_ms"], record["wait_ms"]), (2000.0, 18000.0))   # 总耗时不含等你确认
        self.assertEqual((record["status"], record["answer"]), ("ok", "已记住"))
        self.assertNotIn("index", record)                        # 没检索就不记索引版本

    def test_records_index_version_when_searched(self):
        trace = tracing.start("s1", "q")
        trace["tool_calls"] = [{"round": 1, "name": "search_documents", "args": {}, "ok": True, "ms": 600.0}]
        info = {"model": "intfloat/multilingual-e5-large", "built_at": "2026-10-24T13:01:05+00:00"}
        with patch("tracing.tools.index_info", return_value=info):
            record = tracing.finish(trace, 1.0, AgentResult("ok", "a", 2, 1))
        self.assertEqual(record["index"], info)


class WriteTest(unittest.TestCase):
    def test_appends_one_json_line_per_request(self):
        with tempfile.TemporaryDirectory() as d:
            path = tracing.write({"question": "预训练多少轮？"}, d)
            tracing.write({"question": "第二个问题"}, d)
            lines = Path(path).read_text(encoding="utf-8").splitlines()
        self.assertEqual([json.loads(x)["question"] for x in lines], ["预训练多少轮？", "第二个问题"])
        self.assertIn("预训练", lines[0])                          # 中文原样写入，不转成 \u 转义

    def test_disabled_writes_nothing(self):
        with tempfile.TemporaryDirectory() as d, patch("tracing.config.TRACE_ENABLED", False):
            self.assertIsNone(tracing.write({"question": "q"}, d))
            self.assertEqual(list(Path(d).iterdir()), [])

    def test_write_failure_does_not_raise(self):
        with tempfile.TemporaryDirectory() as d:
            blocker = Path(d) / "logs"
            blocker.write_text("这是文件，不是目录", encoding="utf-8")   # 日志目录的位置被一个文件占了
            with patch("builtins.print"):
                self.assertIsNone(tracing.write({"question": "q"}, blocker))


if __name__ == "__main__":
    unittest.main()
