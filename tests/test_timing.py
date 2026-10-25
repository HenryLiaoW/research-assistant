"""计时口径的离线测试：等用户确认的时间不算工具耗时，也不占总时间预算。python -m unittest tests.test_timing -v"""

import unittest
from unittest.mock import patch

import config
from agent import execute_tool_calls, run_agent
from tests.fakes import FakeClient, ask, reply

SAVE = ("c1", "save_memory", '{"content": "实验用 seed 42"}')
STATS = ("c1", "calculate_stats", '{"numbers": [1, 2]}')
SLOW = config.MAX_TOTAL_SECONDS + 80          # 比总时间预算还长的秒数


class FakeClock:
    """假时钟，代替 time.perf_counter：只有测试里手动拨动时才"过去"时间，其余操作耗时都是 0。"""

    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


@patch("agent.log")
class TimingTest(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        patcher = patch("agent.time")              # 只替换 agent.py 里的 time，别的模块不受影响
        patcher.start().perf_counter = self.clock
        self.addCleanup(patcher.stop)

    def slow_yes(self, name, args_json):
        """用户想了很久才输入 y。"""
        self.clock.now += SLOW
        return True

    def test_wait_is_not_counted_as_tool_time(self, _log):
        tool_calls = reply(tool_calls=[SAVE]).choices[0].message.tool_calls
        with patch("agent.run_tool", return_value={"ok": True, "id": 1}):
            records = execute_tool_calls(tool_calls, ask("记住"), confirm=self.slow_yes)
        self.assertEqual(records[0]["wait_ms"], SLOW * 1000)
        self.assertEqual(records[0]["ms"], 0)                  # 工具本身没花时间

    def test_wait_does_not_use_time_budget(self, _log):
        client = FakeClient([reply(tool_calls=[SAVE], finish_reason="tool_calls"), reply("已记住")])
        with patch("agent.run_tool", return_value={"ok": True, "id": 1}):
            r = run_agent(client, ask("记住：实验用 seed 42"), confirm=self.slow_yes)
        self.assertEqual((r.status, r.answer, r.model_calls), ("ok", "已记住", 2))   # 没有因为等人而超时
        self.assertEqual(r.wait_seconds, SLOW)

    def test_slow_tool_still_hits_time_budget(self, _log):
        def slow_tool(name, args_json):
            self.clock.now += SLOW                             # 工具自己跑得慢：这部分要算进预算
            return {"ok": True}

        client = FakeClient([reply(tool_calls=[STATS], finish_reason="tool_calls")])
        with patch("agent.run_tool", side_effect=slow_tool):
            r = run_agent(client, ask("算 1 2"))
        self.assertEqual(r.status, "time_budget")
        self.assertEqual(r.wait_seconds, 0)


if __name__ == "__main__":
    unittest.main()
