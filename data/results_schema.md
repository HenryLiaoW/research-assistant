# 实验结果数据库：数据字典

文件：`data/results.sqlite3`（由 `scripts/import_results.py` 从毕设原始输出生成，不进 git）。
数据来自硕士论文的实验：PhysioNet 2012 ICU 数据上的深度生存聚类（VaDeSC），研究聚类结果跨随机种子是否稳定。

## 术语

- **ARI**（调整兰德指数）：两次聚类结果的一致程度，1 = 完全一致，0 ≈ 随机水平，可以为负。
- **seed-level ARI**：某个种子与同一配置下其他 9 个种子两两 ARI 的平均。一个配置的"跨种子稳定性"= 10 个种子 seed-level ARI 的平均值（论文报告的就是这个）。
- **C-index**：生存预测的区分能力，0.5 = 随机，越高越好。
- **塌缩**（collapsed）：某个簇的样本占比低于 1%。
- **log-rank 统计量**：各簇生存曲线差异的检验统计量，越大差异越明显。
- **SMD**：标准化均值差，某簇与其余患者在一个临床变量上的差异，正值 = 该簇更高；**q**：多重检验校正后的 p 值（BH-FDR）。

## 表

### configs —— 16 种实验配置（验证集 2×2 实验）
| 列 | 含义 |
|---|---|
| config_id | 如 `GRU_p1_b0_K3`：编码器_是否预训练_是否加平衡_K |
| encoder | `MLP`（手工特征）或 `GRU`（时间序列编码器） |
| k | 聚类数，3 或 5 |
| pretrain | 1 = 有自编码器预训练 |
| balance | 1 = 加了簇平衡项 |
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
| n_valid | 验证集人数（1774） |
| cluster_sizes | 各簇人数，JSON 数组 |
| min_cluster_share | 最小簇占比 |
| collapsed | 1 = 塌缩 |
| logrank_stat, logrank_p | 验证集各簇的 log-rank 检验 |
| seed_level_ari | 这个种子的 seed-level ARI |

### pairwise_ari —— 同一配置内种子两两之间的 ARI（每配置 45 对，共 720 行）
config_id, seed_a, seed_b（seed_a < seed_b）, ari

### test_runs —— 测试集最终评估（端点 B），20 行
固定配置：不预训练、加平衡、K=3。注意：这是另一组训练（端点 B），和 runs 表的验证集实验不是同一批模型，不要混在一起比较。
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

### val_clusters / val_cluster_profiles —— 验证集聚类画像
划分：MLP、不预训练、加平衡、K=3 这 10 次训练的共识划分。簇编号按验证集死亡率排序，0 = 最低。
- val_clusters：cluster, n, mortality（0–1 的比例）
- val_cluster_profiles：cluster, variable（临床变量名，如 BUN、GCS）, n_cluster, n_rest（有该变量记录的人数）, median_cluster, median_rest, smd, p, q, coverage_pct（该变量的记录覆盖率，%）

### test_clusters / test_cluster_profiles / test_anchors —— 测试集聚类画像
划分：GRU 在测试集上的共识划分，簇编号已对齐到端点 B 验证集参考划分（cluster 列；rebuilt_label 是对齐前的编号）。
- test_clusters：cluster, rebuilt_label, n, deaths, mortality
- test_cluster_profiles：cluster, variable, n_cluster, n_rest, smd, p, q（没有中位数）
- test_anchors：8 条预先设定的临床方向（在验证集上确定，在测试集上检验）：cluster, variable, expected_direction（1 = 预期该簇更高，-1 = 更低）, smd, q, direction_matches（1 = 方向一致）, estimable

### import_meta
导入来源路径、导入时间、塌缩阈值。

## 数据库里没有的
患者级数据、原始临床时间序列、dip 检验、严重度十分位分析、K-means / Ward 等传统方法的结果、MIMIC 外部验证、K=5 的聚类画像。问到这些要回答"数据库里没有"。

## 已知的数据事实
- MLP 不预训练时，加不加平衡项在 K=3 下 10 个种子的聚类结果完全相同（两个配置的数字一致不是导入错误）。
- 预训练的 MLP 配置每个种子都塌缩（collapsed = 1）。
