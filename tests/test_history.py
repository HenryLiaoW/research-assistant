"""历史裁剪的离线测试：不碰网络。python -m unittest tests.test_history -v"""

import unittest

from agent import new_history, trim_history

SYSTEM = {"role": "system", "content": "s"}


def turn(i: int, with_tool: bool = False) -> list:
    """造一个回合：user → [assistant(tool_calls) → tool] → assistant。"""
    msgs = [{"role": "user", "content": f"q{i}"}]
    if with_tool:
        msgs.append({"role": "assistant", "content": None,
                     "tool_calls": [{"id": f"c{i}", "type": "function",
                                     "function": {"name": "calculate_stats", "arguments": "{}"}}]})
        msgs.append({"role": "tool", "tool_call_id": f"c{i}", "content": "{}"})
    msgs.append({"role": "assistant", "content": f"a{i}"})
    return msgs


class TrimHistoryTest(unittest.TestCase):
    def test_under_limit_unchanged(self):
        h = [SYSTEM] + turn(1) + turn(2)
        self.assertEqual(trim_history(h, 10), h)

    def test_keeps_system_and_last_n_turns(self):
        h = [SYSTEM] + turn(1) + turn(2) + turn(3)
        out = trim_history(h, 2)
        self.assertEqual(out[0], SYSTEM)
        self.assertEqual([m["content"] for m in out if m["role"] == "user"], ["q2", "q3"])

    def test_never_splits_tool_call_from_tool_result(self):
        h = [SYSTEM] + turn(1, with_tool=True) + turn(2, with_tool=True) + turn(3)
        out = trim_history(h, 2)
        # 每条 tool 消息前面必须能找到带同一 id 的 assistant tool_calls
        for i, m in enumerate(out):
            if m["role"] == "tool":
                ids = [tc["id"] for tc in out[i - 1].get("tool_calls", [])]
                self.assertIn(m["tool_call_id"], ids)
        self.assertEqual(out[1]["role"], "user")  # 裁剪后第一条非 system 一定是 user

    def test_pending_user_message_counts_as_turn(self):
        # run_agent 调用时最后一条是刚追加、还没回答的 user 消息，它本身算一个回合
        h = [SYSTEM] + turn(1) + turn(2) + [{"role": "user", "content": "q3"}]
        out = trim_history(h, 1)
        self.assertEqual(out, [SYSTEM, {"role": "user", "content": "q3"}])

    def test_new_history_has_only_system(self):
        h = new_history()
        self.assertEqual(len(h), 1)
        self.assertEqual(h[0]["role"], "system")


if __name__ == "__main__":
    unittest.main()
