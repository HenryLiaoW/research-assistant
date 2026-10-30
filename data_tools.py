"""
data_tools.py — DataAgent：分析数据库的数据目录（元数据）和只读 SQL 查询。

只管数据库，不知道模型、工具菜单的存在。谁调用：tools.py 的包装函数、两个导入脚本、测试。
所有函数都把数据库路径和上限作为参数传入（由 tools.py 从 config 补上），本模块不读 config，方便测试。

一、数据目录（meta_tables / meta_columns / meta_notes 三张元数据表）
    每张可查询的表在 meta_tables 里登记一行（说明、来源），每列在 meta_columns 里登记说明；
    meta_notes 存术语定义、已知的数据事实、库里没有的内容（相当于一层很薄的"语义层"）。
    导入脚本负责登记；SQL 工具的表白名单和给模型看的表结构都从这里读，所以新数据导入后不用改代码就能查。

二、只读 SQL 查询（query）
    安全由数据库引擎和代码保证，不依赖模型听话：
    - 以只读模式打开文件（mode=ro），引擎层面写不了；
    - set_authorizer：引擎准备语句时对每个操作回调，只放行 SELECT、读白名单表、调用函数、递归 CTE，其余一律拒绝
      （INSERT / UPDATE / DELETE / DROP / ATTACH / PRAGMA / 读 sqlite_master 等）。比用正则检查字符串可靠：
      注释、大小写、多层子查询、CTE 这些写法引擎都看得到，正则看不全；
    - Python sqlite3 的 execute 一次只执行一条语句，"SELECT 1; DROP TABLE x" 直接报错；
    - 返回行数上限（多取一行判断是否截断）、执行超时（progress handler 中断）、SQL 长度上限。
    这是项目"不执行模型生成的代码"原则的有界例外：SQL 只在 SQLite 引擎里运行，碰不到文件系统、网络和进程。

每个对外函数永远返回 dict：成功 ok=True 加结果，失败 ok=False 加 error（和 tools.py 的约定一致）。
"""

import math
import re
import sqlite3
import time
from pathlib import Path

META_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta_tables (
    table_name  TEXT PRIMARY KEY,
    description TEXT NOT NULL,
    source      TEXT NOT NULL            -- 数据从哪来、由哪个脚本导入
);
CREATE TABLE IF NOT EXISTS meta_notes (
    source TEXT NOT NULL,                -- 哪一批数据的说明（同一导入脚本重跑时整批替换）
    topic  TEXT NOT NULL,                -- 术语 / 数据事实 / 不在库里的内容
    text   TEXT NOT NULL,
    PRIMARY KEY (source, topic, text)
);
CREATE TABLE IF NOT EXISTS meta_columns (
    table_name  TEXT NOT NULL REFERENCES meta_tables(table_name),
    column_name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (table_name, column_name)
);
"""
META_TABLES = ("meta_tables", "meta_columns", "meta_notes")
IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


# ---------- 数据目录 ----------

def ensure_meta(conn) -> None:
    conn.executescript(META_SCHEMA)


def register_table(conn, table: str, description: str, source: str, column_docs: dict | None = None) -> None:
    """登记（或更新）一张表的说明和每列说明。列以数据库里的实际列为准：column_docs 里多写的列会报错，
    少写的列说明留空——表结构和说明不会对不上。谁调用：导入脚本。"""
    ensure_meta(conn)
    columns = [r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')]
    if not columns:
        raise ValueError(f"表 {table} 不存在")
    extra = set(column_docs or {}) - set(columns)
    if extra:
        raise ValueError(f"表 {table} 没有这些列：{sorted(extra)}")
    conn.execute("DELETE FROM meta_columns WHERE table_name = ?", (table,))
    conn.execute("INSERT OR REPLACE INTO meta_tables VALUES (?, ?, ?)", (table, description, source))
    conn.executemany("INSERT INTO meta_columns VALUES (?, ?, ?)",
                     [(table, c, (column_docs or {}).get(c, "")) for c in columns])


def register_notes(conn, source: str, notes: list) -> None:
    """整批替换某个来源的说明。notes: [(topic, text)]。谁调用：导入脚本。"""
    ensure_meta(conn)
    conn.execute("DELETE FROM meta_notes WHERE source = ?", (source,))
    conn.executemany("INSERT INTO meta_notes VALUES (?, ?, ?)", [(source, t, x) for t, x in notes])


def notes(db_path: str) -> list:
    """[(topic, text)]，按 topic 分组的顺序。谁调用：describe_catalog()、render_markdown()。"""
    conn = _connect_ro(db_path)
    try:
        return conn.execute("SELECT topic, text FROM meta_notes ORDER BY topic, rowid").fetchall()
    finally:
        conn.close()


def _connect_ro(db_path: str):
    path = Path(db_path)
    if not path.is_file():
        raise FileNotFoundError(f"数据库不存在：{db_path}（先运行导入脚本）")
    return sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)


def catalog(db_path: str) -> dict:
    """{表名: {"description", "source", "columns": [(列名, 类型, 说明)], "rows": 行数}}，只含 meta_tables 登记过的表。
    谁调用：query()（白名单）、describe_catalog()、导入脚本生成文档。"""
    conn = _connect_ro(db_path)
    try:
        out = {}
        for name, desc, source in conn.execute("SELECT table_name, description, source FROM meta_tables ORDER BY table_name"):
            docs = dict(conn.execute("SELECT column_name, description FROM meta_columns WHERE table_name = ?", (name,)))
            cols = [(r[1], r[2] or "", docs.get(r[1], "")) for r in conn.execute(f'PRAGMA table_info("{name}")')]
            rows = conn.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
            out[name] = {"description": desc, "source": source, "columns": cols, "rows": rows}
        return out
    finally:
        conn.close()


def describe_catalog(db_path: str) -> str:
    """给模型看的表结构说明（写进 query_results 工具描述）：每张表的说明、列名、类型、列说明，再加术语和数据事实。"""
    lines = []
    for name, t in catalog(db_path).items():
        lines.append(f"表 {name}（{t['rows']} 行）：{t['description']}")
        for col, typ, desc in t["columns"]:
            lines.append(f"  - {col} {typ}" + (f"：{desc}" if desc else ""))
    current = None
    for topic, text in notes(db_path):
        if topic != current:
            lines.append(f"{topic}：")
            current = topic
        lines.append(f"  - {text}")
    return "\n".join(lines)


def render_markdown(db_path: str, header: str = "", footer: str = "") -> str:
    """给人看的数据字典（data/results_schema.md），由元数据生成，避免文档和数据库不一致。"""
    parts = [header.rstrip(), ""] if header else []
    for name, t in catalog(db_path).items():
        parts += [f"### {name}（{t['rows']} 行）", "", t["description"], "", f"来源：{t['source']}", "",
                  "| 列 | 类型 | 含义 |", "|---|---|---|"]
        parts += [f"| {c} | {typ} | {d} |" for c, typ, d in t["columns"]]
        parts.append("")
    current = None
    for topic, text in notes(db_path):
        if topic != current:
            parts += ["", f"## {topic}", ""]
            current = topic
        parts.append(f"- {text}")
    if footer:
        parts += ["", footer.rstrip()]
    return "\n".join(parts) + "\n"


# ---------- 只读 SQL 查询 ----------

_ALLOWED_ACTIONS = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION, sqlite3.SQLITE_RECURSIVE}
# authorizer 的操作码 → 名字。只列操作码：sqlite3 模块里还有同名前缀的错误码（如 SQLITE_FORMAT = 24，
# 和 SQLITE_ATTACH 同值），从 dir(sqlite3) 自动收集会互相覆盖，导致报错信息写错操作名。
_ACTION_NAMES = {getattr(sqlite3, n): n.replace("SQLITE_", "") for n in (
    "SQLITE_CREATE_INDEX", "SQLITE_CREATE_TABLE", "SQLITE_CREATE_TEMP_INDEX", "SQLITE_CREATE_TEMP_TABLE",
    "SQLITE_CREATE_TEMP_TRIGGER", "SQLITE_CREATE_TEMP_VIEW", "SQLITE_CREATE_TRIGGER", "SQLITE_CREATE_VIEW",
    "SQLITE_DELETE", "SQLITE_DROP_INDEX", "SQLITE_DROP_TABLE", "SQLITE_DROP_TEMP_INDEX", "SQLITE_DROP_TEMP_TABLE",
    "SQLITE_DROP_TEMP_TRIGGER", "SQLITE_DROP_TEMP_VIEW", "SQLITE_DROP_TRIGGER", "SQLITE_DROP_VIEW", "SQLITE_INSERT",
    "SQLITE_PRAGMA", "SQLITE_READ", "SQLITE_SELECT", "SQLITE_TRANSACTION", "SQLITE_UPDATE", "SQLITE_ATTACH",
    "SQLITE_DETACH", "SQLITE_ALTER_TABLE", "SQLITE_REINDEX", "SQLITE_ANALYZE", "SQLITE_CREATE_VTABLE",
    "SQLITE_DROP_VTABLE", "SQLITE_FUNCTION", "SQLITE_SAVEPOINT", "SQLITE_RECURSIVE") if hasattr(sqlite3, n)}
_DENIED_FUNCTIONS = {"load_extension", "readfile", "writefile", "edit", "fts3_tokenizer"}


def _clean(value):
    """结果单元格：非有限浮点数转成 None 并单独计数（JSON 里没有 NaN），bytes 不返回原文。"""
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, bytes):
        return f"<{len(value)} 字节二进制>"
    return value


def query(db_path: str, sql, max_rows: int, timeout_seconds: float, max_sql_chars: int) -> dict:
    """执行一条只读 SELECT。谁调用：tools.query_results()。
    输入：数据库路径、模型写的 SQL、上限。输出：ok + columns + rows（每行一个列表）+ row_count + truncated。
    失败时 error 写清原因（列名写错、表不在白名单、不是 SELECT、超时……），模型下一轮能据此改正。"""
    if not isinstance(sql, str) or not sql.strip():
        return {"ok": False, "error": "sql 必须是非空字符串"}
    sql = sql.strip()
    if len(sql) > max_sql_chars:
        return {"ok": False, "error": f"SQL 太长（{len(sql)} 字符），上限 {max_sql_chars}"}
    try:
        tables = set(catalog(db_path))
        allowed = {t.lower() for t in tables}
        conn = _connect_ro(db_path)
        real = {r[0].lower() for r in conn.execute("SELECT name FROM sqlite_master WHERE type IN ('table', 'view')")}
        real |= {"sqlite_master", "sqlite_schema", "sqlite_temp_master", "sqlite_sequence"}
    except (FileNotFoundError, sqlite3.Error) as e:
        return {"ok": False, "error": f"数据库不可用：{e}"}

    denied = []      # authorizer 拒绝的操作，用来给出比 "not authorized" 更具体的错误

    def authorizer(action, arg1, arg2, db_name, trigger):
        if action == sqlite3.SQLITE_READ:
            # 表名不区分大小写；不是库里真实存在的表的名字，只能是查询里自己定义的 CTE（WITH 子句），放行
            if arg1 is not None and (arg1.lower() in allowed or arg1.lower() not in real):
                return sqlite3.SQLITE_OK
            denied.append(f"读取表 {arg1}（不在可查询的表里）")
            return sqlite3.SQLITE_DENY
        if action == sqlite3.SQLITE_FUNCTION and (arg2 or "").lower() in _DENIED_FUNCTIONS:
            denied.append(f"调用函数 {arg2}")
            return sqlite3.SQLITE_DENY
        if action in _ALLOWED_ACTIONS:
            return sqlite3.SQLITE_OK
        denied.append(f"{_ACTION_NAMES.get(action, action)} 操作（只允许 SELECT 查询）")
        return sqlite3.SQLITE_DENY

    started = time.perf_counter()
    conn.set_progress_handler(lambda: 1 if time.perf_counter() - started > timeout_seconds else 0, 1000)
    conn.set_authorizer(authorizer)
    try:
        cur = conn.execute(sql)
        if cur.description is None:
            return {"ok": False, "error": "只允许 SELECT 查询"}
        columns = [d[0] for d in cur.description]
        fetched = cur.fetchmany(max_rows + 1)
    except (sqlite3.ProgrammingError, sqlite3.Warning) as e:   # 多条语句（不同 Python 版本抛的类型不同）
        return {"ok": False, "error": f"一次只能执行一条 SELECT 语句：{e}"}
    except sqlite3.DatabaseError as e:
        if denied:
            return {"ok": False, "error": "不允许的操作：" + "；".join(dict.fromkeys(denied))
                                          + f"。可查询的表：{'、'.join(sorted(tables))}"}
        if "interrupted" in str(e):
            return {"ok": False, "error": f"查询超过 {timeout_seconds} 秒被中断，请缩小范围或简化查询"}
        return {"ok": False, "error": f"SQL 错误：{e}"}
    finally:
        conn.close()
    truncated = len(fetched) > max_rows
    rows = [[_clean(v) for v in r] for r in fetched[:max_rows]]
    return {"ok": True, "sql": sql, "columns": columns, "rows": rows, "row_count": len(rows), "truncated": truncated,
            **({"note": f"结果超过 {max_rows} 行，只返回前 {max_rows} 行；需要全部时请加聚合或筛选"} if truncated else {})}
