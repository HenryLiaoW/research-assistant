"""受限 CSV 工具的离线测试。python -m unittest tests.test_csv_tools -v

大部分测试在临时目录里造小表，不依赖真实数据；
test_sample_results_* 读项目里的 data/sample_results.csv，答案就是阶段 3 第 ① 步手算核对过的数。
"""

import json
import os
import tempfile
import unittest
from pathlib import Path

import csv_tools

ROWS = 1000     # 测试里的行数上限
GROUPS = 20     # 测试里的组数上限

SMALL_CSV = """config,seed,score,note
A,0,1.0,x
A,1,3.0,y
B,0,,z
B,1,4.0,
"""


class CsvToolsTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.data = self.root / "data"
        self.data.mkdir()
        (self.data / "small.csv").write_text(SMALL_CSV, encoding="utf-8")
        (self.data / "notes.txt").write_text("not a csv", encoding="utf-8")
        (self.root / "secret.csv").write_text("key\nsk-should-never-be-read\n", encoding="utf-8")

    def tearDown(self):
        self._tmp.cleanup()

    def stats(self, value_column, group_by=None, filename="small.csv", max_rows=ROWS, max_groups=GROUPS):
        return csv_tools.group_stats(str(self.data), filename, value_column, group_by, max_rows, max_groups)

    # ---- 正常功能 ----
    def test_list_files_only_csv(self):
        r = csv_tools.list_files(str(self.data))
        self.assertTrue(r["ok"])
        self.assertEqual([f["name"] for f in r["files"]], ["small.csv"])   # notes.txt 不列出

    def test_describe_columns(self):
        r = csv_tools.describe_file(str(self.data), "small.csv", ROWS)
        self.assertTrue(r["ok"])
        self.assertEqual(r["rows"], 4)
        cols = {c["name"]: c for c in r["columns"]}
        self.assertFalse(cols["config"]["numeric"])
        self.assertTrue(cols["score"]["numeric"])
        self.assertEqual(cols["score"]["missing"], 1)
        self.assertEqual(cols["config"]["unique"], 2)

    def test_group_stats_count_and_missing(self):
        r = self.stats("score", "config")
        self.assertTrue(r["ok"])
        a, b = r["groups"]
        self.assertEqual((a["group"], a["count"], a["missing"]), ("A", 2, 0))
        self.assertAlmostEqual(a["mean"], 2.0)
        self.assertAlmostEqual(a["population_std"], 1.0)   # 总体标准差：|1-2|、|3-2| → 1
        self.assertEqual((b["group"], b["count"], b["missing"]), ("B", 1, 1))   # 空值被跳过，并且报出来
        self.assertAlmostEqual(b["mean"], 4.0)

    def test_no_group_by_means_whole_column(self):
        for empty in (None, "", "  "):
            r = self.stats("score", empty)
            self.assertTrue(r["ok"])
            self.assertEqual(len(r["groups"]), 1)
            self.assertEqual((r["groups"][0]["count"], r["groups"][0]["missing"]), (3, 1))

    def test_result_is_json_serializable(self):
        # 按数值列 seed 分组时，组名是 numpy 整数；agent 要 json.dumps 结果，必须能序列化
        r = self.stats("score", "seed")
        self.assertTrue(r["ok"])
        json.dumps(r, ensure_ascii=False)
        self.assertEqual([g["group"] for g in r["groups"]], ["0", "1"])

    def test_non_finite_values_skipped_and_reported(self):
        # pandas 会把 CSV 里的 inf / -inf 读成无穷大；它们不能参与统计，也不能悄悄丢掉
        (self.data / "inf.csv").write_text("config,score\nA,1.0\nA,inf\nA,3.0\nA,-inf\nA,\n", encoding="utf-8")
        r = self.stats("score", filename="inf.csv")
        self.assertTrue(r["ok"])
        g = r["groups"][0]
        self.assertEqual((g["count"], g["missing"], g["non_finite"]), (2, 1, 2))
        self.assertAlmostEqual(g["mean"], 2.0)
        self.assertAlmostEqual(g["population_std"], 1.0)
        json.dumps(r, allow_nan=False)   # 严格 JSON：结果里不能出现 Infinity / NaN

    def test_only_non_finite_values_gives_no_stats(self):
        (self.data / "inf2.csv").write_text("score\ninf\n-inf\n", encoding="utf-8")
        g = self.stats("score", filename="inf2.csv")["groups"][0]
        self.assertEqual((g["count"], g["non_finite"], g["mean"]), (0, 2, None))

    def test_missing_group_key_is_reported(self):
        (self.data / "gap.csv").write_text("g,v\nA,1\n,2\nA,3\n", encoding="utf-8")
        r = self.stats("v", "g", filename="gap.csv")
        self.assertTrue(r["ok"])
        self.assertIn(csv_tools.MISSING_GROUP_LABEL, [g["group"] for g in r["groups"]])

    # ---- 列的校验 ----
    def test_text_column_rejected_with_numeric_list(self):
        r = self.stats("config")
        self.assertFalse(r["ok"])
        self.assertIn("不是数值列", r["error"])
        self.assertIn("seed, score", r["error"])

    def test_unknown_column_rejected_with_column_list(self):
        r = self.stats("accuracy", "config")
        self.assertFalse(r["ok"])
        self.assertIn("config, seed, score, note", r["error"])
        self.assertFalse(self.stats("score", "nope")["ok"])

    def test_group_by_same_as_value_rejected(self):
        self.assertFalse(self.stats("score", "score")["ok"])

    def test_too_many_groups_rejected(self):
        r = self.stats("score", "seed", max_groups=1)
        self.assertFalse(r["ok"])
        self.assertIn("上限", r["error"])

    def test_too_many_rows_rejected(self):
        r = self.stats("score", max_rows=3)
        self.assertFalse(r["ok"])
        self.assertIn("上限", r["error"])

    # ---- 路径白名单 ----
    def test_parent_dir_rejected(self):
        for name in ("../secret.csv", "..", "sub/small.csv", "..\\secret.csv"):
            r = self.stats("key", filename=name)
            self.assertFalse(r["ok"], name)
            self.assertNotIn("sk-should-never-be-read", json.dumps(r, ensure_ascii=False))

    def test_absolute_path_rejected(self):
        r = self.stats("key", filename=str(self.root / "secret.csv"))
        self.assertFalse(r["ok"])

    def test_non_csv_rejected(self):
        for name in (".env", "notes.txt", "small.csv.bak"):
            self.assertFalse(self.stats("score", filename=name)["ok"], name)

    def test_symlink_escaping_rejected(self):
        link = self.data / "link.csv"
        try:
            os.symlink(self.root / "secret.csv", link)
        except (OSError, NotImplementedError):
            self.skipTest("当前系统不能创建符号链接")
        r = self.stats("key", filename="link.csv")
        self.assertFalse(r["ok"])
        self.assertIn("不在允许的目录", r["error"])

    def test_missing_file_lists_available(self):
        r = self.stats("score", filename="nope.csv")
        self.assertFalse(r["ok"])
        self.assertIn("small.csv", r["error"])

    def test_bad_filename_types_rejected(self):
        for name in (None, 123, "", "   "):
            self.assertFalse(self.stats("score", filename=name)["ok"], repr(name))


class SampleResultsTest(unittest.TestCase):
    """项目自带的 data/sample_results.csv：和第 ① 步 pandas 的输出对答案。"""

    def setUp(self):
        r = csv_tools.group_stats("data", "sample_results.csv", "ari", "config", ROWS, GROUPS)
        self.assertTrue(r["ok"])
        self.ari = {g["group"]: g for g in r["groups"]}
        r = csv_tools.group_stats("data", "sample_results.csv", "c_index", "config", ROWS, GROUPS)
        self.c_index = {g["group"]: g for g in r["groups"]}

    def test_sample_results_ari_means(self):
        self.assertAlmostEqual(self.ari["baseline"]["mean"], 0.423, places=3)
        self.assertAlmostEqual(self.ari["survival_sup"]["mean"], 0.551, places=3)
        self.assertAlmostEqual(self.ari["survival_sup_aug"]["mean"], 0.583, places=3)

    def test_sample_results_c_index_missing(self):
        g = self.c_index["survival_sup"]
        self.assertEqual((g["count"], g["missing"]), (9, 1))


if __name__ == "__main__":
    unittest.main()
