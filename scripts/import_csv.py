"""
scripts/import_csv.py — 把任意 CSV 导入分析数据库，成为 query_results 工具可以查询的一张表。

用法（项目目录、已激活 .venv）：
    python scripts/import_csv.py data/sample_results.csv --table sample_results \
        --description "阶段 3 的合成示例数据，不是真实实验结果" --columns eval/../columns.json
    --columns 可选：一个 JSON 文件 {"列名": "列说明"}，说明会进数据字典、给模型看；没写的列说明留空，以后可以重跑补上。
    表已存在时默认拒绝，加 --replace 才覆盖。

做什么：读 CSV（pandas 推断列类型）→ 写进 config.RESULTS_DB_PATH 的一张表 → 在元数据表里登记表说明和列说明
→ 重新生成数据字典 data/results_schema.md。导入后不用改任何代码，Agent 就能用 SQL 查这张表。

为什么要先导入、而不是让 Agent 直接读 CSV：数据来源只有一个（数据库），模型不用在"读文件"和"查库"之间选择，
也就不会把真实问题路由到错误的数据上；多份数据之间还能用 SQL 关联。
"""

import argparse
import json
import sqlite3
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config
import data_tools

MAX_ROWS = 1_000_000
RESERVED = set(data_tools.META_TABLES) | {"import_meta"}


def main() -> None:
    ap = argparse.ArgumentParser(description="把 CSV 导入分析数据库")
    ap.add_argument("csv", help="CSV 文件路径")
    ap.add_argument("--table", required=True, help="表名：字母、数字、下划线，不以数字开头")
    ap.add_argument("--description", required=True, help="这张表是什么数据（会给模型看）")
    ap.add_argument("--columns", help='可选：JSON 文件，{"列名": "列说明"}')
    ap.add_argument("--replace", action="store_true", help="表已存在时覆盖")
    ap.add_argument("--db", default=config.RESULTS_DB_PATH, help=f"数据库（默认 {config.RESULTS_DB_PATH}）")
    args = ap.parse_args()

    if not data_tools.IDENTIFIER.match(args.table) or args.table.lower() in RESERVED:
        raise SystemExit(f"表名不合法或是保留名：{args.table}")
    df = pd.read_csv(args.csv)
    if len(df) > MAX_ROWS:
        raise SystemExit(f"超过 {MAX_ROWS} 行，拒绝导入")
    bad = [c for c in df.columns if not data_tools.IDENTIFIER.match(str(c))]
    if bad:
        raise SystemExit(f"这些列名不是合法标识符（字母、数字、下划线），请先改列名：{bad}")
    docs = json.loads(Path(args.columns).read_text(encoding="utf-8")) if args.columns else {}

    conn = sqlite3.connect(args.db)
    try:
        exists = conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (args.table,)).fetchone()
        if exists and not args.replace:
            raise SystemExit(f"表 {args.table} 已存在；确认要覆盖请加 --replace")
        with conn:
            df.to_sql(args.table, conn, if_exists="replace", index=False)
            data_tools.register_table(conn, args.table, args.description,
                                      f"{Path(args.csv).name}，scripts/import_csv.py 导入", docs)
    finally:
        conn.close()
    Path(config.RESULTS_SCHEMA_DOC).write_text(data_tools.render_markdown(args.db, header=(
        "# 分析数据库：数据字典\n\n本文件由元数据表自动生成（scripts/import_results.py、scripts/import_csv.py 导入时更新），不要手改。\n"
        "Agent 的 query_results 工具只能查询下面登记过的表。\n")), encoding="utf-8")
    undocumented = [c for c in df.columns if c not in docs]
    print(f"已导入 {len(df)} 行 → 表 {args.table}；数据字典已更新：{config.RESULTS_SCHEMA_DOC}")
    if undocumented:
        print(f"[提示] 这些列还没有说明（模型只能看到列名）：{undocumented}；可以写进 --columns 文件后加 --replace 重跑")


if __name__ == "__main__":
    main()
