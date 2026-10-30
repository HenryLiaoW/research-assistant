# 实验结果数据库：数据字典

文件：`data/results.sqlite3`（由 `scripts/import_results.py` 从毕设原始输出生成，不进 git）。
数据来自硕士论文最终版（端点 B）的实验：PhysioNet 2012 ICU 数据上的深度生存聚类（VaDeSC），研究聚类结果跨随机种子是否稳定。验证集 1766 人，测试集 1763 人。导入时已和论文表 4.1、4.4、4.6 逐项核对。

## 术语

- **ARI**（调整兰德指数）：两次聚类结果的一致程度，1 = 完全一致，0 ≈ 随机水平，可以为负。
- **seed-level ARI**：某个种子与同一配置下其他 9 个种子两两 ARI 的平均。一个配置的"跨种子稳定性"= 10 个种子 seed-level ARI 的平均值（论文报告的就是这个）。
- **C-index**：生存预测的区分能力，0.5 = 随机，越高越好。
- **塌缩**（collapsed）：某个簇的样本占比低于 1%。
- **log-rank 统计量**：各簇生存曲线差异的检验统计量，越大差异越明显。
- **SMD**：标准化均值差，某簇与其余患者在一个临床变量上的差异，正值 = 该簇更高；**q**：多重检验校正后的 p 值（BH-FDR）。

## 表

### configs —— 16 种实验配置（论文表 4.1 的析因实验）
| 列 | 含义 |
|---|---|
| config_id | 如 `GRU_p1_b0_K3`：编码器_是否预训练_是否加平衡_K |
| is_reference | 1 = 共同参考配置（不预训练、加平衡、K=3）；论文的聚类画像和测试集评估都用这个配置 |
| encoder | `MLP`（手工特征）或 `GRU`（时间序列编码器） |
| k | 聚类数，3 或 5 |
| pretrain | 1 = 有自编码器预训练 |
| balance | 1 = 加了最小占用惩罚（occupancy penalty，论文里也叫平衡项） |
| label | 原论文里的格子名，如 `no pretrain, + balance` |
| epochs, batch_size, latent_dim, learning_rate, pretrain_epochs, weibull_shape | 训练超参数（16 种配置都相同：200、256、16、0.001、50、1） |

### runs —— 160 次训练（每个配置 10 个种子 0–9），验证集结果
| 列 | 含义 |
|---|---|
| run_id | 原始输出目录名 |
| config_id | 关联 configs |
| seed | 随机种子 |
| ci_valid | 验证集 C-index（原始文件只保留 3 位小数） |
| ci_train_wt / ci_train_wot | 训练集 C-index（使用 / 不使用时间信息） |
| n_valid | 验证集人数（1766） |
| cluster_sizes | 各簇人数，JSON 数组 |
| min_cluster_share | 最小簇占比 |
| collapsed | 1 = 塌缩 |
| logrank_stat, logrank_p | 验证集各簇的 log-rank 检验（按修正后的结局；论文表 4.1 报告的是每个配置的中位数） |
| seed_level_ari | 这个种子的 seed-level ARI |

### pairwise_ari —— 同一配置内种子两两之间的 ARI（每配置 45 对，共 720 行）
config_id, seed_a, seed_b（seed_a < seed_b）, ari

### test_runs —— 测试集最终评估（端点 B），20 行
固定配置：参考配置（不预训练、加平衡、K=3），就是 runs 表里 is_reference = 1 的那 20 次训练（每个编码器 10 次）在测试集上的评估。
| 列 | 含义 |
|---|---|
| encoder, seed | MLP / GRU，种子 0–9 |
| cindex | 测试集 C-index |
| logrank_stat, logrank_p | 测试集 log-rank 检验 |
| cluster_sizes, min_cluster_share, collapsed | 同 runs |
| seed_level_ari | 测试集 seed-level ARI |

### test_pairwise_ari —— 测试集种子两两 ARI（每编码器 45 对）
encoder, seed_a, seed_b, ari

### controls —— 对照实验（端点 B，MLP，K=3），20 行
arm：`gaussian`（输入换成高斯噪声）或 `shuffled`（打乱生存结局与输入的对应关系）；每组 10 个种子。
cindex_real / logrank_real_*：用真实结局评估；logrank_shuffled_*：用打乱后的结局评估（只有 shuffled 组有）；cluster_sizes、min_cluster_share、collapsed 同上。

### val_clusters / val_cluster_profiles —— 验证集聚类画像（论文表 4.4、图 4.2）
划分：每个编码器参考配置 10 次训练的共识划分（K=3）。论文以 GRU 为主，MLP 作对照。簇用 mortality_rank 标识：按验证集死亡率排序，0 = 最低。
- val_clusters：encoder, mortality_rank, consensus_label（共识里的原始编号）, n, mortality（0–1 的比例）
- val_cluster_profiles：encoder, mortality_rank, variable（临床变量名，如 BUN、GCS）, n_cluster, n_rest（有该变量记录的人数）, median_cluster, median_rest, smd, p, q, measured_pct（该变量的记录覆盖率，%）

### test_clusters / test_cluster_profiles / test_anchors —— 测试集聚类画像
划分：GRU 在测试集上的共识划分。cluster 列已对齐到 GRU 验证集共识的 consensus_label；val_mortality_rank 是对应的验证集死亡率名次（和 val_clusters 用 encoder = 'GRU' 关联）；rebuilt_label 是对齐前的编号。
- test_clusters：cluster, val_mortality_rank, rebuilt_label, n, deaths, mortality
- test_cluster_profiles：cluster, variable, n_cluster, n_rest, smd, p, q（没有中位数）
- test_anchors：8 条预先设定的临床方向（在验证集上确定，在测试集上检验）：cluster, variable, expected_direction（1 = 预期该簇更高，-1 = 更低）, smd, q, direction_matches（1 = 方向一致）, estimable

### import_meta
导入来源路径、导入时间、塌缩阈值。

## 数据库里没有的
患者级数据、原始临床时间序列、dip 检验、严重度十分位分析、K-means / Ward 等对比方法的结果、K=2–8 扫描、潜空间 PCA 投影、K=5 的聚类画像、早期流程（验证集 1774 人）的结果。问到这些要回答"数据库里没有"。

## 已知的数据事实
- MLP 不预训练、K=3 时，加不加平衡项 10 个种子的聚类结果完全相同（两个配置的数字一致不是导入错误）。
- MLP 共塌缩 41/80 次：预训练的 40 次全部塌缩，另 1 次是 K=5、不预训练、不加平衡。GRU 没有塌缩。
- 验证集 C-index（runs.ci_valid）来自训练时记录的 3 位小数；参考配置的平均值（GRU 0.7605、MLP 0.7518）与论文表 4.6 的 0.761、0.752 一致。
