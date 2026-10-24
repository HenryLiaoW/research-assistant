"""证据编号与引用校验的离线测试。python -m unittest tests.test_citations -v"""

import json
import unittest
from unittest.mock import patch

import citations
import tools
from agent import run_agent
from tests.fakes import FakeClient, ask, reply


def chunk(cid, page, section="3.3.3 Pretraining", label="14"):
    return {"chunk_id": cid, "file": "d.pdf", "page": page, "label": label, "section": section,
            "score": 0.8, "text": f"text of {cid}"}


def tool_msg(results):
    return {"role": "tool", "tool_call_id": "x", "content": json.dumps({"ok": True, "results": results})}


class AssignIdsTest(unittest.TestCase):
    def test_numbers_new_results_in_order(self):
        results = [chunk("a", 1), chunk("b", 2)]
        citations.assign_ids(results, [])
        self.assertEqual([r["evidence_id"] for r in results], ["E1", "E2"])

    def test_continues_numbering_and_reuses_ids_from_history(self):
        earlier = [chunk("a", 1), chunk("b", 2)]
        citations.assign_ids(earlier, [])
        history = [tool_msg(earlier)]
        results = [chunk("b", 2), chunk("c", 3)]          # b 之前检索到过，c 是新的
        citations.assign_ids(results, history)
        self.assertEqual([r["evidence_id"] for r in results], ["E2", "E3"])

    def test_memory_results_are_not_evidence(self):
        history = [{"role": "tool", "tool_call_id": "m",
                    "content": json.dumps({"ok": True, "results": [{"id": 1, "content": "记忆"}]})}]
        self.assertEqual(citations.collect_evidence(history), {})


class RenderTest(unittest.TestCase):
    def setUp(self):
        results = [chunk("a", 22), chunk("b", 23, section="3.4 Experimental protocol", label="15")]
        citations.assign_ids(results, [])
        self.evidence = citations.collect_evidence([tool_msg(results)])

    def test_extracts_citation_formats_in_order(self):
        self.assertEqual(citations.extract_citations("x [E2] y [E1, E2] z [E3，E1]"), ["E2", "E1", "E3"])

    def test_program_writes_sources_for_valid_ids(self):
        shown, valid, invalid = citations.render("预训练 50 轮 [E1]，联合训练 200 轮 [E2]。", self.evidence)
        self.assertEqual((valid, invalid), (["E1", "E2"], []))
        self.assertIn("[E1] d.pdf 第 22 页（印刷页 14） · 3.3.3 Pretraining", shown)
        self.assertIn("[E2] d.pdf 第 23 页（印刷页 15） · 3.4 Experimental protocol", shown)

    def test_unknown_id_is_flagged(self):
        shown, valid, invalid = citations.render("见 [E1][E9]。", self.evidence)
        self.assertEqual((valid, invalid), (["E1"], ["E9"]))
        self.assertIn("无效引用（不在检索结果中）：E9", shown)

    def test_no_citation_leaves_answer_unchanged(self):
        self.assertEqual(citations.render("没有找到相关内容。", self.evidence), ("没有找到相关内容。", [], []))


class FakeSearcher:
    def search(self, query, k):
        return [chunk("p22", 22), chunk("p23", 23, section="3.4 Experimental protocol", label="15")][:k]


@patch("agent.log")
class AgentCitationTest(unittest.TestCase):
    def setUp(self):
        self.saved = tools._searcher
        tools._searcher = FakeSearcher()

    def tearDown(self):
        tools._searcher = self.saved

    def run_with_answer(self, answer):
        client = FakeClient([
            reply(tool_calls=[("c1", "search_documents", '{"query": "pretraining epochs", "k": 2}')],
                  finish_reason="tool_calls"),
            reply(answer),
        ])
        history = ask("预训练跑多少轮？")
        return run_agent(client, history), client, history

    def test_model_sees_evidence_ids_and_answer_gets_sources(self, _log):
        r, client, history = self.run_with_answer("50 轮 [E1]。")
        tool_content = [m for m in client.sent[1] if m["role"] == "tool"][0]["content"]
        self.assertIn('"evidence_id": "E1"', tool_content)          # 模型拿到的片段带编号
        self.assertEqual((r.citations, r.invalid_citations, r.uncited), (["E1"], [], False))
        self.assertIn("[E1] d.pdf 第 22 页", r.answer)                # 出处由程序补上
        self.assertEqual(history[-1]["content"], "50 轮 [E1]。")      # 历史里保留模型原文

    def test_made_up_id_is_reported(self, _log):
        r, _, _ = self.run_with_answer("50 轮 [E7]。")
        self.assertEqual((r.citations, r.invalid_citations), ([], ["E7"]))

    def test_answer_without_citation_after_search_is_flagged(self, _log):
        r, _, _ = self.run_with_answer("50 轮。")
        self.assertTrue(r.uncited)

    def test_no_search_no_flag(self, _log):
        r = run_agent(FakeClient([reply("你好")]), ask("hi"))
        self.assertFalse(r.uncited)


if __name__ == "__main__":
    unittest.main()
