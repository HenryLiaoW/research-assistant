"""
离线测试：只测确定性的工具逻辑，不碰 API、不花钱。
运行：python -m unittest -v tests.test_tools
"""

import math
import unittest

from tools import calculate_stats, run_tool


class CalculateStatsTests(unittest.TestCase):
    def test_example_from_brief(self):
        # 0.52, 0.55, 0.61, 0.49：均值 0.5425；总体标准差 sqrt(0.007875 / 4) = sqrt(0.00196875)
        r = calculate_stats([0.52, 0.55, 0.61, 0.49])
        self.assertTrue(r["ok"])
        self.assertEqual(r["count"], 4)
        self.assertAlmostEqual(r["mean"], 0.5425, places=10)
        self.assertAlmostEqual(r["min"], 0.49, places=10)
        self.assertAlmostEqual(r["max"], 0.61, places=10)
        self.assertAlmostEqual(r["population_std"], math.sqrt(0.00196875), places=10)
        self.assertIn("N", r["std_definition"])

    def test_order_does_not_matter(self):
        a = calculate_stats([0.52, 0.55, 0.61, 0.49])
        b = calculate_stats([0.61, 0.49, 0.52, 0.55])
        for key in ("count", "mean", "min", "max", "population_std"):
            self.assertAlmostEqual(a[key], b[key], places=12)

    def test_single_value(self):
        r = calculate_stats([3])
        self.assertTrue(r["ok"])
        self.assertEqual(r["mean"], 3.0)
        self.assertEqual(r["population_std"], 0.0)

    def test_empty_list_is_an_error(self):
        r = calculate_stats([])
        self.assertFalse(r["ok"])
        self.assertIn("空", r["error"])

    def test_non_numeric_element_is_an_error(self):
        r = calculate_stats([0.5, "0.6"])
        self.assertFalse(r["ok"])
        self.assertIn("第 2 个元素", r["error"])

    def test_bool_is_not_a_number(self):
        self.assertFalse(calculate_stats([True, 1])["ok"])

    def test_nan_is_an_error(self):
        self.assertFalse(calculate_stats([1.0, float("nan")])["ok"])

    def test_not_a_list_is_an_error(self):
        self.assertFalse(calculate_stats("1,2,3")["ok"])


class RunToolTests(unittest.TestCase):
    def test_happy_path_through_dispatcher(self):
        r = run_tool("calculate_stats", '{"numbers": [1, 2, 3]}')
        self.assertTrue(r["ok"])
        self.assertAlmostEqual(r["mean"], 2.0)

    def test_unknown_tool(self):
        r = run_tool("delete_everything", "{}")
        self.assertFalse(r["ok"])
        self.assertIn("未知工具", r["error"])

    def test_invalid_json_arguments(self):
        r = run_tool("calculate_stats", "{numbers: [1, 2]}")
        self.assertFalse(r["ok"])
        self.assertIn("JSON", r["error"])

    def test_missing_required_argument(self):
        r = run_tool("calculate_stats", "{}")
        self.assertFalse(r["ok"])
        self.assertIn("参数不匹配", r["error"])

    def test_unexpected_extra_argument(self):
        r = run_tool("calculate_stats", '{"numbers": [1], "mode": "fast"}')
        self.assertFalse(r["ok"])

    def test_never_raises(self):
        # 无论模型给什么，run_tool 都返回 dict，而不是让程序崩溃
        for bad in ["", "null", "[1,2]", '"text"']:
            r = run_tool("calculate_stats", bad)
            self.assertIsInstance(r, dict)
            self.assertFalse(r["ok"])


if __name__ == "__main__":
    unittest.main()
