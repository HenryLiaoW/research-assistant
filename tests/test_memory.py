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

    def test_persists_across_connections(self):
        memory.save(self.conn, "跨连接还在")
        self.conn.close()
        conn2 = memory.connect(self.db_path)
        self.assertEqual(memory.list_all(conn2)[0]["content"], "跨连接还在")
        self.conn = conn2  # 让 tearDown 正常关闭


if __name__ == "__main__":
    unittest.main()
