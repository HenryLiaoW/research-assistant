"""
scripts/import_results.py — DataAgent 块 1：把毕设实验的原始输出导入一个只读查询用的 SQLite 数据库。

用法（项目目录、已激活 .venv）：
    python scripts/import_results.py              原始输出在 config.RESULTS_RAW_DIR（data/raw_results/）
    python scripts/import_results.py --source <目录>   用别处的原始输出

做什么：
    读 160 个验证集 run（MLP / GRU × 预训练 × 平衡 × K=3/5 × 10 个种子）的指标和聚类标签、
    端点 B 测试集的最终评估、对照实验、聚类画像，整理成 config.RESULTS_DB_PATH 里的若干张表（表结构见 SCHEMA）。
    跨种子 ARI 由聚类标签重新计算，不抄汇总表。

为什么要核对：解析 txt 拿错一列、种子对错、ARI 实现有偏差，都不会报错，只会让数悄悄不对。
所以写完库以后从库里用 SQL 重算每个格子的汇总，和论文冻结的汇总表（RESULTS.json、analyze_2x2_gru.txt、
analysis.json、controls_analysis.json）逐项比对；任何一项对不上就不替换旧库，退出码非 0。

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
CELL_DIR = re.compile(r"^pretrain([01])_balance([01])_K(\d)$")
RUN_DIR = re.compile(r"^K(\d+)_seed(\d+)_run0_")
ENCODER_DIRS = {"MLP": "pretrain_balance_2x2", "GRU": "pretrain_balance_2x2_gru"}

SCHEMA = """
CREATE TABLE configs (
    config_id      TEXT PRIMARY KEY,      -- 如 MLP_p0_b1_K3
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
    ci_valid          REAL,               -- 验证集 C-index（不用时间 t），txt 里保留 3 位小数
    ci_train_wt       REAL,
    ci_train_wot      REAL,
    n_valid           INTEGER NOT NULL,
    cluster_sizes     TEXT NOT NULL,      -- JSON 数组，按簇编号
    min_cluster_share REAL NOT NULL,
    collapsed         INTEGER NOT NULL,   -- 1 = 最小簇占比 < 1%
    logrank_stat      REAL,               -- 验证集各簇生存曲线的 log-rank 统计量
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
    cluster INTEGER PRIMARY KEY,          -- 按验证集死亡率排序：0 最低
    n INTEGER NOT NULL, mortality REAL NOT NULL
);
CREATE TABLE val_cluster_profiles (
    cluster INTEGER NOT NULL REFERENCES val_clusters(cluster),
    variable TEXT NOT NULL,
    n_cluster INTEGER, n_rest INTEGER, median_cluster REAL, median_rest REAL,
    smd REAL, p REAL, q REAL, coverage_pct REAL,
    PRIMARY KEY (cluster, variable)
);
CREATE TABLE test_clusters (
    cluster INTEGER PRIMARY KEY,          -- 对齐后的编号（端点 B 验证集参考划分的编号）
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

def build_validation(src: Path, conn) -> None:
    base = src / "models/experiments/physionet2012"
    for encoder, dirname in ENCODER_DIRS.items():
        enc_dir = base / dirname
        cells = sorted(d for d in enc_dir.iterdir() if d.is_dir() and CELL_DIR.match(d.name))
        if len(cells) != 8:
            raise SystemExit(f"{enc_dir} 下应有 8 个格子目录，找到 {len(cells)} 个")
        for cell in cells:
            pretrain, balance, k = map(int, CELL_DIR.match(cell.name).groups())
            config_id = f"{encoder}_p{pretrain}_b{balance}_K{k}"
            runs = []
            for rd in sorted(d for d in cell.iterdir() if d.is_dir() and RUN_DIR.match(d.name)):
                parsed = parse_results_txt((rd / "results_physionet2012.txt").read_text(encoding="utf-8", errors="replace"))
                seed = int(RUN_DIR.match(rd.name).group(2))
                if parsed["seed"] != seed or parsed["k"] != k:
                    raise SystemExit(f"{rd}：目录名（K{k} seed{seed}）和 txt 里的参数不一致")
                labels = np.load(rd / "labels_valid_wot.npy", allow_pickle=False)
                diag = rd / "diagnostics.txt"
                lr_stat, lr_p = parse_diagnostics(diag.read_text(encoding="utf-8", errors="replace")) if diag.exists() else (None, None)
                runs.append({"run_id": rd.name, "seed": seed, "labels": labels, "lr": (lr_stat, lr_p), **parsed})
            if len(runs) != 10:
                raise SystemExit(f"{cell} 应有 10 个 run，找到 {len(runs)} 个")
            hp = runs[0]
            conn.execute("INSERT INTO configs VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                         (config_id, encoder, k, pretrain, balance, label_of(pretrain, balance), hp["epochs"],
                          hp["batch_size"], hp["latent_dim"], hp["learning_rate"], hp["pretrain_epochs"], hp["weibull_shape"]))
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
                              r["lr"][0], r["lr"][1], sl[r["seed"]]))


def build_test(src: Path, conn) -> dict:
    """端点 B 测试集：analysis.json 的逐 run 指标；跨种子 ARI 由推理输出的标签重算，再和 analysis.json 比对。"""
    tdir = src / "models/experiments/physionet2012/endpoint_training_sensitivity/test_eval_B"
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
    for m in analysis["clinical_label_mapping"]:
        rb, ref_label = m["rebuilt_label"], m["reference_label"]
        conn.execute("INSERT INTO test_clusters VALUES (?,?,?,?,?)",
                     (ref_label, rb, cons["sizes"][rb], cons["deaths"][rb], cons["mortality"][rb]))
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
    df = pd.read_csv(src / "models/experiments/physionet2012/endpoint_training_sensitivity/controls_B/controls_per_run.csv")
    for r in df.itertuples(index=False):
        def num(v):
            return None if pd.isna(v) else float(v)
        conn.execute("INSERT INTO controls VALUES (?,?,?,?,?,?,?,?,?,?)",
                     (r.arm, int(r.seed), num(r.cindex_real), num(r.logrank_real_stat), num(r.logrank_real_p),
                      num(r.logrank_shuffled_stat), num(r.logrank_shuffled_p), r.cluster_sizes,
                      num(r.min_share), int(str(r.collapsed) == "True")))


def build_val_profiles(src: Path, conn) -> None:
    """验证集聚类画像：MLP 2×2 实验 K=3 的共识划分，簇按验证集死亡率排序（0 最低）。"""
    pdir = src / "models/experiments/physionet2012/cluster_profiles_smd"
    summary = (pdir / "SUMMARY.txt").read_text(encoding="utf-8")
    for rank, n, mort in re.findall(r"rank (\d) \(n=(\d+), mortality ([\d.]+)%\)", summary):
        conn.execute("INSERT INTO val_clusters VALUES (?,?,?)", (int(rank), int(n), float(mort) / 100))
    df = pd.read_csv(pdir / "cluster_profiles_K3.csv")
    for r in df.itertuples(index=False):
        conn.execute("INSERT INTO val_cluster_profiles VALUES (?,?,?,?,?,?,?,?,?,?)",
                     (int(r.rank), r.variable, int(r.n_cluster), int(r.n_rest), float(r.median_cluster),
                      float(r.median_rest), float(r.smd), float(r.p), float(r.q), float(r.coverage)))


# ---------- 核对：从库里用 SQL 重算，和冻结的汇总表比 ----------

GRU_LINE = re.compile(r"^(no pretrain|pretrain),\s+(no balance|\+ balance)\s+(\d)\s+(\d+)\s+[\d.]+ \([\d.]+\) med=[\d.]+\s+"
                      r"\[[-\d.]+, [-\d.]+\]\s+([\d.]+) \(([\d.]+)\)\s+(\d+)%\s+([\d.]+)%\s+([\d.]+)")


def frozen_cells(src: Path) -> dict:
    """{config_id: 冻结的汇总}。MLP 来自 RESULTS.json；GRU 没有 RESULTS.json，来自 analyze_2x2_gru.txt 的表格。"""
    out = {}
    res = json.loads((src / "models/experiments/physionet2012/pretrain_balance_2x2/RESULTS.json").read_text(encoding="utf-8"))
    for c in res["cells"]:
        cid = f"MLP_p{c['pretrain']}_b{int(c['balance_lambda'] > 0)}_K{c['K']}"
        out[cid] = {"seed_level_ari": c["seed_level_ari"], "seed_level_ari_sd": c["seed_level_ari_sd"],
                    "collapse_rate": c["collapse_rate"], "min_share": c["mean_min_cluster_share"],
                    "mean_ci": c["mean_ci_wo_t"], "digits": 4}
    for line in (src / "final_reporting/analyze_2x2_gru.txt").read_text(encoding="utf-8").splitlines():
        m = GRU_LINE.match(line.strip())
        if not m:
            continue
        p = 1 if m.group(1) == "pretrain" else 0
        b = 1 if m.group(2) == "+ balance" else 0
        out[f"GRU_p{p}_b{b}_K{m.group(3)}"] = {"seed_level_ari": float(m.group(5)), "seed_level_ari_sd": float(m.group(6)),
                                              "collapse_rate": float(m.group(7)) / 100, "min_share": float(m.group(8)) / 100,
                                              "mean_ci": float(m.group(9)), "digits": 3}
    return out


def verify(conn, src: Path, test_checks: dict) -> list:
    """返回失败项列表；空列表 = 全部通过。"""
    fails = []

    def expect(name, got, want, tol):
        ok = got is not None and want is not None and abs(got - want) <= tol
        print(f"  {'✅' if ok else '❌'} {name}: 库里 {got if got is None else round(got, 6)}，冻结值 {want}")
        if not ok:
            fails.append(name)

    counts = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
              for t in ("configs", "runs", "pairwise_ari", "test_runs", "test_pairwise_ari", "controls",
                        "val_clusters", "val_cluster_profiles", "test_clusters", "test_cluster_profiles", "test_anchors")}
    want_counts = {"configs": 16, "runs": 160, "pairwise_ari": 720, "test_runs": 20, "test_pairwise_ari": 90,
                   "controls": 20, "val_clusters": 3, "test_clusters": 3, "test_anchors": 8}
    print("行数：")
    for t, n in counts.items():
        ok = t not in want_counts or n == want_counts[t]
        print(f"  {'✅' if ok else '❌'} {t}: {n}" + ("" if t not in want_counts else f"（应为 {want_counts[t]}）"))
        if not ok:
            fails.append(f"rows:{t}")

    print("16 个格子（SQL 重算 vs 冻结汇总；MLP 比 4 位小数，GRU 表格只有 3 位）：")
    frozen = frozen_cells(src)
    if len(frozen) != 16:
        fails.append(f"frozen cells parsed: {len(frozen)}")
        print(f"  ❌ 冻结汇总只解析出 {len(frozen)} 个格子")
    rows = conn.execute("""
        SELECT config_id, AVG(seed_level_ari), AVG(collapsed), AVG(min_cluster_share), AVG(ci_valid),
               GROUP_CONCAT(seed_level_ari)
        FROM runs GROUP BY config_id ORDER BY config_id""").fetchall()
    for cid, sl_mean, collapse, min_share, ci_mean, sl_all in rows:
        f = frozen.get(cid)
        if f is None:
            fails.append(f"no frozen value for {cid}")
            continue
        tol = 0.5 * 10 ** -f["digits"] + 1e-9
        sl_sd = statistics.pstdev(float(x) for x in sl_all.split(","))
        expect(f"{cid} seed-level ARI", sl_mean, f["seed_level_ari"], tol)
        expect(f"{cid} seed-level ARI sd", sl_sd, f["seed_level_ari_sd"], tol)
        expect(f"{cid} collapse rate", collapse, f["collapse_rate"], 1e-9)
        expect(f"{cid} mean min-cluster share", min_share, f["min_share"], 0.0005 if f["digits"] == 3 else 1e-6)
        expect(f"{cid} mean valid C-index", ci_mean, f["mean_ci"], tol)

    print("测试集（端点 B）与对照：")
    for enc, want in (("GRU", 0.4740863181742458), ("MLP", 0.36350410882695827)):
        got = conn.execute("SELECT AVG(seed_level_ari) FROM test_runs WHERE encoder = ?", (enc,)).fetchone()[0]
        expect(f"test {enc} seed-level ARI", got, want, 1e-9)
    for name, diff in test_checks.items():
        expect(name + "（标签重算 vs analysis.json）", diff, 0.0, 1e-9)
    mlp_ci = sorted(r[0] for r in conn.execute("SELECT cindex FROM test_runs WHERE encoder = 'MLP'"))
    expect("test MLP C-index median", statistics.median(mlp_ci), 0.7548979042161597, 1e-9)
    shuf = [r[0] for r in conn.execute("SELECT cindex_real FROM controls WHERE arm = 'shuffled'")]
    expect("shuffled control C-index median", statistics.median(shuf), 0.5030356559973888, 1e-9)
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
