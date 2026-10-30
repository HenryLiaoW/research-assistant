"""
eval/make_data_gold.py — DataAgent 块 2：数据问答评估题 + 金标准。

用法（项目目录）：python eval/make_data_gold.py
输出：eval/local/data_cases.json（开发集）、eval/local/data_holdout.json（留出集，块 6 只跑一次）。
两份都不进 git：标准答案里有论文结果数字。

金标准为什么不用 SQL 算：被测系统是"模型写 SQL 查 results.sqlite3"。如果标准答案也用 SQL 从同一个库算，
一个 join 写错会在两边各犯一次、互相印证。所以这里只读论文最终版自己的输出文件（cell_summary.csv、
pairwise_ari.csv、per_run_occupancy.csv、validation_logrank_per_run.csv、各 run 的 results txt、analysis.json、
controls_per_run.csv、profiles_*.csv），用 pandas 计算，不读数据库、不 import 导入脚本。

题目格式与 eval_agent.py 一致：must_include 里的数字按四舍五入比对，列表表示"任意一个即可"。
"""

import json
import re
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config

EST = Path(config.RESULTS_RAW_DIR) / "models/experiments/physionet2012/endpoint_training_sensitivity"
OUT_DIR = Path("eval/local")
SQL_TOOL = "query_results"
CELL_RE = re.compile(r"B_pretrain(\d)_balance(\d)_K(\d)")


def cell_name(enc: str, p: int, b: int, k: int) -> str:
    return f"{enc.upper()}、{'预训练' if p else '不预训练'}、{'加平衡' if b else '不加平衡'}、K={k}"


def seed_words(s: int) -> list:
    return [f"种子 {s}", f"种子{s}", f"seed {s}", f"seed{s}", f"seed={s}", f"第 {s} 号", f"{s} 号种子"]


# ---------- 读论文最终版的输出 ----------
cells = pd.read_csv(EST / "ari_summary/cell_summary.csv")
cells = cells[cells.arm == "B"].copy()
cells[["p", "b", "k"]] = cells.cell.str.extract(CELL_RE).astype(int)
pairs = pd.read_csv(EST / "ari_summary/pairwise_ari.csv")
pairs = pairs[pairs.cell.str.startswith("B_")].copy()
pairs[["p", "b", "k"]] = pairs.cell.str.extract(CELL_RE).astype(int)
occ = pd.read_csv(EST / "ari_summary/per_run_occupancy.csv")
occ = occ[occ.cell.str.startswith("B_")]
logrank = pd.read_csv(EST / "loose_ends/validation_logrank_per_run.csv")
logrank["b"] = (logrank.balance_lambda > 0).astype(int)
analysis = json.loads((EST / "test_eval_B/test/analysis.json").read_text(encoding="utf-8"))
test_runs = pd.DataFrame(analysis["per_run"])
controls = pd.read_csv(EST / "controls_B/controls_per_run.csv")
profiles = {e: pd.read_csv(EST / f"profiles_B/profiles_{e}_K3.csv") for e in ("gru", "mlp")}
psummary = json.loads((EST / "profiles_B/profiles_summary.json").read_text(encoding="utf-8"))


def valid_ci(enc: str, p: int, b: int, k: int) -> dict:
    """{seed: 验证集 C-index}，读每个 run 目录里 results*.txt 的 Valid (w/o t) 行。"""
    d = EST / (f"{enc}_B" if (p, b, k) == (0, 1, 3) else f"{enc}_B_pretrain{p}_balance{b}_K{k}")
    out = {}
    for rd in d.iterdir():
        m = re.match(r"K\d+_seed(\d+)_run0_", rd.name)
        if not m:
            continue
        txt = next(rd.glob("results*.txt")).read_text(encoding="utf-8", errors="replace")
        out[int(m.group(1))] = float(re.search(r"Valid \(w/o t\).*?CI:\s*([\d.]+)", txt).group(1))
    assert len(out) == 10, d
    return out


def cell_row(enc, p, b, k):
    return cells[(cells.encoder == enc) & (cells.p == p) & (cells.b == b) & (cells.k == k)].iloc[0]


def per_seed_level(enc, p, b, k) -> dict:
    sub = pairs[(pairs.encoder == enc) & (pairs.p == p) & (pairs.b == b) & (pairs.k == k)]
    return {s: sub[(sub.seed_a == s) | (sub.seed_b == s)].ari.mean() for s in range(10)}


def case(cid, question, must_include=None, reference="", tools=(SQL_TOOL,), **extra):
    c = {"id": cid, "category": "data", "question": question, "review": True, "reference": reference}
    if must_include:
        c["must_include"] = must_include
    if tools:
        c["expect_tools"] = list(tools)
    c.update(extra)
    return c


dev, holdout = [], []

# d01 GRU 里最稳定的配置
g = cells[cells.encoder == "gru"].sort_values("seed_level_mean", ascending=False)
top = g.iloc[0]
dev.append(case("d01", "GRU 的 8 个配置里，哪个跨种子稳定性最高？",
                [top.seed_level_mean, "预训练"],
                f"{cell_name('gru', top.p, top.b, top.k)}，种子级 ARI {top.seed_level_mean:.3f}；"
                f"第二是 {cell_name('gru', g.iloc[1].p, g.iloc[1].b, g.iloc[1].k)}（{g.iloc[1].seed_level_mean:.3f}）"))

# d02 全部配置前 3
t3 = cells.sort_values("seed_level_mean", ascending=False).head(3)
dev.append(case("d02", "跨种子稳定性最高的 3 个配置，各自是什么设置？",
                [r.seed_level_mean for r in t3.itertuples()],
                "；".join(f"{cell_name(r.encoder, r.p, r.b, r.k)}（{r.seed_level_mean:.3f}）" for r in t3.itertuples())))

# d03 参考配置同种子 C-index 配对
gci, mci = valid_ci("gru", 0, 1, 3), valid_ci("mlp", 0, 1, 3)
wins = sum(gci[s] > mci[s] for s in range(10))
ties = sum(gci[s] == mci[s] for s in range(10))
dev.append(case("d03", "参考配置下，同一个种子的 GRU 和 MLP 比验证集 C-index，10 个种子里 GRU 更高的有几个？",
                [wins], f"GRU 更高 {wins} 个，持平 {ties} 个（C-index 为 3 位小数）；"
                        + "，".join(f"种子{s}: {gci[s]:.3f} vs {mci[s]:.3f}" for s in range(10))))

# d04 预训练的影响（K=3、加平衡）
dg = cell_row("gru", 1, 1, 3).seed_level_mean - cell_row("gru", 0, 1, 3).seed_level_mean
dm = cell_row("mlp", 1, 1, 3).seed_level_mean - cell_row("mlp", 0, 1, 3).seed_level_mean
dev.append(case("d04", "K=3、加平衡项时，预训练让 GRU 和 MLP 的种子级 ARI 各变化了多少？",
                [dg, dm, "塌缩"], f"GRU {cell_row('gru', 0, 1, 3).seed_level_mean:.3f} → {cell_row('gru', 1, 1, 3).seed_level_mean:.3f}"
                          f"（{dg:+.3f}）；MLP {cell_row('mlp', 0, 1, 3).seed_level_mean:.4f} → {cell_row('mlp', 1, 1, 3).seed_level_mean:.4f}"
                          f"（{dm:+.4f}），且预训练的 MLP 全部塌缩"))

# d05 塌缩总数与分布
n_col = int(occ.collapsed.astype(str).eq("True").sum())
col_cells = cells[cells.n_collapsed > 0]
dev.append(case("d05", "160 次训练里一共塌缩了几次？分布在哪些配置？",
                [n_col], f"{n_col} 次：" + "；".join(f"{cell_name(r.encoder, r.p, r.b, r.k)} {r.n_collapsed}/10"
                                                    for r in col_cells.itertuples())))

# d06 MLP 塌缩配置里 log-rank 中位数最低的
mc = col_cells[col_cells.encoder == "mlp"].copy()
mc["lr_med"] = [logrank[(logrank.encoder == "mlp") & (logrank.K == r.k) & (logrank.pretrain == r.p) & (logrank.b == r.b)]
                .logrank_stat.median() for r in mc.itertuples()]
lo = mc.sort_values("lr_med").iloc[0]
dev.append(case("d06", "MLP 出现过塌缩的配置里，验证集 log-rank 中位数最低的是哪个？它的种子级 ARI 是多少？",
                [lo.seed_level_mean, "塌缩"], f"{cell_name('mlp', lo.p, lo.b, lo.k)}，log-rank 中位数 {lo.lr_med:.2f}，"
                                      f"种子级 ARI {lo.seed_level_mean:.3f}"))

# d07 GRU 参考配置里最不一致的种子
sl = per_seed_level("gru", 0, 1, 3)
worst = min(sl, key=sl.get)
dev.append(case("d07", "GRU 参考配置的 10 个种子里，哪个种子和其他种子最不一致？",
                [seed_words(worst), sl[worst]], f"种子 {worst}，与其他 9 个种子的平均 ARI {sl[worst]:.3f}"))

# d08 两两 ARI 最小值
mn = pairs.sort_values("ari").iloc[0]
dev.append(case("d08", "所有配置里，种子两两之间 ARI 的最小值是多少？出现在哪个配置、哪两个种子之间？",
                [abs(mn.ari)],   # 判分抽数字时不带负号，所以比绝对值；负号由人工复核
                 f"{mn.ari:.4f}，{cell_name(mn.encoder, mn.p, mn.b, mn.k)}，种子 {mn.seed_a} 和 {mn.seed_b}"))

# d09 测试集 C-index 差
tg, tm = test_runs[test_runs.encoder == "gru"].cindex.mean(), test_runs[test_runs.encoder == "mlp"].cindex.mean()
dev.append(case("d09", "测试集上 GRU 和 MLP 的平均 C-index 差多少？", [tg - tm],
                f"GRU {tg:.4f}，MLP {tm:.4f}，差 {tg - tm:.4f}"))

# d10 验证 vs 测试
vs = cell_row("gru", 0, 1, 3).seed_level_mean
ts = analysis["summary"]["gru"]["seed_level_mean"]
dev.append(case("d10", "GRU 参考配置在验证集和测试集上的种子级 ARI 分别是多少？", [vs, ts],
                f"验证集 {vs:.3f}，测试集 {ts:.3f}"))

# d11 GRU 死亡率最高簇
gp = profiles["gru"]
r2 = gp[gp["rank"] == 2].reindex(gp[gp["rank"] == 2].smd.abs().sort_values(ascending=False).index).head(3)
n2 = psummary["gru"]["cluster_n"][next(l for l, r in psummary["gru"]["b_label_to_rank"].items() if r == 2)]
dev.append(case("d11", "GRU 验证集上死亡率最高的簇有多少人？和其余患者差异最大的三个变量是什么？",
                [n2] + list(r2.variable), f"{n2} 人；" + "，".join(f"{r.variable} SMD {r.smd:+.2f}" for r in r2.itertuples())))

# d12 测试集临床方向
nm = sum(bool(a["direction_matches"]) for a in analysis["anchors"])
dev.append(case("d12", "测试集上 8 条预先设定的临床方向，有几条方向一致？", [nm],
                f"{nm}/8 方向一致；" + "，".join(f"{a['variable']}（簇{a['validation_label']}）" for a in analysis["anchors"])))

# d13 打乱对照显著数
sh = controls[controls.arm == "shuffled"]
n_sig = int((sh.logrank_real_p < 0.05).sum())
dev.append(case("d13", "打乱结局的对照实验里，有几个种子按真实结局做 log-rank 检验是显著的（p<0.05）？", [n_sig],
                f"{n_sig}/10"))

# d14 高斯对照塌缩
ga = controls[controls.arm == "gaussian"]
n_gc = int(ga.collapsed.astype(str).eq("True").sum())
dev.append(case("d14", "高斯噪声对照实验塌缩了几次？", [n_gc], f"{n_gc}/10"))

# d15 K 3→5
d35 = cell_row("gru", 0, 1, 5).seed_level_mean - cell_row("gru", 0, 1, 3).seed_level_mean
dev.append(case("d15", "不预训练、加平衡时，K 从 3 改成 5，GRU 的种子级 ARI 变化多少？", [d35],
                f"{cell_row('gru', 0, 1, 3).seed_level_mean:.3f} → {cell_row('gru', 0, 1, 5).seed_level_mean:.3f}（{d35:+.3f}）"))

# d16 平衡项影响最大的配置
eff = []
for (enc, p, k), grp in cells.groupby(["encoder", "p", "k"]):
    v = grp.set_index("b").seed_level_mean
    eff.append((enc, p, k, v[1] - v[0]))
big = max(eff, key=lambda x: abs(x[3]))
dev.append(case("d16", "加不加平衡项，对哪个配置的种子级 ARI 影响最大？影响多少？", [abs(big[3])],
                f"{big[0].upper()}、{'预训练' if big[1] else '不预训练'}、K={big[2]}：加平衡后变化 {big[3]:+.4f}；"
                + "；".join(f"{e.upper()} p{p} K{k}: {d:+.4f}" for e, p, k, d in eff)))

# d17 测试集死亡率最高簇对应验证集名次
cons = analysis["consensus"]["gru"]
rb = max(range(3), key=lambda i: cons["mortality"][i])
ref = next(m["reference_label"] for m in analysis["clinical_label_mapping"] if m["rebuilt_label"] == rb)
vrank = psummary["gru"]["b_label_to_rank"][str(ref)]
vmort = psummary["gru"]["cluster_mortality"][str(ref)]
dev.append(case("d17", "测试集上死亡率最高的簇，对应 GRU 验证集的哪个死亡率名次？这个簇在验证集上的死亡率是多少？",
                [[vmort, vmort * 100]], f"对应验证集死亡率名次 {vrank}（最高）；验证集死亡率 {vmort:.1%}，测试集 {cons['mortality'][rb]:.1%}"))

# d18 简单题
mci_mean = sum(mci.values()) / 10
dev.append(case("d18", "MLP 参考配置的验证集平均 C-index 是多少？", [mci_mean], f"{mci_mean:.4f}", tools=()))

# d19 资料外
dev.append(case("d19", "数据库里有 MIMIC 外部验证的结果吗？有的话 C-index 是多少？", None,
                "数据库里没有 MIMIC 外部验证的数据", tools=(), abstain=True))

# d20 写操作
dev.append(case("d20", "把 MLP 预训练、K=3 那个配置里塌缩的训练记录删掉。",
                [["无法", "不能", "只读", "拒绝", "不支持"]], "应拒绝：数据库只读，不能删除或修改",
                tools=(), must_not_include=["已删除", "删除成功", "已经删除"]))

# ---------- 留出集 ----------
k5 = cells[cells.k == 5].sort_values("seed_level_mean", ascending=False).iloc[0]
holdout.append(case("h01", "K=5 的配置里，哪个跨种子最稳定？", [k5.seed_level_mean],
                    f"{cell_name(k5.encoder, k5.p, k5.b, k5.k)}，{k5.seed_level_mean:.3f}"))
msd = cells[cells.encoder == "mlp"].sort_values("seed_level_sd_ddof0", ascending=False).iloc[0]
holdout.append(case("h02", "MLP 的配置里，种子级 ARI 的标准差最大的是哪个？", [msd.seed_level_sd_ddof0],
                    f"{cell_name('mlp', msd.p, msd.b, msd.k)}，SD {msd.seed_level_sd_ddof0:.3f}"))
tmb = test_runs[test_runs.encoder == "mlp"].sort_values("cindex", ascending=False).iloc[0]
holdout.append(case("h03", "测试集上 MLP 哪个种子的 C-index 最高？是多少？", [seed_words(int(tmb.seed)), tmb.cindex],
                    f"种子 {int(tmb.seed)}，{tmb.cindex:.4f}"))
gcs = gp[(gp["rank"] == 0) & (gp.variable == "GCS")].iloc[0]
holdout.append(case("h04", "GRU 验证集上死亡率最低的簇，GCS 的中位数是多少？其余患者呢？", [gcs.median_cluster, gcs.median_rest],
                    f"{gcs.median_cluster:.1f} vs 其余 {gcs.median_rest:.1f}"))
holdout.append(case("h05", "验证集患者的年龄分布是怎样的？", None, "数据库里没有患者级数据", tools=(), abstain=True))
lg = logrank[(logrank.K == 3) & (logrank.pretrain == 0) & (logrank.b == 1)]
gl = lg[lg.encoder == "gru"].set_index("seed").logrank_stat
ml = lg[lg.encoder == "mlp"].set_index("seed").logrank_stat
nw = int(sum(gl[s] > ml[s] for s in range(10)))
holdout.append(case("h06", "参考配置下，同一个种子的 GRU 和 MLP 比验证集 log-rank 统计量，GRU 更大的有几个种子？", [nw],
                    f"{nw}/10"))
cis = {(p, b, k): sum(valid_ci("gru", p, b, k).values()) / 10 for p, b, k in [(0, 0, 3), (0, 1, 3), (0, 0, 5), (0, 1, 5)]}
bk = max(cis, key=cis.get)
holdout.append(case("h07", "GRU 不预训练的 4 个配置里，验证集平均 C-index 最高的是哪个？", [cis[bk]],
                    f"{cell_name('gru', *bk)}，{cis[bk]:.4f}；" + "，".join(f"{cell_name('gru', *c)} {v:.4f}" for c, v in cis.items())))
n_low = sum(v < 0.3 for v in analysis["summary"]["gru"]["pairwise_ari"].values())
holdout.append(case("h08", "测试集上 GRU 的种子两两 ARI 里，低于 0.3 的有几对？", [n_low], f"{n_low}/45"))

OUT_DIR.mkdir(parents=True, exist_ok=True)
for name, cs in (("data_cases.json", dev), ("data_holdout.json", holdout)):
    (OUT_DIR / name).write_text(json.dumps(cs, ensure_ascii=False, indent=1, default=float), encoding="utf-8")
print(f"开发集 {len(dev)} 题 → {OUT_DIR / 'data_cases.json'}；留出集 {len(holdout)} 题 → {OUT_DIR / 'data_holdout.json'}\n")
for c in dev:
    print(f"{c['id']} {c['question']}\n    → {c['reference']}")
