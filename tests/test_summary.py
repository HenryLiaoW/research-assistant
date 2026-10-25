"""终端摘要的离线测试。python -m unittest tests.test_summary -v"""

import unittest

from agent import SUMMARY_CHARS, summarize_result


class SummaryTest(unittest.TestCase):
    def test_search_shows_ids_and_pages_not_text(self):
        result = {"ok": True, "query": "q", "results": [
            {"evidence_id": "E4", "file": "dissertation.pdf", "page": 74, "score": 0.8272, "text": "很长的原文……"},
            {"evidence_id": "E5", "file": "dissertation.pdf", "page": 71, "score": 0.8238, "text": "另一段原文……"}]}
        line = summarize_result("search_documents", result)
        self.assertEqual(line, "2 段：E4 dissertation.pdf 第 74 页（0.8272）；E5 dissertation.pdf 第 71 页（0.8238）")

    def test_short_result_shown_in_full(self):
        self.assertEqual(summarize_result("save_memory", {"ok": True, "id": 3}), '{"ok": true, "id": 3}')

    def test_long_result_is_cut(self):
        result = {"ok": True, "groups": ["x" * 50] * 20}
        line = summarize_result("group_statistics", result)
        self.assertTrue(line.startswith('{"ok": true'))
        self.assertIn("完整结果已发给模型", line)
        self.assertLess(len(line), SUMMARY_CHARS + 40)


if __name__ == "__main__":
    unittest.main()
