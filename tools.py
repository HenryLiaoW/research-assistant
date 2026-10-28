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

import config
import csv_tools
import documents
import memory
import retrieval

# ---- 菜单：发给模型的工具定义（OpenAI 兼容格式）----

def _search_description() -> str:
    """search_documents 的说明：把索引里的文件名写进去，模型才知道 file 参数能填什么。
    文件名读自 manifest.json（进程启动时读一次，不加载模型）；没有索引时就不列。"""
    text = ("在资料库（论文、实验协议等 PDF）里检索和问题相关的段落，返回原文片段和出处（文件、PDF 页码、章节）。"
            "用户问到资料里的内容时调用。资料是英文的，所以 query 要用英文；"
            "如果用户用其他语言提问，先把问题翻译成英文再检索。")
    files = retrieval.indexed_files(config.INDEX_DIR)
    if files:
        text += f"资料库现有文件：{'、'.join(files)}。用户指明了某一份时，用 file 参数只在它里面检索。"
    return text


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
    },
    {
        "type": "function",
        "function": {
            "name": "save_memory",
            "description": (
                "把一条信息写入长期记忆（跨会话保存）。"
                "只有当用户明确要求记住、记下来、保存某件事时才调用；内容用用户的原话。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "content": {"type": "string", "description": "要记住的内容（用户原话）"}
                },
                "required": ["content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_memory",
            "description": (
                "按关键词在长期记忆里查找，返回最近的若干条（含 id）。"
                "用户问到以前说过的事时调用；关键词留空则返回最近记录。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "keyword": {"type": "string", "description": "关键词，子串匹配；可为空字符串"}
                },
                "required": ["keyword"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "forget_memory",
            "description": (
                "按 id 删除一条长期记忆（软删除，之后查不到）。"
                "只有当用户明确要求忘掉、删掉某条记忆时才调用；id 必须来自 search_memory 的结果。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "memory_id": {"type": "integer", "description": "要删除的记忆 id"}
                },
                "required": ["memory_id"],
            },
        },
    },
        {
        "type": "function",
        "function": {
            "name": "list_experiment_files",
            "description": "列出实验数据目录里有哪些 CSV 文件（文件名和大小）。不确定有哪些文件时先调用。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "describe_experiment_file",
            "description": (
                "查看一个实验 CSV 文件的表头：总行数，以及每列的名字、类型、是否数值列、缺失数、不同取值个数。"
                "不返回原始数据。调用 group_statistics 之前用它确认列名。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "file": {"type": "string", "description": "文件名，必须来自 list_experiment_files 的结果，如 sample_results.csv"}
                },
                "required": ["file"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "group_statistics",
            "description": (
                "对实验 CSV 文件里的一个数值列做描述统计，可按另一列分组。"
                "每组返回 count（有效值个数）、missing（缺失个数）、mean、min、max、population_std（总体标准差）。"
                "只返回汇总，不返回原始行。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "file": {"type": "string", "description": "文件名，必须来自 list_experiment_files 的结果"},
                    "value_column": {"type": "string", "description": "要统计的数值列名，必须来自 describe_experiment_file 的结果"},
                    "group_by": {"type": "string", "description": "按哪一列分组；不分组时省略"},
                },
                "required": ["file", "value_column"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_documents",
            "description": _search_description(),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "英文检索词，例如 'number of pretraining epochs'"},
                    "k": {"type": "integer", "description": f"返回几个片段，1–{config.MAX_SEARCH_K}，默认 {config.SEARCH_TOP_K}"},
                    "file": {"type": "string", "description": "只在这一份文件里检索，必须是资料库现有文件名；用户没有指明文件时省略"},
                },
                "required": ["query"],
            },
        },
    },
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


# ---- 长期记忆工具（阶段 2 下半）：薄薄一层，把 memory.py 的函数包成工具 ----
# 数据库连接在模块加载时打开一次，整个进程共用。路径来自 config，文件不进 git。
_memory_conn = memory.connect(config.MEMORY_DB_PATH)

# 当前记忆作用域（课题）。由用户/程序设定，模型的工具参数里没有它：模型只给内容，作用域由这里盖上。
_memory_scope = config.MEMORY_SCOPE


def memory_scope() -> str:
    """当前作用域。谁调用：chat.py 显示、tracing.start 记日志、webapp.py。"""
    return _memory_scope


def set_memory_scope(name) -> dict:
    """切换作用域。谁调用：chat.py 的 /scope、webapp.py。名字不合法时不切换，返回 ok=False。"""
    global _memory_scope
    if not memory.valid_scope(name):
        return {"ok": False, "error": f"作用域名无效：{name!r}（非空、不超过 {memory.MAX_SCOPE_CHARS} 字符、不含换行）"}
    _memory_scope = name.strip()
    return {"ok": True, "scope": _memory_scope}


def save_memory(content) -> dict:
    """写入一条记忆到当前作用域。谁调用：run_tool()。输入：用户原话。输出：ok + 新 id + scope。"""
    return memory.save(_memory_conn, content, scope=_memory_scope)


def search_memory(keyword) -> dict:
    """在当前作用域里按关键词查找。谁调用：run_tool()。输出：ok + results 列表（每条含 id / content / created_at / scope）。"""
    return memory.search(_memory_conn, keyword, limit=config.MEMORY_SEARCH_LIMIT, scope=_memory_scope)


def describe_write(name: str, args_json: str) -> str:
    """把一次写操作说成给用户看的一句话，确认时显示。谁调用：chat.confirm_write()、webapp.py。
    删除时把这条记忆的原文也查出来：只显示编号的话，用户不知道删的是哪一条。"""
    try:
        args = json.loads(args_json)
    except (TypeError, json.JSONDecodeError):
        args = None
    if not isinstance(args, dict):
        args = {}
    if name == "save_memory":
        return f"保存一条长期记忆到课题「{_memory_scope}」：「{args.get('content')}」"
    if name == "forget_memory":
        try:
            memory_id = int(args.get("memory_id"))
        except (TypeError, ValueError):
            return f"删除长期记忆 #{args.get('memory_id')}（编号无效）"
        found = next((m for m in list_memories() if m["id"] == memory_id), None)
        return f"删除长期记忆 #{memory_id}：" + (f"「{found['content']}」" if found else "（没有找到这条有效记忆）")
    return f"执行 {name}（参数 {args_json}）"


def forget_memory(memory_id) -> dict:
    """软删除。谁调用：run_tool()、webapp.py（记忆面板）。输入：id。输出：ok；id 不存在或已删则 ok=False。"""
    return memory.forget(_memory_conn, memory_id)


def list_memories(scope=None) -> list:
    """有效记忆：scope 为 None 列全部作用域（每条带 scope），否则只列那个作用域。
    不是模型工具（不在 TOOL_IMPLEMENTATIONS 里），谁调用：describe_write()、webapp.py（记忆面板）。"""
    return memory.list_all(_memory_conn, scope)


def memory_scopes() -> list:
    """每个作用域的有效记忆数。谁调用：chat.py 的 /scope。"""
    return memory.scopes(_memory_conn)



# ---- 受限 CSV 工具（阶段 3）：薄薄一层，把 csv_tools.py 的函数包成工具 ----
# 模型只给文件名和列名；目录和上限由这里从 config 补上，模型无法指定读哪个目录。
def list_experiment_files() -> dict:
    """列出数据目录里的 CSV。谁调用：run_tool()。没有参数。"""
    return csv_tools.list_files(config.DATA_DIR)


def describe_experiment_file(file) -> dict:
    """看表头。谁调用：run_tool()。输入：文件名。"""
    return csv_tools.describe_file(config.DATA_DIR, file, config.MAX_CSV_ROWS)


def group_statistics(file, value_column, group_by=None) -> dict:
    """分组统计。谁调用：run_tool()。输入：文件名、值列、可选的分组列。"""
    return csv_tools.group_stats(config.DATA_DIR, file, value_column, group_by,
                                 config.MAX_CSV_ROWS, config.MAX_GROUPS)



# ---- 资料检索工具（阶段 4e）：把 retrieval.DocumentSearch 包成工具 ----
# 检索器第一次被调用时才创建（要加载约 2 GB 的嵌入模型，十几秒），之后整个进程复用。
# 注意：检索到的原文片段会作为工具结果发给模型 API；整篇论文不会发出去。
_searcher = None


def _get_searcher():
    """懒加载检索器。先检查索引在不在、资料变没变，不通过就直接报错，不白白加载模型（约 10 秒）。"""
    global _searcher
    if _searcher is None:
        retrieval.ensure_fresh(config.INDEX_DIR, documents.pdf_files(config.DOCS_DIR))
        _searcher = retrieval.DocumentSearch(config.INDEX_DIR, retrieval.Embedder(config.EMBEDDING_MODEL))
    return _searcher


def index_info():
    """已加载索引的版本（嵌入模型 + 建立时间），给运行日志用；本次运行还没检索过时返回 None。谁调用：tracing.finish()。"""
    if _searcher is None:
        return None
    return {"model": _searcher.manifest.get("model"), "built_at": _searcher.manifest.get("built_at")}


def search_documents(query, k=None, file=None) -> dict:
    """检索资料。谁调用：run_tool()。输入：检索词、可选的条数、可选的文件名（只在这一份里找）。
    输出：ok + 每个片段的原文和出处。file 写错时返回现有文件名，模型下一轮能改对。"""
    if not isinstance(query, str) or not query.strip():
        return {"ok": False, "error": "query 必须是非空字符串"}
    if len(query) > config.MAX_QUERY_CHARS:
        return {"ok": False, "error": f"query 太长（{len(query)} 字符），上限 {config.MAX_QUERY_CHARS}"}
    if k is None:
        k = config.SEARCH_TOP_K
    if isinstance(k, bool) or not isinstance(k, int) or not 1 <= k <= config.MAX_SEARCH_K:
        return {"ok": False, "error": f"k 必须是 1 到 {config.MAX_SEARCH_K} 之间的整数，收到 {k!r}"}
    if file is not None and (not isinstance(file, str) or not file.strip()):
        return {"ok": False, "error": "file 必须是文件名字符串，不指定文件时省略"}
    try:
        searcher = _get_searcher()
    except (FileNotFoundError, ValueError) as e:     # 没建索引 / 资料变了 / 索引和当前模型不一致
        return {"ok": False, "error": str(e)}
    if file is not None:
        file = file.strip()
        if file not in searcher.files:
            return {"ok": False, "error": f"资料库里没有文件 {file}，现有文件：{'、'.join(searcher.files)}"}
    results = searcher.search(query.strip(), k, file=file) # 核心代码
    return {
        "ok": True,
        "query": query.strip(),
        "file": file,
        "results": [{"chunk_id": r["chunk_id"], "file": r["file"], "page": r["page"], "label": r["label"],
                     "section": r["section"], "score": r["score"], "text": r["text"]} for r in results],
    }


# 工具名 → 实现函数。模型给的名字只在这张表里查，不会凭名字去执行任意代码。
TOOL_IMPLEMENTATIONS = {
    "calculate_stats": calculate_stats,
    "save_memory": save_memory,
    "search_memory": search_memory,
    "forget_memory": forget_memory,
    "list_experiment_files": list_experiment_files,
    "describe_experiment_file": describe_experiment_file,
    "group_statistics": group_statistics,
    "search_documents": search_documents,
}


def run_tool(name: str, arguments_json: str) -> dict:
    """统一执行入口：查表 → 解析参数 JSON → 调用实现 → 永远返回 dict。

    谁调用：agent.execute_tool_calls()。
    输入：模型给的工具名和参数 JSON 字符串。输出：带 ok 字段的 dict，任何失败都不抛异常。
    """
    impl = TOOL_IMPLEMENTATIONS.get(name) # for example, "calculate_stats" -> calculate_stats()
    if impl is None:
        return {"ok": False, "error": f"未知工具：{name}"}
    try:
        # 之前可能：arguments_json = '{"numbers": [0.62, 0.58, 0.65]}'
        # 之后（dict）：args = {"numbers": [0.62, 0.58, 0.65]}  
        args = json.loads(arguments_json)
    except json.JSONDecodeError as e:
        return {"ok": False, "error": f"参数不是合法 JSON：{e}"}
    if not isinstance(args, dict):
        return {"ok": False, "error": "参数必须是 JSON 对象（键值对）"}
    try:
        return impl(**args) # impl(**args) 就等于 calculate_stats(numbers=[0.62, 0.58, 0.65])
    except TypeError as e:  # 缺少必填参数，或多了不认识的参数
        return {"ok": False, "error": f"参数不匹配：{e}"}
    except Exception as e:  # 工具内部的意外错误也不能让整个程序崩溃
        return {"ok": False, "error": f"工具执行异常：{type(e).__name__}: {e}"}
