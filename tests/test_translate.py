import unittest

from translate_questions import new_numbers, translate_all


class NewNumbersTest(unittest.TestCase):
    def test_same_numbers_no_warning(self):
        self.assertEqual(new_numbers("塌缩阈值提高到 5% 和 10% 时", "When the threshold is raised to 5% and 10%"), [])

    def test_added_number_is_reported(self):
        self.assertEqual(new_numbers("预训练跑多少轮？", "How many epochs (50) is pretraining run for?"), ["50"])

    def test_decimal_counts_as_one_number(self):
        self.assertEqual(new_numbers("跨种子 ARI 是多少？", "What is the cross-seed ARI (0.991)?"), ["0.991"])

    def test_each_new_number_reported_once_in_order(self):
        self.assertEqual(new_numbers("训练了多少轮？", "50 epochs, then 200 epochs, 50 again"), ["50", "200"])

    def test_digits_inside_names_compared_too(self):
        self.assertEqual(new_numbers("GRU 的 PC1 呢？", "What about PC1 of the GRU?"), [])


class TranslateAllTest(unittest.TestCase):
    def test_keeps_ids_and_groups_and_records_source(self):
        qs = [{"id": "q1", "question": "中文", "file": "d.pdf", "groups": [[1]]}]
        out = translate_all(qs, lambda text: " English ")
        self.assertEqual(out, [{"id": "q1", "question": "English", "file": "d.pdf", "groups": [[1]],
                                "source_question": "中文"}])
        self.assertEqual(qs[0]["question"], "中文")   # 原来的列表不被改动

    def test_empty_translation_refused(self):
        with self.assertRaises(ValueError):
            translate_all([{"id": "q1", "question": "中文", "file": "d.pdf", "groups": [[1]]}], lambda text: "  ")
