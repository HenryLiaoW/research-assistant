"""
离线测试：只测确定性的工具逻辑，不碰 API、不花钱。
运行：python -m unittest -v tests.test_tools
"""

import json
import math
import unittest

import tools
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



class MemoryScopeTests(unittest.TestCase):
    """作用域由程序设定、由工具盖上：模型的参数里没有它。"""

    def setUp(self):
        import tempfile
        from pathlib import Path
        import memory
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        conn = memory.connect(str(Path(tmp.name) / "m.sqlite3"))
        self.addCleanup(conn.close)
        self.saved_conn, self.saved_scope = tools._memory_conn, tools._memory_scope
        tools._memory_conn = conn
        tools._memory_scope = "default"

    def tearDown(self):
        tools._memory_conn, tools._memory_scope = self.saved_conn, self.saved_scope

    def test_tools_stamp_current_scope(self):
        self.assertTrue(tools.set_memory_scope("icu")["ok"])
        r = run_tool("save_memory", '{"content": "下次实验用 seed 42"}')
        self.assertEqual((r["ok"], r["scope"]), (True, "icu"))
        tools.set_memory_scope("kline")
        self.assertEqual(run_tool("search_memory", '{"keyword": "seed"}')["results"], [])   # 换了课题就查不到
        tools.set_memory_scope("icu")
        self.assertEqual([m["content"] for m in run_tool("search_memory", '{"keyword": "seed"}')["results"]],
                         ["下次实验用 seed 42"])
        self.assertIn("课题「icu」", tools.describe_write("save_memory", '{"content": "x"}'))   # 确认时看得到课题

    def test_model_cannot_pass_scope(self):
        r = run_tool("save_memory", '{"content": "x", "scope": "kline"}')
        self.assertFalse(r["ok"])                                                           # 多余参数：参数不匹配
        self.assertEqual(tools.list_memories(), [])

    def test_bad_scope_name_keeps_current(self):
        tools.set_memory_scope("icu")
        for bad in ("", "  ", "a\nb", "x" * 65, None):
            self.assertFalse(tools.set_memory_scope(bad)["ok"], repr(bad))
        self.assertEqual(tools.memory_scope(), "icu")

    def test_listing_by_scope(self):
        tools.set_memory_scope("icu"); run_tool("save_memory", '{"content": "a"}')
        tools.set_memory_scope("kline"); run_tool("save_memory", '{"content": "b"}')
        self.assertEqual([m["scope"] for m in tools.list_memories()], ["icu", "kline"])
        self.assertEqual([m["content"] for m in tools.list_memories("kline")], ["b"])
        self.assertEqual(tools.memory_scopes(), [{"scope": "icu", "count": 1}, {"scope": "kline", "count": 1}])


class CsvToolDispatchTests(unittest.TestCase):
    """阶段 3：CSV 工具经过 run_tool 调度，和模型真实调用时走同一条路。读项目里的 data/sample_results.csv。"""

    def test_menu_and_registry_match(self):
        # 菜单里的名字和登记表的名字必须一一对应，拼错一个字模型就调不到
        menu_names = {t["function"]["name"] for t in tools.TOOLS}
        self.assertEqual(menu_names, set(tools.TOOL_IMPLEMENTATIONS))

    def test_group_statistics_through_dispatcher(self):
        r = run_tool("group_statistics",
                     '{"file": "sample_results.csv", "value_column": "c_index", "group_by": "config"}')
        self.assertTrue(r["ok"])
        self.assertEqual([g["group"] for g in r["groups"]], ["baseline", "survival_sup", "survival_sup_aug"])
        json.dumps(r, ensure_ascii=False)   # agent.py 要做的事，必须不报错

    def test_group_by_is_optional(self):
        for args in ('{"file": "sample_results.csv", "value_column": "ari"}',
                     '{"file": "sample_results.csv", "value_column": "ari", "group_by": null}'):
            r = run_tool("group_statistics", args)
            self.assertTrue(r["ok"], args)
            self.assertEqual(r["groups"][0]["count"], 30)

    def test_path_attack_through_dispatcher(self):
        r = run_tool("describe_experiment_file", '{"file": "../.env"}')
        self.assertFalse(r["ok"])

    def test_model_cannot_choose_directory(self):
        self.assertTrue(run_tool("list_experiment_files", "{}")["ok"])
        r = run_tool("list_experiment_files", '{"data_dir": "/"}')   # 菜单里没有这个参数，硬塞也不行
        self.assertFalse(r["ok"])

if __name__ == "__main__":
    unittest.main()
