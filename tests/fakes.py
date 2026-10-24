"""测试用的假对象：假模型客户端和造回复的工具函数。test_agent_loop、test_citations 共用。"""

from types import SimpleNamespace

from agent import new_history


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


def ask(text, history=None):
    """在历史末尾追加一条用户消息；不给历史时新开一段会话。"""
    history = history if history is not None else new_history()
    history.append({"role": "user", "content": text})
    return history
