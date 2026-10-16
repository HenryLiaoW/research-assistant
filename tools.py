"""
tools.py — 工具箱：工具的"菜单"（给模型看的 JSON Schema）和"实现"（给程序执行的 Python 函数）。

设计原则：
- 模型只看到 TOOLS 里的名称、描述、参数 schema，看不到任何代码；
- 模型只会"提出"调用（工具名 + 参数的 JSON 字符串），真正执行的是 run_tool()；
- 每个工具永远返回一个 dict，并带 "ok" 字段：成功 ok=True 加结果，失败 ok=False 加 error。
  模型拿到的是结构化数据，失败时能如实转述，而不是编一个数；
- 不用 eval / exec，不执行模型生成的代码；参数先校验再使用。
"""

import json
import math
import statistics

# ---- 菜单：发给模型的工具定义（OpenAI 兼容格式）----
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "calculate_stats",
            "description": (
                "对一组数值计算描述统计：数量、均值、最小值、最大值、总体标准差（分母为 N）。"
                "当用户给出具体数字并要求均值、标准差、极值等统计量时调用。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "numbers": {
                        "type": "array",
                        "items": {"type": "number"},
                        "description": "要统计的数值列表，至少包含 1 个数",
                    }
                },
                "required": ["numbers"],
            },
        },
    }
]


# ---- 实现 ----
def calculate_stats(numbers) -> dict:
    """计算数量、均值、最小值、最大值、总体标准差。

    谁调用：run_tool()（参数来自模型，所以必须先校验）。
    输入：数值列表。输出：dict，成功 ok=True，失败 ok=False 加 error。
    """
    if not isinstance(numbers, list):
        return {"ok": False, "error": f"numbers 必须是数值列表，收到的是 {type(numbers).__name__}"}
    if len(numbers) == 0:
        return {"ok": False, "error": "numbers 是空列表，无法计算统计量"}
    for i, x in enumerate(numbers):
        # bool 是 int 的子类，True/False 不应被当成 1/0 算进去
        if isinstance(x, bool) or not isinstance(x, (int, float)):
            return {"ok": False, "error": f"第 {i + 1} 个元素 {x!r} 不是数值"}
        if not math.isfinite(x):
            return {"ok": False, "error": f"第 {i + 1} 个元素 {x!r} 不是有限数值"}

    values = [float(x) for x in numbers]
    return {
        "ok": True,
        "count": len(values),
        "mean": statistics.fmean(values),
        "min": min(values),
        "max": max(values),
        "population_std": statistics.pstdev(values),
        "std_definition": "总体标准差：sqrt(Σ(x - mean)² / N)，分母为 N；不是样本标准差（分母 N-1）",
    }


# 工具名 → 实现函数。模型给的名字只在这张表里查，不会凭名字去执行任意代码。
TOOL_IMPLEMENTATIONS = {
    "calculate_stats": calculate_stats,
}


def run_tool(name: str, arguments_json: str) -> dict:
    """统一执行入口：查表 → 解析参数 JSON → 调用实现 → 永远返回 dict。

    谁调用：agent.execute_tool_calls()。
    输入：模型给的工具名和参数 JSON 字符串。输出：带 ok 字段的 dict，任何失败都不抛异常。
    """
    impl = TOOL_IMPLEMENTATIONS.get(name)
    if impl is None:
        return {"ok": False, "error": f"未知工具：{name}"}
    try:
        args = json.loads(arguments_json)
    except json.JSONDecodeError as e:
        return {"ok": False, "error": f"参数不是合法 JSON：{e}"}
    if not isinstance(args, dict):
        return {"ok": False, "error": "参数必须是 JSON 对象（键值对）"}
    try:
        return impl(**args)
    except TypeError as e:  # 缺少必填参数，或多了不认识的参数
        return {"ok": False, "error": f"参数不匹配：{e}"}
    except Exception as e:  # 工具内部的意外错误也不能让整个程序崩溃
        return {"ok": False, "error": f"工具执行异常：{type(e).__name__}: {e}"}
