import unittest

from eval_retrieval import evaluate, score_question


class ScoreQuestionTest(unittest.TestCase):
    def test_hit_at_rank_one(self):
        self.assertEqual(score_question([17, 30, 5], [[17, 71]]), {"full": 1, "recall": 1.0, "rr": 1.0})

    def test_hit_at_rank_two(self):
        self.assertEqual(score_question([18, 60, 5], [[60]]), {"full": 1, "recall": 1.0, "rr": 0.5})

    def test_hit_at_rank_three(self):
        self.assertAlmostEqual(score_question([1, 2, 60], [[60]])["rr"], 1 / 3)

    def test_miss(self):
        self.assertEqual(score_question([1, 2, 3], [[60]]), {"full": 0, "recall": 0.0, "rr": 0.0})

    def test_two_groups_one_found(self):
        self.assertEqual(score_question([5, 23, 9], [[22], [23]]), {"full": 0, "recall": 0.5, "rr": 0.5})

    def test_two_groups_both_found(self):
        self.assertEqual(score_question([22, 5, 23], [[22], [23]]), {"full": 1, "recall": 1.0, "rr": 1.0})

    def test_same_group_found_twice_counts_once(self):
        self.assertEqual(score_question([17, 17, 5], [[17], [23]]), {"full": 0, "recall": 0.5, "rr": 1.0})

    def test_one_page_can_satisfy_two_groups(self):
        # 像 q16：第 4 页（摘要）同时属于两组，单独取到它就算两处证据都找到
        self.assertEqual(score_question([4, 1, 2], [[4, 22], [4, 23, 29]]), {"full": 1, "recall": 1.0, "rr": 1.0})

    def test_none_never_matches(self):
        # 其他文件的结果在 evaluate 里被换成 None
        self.assertEqual(score_question([None, 17, 5], [[17]]), {"full": 1, "recall": 1.0, "rr": 0.5})


class FakeSearcher:
    def __init__(self, results):
        self.results = results

    def search(self, query, k):
        return self.results[query][:k]


def hit(file, page):
    return {"file": file, "page": page, "section": "s", "score": 0.8}


class EvaluateTest(unittest.TestCase):
    def test_averages_and_ignores_other_files(self):
        questions = [{"id": "q1", "question": "A", "file": "d.pdf", "groups": [[1]]},
                     {"id": "q2", "question": "B", "file": "d.pdf", "groups": [[2]]}]
        searcher = FakeSearcher({"A": [hit("d.pdf", 1), hit("d.pdf", 9)],
                                 "B": [hit("other.pdf", 2), hit("d.pdf", 2)]})   # other.pdf 的第 2 页不算
        rows, summary = evaluate(questions, searcher, 3)
        self.assertEqual([r["pages"] for r in rows], [[1, 9], [None, 2]])
        self.assertEqual(summary, {"questions": 2, "full_recall": 1.0, "recall": 1.0, "mrr": 0.75})
