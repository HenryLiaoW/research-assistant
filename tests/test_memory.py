"""记忆存储层的离线测试：每个测试用一个临时数据库文件。python -m unittest tests.test_memory -v"""

import os
import tempfile
import unittest

import memory


class MemoryStoreTest(unittest.TestCase):
    def setUp(self):
        # make a temp SQLite file for each test
        fd, self.db_path = tempfile.mkstemp(suffix=".sqlite3")
        os.close(fd)
        self.conn = memory.connect(self.db_path) #create lib on empty db

    def tearDown(self):
        self.conn.close()
        os.remove(self.db_path) # delete it after testing

    def test_save_then_search_hits(self):
        r = memory.save(self.conn, "ARI 实验用了 10 个随机种子")
        self.assertTrue(r["ok"])
        found = memory.search(self.conn, "种子")
        self.assertEqual([m["id"] for m in found["results"]], [r["id"]])
        self.assertEqual(found["results"][0]["content"], "ARI 实验用了 10 个随机种子")

    def test_search_miss_returns_empty(self):
        memory.save(self.conn, "论文截止 11 月 5 日")
        self.assertEqual(memory.search(self.conn, "种子")["results"], [])

    def test_search_newest_first_and_limit(self):
        for i in range(1, 8):
            memory.save(self.conn, f"实验 {i} 完成")
        found = memory.search(self.conn, "实验", limit=3)["results"]
        self.assertEqual([m["content"] for m in found], ["实验 7 完成", "实验 6 完成", "实验 5 完成"])

    def test_empty_keyword_returns_recent(self):
        memory.save(self.conn, "a")
        memory.save(self.conn, "b")
        found = memory.search(self.conn, "", limit=5)["results"]
        self.assertEqual([m["content"] for m in found], ["b", "a"])

    def test_forget_is_soft_delete(self):
        r = memory.save(self.conn, "要忘掉的事")
        self.assertTrue(memory.forget(self.conn, r["id"])["ok"])
        self.assertEqual(memory.search(self.conn, "忘掉")["results"], [])  # 查不到了
        self.assertEqual(memory.list_all(self.conn), [])  # 列表里也没有
        row = self.conn.execute("SELECT deleted FROM memories WHERE id = ?", (r["id"],)).fetchone()
        self.assertEqual(row["deleted"], 1)   # 但行还在，只是标记

    def test_forget_missing_or_twice_fails(self):
        self.assertFalse(memory.forget(self.conn, 999)["ok"])
        r = memory.save(self.conn, "x")
        memory.forget(self.conn, r["id"])
        self.assertFalse(memory.forget(self.conn, r["id"])["ok"])  # 删第二次：已经不是有效记忆

    def test_forget_rejects_non_int(self):
        self.assertFalse(memory.forget(self.conn, "1")["ok"])
        self.assertFalse(memory.forget(self.conn, True)["ok"])

    def test_save_rejects_empty(self):
        self.assertFalse(memory.save(self.conn, "")["ok"])
        self.assertFalse(memory.save(self.conn, "   ")["ok"])

    def test_sql_in_content_is_just_text(self):
        # 模型/用户给的文字含 SQL 片段，也只是被当成值存起来，表不会被破坏
        evil = "x'); DROP TABLE memories; --"
        r = memory.save(self.conn, evil)
        self.assertTrue(r["ok"])
        self.assertEqual(memory.search(self.conn, "DROP")["results"][0]["content"], evil)
        self.assertEqual(len(memory.list_all(self.conn)), 1)

    def test_scopes_are_isolated(self):
        a = memory.save(self.conn, "下次实验用 seed 42", scope="icu")
        memory.save(self.conn, "下次实验用 seed 7", scope="kline")
        found = memory.search(self.conn, "seed", scope="icu")["results"]
        self.assertEqual([m["id"] for m in found], [a["id"]])                 # 另一个课题的记忆不会被召回
        self.assertEqual(found[0]["scope"], "icu")
        self.assertEqual(memory.search(self.conn, "seed", scope="other")["results"], [])
        self.assertEqual(memory.search(self.conn, "", scope="kline")["results"][0]["content"], "下次实验用 seed 7")

    def test_default_scope_and_listing(self):
        memory.save(self.conn, "默认课题的事")
        memory.save(self.conn, "icu 的事", scope="icu")
        self.assertEqual([m["content"] for m in memory.list_all(self.conn, "default")], ["默认课题的事"])
        self.assertEqual([m["scope"] for m in memory.list_all(self.conn)], ["default", "icu"])   # None = 全部
        self.assertEqual(memory.scopes(self.conn), [{"scope": "default", "count": 1}, {"scope": "icu", "count": 1}])

    def test_bad_scope_rejected(self):
        for bad in ("", "   ", "a\nb", "x" * 65, None, 3):
            self.assertFalse(memory.save(self.conn, "x", scope=bad)["ok"], repr(bad))
            self.assertFalse(memory.search(self.conn, "x", scope=bad)["ok"], repr(bad))
        self.assertTrue(memory.save(self.conn, "x", scope="  icu  ")["ok"])
        self.assertEqual(memory.list_all(self.conn)[0]["scope"], "icu")                    # 首尾空白去掉

    def test_old_database_without_scope_column_is_migrated(self):
        import sqlite3
        self.conn.close()
        os.remove(self.db_path)
        old = sqlite3.connect(self.db_path)                                               # 阶段 2 的建表语句
        old.execute("CREATE TABLE memories (id INTEGER PRIMARY KEY AUTOINCREMENT, content TEXT NOT NULL, "
                    "created_at TEXT NOT NULL, deleted INTEGER NOT NULL DEFAULT 0)")
        old.execute("INSERT INTO memories (content, created_at) VALUES ('旧记忆', '2026-10-19T00:00:00+00:00')")
        old.commit(); old.close()
        self.conn = memory.connect(self.db_path)
        rows = memory.list_all(self.conn)
        self.assertEqual([(m["content"], m["scope"]) for m in rows], [("旧记忆", "default")])   # 旧记忆归入 default
        self.assertEqual(memory.search(self.conn, "旧", scope="default")["results"][0]["content"], "旧记忆")

    def test_persists_across_connections(self):
        memory.save(self.conn, "跨连接还在")
        self.conn.close()
        conn2 = memory.connect(self.db_path)
        self.assertEqual(memory.list_all(conn2)[0]["content"], "跨连接还在")
        self.conn = conn2  # 让 tearDown 正常关闭


if __name__ == "__main__":
    unittest.main()
