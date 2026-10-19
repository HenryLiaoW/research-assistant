"""
memory.py — 阶段 2 下半：长期记忆的存储层（SQLite）。

只管数据库，不知道模型、工具、对话的存在。谁调用：tools.py 里的记忆工具、chat.py 的 /memories。

一张表 memories：
    id          自增主键，用户/模型引用一条记忆时用它
    content     记忆原文（用户要求记住的那句话）
    created_at  写入时间（UTC，ISO 格式字符串）
    deleted     0 = 有效，1 = 已软删除（查询时过滤掉，不物理删除，方便追溯）

安全约定：
- 所有 SQL 用 ? 占位符传参，外部文字（含模型给的）永远是"值"，不拼进语句；
- 本模块不提供"执行任意 SQL"的入口，模型也没有这样的工具。
"""

import sqlite3
from datetime import datetime, timezone


def connect(db_path: str) -> sqlite3.Connection:
    """打开（不存在则创建）数据库并确保表存在。谁调用：tools.py 启动时、chat.py、测试。"""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row  # 查询结果可以按列名取值：row["content"]
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS memories (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            content    TEXT    NOT NULL,
            created_at TEXT    NOT NULL,
            deleted    INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    conn.commit()
    return conn


def _row_to_dict(row: sqlite3.Row) -> dict:
    return {"id": row["id"], "content": row["content"], "created_at": row["created_at"]}


def save(conn: sqlite3.Connection, content: str) -> dict:
    """写入一条记忆。输入：原文。输出：{"ok": True, "id": 新 id}；空内容返回 ok=False。"""
    if not isinstance(content, str) or not content.strip():
        return {"ok": False, "error": "记忆内容不能为空"}
    content = content.strip()
    created_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    cur = conn.execute(
        "INSERT INTO memories (content, created_at) VALUES (?, ?)",
        (content, created_at), #防注入
    )
    conn.commit()
    return {"ok": True, "id": cur.lastrowid, "content": content}


def search(conn: sqlite3.Connection, keyword: str, limit: int = 5) -> dict:
    """按关键词子串匹配有效记忆，最新的在前。输入：关键词、条数上限。输出：{"ok": True, "results": [...]}。

    空关键词返回最近 limit 条（相当于"最近记了什么"）。
    """
    if not isinstance(keyword, str):
        return {"ok": False, "error": "关键词必须是字符串"}
    keyword = keyword.strip()
    if keyword:
        rows = conn.execute(
            "SELECT id, content, created_at FROM memories "
            "WHERE deleted = 0 AND content LIKE ? "
            "ORDER BY id DESC LIMIT ?",
            (f"%{keyword}%", limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT id, content, created_at FROM memories "
            "WHERE deleted = 0 ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return {"ok": True, "keyword": keyword, "results": [_row_to_dict(r) for r in rows]}


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


def list_all(conn: sqlite3.Connection) -> list:
    """列出全部有效记忆（按 id 升序）。给 chat.py 的 /memories 用，不给模型用。"""
    rows = conn.execute(
        "SELECT id, content, created_at FROM memories WHERE deleted = 0 ORDER BY id"
    ).fetchall()
    return [_row_to_dict(r) for r in rows]
