"""Agent 循环的离线测试：用"假模型"代替真实 API，不联网、不花钱。python -m unittest tests.test_agent_loop -v

假模型按顺序返回事先写好的回复，并记下每次收到的 messages，用来检查：
正常回答、调用工具后再回答、截断、轮数上限这几种结束方式，以及历史里的工具协议是否完整。
"""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

import config
from agent import new_history, run_agent


def reply(content=None, tool_calls=None, finish_reason="stop"):
    """造一个和 openai SDK 返回结构一样的回复对象（只造 run_agent 用到的字段）。"""
    calls = None
    if tool_calls:
        calls = [SimpleNamespace(id=cid, function=SimpleNamespace(name=name, arguments=args))
                 for cid, name, args in tool_calls]
    message = SimpleNamespace(content=content, tool_calls=calls)
    return SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason=finish_reason)],
                           usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5))


class FakeClient:
    """假的 API 客户端：client.chat.completions.create(...) 依次返回 replies 里的回复。"""

    def __init__(self, replies):
        self.replies = list(replies)
        self.sent = []                                   # 每次调用收到的 messages（复制一份）
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.sent.append([dict(m) for m in kwargs["messages"]])
        if not self.replies:
            raise AssertionError("假模型被多调用了一次")
        return self.replies.pop(0)


def ask(text):
    history = new_history()
    history.append({"role": "user", "content": text})
    return history


def assert_protocol_complete(test, history):
    """每条带 tool_calls 的 assistant 消息后面，必须紧跟每个 id 对应的 tool 结果。"""
    for i, m in enumerate(history):
        if m.get("tool_calls"):
            ids = [tc["id"] for tc in m["tool_calls"]]
            following = [x.get("tool_call_id") for x in history[i + 1:i + 1 + len(ids)]]
            test.assertEqual(following, ids)


@patch("agent.log")                                      # 测试时不打印执行记录
class AgentLoopTest(unittest.TestCase):
    def test_plain_answer(self, _log):
        client = FakeClient([reply("你好")])
        history = ask("hi")
        r = run_agent(client, history)
        self.assertEqual((r.status, r.answer, r.model_calls, r.tool_calls), ("ok", "你好", 1, 0))
        self.assertEqual(history[-1], {"role": "assistant", "content": "你好"})

    def test_tool_round_then_answer(self, _log):
        client = FakeClient([
            reply(tool_calls=[("c1", "calculate_stats", '{"numbers": [1, 2, 3]}')], finish_reason="tool_calls"),
            reply("均值是 2"),
        ])
        history = ask("算一下 1 2 3 的均值")
        r = run_agent(client, history)
        self.assertEqual((r.status, r.model_calls, r.tool_calls), ("ok", 2, 1))
        tool_msg = [m for m in client.sent[1] if m["role"] == "tool"][0]   # 第二次请求带上了真实工具结果
        self.assertEqual(tool_msg["tool_call_id"], "c1")
        self.assertIn('"mean": 2', tool_msg["content"])
        assert_protocol_complete(self, history)

    def test_truncated_reply_with_tool_call_is_not_executed(self, _log):
        # 被截断的回复里带着工具请求：不能执行，也不能再去调模型
        client = FakeClient([reply(tool_calls=[("c1", "save_memory", '{"content": "半截')], finish_reason="length")])
        history = ask("记住……")
        with patch("agent.run_tool") as run_tool:
            r = run_agent(client, history)
        run_tool.assert_not_called()
        self.assertEqual((r.status, r.model_calls, r.tool_calls), ("truncated", 1, 0))
        self.assertIn("没有执行", r.answer)
        self.assertNotIn("tool_calls", history[-1])      # 历史里不留下没有结果的工具请求
        assert_protocol_complete(self, history)

    def test_truncated_plain_answer(self, _log):
        r = run_agent(FakeClient([reply("说到一半", finish_reason="length")]), ask("讲讲"))
        self.assertEqual((r.status, r.answer), ("truncated", "说到一半"))

    def test_max_rounds_stops_and_keeps_protocol(self, _log):
        replies = [reply(tool_calls=[(f"c{i}", "calculate_stats", '{"numbers": [1]}')], finish_reason="tool_calls")
                   for i in range(config.MAX_TOOL_ROUNDS)]
        client = FakeClient(replies)
        history = ask("一直调工具")
        r = run_agent(client, history)
        self.assertEqual((r.status, r.model_calls, r.tool_calls),
                         ("max_rounds", config.MAX_TOOL_ROUNDS, config.MAX_TOOL_ROUNDS))
        self.assertEqual(len(client.sent), config.MAX_TOOL_ROUNDS)   # 到上限后不再调模型
        assert_protocol_complete(self, history)


if __name__ == "__main__":
    unittest.main()
