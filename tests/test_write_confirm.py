"""写操作需要用户确认的离线测试。python -m unittest tests.test_write_confirm -v"""

import unittest
from unittest.mock import MagicMock, patch

from agent import run_agent
from tests.fakes import FakeClient, ask, reply

SAVE = ("c1", "save_memory", '{"content": "实验用 seed 42"}')


@patch("agent.log")
class WriteConfirmTest(unittest.TestCase):
    def run_save(self, confirm):
        client = FakeClient([reply(tool_calls=[SAVE], finish_reason="tool_calls"), reply("好的")])
        history = ask("记住：实验用 seed 42")
        with patch("agent.run_tool", return_value={"ok": True, "id": 1}) as run_tool:
            r = run_agent(client, history, confirm=confirm)
        tool_result = [m for m in history if m["role"] == "tool"][0]["content"]
        return r, run_tool, tool_result

    def test_confirmed_write_is_executed(self, _log):
        confirm = MagicMock(return_value=True)
        _, run_tool, _ = self.run_save(confirm)
        confirm.assert_called_once_with("save_memory", '{"content": "实验用 seed 42"}')   # 用户看到的就是要写的内容
        run_tool.assert_called_once()

    def test_refused_write_is_not_executed(self, _log):
        _, run_tool, tool_result = self.run_save(MagicMock(return_value=False))
        run_tool.assert_not_called()
        self.assertIn("没有确认", tool_result)                       # 模型被如实告知没有写入

    def test_no_confirm_function_means_no_write(self, _log):
        _, run_tool, _ = self.run_save(None)
        run_tool.assert_not_called()

    def test_read_tools_do_not_ask(self, _log):
        confirm = MagicMock(return_value=False)
        client = FakeClient([reply(tool_calls=[("c1", "calculate_stats", '{"numbers": [1, 2]}')],
                                   finish_reason="tool_calls"), reply("均值 1.5")])
        r = run_agent(client, ask("算 1 2"), confirm=confirm)
        confirm.assert_not_called()
        self.assertEqual(r.tool_calls, 1)


class DescribeWriteTest(unittest.TestCase):
    """确认时给用户看的那句话。"""

    def setUp(self):
        import tempfile
        from pathlib import Path
        import memory
        import tools
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.conn = memory.connect(str(Path(tmp.name) / "m.sqlite3"))
        memory.save(self.conn, "下次实验用 seed 42")
        p = patch.object(tools, "_memory_conn", self.conn)
        p.start()
        self.addCleanup(p.stop)
        self.tools = tools

    def test_forget_shows_the_memory_text(self):
        detail = self.tools.describe_write("forget_memory", '{"memory_id": 1}')
        self.assertIn("下次实验用 seed 42", detail)                     # 不只显示编号，用户知道删的是哪一条

    def test_unknown_or_bad_id(self):
        self.assertIn("没有找到", self.tools.describe_write("forget_memory", '{"memory_id": 99}'))
        self.assertIn("编号无效", self.tools.describe_write("forget_memory", '{"memory_id": "abc"}'))

    def test_ctrl_d_at_prompt_means_no(self):
        import chat
        with patch("builtins.input", side_effect=EOFError), patch("builtins.print"):
            self.assertFalse(chat.confirm_write("save_memory", '{"content": "x"}'))   # 不再让程序崩溃退出


if __name__ == "__main__":
    unittest.main()
