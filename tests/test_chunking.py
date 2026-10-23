"""4b 分块测试。第 1 步：把一页的行变成句子。"""

import unittest

import documents


class PageSentencesTest(unittest.TestCase):
    def test_lines_joined_with_space_and_split_into_sentences(self):
        texts = ["The cohort was split.", "Training used 8,215 stays."]
        self.assertEqual(documents.page_sentences(texts),
                         ["The cohort was split.", "Training used 8,215 stays."])

    def test_hyphen_at_line_end_joined_without_space(self):
        self.assertEqual(documents.page_sentences(["the log-", "rank test was used."]),
                         ["the log-rank test was used."])

    def test_sentence_break_inside_a_line(self):
        texts = ["A value of 0.001 was added. Then", "times were scaled."]
        self.assertEqual(documents.page_sentences(texts),
                         ["A value of 0.001 was added.", "Then times were scaled."])

    def test_no_split_after_et_al_before_bracket(self):
        texts = ["As shown by Qiu et al. (2025), agreement varied."]
        self.assertEqual(documents.page_sentences(texts), texts)

    def test_no_split_after_abbreviation_before_lowercase(self):
        texts = ["Variables, e.g. heart rate, were kept."]
        self.assertEqual(documents.page_sentences(texts), texts)

    def test_section_number_is_not_sentence_end(self):
        self.assertEqual(documents.page_sentences(["See Section 3.2 for details. Results follow."]),
                         ["See Section 3.2 for details.", "Results follow."])

    def test_empty_page(self):
        self.assertEqual(documents.page_sentences([]), [])



def s(n, ch):
    """造一个长度为 n 的句子：n-1 个 ch 加一个句号。"""
    return ch * (n - 1) + "."


class PackSentencesTest(unittest.TestCase):
    def test_all_fit_in_one_chunk(self):
        self.assertEqual(documents.pack_sentences(["One.", "Two."], 100, 30), ["One. Two."])

    def test_new_chunk_starts_with_overlap_sentence(self):
        s1, s2, s3 = s(50, "a"), s(25, "b"), s(40, "c")
        self.assertEqual(documents.pack_sentences([s1, s2, s3], 100, 30), [f"{s1} {s2}", f"{s2} {s3}"])

    def test_sentence_longer_than_overlap_is_not_repeated(self):
        s1, s2, s3 = s(50, "a"), s(40, "b"), s(40, "c")
        self.assertEqual(documents.pack_sentences([s1, s2, s3], 100, 30), [f"{s1} {s2}", s3])

    def test_overlap_dropped_when_next_sentence_would_not_fit(self):
        s1, s2, s3 = s(50, "a"), s(25, "b"), s(90, "c")
        self.assertEqual(documents.pack_sentences([s1, s2, s3], 100, 30), [f"{s1} {s2}", s3])

    def test_very_long_sentence_is_cut_at_spaces(self):
        words = [f"w{i:03d}" for i in range(60)]
        chunks = documents.pack_sentences([" ".join(words)], 100, 30)
        self.assertTrue(all(len(c) <= 100 for c in chunks))
        self.assertEqual(" ".join(chunks).split(), words)

    def test_overlap_keeps_sentence_order(self):
        s1, s2, s3, s4 = s(50, "a"), s(10, "b"), s(10, "c"), s(40, "d")
        self.assertEqual(documents.pack_sentences([s1, s2, s3, s4], 100, 30),
                         [f"{s1} {s2} {s3}", f"{s2} {s3} {s4}"])

    def test_empty_input(self):
        self.assertEqual(documents.pack_sentences([], 100, 30), [])

    def test_bad_parameters_rejected(self):
        with self.assertRaises(ValueError):
            documents.pack_sentences(["One."], 100, 100)
        with self.assertRaises(ValueError):
            documents.pack_sentences(["One."], 0, 0)




if __name__ == "__main__":
    unittest.main()