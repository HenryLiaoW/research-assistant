"""
csv_tools.py — 阶段 3：受限 CSV 统计。只读指定目录下的 .csv，只做这里定义的几种统计。

只管文件和计算，不知道模型、工具菜单的存在。谁调用：tools.py 里的包装函数、测试。
所有函数都把目录和上限作为参数传入（由 tools.py 从 config 补上），本模块不读 config，方便测试。

安全约定：
- 文件名只能是 data_dir 里的一个文件名（如 "sample_results.csv"），不接受 / \\ 和 ".."，也就不接受绝对路径；
- 解析后的真实路径必须仍在 data_dir 里（防止 data/ 里放一个指向外面的符号链接）；
- 只接受 .csv 后缀；
- 列名只用来按名字取列，不拼进任何代码或 SQL；不执行任何来自参数的代码；
- 返回给模型的只有汇总（数量、缺失、均值……），不返回原始行。

每个函数永远返回 dict：成功 ok=True 加结果，失败 ok=False 加 error（和 tools.py 的约定一致）。


对外（tools.py 会调用）        内部（下划线开头，只在本文件用）

list_files ──────────────────→ _csv_names

describe_file ──→ _load ──→ _resolve ──→ _csv_names

group_stats ────→ _load      _is_number_column
                └────────────→ _is_number_column

"""

from pathlib import Path

import pandas as pd
from pandas.api.types import is_bool_dtype, is_numeric_dtype

STD_DEFINITION = "总体标准差：sqrt(Σ(x - mean)² / N)，分母为 N；不是样本标准差（分母 N-1）"
MISSING_GROUP_LABEL = "(缺失)"


# ---- 内部小工具 ----
def _csv_names(base: Path) -> list:
    """目录里所有 .csv 文件名，按名字排序。"""
    return sorted(p.name for p in base.iterdir() if p.is_file() and p.suffix.lower() == ".csv")


def _resolve(data_dir: str, filename) -> tuple:
    """把模型给的文件名变成安全的绝对路径。返回 (path, None) 或 (None, 错误信息)。"""
    # Part1： 只看文件title内容
    # 第 0 道门拦：不是字符串，或者是空的
    if not isinstance(filename, str) or not filename.strip():
        return None, "文件名必须是非空字符串"

    # 第 1 道门拦：带路径的
    filename = filename.strip()
    if "/" in filename or "\\" in filename or filename in (".", ".."):
        return None, f"只接受 data 目录里的文件名，不接受路径：{filename}"

    # 第 2 道门拦：后缀不是 .csv
    if not filename.lower().endswith(".csv"):
        return None, f"只允许读取 .csv 文件：{filename}"

    # Part2: 只看磁盘真实位置
    # 第 3 道门拦：文件真实位置不在 data 里（之类确保resolve后link不会跳走）
    base = Path(data_dir).resolve()
    path = (base / filename).resolve()   # resolve() 会顺着符号链接找到真实位置
    if path.parent != base:
        return None, f"文件不在允许的目录里：{filename}"

    # 第 4 道门拦：文件不存在的
    if not path.is_file():
        available = ", ".join(_csv_names(base)) or "无"
        return None, f"文件不存在：{filename}。可用文件：{available}"
    return path, None


def _is_number_column(series: pd.Series) -> bool:
    """数值列才能算统计量。True/False 列在 pandas 里也算数值，这里排除掉。"""
    return is_numeric_dtype(series) and not is_bool_dtype(series)


def _load(data_dir: str, filename, max_rows: int) -> tuple:
    """校验路径并读表。返回 (DataFrame, None) 或 (None, 错误信息)。"""
    path, error = _resolve(data_dir, filename)
    if error:
        return None, error
    try:
        # 多读一行，用来判断是否超过上限；超大文件不会被整个读进内存
        df = pd.read_csv(path, nrows=max_rows + 1)
    except (pd.errors.EmptyDataError, pd.errors.ParserError, UnicodeDecodeError) as e:
        return None, f"无法解析 CSV：{type(e).__name__}: {e}"
    if len(df) > max_rows:
        return None, f"文件超过 {max_rows} 行的上限，拒绝读取"
    return df, None


# ---- 对外的三个函数 ----
def list_files(data_dir: str) -> dict:
    """列出 data_dir 里的 CSV 文件。输出：{"ok": True, "files": [{"name", "size_bytes"}]}。"""
    base = Path(data_dir).resolve()
    if not base.is_dir():
        return {"ok": False, "error": "数据目录不存在"}
    files = [{"name": n, "size_bytes": (base / n).stat().st_size} for n in _csv_names(base)]
    return {"ok": True, "files": files}


def describe_file(data_dir: str, filename, max_rows: int) -> dict:
    """看表头：行数，以及每列的类型、是否数值、缺失数、不同取值个数。不返回任何原始行。"""
    df, error = _load(data_dir, filename, max_rows)
    if error:
        return {"ok": False, "error": error}
    columns = [
        {
            "name": str(col),
            "dtype": str(df[col].dtype),
            "numeric": _is_number_column(df[col]),
            "missing": int(df[col].isna().sum()),
            "unique": int(df[col].nunique()),
        }
        for col in df.columns
    ]
    return {"ok": True, "file": filename.strip(), "rows": len(df), "columns": columns}


def group_stats(data_dir: str, filename, value_column, group_by, max_rows: int, max_groups: int) -> dict:
    """对一个数值列做（可选分组的）描述统计。

    输入：文件名、值列名、分组列名（None 或空字符串表示不分组）、行数和组数上限。
    输出：每组的 count（实际参与计算的个数）、missing（被跳过的空值个数）、mean、min、max、population_std。
    """
    df, error = _load(data_dir, filename, max_rows)
    if error:
        return {"ok": False, "error": error}

    # group_stats()第二部分：校验列
    # 准备两份清单，一份是所有列名，一份是numeric列名
    all_columns = [str(c) for c in df.columns]
    numeric_columns = [str(c) for c in df.columns if _is_number_column(df[c])]

    if not isinstance(value_column, str) or value_column not in df.columns:
        return {"ok": False, "error": f"没有列 {value_column!r}。可用列：{', '.join(all_columns)}"}
    if not _is_number_column(df[value_column]):
        return {
            "ok": False,
            "error": f"列 {value_column} 不是数值列，无法计算统计量。"
                     f"可用的数值列：{', '.join(numeric_columns) or '无'}",
        }

    # group_stats()第三部分：分组列
    # 分组列没填（None），或者填了一个空字符串、只有空格的字符串，都当作不分组
    if group_by is None or (isinstance(group_by, str) and not group_by.strip()):
        groups = [("全部", df[value_column])]
        group_by = None
    else:
        if not isinstance(group_by, str) or group_by not in df.columns:
            return {"ok": False, "error": f"没有分组列 {group_by!r}。可用列：{', '.join(all_columns)}"}
        if group_by == value_column:
            return {"ok": False, "error": "分组列和值列不能是同一列"}
        n_groups = df[group_by].nunique(dropna=False)
        if n_groups > max_groups:
            return {"ok": False, "error": f"列 {group_by} 有 {n_groups} 个不同取值，超过 {max_groups} 组的上限"}

        # group_stats 第四部分：真正分组
        # dropna=False：分组列为空的行单独成组，而不是被 pandas 悄悄丢掉
        groups = [
            (MISSING_GROUP_LABEL if pd.isna(key) else str(key), part[value_column])
            for key, part in df.groupby(group_by, dropna=False, sort=True)
        ]

    # group_stats() 第五部分：逐组计算
    results = []
    for label, values in groups:
        valid = values.dropna()
        row = {"group": label, "count": int(len(valid)), "missing": int(values.isna().sum())}
        if len(valid) == 0:
            row.update({"mean": None, "min": None, "max": None, "population_std": None})
        else:
            row.update({
                "mean": float(valid.mean()),
                "min": float(valid.min()),
                "max": float(valid.max()),
                "population_std": float(valid.std(ddof=0)),
            })
        results.append(row)

    return {
        "ok": True,
        "file": filename.strip(),
        "value_column": value_column,
        "group_by": group_by,
        "std_definition": STD_DEFINITION,
        "groups": results,
    }
