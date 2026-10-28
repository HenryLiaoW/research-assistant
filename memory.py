"""
memory.py — 阶段 2 下半：长期记忆的存储层（SQLite）。

只管数据库，不知道模型、工具、对话的存在。谁调用：tools.py 里的记忆工具、chat.py 的 /memories。

一张表 memories：
    id          自增主键，用户/模型引用一条记忆时用它
    content     记忆原文（用户要求记住的那句话）
    created_at  写入时间（UTC，ISO 格式字符串）
    deleted     0 = 有效，1 = 已软删除（查询时过滤掉，不物理删除，方便追溯）
    scope       作用域（课题名），写入和检索都按它隔离；旧库没有这一列时自动补上，旧记忆归入 "default"

作用域为什么存在：记忆是按课题说的（"下次实验用 seed 42"是哪个实验的？），课题一多，关键词检索会把
另一个课题的记忆召回来，模型拿着答得很顺——数字对、来源错。所以每条记忆带作用域，检索只在当前作用域里找。
当前作用域由用户/程序设定（tools.set_memory_scope），不是模型的参数：模型只给内容，作用域由程序盖上，
资料里夹带的指令也改不了它。

安全约定：
- 所有 SQL 用 ? 占位符传参，外部文字（含模型给的）永远是"值"，不拼进语句；
- 本模块不提供"执行任意 SQL"的入口，模型也没有这样的工具。
"""

import sqlite3
from datetime import datetime, timezone

DEFAULT_SCOPE = "default"
MAX_SCOPE_CHARS = 64


def valid_scope(scope) -> bool:
    """作用域名：非空字符串，去掉首尾空白后不超过 MAX_SCOPE_CHARS，不含换行。谁调用：save / search、tools.set_memory_scope。"""
    return (isinstance(scope, str) and bool(scope.strip()) and len(scope.strip()) <= MAX_SCOPE_CHARS
            and "\n" not in scope)


def connect(db_path: str) -> sqlite3.Connection:
    """打开（不存在则创建）数据库并确保表存在。谁调用：tools.py 启动时、chat.py、测试。
    旧库（没有 scope 列）打开时补上这一列，已有记忆归入 DEFAULT_SCOPE，不丢数据。"""
    # check_same_thread=False：网页版在后台线程里执行工具，连接是在主线程建的。
    # webapp.py 保证同一时间只处理一条消息，不会有两个线程同时写。
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row  # 查询结果可以按列名取值：row["content"]
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS memories (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            content    TEXT    NOT NULL,
            created_at TEXT    NOT NULL,
            deleted    INTEGER NOT NULL DEFAULT 0,
            scope      TEXT    NOT NULL DEFAULT 'default'
        )
        """
    )
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(memories)")}
    if "scope" not in columns:                       # 阶段 2 建的旧库：加列，旧行自动得到默认值
        conn.execute("ALTER TABLE memories ADD COLUMN scope TEXT NOT NULL DEFAULT 'default'")
    conn.commit()
    return conn


def _row_to_dict(row: sqlite3.Row) -> dict:
    return {"id": row["id"], "content": row["content"], "created_at": row["created_at"], "scope": row["scope"]}


def save(conn: sqlite3.Connection, content: str, scope: str = DEFAULT_SCOPE) -> dict:
    """写入一条记忆到指定作用域。输入：原文、作用域。输出：{"ok": True, "id": 新 id}；空内容或坏作用域返回 ok=False。"""
    if not isinstance(content, str) or not content.strip():
        return {"ok": False, "error": "记忆内容不能为空"}
    if not valid_scope(scope):
        return {"ok": False, "error": f"作用域无效：{scope!r}"}
    content = content.strip()
    created_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    cur = conn.execute(
        "INSERT INTO memories (content, created_at, scope) VALUES (?, ?, ?)",
        (content, created_at, scope.strip()), #防注入
    )
    conn.commit()
    return {"ok": True, "id": cur.lastrowid, "content": content, "scope": scope.strip()}


def search(conn: sqlite3.Connection, keyword: str, limit: int = 5, scope: str = DEFAULT_SCOPE) -> dict:
    """在一个作用域里按关键词子串匹配有效记忆，最新的在前。输入：关键词、条数上限、作用域。
    输出：{"ok": True, "results": [...]}。空关键词返回该作用域最近 limit 条（相当于"最近记了什么"）。
    其他作用域的记忆不会出现：过滤在 SQL 里做，不是取出来再筛。
    """
    if not isinstance(keyword, str):
        return {"ok": False, "error": "关键词必须是字符串"}
    if not valid_scope(scope):
        return {"ok": False, "error": f"作用域无效：{scope!r}"}
    keyword = keyword.strip()
    if keyword:
        rows = conn.execute(
            "SELECT id, content, created_at, scope FROM memories "
            "WHERE deleted = 0 AND scope = ? AND content LIKE ? "
            "ORDER BY id DESC LIMIT ?",
            (scope.strip(), f"%{keyword}%", limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT id, content, created_at, scope FROM memories "
            "WHERE deleted = 0 AND scope = ? ORDER BY id DESC LIMIT ?",
            (scope.strip(), limit),
        ).fetchall()
    return {"ok": True, "keyword": keyword, "scope": scope.strip(), "results": [_row_to_dict(r) for r in rows]}


def forget(conn: sqlite3.Connection, memory_id: int) -> dict:
    """软删除一条记忆。输入：id。输出：{"ok": True, "id": id}；不存在或已删返回 ok=False。"""
    if isinstance(memory_id, bool) or not isinstance(memory_id, int):
        return {"ok": False, "error": f"id 必须是整数，收到的是 {memory_id!r}"}
    cur = conn.execute(
        "UPDATE memories SET deleted = 1 WHERE id = ? AND deleted = 0",
        (memory_id,),
    )
    conn.commit()
    if cur.rowcount == 0:
        return {"ok": False, "error": f"没有 id 为 {memory_id} 的有效记忆"}
    return {"ok": True, "id": memory_id}


def list_all(conn: sqlite3.Connection, scope: str = None) -> list:
    """列出有效记忆（按 id 升序）：scope 为 None 列全部作用域（每条带 scope），否则只列那个作用域。
    给 chat.py 的 /memories 和网页记忆面板用，不给模型用。"""
    if scope is None:
        rows = conn.execute(
            "SELECT id, content, created_at, scope FROM memories WHERE deleted = 0 ORDER BY id"
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT id, content, created_at, scope FROM memories WHERE deleted = 0 AND scope = ? ORDER BY id",
            (scope,),
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


def scopes(conn: sqlite3.Connection) -> list:
    """每个作用域有几条有效记忆：[{"scope": ..., "count": ...}]，按名字排序。给 chat.py 的 /scope 用。"""
    rows = conn.execute(
        "SELECT scope, COUNT(*) AS n FROM memories WHERE deleted = 0 GROUP BY scope ORDER BY scope"
    ).fetchall()
    return [{"scope": r["scope"], "count": r["n"]} for r in rows]
