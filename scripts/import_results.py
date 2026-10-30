"""
scripts/import_results.py — DataAgent 块 1：把毕设实验的原始输出导入一个只读查询用的 SQLite 数据库。

用法（项目目录、已激活 .venv）：
    python scripts/import_results.py              原始输出在 config.RESULTS_RAW_DIR（data/raw_results/）
    python scripts/import_results.py --source <目录>   用别处的原始输出

做什么：
    读论文最终版（端点 B，验证集 1766 人）的数据：160 个验证集 run（MLP / GRU × 预训练 × 平衡 × K=3/5 × 10 个种子）
    的指标和聚类标签、测试集最终评估、对照实验、验证集与测试集聚类画像，整理成 config.RESULTS_DB_PATH 里的
    若干张表（表结构见 SCHEMA）。跨种子 ARI 由聚类标签重新计算，不抄汇总表。
    注意：physionet2012/pretrain_balance_2x2(_gru)/ 是论文早期流程（验证集 1774 人）的输出，已被端点 B 取代，不导入。

为什么要核对：解析 txt 拿错一列、种子对错、ARI 实现有偏差，都不会报错，只会让数悄悄不对。
所以写完库以后从库里用 SQL 重算每个格子的汇总，和论文正文的表 4.1、表 4.4、表 4.6 以及最终版输出
（cell_summary.csv、analysis.json）逐项比对；任何一项对不上就不替换旧库，退出码非 0。

只读原始文件，不修改；新库先写到系统临时目录，核对全部通过才移到目标位置。原始数据（data/raw_results/）和库都不进 git。
"""

import argparse
import json
import os
import re
import shutil
import sqlite3
import statistics
import sys
import tempfile
from datetime import datetime
from itertools import combinations
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))   # 从项目目录 import config
import config

COLLAPSE_THRESHOLD = 0.01        # 和论文 analyze_2x2.py 一致：最小簇占比低于 1% 记为塌缩
EST = "models/experiments/physionet2012/endpoint_training_sensitivity"   # 端点 B 的输出都在这里
CELL_DIR = re.compile(r"^(gru|mlp)_B_pretrain([01])_balance([01])_K(\d)$")
REFERENCE_DIR = re.compile(r"^(gru|mlp)_B$")      # 参考格子（不预训练、加平衡、K=3）直接放在 gru_B/、mlp_B/ 下
RUN_DIR = re.compile(r"^K(\d+)_seed(\d+)_run0_")

SCHEMA = """
CREATE TABLE configs (
    config_id      TEXT PRIMARY KEY,      -- 如 MLP_p0_b1_K3
    is_reference   INTEGER NOT NULL,      -- 1 = 论文的共同参考配置（不预训练、加平衡、K=3）
    encoder        TEXT NOT NULL CHECK (encoder IN ('MLP', 'GRU')),
    k              INTEGER NOT NULL,
    pretrain       INTEGER NOT NULL CHECK (pretrain IN (0, 1)),
    balance        INTEGER NOT NULL CHECK (balance IN (0, 1)),
    label          TEXT NOT NULL,
    epochs         INTEGER, batch_size INTEGER, latent_dim INTEGER, learning_rate REAL,
    pretrain_epochs INTEGER, weibull_shape REAL
);
CREATE TABLE runs (
    run_id            TEXT PRIMARY KEY,
    config_id         TEXT NOT NULL REFERENCES configs(config_id),
    seed              INTEGER NOT NULL,
    ci_valid          REAL,               -- 验证集 C-index（训练时记录，不用时间 t），txt 里保留 3 位小数
    ci_train_wt       REAL,
    ci_train_wot      REAL,
    n_valid           INTEGER NOT NULL,
    cluster_sizes     TEXT NOT NULL,      -- JSON 数组，按簇编号
    min_cluster_share REAL NOT NULL,
    collapsed         INTEGER NOT NULL,   -- 1 = 最小簇占比 < 1%
    logrank_stat      REAL,               -- 验证集各簇生存曲线的 log-rank 统计量（按修正后的结局计算，同论文表 4.1）
    logrank_p         REAL,
    seed_level_ari    REAL NOT NULL,      -- 与同配置其他 9 个种子的平均 ARI
    UNIQUE (config_id, seed)
);
CREATE TABLE pairwise_ari (
    config_id TEXT NOT NULL REFERENCES configs(config_id),
    seed_a INTEGER NOT NULL, seed_b INTEGER NOT NULL, ari REAL NOT NULL,
    PRIMARY KEY (config_id, seed_a, seed_b), CHECK (seed_a < seed_b)
);
CREATE TABLE test_runs (
    encoder TEXT NOT NULL CHECK (encoder IN ('MLP', 'GRU')),
    seed INTEGER NOT NULL,
    cindex REAL NOT NULL, logrank_stat REAL, logrank_p REAL,
    cluster_sizes TEXT NOT NULL, min_cluster_share REAL NOT NULL, collapsed INTEGER NOT NULL,
    seed_level_ari REAL NOT NULL,
    PRIMARY KEY (encoder, seed)
);
CREATE TABLE test_pairwise_ari (
    encoder TEXT NOT NULL, seed_a INTEGER NOT NULL, seed_b INTEGER NOT NULL, ari REAL NOT NULL,
    PRIMARY KEY (encoder, seed_a, seed_b), CHECK (seed_a < seed_b)
);
CREATE TABLE controls (
    arm TEXT NOT NULL CHECK (arm IN ('gaussian', 'shuffled')),
    seed INTEGER NOT NULL,
    cindex_real REAL, logrank_real_stat REAL, logrank_real_p REAL,
    logrank_shuffled_stat REAL, logrank_shuffled_p REAL,
    cluster_sizes TEXT, min_cluster_share REAL, collapsed INTEGER,
    PRIMARY KEY (arm, seed)
);
CREATE TABLE val_clusters (
    encoder TEXT NOT NULL CHECK (encoder IN ('MLP', 'GRU')),
    mortality_rank INTEGER NOT NULL,      -- 按验证集死亡率排序：0 最低（论文表 4.4 的 rank）
    consensus_label INTEGER NOT NULL,     -- 共识划分里的原始簇编号
    n INTEGER NOT NULL, mortality REAL NOT NULL,
    PRIMARY KEY (encoder, mortality_rank)
);
CREATE TABLE val_cluster_profiles (
    encoder TEXT NOT NULL, mortality_rank INTEGER NOT NULL,
    variable TEXT NOT NULL,
    n_cluster INTEGER, n_rest INTEGER, median_cluster REAL, median_rest REAL,
    smd REAL, p REAL, q REAL, measured_pct REAL,
    PRIMARY KEY (encoder, mortality_rank, variable)
);
CREATE TABLE test_clusters (
    cluster INTEGER PRIMARY KEY,          -- 对齐后的编号 = GRU 验证集共识的 consensus_label
    val_mortality_rank INTEGER NOT NULL,  -- 对应的 GRU 验证集死亡率名次
    rebuilt_label INTEGER NOT NULL, n INTEGER NOT NULL, deaths INTEGER NOT NULL, mortality REAL NOT NULL
);
CREATE TABLE test_cluster_profiles (
    cluster INTEGER NOT NULL REFERENCES test_clusters(cluster),
    variable TEXT NOT NULL,
    n_cluster INTEGER, n_rest INTEGER, smd REAL, p REAL, q REAL,
    PRIMARY KEY (cluster, variable)
);
CREATE TABLE test_anchors (
    cluster INTEGER NOT NULL, variable TEXT NOT NULL,
    expected_direction INTEGER NOT NULL, smd REAL, q REAL,
    n_cluster INTEGER, n_rest INTEGER,
    direction_matches INTEGER, estimable INTEGER NOT NULL,
    PRIMARY KEY (cluster, variable)
);
CREATE TABLE import_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


# ---------- ARI：和 sklearn.metrics.adjusted_rand_score 同一公式（pair confusion matrix），不依赖 sklearn ----------

def ari(a, b) -> float:
    """两组聚类标签的调整兰德指数。谁调用：build_*、测试。用 Python 整数算，避免溢出。"""
    a = np.asarray(a)
    b = np.asarray(b)
    if a.shape != b.shape or a.ndim != 1:
        raise ValueError(f"标签长度不一致：{a.shape} vs {b.shape}")
    n = int(a.size)
    _, ai = np.unique(a, return_inverse=True)
    _, bi = np.unique(b, return_inverse=True)
    cont = np.zeros((ai.max() + 1, bi.max() + 1), dtype=np.int64)
    np.add.at(cont, (ai, bi), 1)
    n_c = cont.sum(axis=1)
    n_k = cont.sum(axis=0)
    sum_sq = int((cont.astype(object) ** 2).sum())
    c01 = int(cont.dot(n_k).sum()) - sum_sq
    c10 = int(cont.T.dot(n_c).sum()) - sum_sq
    c11 = sum_sq - n
    c00 = n * n - c01 - c10 - sum_sq
    tn, fp, fn, tp = c00, c01, c10, c11
    if fn == 0 and fp == 0:
        return 1.0
    return 2.0 * (tp * tn - fn * fp) / ((tp + fn) * (fn + tn) + (tp + fp) * (fp + tn))


def seed_level(pairs: dict, seeds: list) -> dict:
    """每个种子与其他种子的平均 ARI（论文的 seed-level ARI）。pairs: {(sa, sb): ari}，sa < sb。"""
    out = {}
    for s in seeds:
        vals = [v for (x, y), v in pairs.items() if s in (x, y)]
        out[s] = float(np.mean(vals))
    return out


# ---------- 解析 ----------

def parse_results_txt(text: str) -> dict:
    """results_physionet2012.txt：首行超参数，后面三行 Train(w t) / Train(w/o t) / Valid(w/o t) 的 CI。"""
    header = text.splitlines()[0]
    params = dict(re.findall(r"([A-Za-z_ ]+?)=\s*([^,]+?)(?:,|\.$|$)", header))
    params = {k.strip(): v.strip() for k, v in params.items()}

    def ci(tag):
        m = re.search(re.escape(tag) + r"\s*\|.*?CI:\s*([\d.]+)", text)
        return float(m.group(1)) if m else None

    return {"epochs": int(params["Epochs"]), "batch_size": int(params["batch_size"]),
            "latent_dim": int(params["latent_dim"]), "k": int(params["K"]),
            "learning_rate": float(params["learning_rate"]), "pretrain_epochs": int(params["pretrain_e"]),
            "weibull_shape": float(params["weibull_shape"]), "seed": int(params["seed"]),
            "ci_train_wt": ci("Train (w t)"), "ci_train_wot": ci("Train (w/o t)"), "ci_valid": ci("Valid (w/o t)")}


def parse_diagnostics(text: str):
    """diagnostics.txt 的 valid w/o t 行：logrank 统计量和 p 值。没有这一行时返回 (None, None)。"""
    m = re.search(r"valid w/o t\s+sizes=\[[^\]]*\]\s+logrank_p=([\d.eE+-]+)\s+stat=([\d.]+)", text)
    return (float(m.group(2)), float(m.group(1))) if m else (None, None)


def label_of(pretrain: int, balance: int) -> str:
    return f"{'pretrain' if pretrain else 'no pretrain'}, {'+ balance' if balance else 'no balance'}"


# ---------- 构建 ----------

def cell_dirs(est: Path) -> list:
    """[(encoder, pretrain, balance, k, 目录)]，16 个格子。参考格子在 gru_B/、mlp_B/，其余在 {enc}_B_pretrain*_balance*_K*/。"""
    cells = []
    for d in sorted(p for p in est.iterdir() if p.is_dir()):
        m = CELL_DIR.match(d.name)
        if m:
            enc, p, b, k = m.groups()
            cells.append((enc.upper(), int(p), int(b), int(k), d))
        elif REFERENCE_DIR.match(d.name):
            cells.append((d.name.split("_")[0].upper(), 0, 1, 3, d))
    if len(cells) != 16:
        raise SystemExit(f"{est} 下应有 16 个格子目录，找到 {len(cells)} 个")
    return cells


def build_validation(src: Path, conn) -> None:
    est = src / EST
    lr = pd.read_csv(est / "loose_ends/validation_logrank_per_run.csv")
    lr_map = {(r.encoder.upper(), int(r.K), int(r.pretrain), int(r.balance_lambda > 0), int(r.seed)): (r.logrank_stat, r.logrank_p)
              for r in lr.itertuples(index=False)}
    for encoder, pretrain, balance, k, cell in cell_dirs(est):
        config_id = f"{encoder}_p{pretrain}_b{balance}_K{k}"
        runs = []
        for rd in sorted(d for d in cell.iterdir() if d.is_dir() and RUN_DIR.match(d.name)):
            txts = sorted(rd.glob("results*.txt"))
            if len(txts) != 1:
                raise SystemExit(f"{rd}：应有 1 个 results*.txt，找到 {len(txts)} 个")
            parsed = parse_results_txt(txts[0].read_text(encoding="utf-8", errors="replace"))
            seed = int(RUN_DIR.match(rd.name).group(2))
            if parsed["seed"] != seed or parsed["k"] != k:
                raise SystemExit(f"{rd}：目录名（K{k} seed{seed}）和 txt 里的参数不一致")
            labels = np.load(rd / "labels_valid_wot.npy", allow_pickle=False)
            key = (encoder, k, pretrain, balance, seed)
            if key not in lr_map:
                raise SystemExit(f"validation_logrank_per_run.csv 里没有 {key}")
            runs.append({"run_id": rd.name, "seed": seed, "labels": labels, "lr": lr_map[key], **parsed})
        if len(runs) != 10:
            raise SystemExit(f"{cell} 应有 10 个 run，找到 {len(runs)} 个")
        hp = runs[0]
        conn.execute("INSERT INTO configs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     (config_id, int(pretrain == 0 and balance == 1 and k == 3), encoder, k, pretrain, balance,
                      label_of(pretrain, balance), hp["epochs"], hp["batch_size"], hp["latent_dim"], hp["learning_rate"],
                      hp["pretrain_epochs"], hp["weibull_shape"]))
        pairs = {}
        for ra, rb in combinations(sorted(runs, key=lambda r: r["seed"]), 2):
            pairs[(ra["seed"], rb["seed"])] = ari(ra["labels"], rb["labels"])
            conn.execute("INSERT INTO pairwise_ari VALUES (?,?,?,?)", (config_id, ra["seed"], rb["seed"], pairs[(ra["seed"], rb["seed"])]))
        sl = seed_level(pairs, [r["seed"] for r in runs])
        for r in runs:
            sizes = np.bincount(r["labels"], minlength=k)
            share = float(sizes.min() / sizes.sum())
            conn.execute("INSERT INTO runs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                         (r["run_id"], config_id, r["seed"], r["ci_valid"], r["ci_train_wt"], r["ci_train_wot"],
                          int(sizes.sum()), json.dumps(sizes.tolist()), share, int(share < COLLAPSE_THRESHOLD),
                          float(r["lr"][0]), float(r["lr"][1]), sl[r["seed"]]))


def build_test(src: Path, conn) -> dict:
    """端点 B 测试集：analysis.json 的逐 run 指标；跨种子 ARI 由推理输出的标签重算，再和 analysis.json 比对。"""
    tdir = src / EST / "test_eval_B"
    analysis = json.loads((tdir / "test/analysis.json").read_text(encoding="utf-8"))
    checks = {}
    for enc in ("gru", "mlp"):
        labels = {}
        for s in range(10):
            z = np.load(tdir / f"inference/test_{enc}_seed{s}.npz", allow_pickle=False)
            labels[s] = z["labels"].astype(np.int64)
        pairs = {(a, b): ari(labels[a], labels[b]) for a, b in combinations(range(10), 2)}
        ref = analysis["summary"][enc]["pairwise_ari"]
        worst = max(abs(pairs[(a, b)] - ref[f"{a}_{b}"]) for a, b in pairs)
        checks[f"test_{enc}_pairwise_max_abs_diff"] = worst
        sl = seed_level(pairs, list(range(10)))
        for (a, b), v in pairs.items():
            conn.execute("INSERT INTO test_pairwise_ari VALUES (?,?,?,?)", (enc.upper(), a, b, v))
        for r in analysis["per_run"]:
            if r["encoder"] != enc:
                continue
            sizes = r["cluster_sizes"]
            conn.execute("INSERT INTO test_runs VALUES (?,?,?,?,?,?,?,?,?)",
                         (enc.upper(), r["seed"], r["cindex"], r["logrank_stat"], r["logrank_p"], json.dumps(sizes),
                          r["min_share"], int(bool(r["collapsed"])), sl[r["seed"]]))
    # 测试集聚类画像：GRU 的测试集共识划分，簇编号已对齐到端点 B 验证集的参考划分（cluster 列）
    cons = analysis["consensus"]["gru"]
    rank_of = json.loads((src / EST / "profiles_B/profiles_summary.json").read_text(encoding="utf-8"))["gru"]["b_label_to_rank"]
    for m in analysis["clinical_label_mapping"]:
        rb, ref_label = m["rebuilt_label"], m["reference_label"]
        conn.execute("INSERT INTO test_clusters VALUES (?,?,?,?,?,?)",
                     (ref_label, int(rank_of[str(ref_label)]), rb, cons["sizes"][rb], cons["deaths"][rb], cons["mortality"][rb]))
    prof = pd.read_csv(tdir / "test/cluster_profiles.csv")
    for row in prof.itertuples(index=False):
        conn.execute("INSERT INTO test_cluster_profiles VALUES (?,?,?,?,?,?,?)",
                     (int(row.cluster), row.variable, int(row.n_cluster), int(row.n_rest), row.smd, row.p, row.q))
    for a in analysis["anchors"]:
        conn.execute("INSERT INTO test_anchors VALUES (?,?,?,?,?,?,?,?,?)",
                     (a["validation_label"], a["variable"], a["expected_direction"], a.get("smd"), a.get("q"),
                      a.get("n_cluster"), a.get("n_rest"),
                      None if a.get("direction_matches") is None else int(a["direction_matches"]), int(bool(a["estimable"]))))
    return checks


def build_controls(src: Path, conn) -> None:
    df = pd.read_csv(src / EST / "controls_B/controls_per_run.csv")
    for r in df.itertuples(index=False):
        def num(v):
            return None if pd.isna(v) else float(v)
        conn.execute("INSERT INTO controls VALUES (?,?,?,?,?,?,?,?,?,?)",
                     (r.arm, int(r.seed), num(r.cindex_real), num(r.logrank_real_stat), num(r.logrank_real_p),
                      num(r.logrank_shuffled_stat), num(r.logrank_shuffled_p), r.cluster_sizes,
                      num(r.min_share), int(str(r.collapsed) == "True")))


def build_val_profiles(src: Path, conn) -> None:
    """验证集聚类画像（论文表 4.4 / 图 4.2）：两条流程各自参考配置 10 次训练的共识划分，簇按验证集死亡率排名（0 最低）。"""
    pdir = src / EST / "profiles_B"
    summary = json.loads((pdir / "profiles_summary.json").read_text(encoding="utf-8"))
    for enc in ("gru", "mlp"):
        s = summary[enc]
        for label, rank in s["b_label_to_rank"].items():
            conn.execute("INSERT INTO val_clusters VALUES (?,?,?,?,?)",
                         (enc.upper(), int(rank), int(label), int(s["cluster_n"][label]), float(s["cluster_mortality"][label])))
        df = pd.read_csv(pdir / f"profiles_{enc}_K3.csv")
        for r in df.itertuples(index=False):
            conn.execute("INSERT INTO val_cluster_profiles VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                         (enc.upper(), int(r.rank), r.variable, int(r.n_cluster), int(r.n_rest), float(r.median_cluster),
                          float(r.median_rest), float(r.smd), float(r.p), float(r.q), float(r.measured_pct)))


# ---------- 核对：从库里用 SQL 重算，和冻结的汇总表比 ----------

# 论文表 4.1（正文印刷值）：(encoder, K, pretrain, balance) -> (seed-level ARI, SD, 塌缩次数, 平均最小簇占比 %, log-rank 中位数)
THESIS_TABLE_4_1 = {
    ("MLP", 3, 0, 0): (0.328, 0.027, 0, 24.3, 36.2), ("MLP", 3, 0, 1): (0.328, 0.027, 0, 24.3, 36.2),
    ("MLP", 3, 1, 0): (0.329, 0.163, 10, 0.0, 0.9), ("MLP", 3, 1, 1): (0.330, 0.168, 10, 0.0, 1.6),
    ("MLP", 5, 0, 0): (0.291, 0.024, 1, 8.9, 46.9), ("MLP", 5, 0, 1): (0.290, 0.024, 0, 9.7, 52.2),
    ("MLP", 5, 1, 0): (0.413, 0.057, 10, 0.0, 19.9), ("MLP", 5, 1, 1): (0.403, 0.060, 10, 0.0, 29.6),
    ("GRU", 3, 0, 0): (0.455, 0.087, 0, 22.4, 37.7), ("GRU", 3, 0, 1): (0.456, 0.087, 0, 22.4, 37.8),
    ("GRU", 3, 1, 0): (0.667, 0.061, 0, 27.3, 29.6), ("GRU", 3, 1, 1): (0.666, 0.061, 0, 27.2, 29.6),
    ("GRU", 5, 0, 0): (0.604, 0.024, 0, 10.2, 44.0), ("GRU", 5, 0, 1): (0.598, 0.031, 0, 10.0, 45.6),
    ("GRU", 5, 1, 0): (0.615, 0.038, 0, 8.6, 43.1), ("GRU", 5, 1, 1): (0.612, 0.036, 0, 8.8, 44.7),
}
# 论文表 4.4（GRU）和 4.5 节正文（MLP）：mortality_rank -> (n, 死亡率 %)
THESIS_CLUSTERS = {"GRU": {0: (469, 8.5), 1: (731, 10.1), 2: (566, 23.7)},
                   "MLP": {0: (471, 6.4), 1: (565, 9.9), 2: (730, 22.2)}}
# 论文表 4.4 每个 GRU 簇绝对值最大的效应：(rank, 变量, SMD)
THESIS_TOP_SMD = [(0, "GCS", 0.86), (1, "BUN", -0.66), (2, "BUN", 0.81)]


def verify(conn, src: Path, test_checks: dict) -> list:
    """返回失败项列表；空列表 = 全部通过。"""
    fails = []

    def expect(name, got, want, tol):
        ok = got is not None and want is not None and abs(got - want) <= tol
        if not ok:
            fails.append(name)
        return ok

    def report(title, results):
        bad = [r for r in results if not r[1]]
        print(f"  {'✅' if not bad else '❌'} {title}：{len(results) - len(bad)}/{len(results)} 项一致")
        for name, _, got, want in bad:
            print(f"      ❌ {name}: 库里 {got}，期望 {want}")

    counts = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
              for t in ("configs", "runs", "pairwise_ari", "test_runs", "test_pairwise_ari", "controls",
                        "val_clusters", "val_cluster_profiles", "test_clusters", "test_cluster_profiles", "test_anchors")}
    want_counts = {"configs": 16, "runs": 160, "pairwise_ari": 720, "test_runs": 20, "test_pairwise_ari": 90,
                   "controls": 20, "val_clusters": 6, "val_cluster_profiles": 222, "test_clusters": 3, "test_anchors": 8}
    print("行数：")
    for t, n in counts.items():
        ok = t not in want_counts or n == want_counts[t]
        print(f"  {'✅' if ok else '❌'} {t}: {n}" + ("" if t not in want_counts else f"（应为 {want_counts[t]}）"))
        if not ok:
            fails.append(f"rows:{t}")

    rows = conn.execute("""
        SELECT c.encoder, c.k, c.pretrain, c.balance, c.config_id, AVG(r.seed_level_ari), SUM(r.collapsed),
               AVG(r.min_cluster_share), GROUP_CONCAT(r.seed_level_ari), GROUP_CONCAT(r.logrank_stat)
        FROM runs r JOIN configs c ON c.config_id = r.config_id
        GROUP BY c.config_id ORDER BY c.config_id""").fetchall()

    # 1) 论文表 4.1 印刷值（3 位 / 1 位小数）
    res = []
    for enc, k, p, b, cid, sl_mean, n_col, min_share, sl_all, lr_all in rows:
        want = THESIS_TABLE_4_1[(enc, k, p, b)]
        sl_sd = statistics.pstdev(float(x) for x in sl_all.split(","))
        lr_med = statistics.median(float(x) for x in lr_all.split(","))
        for name, got, w, tol in ((f"{cid} seed-level ARI", sl_mean, want[0], 0.0005 + 1e-9),
                                  (f"{cid} ARI SD", sl_sd, want[1], 0.0005 + 1e-9),
                                  (f"{cid} 塌缩次数", n_col, want[2], 0),
                                  (f"{cid} 平均最小簇占比 %", 100 * min_share, want[3], 0.05 + 1e-9),
                                  (f"{cid} log-rank 中位数", lr_med, want[4], 0.05 + 1e-9)):
            res.append((name, expect(name, got, w, tol), round(got, 4), w))
    print("论文表 4.1（16 个格子 × 5 项，按印刷位数比）：")
    report("表 4.1", res)

    # 2) 最终版输出 cell_summary.csv（全精度）
    cs = pd.read_csv(src / EST / "ari_summary/cell_summary.csv")
    cs = cs[cs["arm"] == "B"]
    res = []
    for enc, k, p, b, cid, sl_mean, n_col, _, sl_all, _ in rows:
        r = cs[(cs.encoder == enc.lower()) & (cs.K == k) & (cs.pretrain == p) & ((cs.balance_lambda > 0) == bool(b))].iloc[0]
        sl_sd = statistics.pstdev(float(x) for x in sl_all.split(","))
        for name, got, w in ((f"{cid} seed-level ARI", sl_mean, r.seed_level_mean),
                             (f"{cid} ARI SD", sl_sd, r.seed_level_sd_ddof0), (f"{cid} 塌缩次数", n_col, r.n_collapsed)):
            res.append((name, expect(name + "（cell_summary）", got, float(w), 1e-9), got, float(w)))
    print("最终版 cell_summary.csv（全精度，验证 ARI 重算）：")
    report("cell_summary", res)

    # 3) 聚类画像：表 4.4
    res = []
    for enc, ranks in THESIS_CLUSTERS.items():
        for rank, (n, mort) in ranks.items():
            got_n, got_m = conn.execute("SELECT n, mortality FROM val_clusters WHERE encoder = ? AND mortality_rank = ?",
                                        (enc, rank)).fetchone()
            res.append((f"{enc} rank {rank} n", expect(f"{enc} rank {rank} n", got_n, n, 0), got_n, n))
            res.append((f"{enc} rank {rank} 死亡率", expect(f"{enc} rank {rank} mortality", 100 * got_m, mort, 0.05 + 1e-9),
                        round(100 * got_m, 2), mort))
    for rank, var, smd in THESIS_TOP_SMD:
        got = conn.execute("""SELECT variable, smd FROM val_cluster_profiles WHERE encoder = 'GRU' AND mortality_rank = ?
                              ORDER BY ABS(smd) DESC LIMIT 1""", (rank,)).fetchone()
        ok = got[0] == var and expect(f"GRU rank {rank} top SMD", got[1], smd, 0.005 + 1e-9)
        if got[0] != var:
            fails.append(f"GRU rank {rank} top variable")
        res.append((f"GRU rank {rank} 最大效应", ok, f"{got[0]} {got[1]:+.2f}", f"{var} {smd:+.2f}"))
    print("论文表 4.4 与 4.5 节（簇大小、死亡率、最大效应）：")
    report("聚类画像", res)

    # 4) 测试集（表 4.6）与对照（4.4 节）
    res = []
    for enc, want in (("GRU", 0.4740863181742458), ("MLP", 0.36350410882695827)):
        got = conn.execute("SELECT AVG(seed_level_ari) FROM test_runs WHERE encoder = ?", (enc,)).fetchone()[0]
        res.append((f"test {enc} seed-level ARI", expect(f"test {enc} ARI", got, want, 1e-9), got, want))
    for enc, want in (("GRU", 0.768), ("MLP", 0.755)):
        got = conn.execute("SELECT AVG(cindex) FROM test_runs WHERE encoder = ?", (enc,)).fetchone()[0]
        res.append((f"test {enc} mean C-index", expect(f"test {enc} C-index", got, want, 0.0005 + 1e-9), round(got, 4), want))
    for name, diff in test_checks.items():
        res.append((name, expect(name, diff, 0.0, 1e-9), diff, 0.0))
    shuf = [r[0] for r in conn.execute("SELECT cindex_real FROM controls WHERE arm = 'shuffled'")]
    res.append(("打乱结局对照 C-index 中位数", expect("shuffled C-index", statistics.median(shuf), 0.503, 0.0005 + 1e-9),
                round(statistics.median(shuf), 4), 0.503))
    print("测试集（表 4.6）与对照（4.4 节）：")
    report("测试集与对照", res)
    return fails


def main() -> None:
    ap = argparse.ArgumentParser(description="把毕设实验结果导入 SQLite（只读原始文件）")
    ap.add_argument("--source", default=config.RESULTS_RAW_DIR,
                    help=f"原始输出目录，里面有 models/ 和 final_reporting/（默认 {config.RESULTS_RAW_DIR}）")
    ap.add_argument("--out", default=config.RESULTS_DB_PATH, help=f"输出数据库（默认 {config.RESULTS_DB_PATH}）")
    args = ap.parse_args()
    src = Path(args.source).expanduser().resolve()
    if not (src / "models/experiments/physionet2012").is_dir():
        raise SystemExit(f"{src} 下找不到 models/experiments/physionet2012，--source 指错了？")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(suffix=".sqlite3")   # 先在系统临时目录建库，核对通过再移过去
    os.close(fd)
    conn = sqlite3.connect(tmp)
    try:
        conn.executescript(SCHEMA)
        build_validation(src, conn)
        test_checks = build_test(src, conn)
        build_controls(src, conn)
        build_val_profiles(src, conn)
        meta = {"source": str(src), "imported_at": datetime.now().isoformat(timespec="seconds"),
                "collapse_threshold": str(COLLAPSE_THRESHOLD)}
        conn.executemany("INSERT INTO import_meta VALUES (?,?)", meta.items())
        conn.commit()
        fails = verify(conn, src, test_checks)
    finally:
        conn.close()
    if fails:
        os.remove(tmp)
        raise SystemExit(f"\n核对失败 {len(fails)} 项，没有替换 {out}：{fails}")
    shutil.move(tmp, out)
    print(f"\n全部核对通过，已写入 {out}")


if __name__ == "__main__":
    main()
