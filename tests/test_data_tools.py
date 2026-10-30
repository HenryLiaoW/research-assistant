"""只读 SQL 工具与数据目录的离线测试。python -m unittest tests.test_data_tools -v

每个测试在临时目录里造一个小库：两张登记过的表（runs、configs）、一张没登记的表（secret），不依赖真实数据。
"""

import sqlite3
import tempfile
import unittest
from pathlib import Path

import data_tools

ROWS, TIMEOUT, SQL_CHARS = 5, 1.0, 2000


class DataToolsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = str(Path(self.tmp.name) / "t.sqlite3")
        conn = sqlite3.connect(self.db)
        conn.executescript("""
            CREATE TABLE configs (config_id TEXT PRIMARY KEY, encoder TEXT);
            CREATE TABLE runs (config_id TEXT, seed INTEGER, ari REAL);
            CREATE TABLE secret (x TEXT);
            INSERT INTO configs VALUES ('A', 'GRU'), ('B', 'MLP');
            INSERT INTO secret VALUES ('hidden');
        """)
        conn.executemany("INSERT INTO runs VALUES (?, ?, ?)", [("A", s, 0.5 + s / 100) for s in range(10)]
                         + [("B", s, 0.3) for s in range(10)])
        data_tools.register_table(conn, "configs", "配置表", "测试", {"encoder": "编码器"})
        data_tools.register_table(conn, "runs", "训练表", "测试", {"ari": "ARI"})
        data_tools.register_notes(conn, "测试", [("术语", "ARI 越高越一致")])
        conn.commit()
        conn.close()

    def tearDown(self):
        self.tmp.cleanup()

    def q(self, sql):
        return data_tools.query(self.db, sql, ROWS, TIMEOUT, SQL_CHARS)

    # ---- 正常查询 ----
    def test_join_and_aggregate(self):
        r = self.q("SELECT c.encoder, ROUND(AVG(r.ari), 3) FROM runs r JOIN configs c USING (config_id) GROUP BY c.encoder ORDER BY 1")
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["columns"], ["encoder", "ROUND(AVG(r.ari), 3)"])
        self.assertEqual(r["rows"], [["GRU", 0.545], ["MLP", 0.3]])

    def test_case_insensitive_and_cte(self):
        self.assertTrue(self.q("select count(*) from RUNS")["ok"])
        r = self.q("WITH t AS (SELECT * FROM runs WHERE config_id = 'A') SELECT COUNT(*) FROM t")
        self.assertEqual(r["rows"], [[10]])

    def test_row_limit(self):
        r = self.q("SELECT * FROM runs")
        self.assertEqual((r["row_count"], r["truncated"]), (ROWS, True))
        self.assertIn("note", r)
        self.assertFalse(self.q("SELECT * FROM configs")["truncated"])

    # ---- 拒绝 ----
    def assertDenied(self, sql, word):
        r = self.q(sql)
        self.assertFalse(r["ok"], sql)
        self.assertIn(word, r["error"], sql)

    def test_writes_denied(self):
        for sql in ("DELETE FROM runs", "UPDATE runs SET ari = 1", "INSERT INTO runs VALUES ('C', 0, 1)",
                    "DROP TABLE runs", "CREATE TABLE x (a)", "ALTER TABLE runs ADD COLUMN y"):
            self.assertDenied(sql, "不允许")
        self.assertEqual(self.q("SELECT COUNT(*) FROM runs")["rows"], [[20]])   # 数据没被改

    def test_unregistered_and_system_tables_denied(self):
        for sql in ("SELECT * FROM secret", "SELECT * FROM sqlite_master", "SELECT * FROM meta_tables",
                    "WITH t AS (SELECT * FROM secret) SELECT * FROM t", "SELECT * FROM runs JOIN secret"):
            self.assertDenied(sql, "不允许")

    def test_pragma_attach_and_functions_denied(self):
        self.assertDenied("PRAGMA table_info(runs)", "PRAGMA")
        self.assertDenied(f"ATTACH DATABASE '{self.db}' AS other", "ATTACH")
        self.assertDenied("SELECT load_extension('x')", "load_extension")

    def test_multiple_statements_denied(self):
        self.assertDenied("SELECT 1; DROP TABLE runs", "一条")
        self.assertEqual(self.q("SELECT COUNT(*) FROM runs")["rows"], [[20]])

    def test_timeout(self):
        self.assertDenied("WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM c) SELECT COUNT(*) FROM c", "中断")

    def test_bad_input(self):
        self.assertDenied("", "非空")
        self.assertDenied("SELECT " + "1," * 2000 + "1", "太长")
        self.assertDenied("SELECT nope FROM runs", "no such column")
        r = data_tools.query(str(Path(self.tmp.name) / "missing.sqlite3"), "SELECT 1", ROWS, TIMEOUT, SQL_CHARS)
        self.assertIn("不存在", r["error"])

    def test_read_only_connection(self):
        """就算 authorizer 有漏洞，只读连接本身也写不进去。"""
        conn = data_tools._connect_ro(self.db)
        with self.assertRaises(sqlite3.OperationalError):
            conn.execute("DELETE FROM runs")
        conn.close()

    def test_non_finite_values_become_null(self):
        conn = sqlite3.connect(self.db)
        conn.execute("INSERT INTO runs VALUES ('C', 0, 9e999)")
        conn.commit()
        conn.close()
        self.assertEqual(self.q("SELECT ari FROM runs WHERE config_id = 'C'")["rows"], [[None]])

    # ---- 数据目录 ----
    def test_catalog_lists_only_registered_tables(self):
        cat = data_tools.catalog(self.db)
        self.assertEqual(sorted(cat), ["configs", "runs"])
        self.assertEqual(cat["runs"]["rows"], 20)
        self.assertIn(("ari", "REAL", "ARI"), cat["runs"]["columns"])
        text = data_tools.describe_catalog(self.db)
        self.assertIn("表 runs（20 行）：训练表", text)
        self.assertIn("ARI 越高越一致", text)
        self.assertNotIn("secret", text)

    def test_register_rejects_unknown_columns(self):
        conn = sqlite3.connect(self.db)
        with self.assertRaises(ValueError):
            data_tools.register_table(conn, "runs", "x", "测试", {"no_such_column": "?"})
        with self.assertRaises(ValueError):
            data_tools.register_table(conn, "no_such_table", "x", "测试")
        conn.close()

    def test_newly_registered_table_becomes_queryable(self):
        """泛化：新表登记后，不改代码就能查。"""
        self.assertDenied("SELECT * FROM secret", "不允许")
        conn = sqlite3.connect(self.db)
        data_tools.register_table(conn, "secret", "现在登记了", "测试")
        conn.commit()
        conn.close()
        self.assertEqual(self.q("SELECT * FROM secret")["rows"], [["hidden"]])

    def test_markdown(self):
        md = data_tools.render_markdown(self.db, header="# 字典")
        self.assertIn("### runs（20 行）", md)
        self.assertIn("| ari | REAL | ARI |", md)
        self.assertIn("## 术语", md)


if __name__ == "__main__":
    unittest.main()
