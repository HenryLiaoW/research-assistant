# 分析数据库：数据字典

本文件由元数据表自动生成（scripts/import_results.py、scripts/import_csv.py 导入时更新），不要手改。
Agent 的 query_results 工具只能查询下面登记过的表。

### configs（16 行）

16 种实验配置（论文表 4.1 的析因实验：编码器 × 是否预训练 × 是否加平衡项 × K）。

来源：论文最终版（端点 B）实验输出，scripts/import_results.py 导入并与论文表 4.1/4.4/4.6 核对

| 列 | 类型 | 含义 |
|---|---|---|
| config_id | TEXT | 配置编号，如 GRU_p1_b0_K3 = GRU、预训练、不加平衡、K=3 |
| is_reference | INTEGER | 1 = 共同参考配置（不预训练、加平衡、K=3）；论文的聚类画像和测试集评估都用这个配置 |
| encoder | TEXT | MLP（手工特征）或 GRU（时间序列编码器） |
| k | INTEGER | 聚类数，3 或 5 |
| pretrain | INTEGER | 1 = 有自编码器预训练 |
| balance | INTEGER | 1 = 加了最小占用惩罚（occupancy penalty，也叫平衡项） |
| label | TEXT | 论文里的格子名，如 no pretrain, + balance |
| epochs | INTEGER | 训练轮数（16 种配置相同） |
| batch_size | INTEGER | 批大小 |
| latent_dim | INTEGER | 潜空间维度 |
| learning_rate | REAL | 学习率 |
| pretrain_epochs | INTEGER | 预训练轮数（只在 pretrain=1 时生效） |
| weibull_shape | REAL | Weibull 形状参数 |

### controls（20 行）

对照实验（MLP，参考配置）：gaussian = 输入换成高斯噪声；shuffled = 打乱生存结局与输入的对应关系。每组 10 个种子，在验证集上评估。

来源：论文最终版（端点 B）实验输出，scripts/import_results.py 导入并与论文表 4.1/4.4/4.6 核对

| 列 | 类型 | 含义 |
|---|---|---|
| arm | TEXT | gaussian 或 shuffled |
| seed | INTEGER | 随机种子 0–9 |
| cindex_real | REAL | 按真实结局计算的 C-index |
| logrank_real_stat | REAL | 按真实结局的 log-rank 统计量 |
| logrank_real_p | REAL | 按真实结局的 log-rank p 值 |
| logrank_shuffled_stat | REAL | 按打乱后结局的 log-rank 统计量（只有 shuffled 组有） |
| logrank_shuffled_p | REAL | 按打乱后结局的 log-rank p 值（只有 shuffled 组有） |
| cluster_sizes | TEXT | 各簇人数，JSON 数组 |
| min_cluster_share | REAL | 最小簇占比 |
| collapsed | INTEGER | 1 = 塌缩 |

### pairwise_ari（720 行）

同一配置内种子两两之间的 ARI（每配置 45 对）。

来源：论文最终版（端点 B）实验输出，scripts/import_results.py 导入并与论文表 4.1/4.4/4.6 核对

| 列 | 类型 | 含义 |
|---|---|---|
| config_id | TEXT | 关联 configs.config_id |
| seed_a | INTEGER | 较小的种子 |
| seed_b | INTEGER | 较大的种子 |
| ari | REAL | 两次聚类结果的 ARI |

### runs（160 行）

160 次训练（每个配置 10 个种子 0–9）在验证集（1766 人）上的结果。

来源：论文最终版（端点 B）实验输出，scripts/import_results.py 导入并与论文表 4.1/4.4/4.6 核对

| 列 | 类型 | 含义 |
|---|---|---|
| run_id | TEXT | 原始输出目录名 |
| config_id | TEXT | 关联 configs.config_id |
| seed | INTEGER | 随机种子 0–9 |
| ci_valid | REAL | 验证集 C-index（训练时记录，3 位小数） |
| ci_train_wt | REAL | 训练集 C-index（使用时间信息） |
| ci_train_wot | REAL | 训练集 C-index（不使用时间信息） |
| n_valid | INTEGER | 验证集人数 |
| cluster_sizes | TEXT | 各簇人数，JSON 数组，按簇编号 |
| min_cluster_share | REAL | 最小簇占比（0–1） |
| collapsed | INTEGER | 1 = 塌缩（最小簇占比 < 1%） |
| logrank_stat | REAL | 验证集各簇生存曲线的 log-rank 统计量（按修正后的结局） |
| logrank_p | REAL | log-rank 检验 p 值 |
| seed_level_ari | REAL | 这个种子与同配置其他 9 个种子的平均 ARI |

### sample_results（30 行）

阶段 3 的合成示例数据（30 行），不是论文的真实实验结果；只在用户明确问示例数据时使用

来源：sample_results.csv，scripts/import_csv.py 导入

| 列 | 类型 | 含义 |
|---|---|---|
| config | TEXT | 实验条件名（baseline / survival_sup 等） |
| seed | INTEGER | 随机种子 |
| ari | REAL | ARI |
| c_index | REAL | C-index |

### test_anchors（8 行）

8 条预先设定的临床方向：在验证集上确定，在测试集上检验是否一致。

来源：论文最终版（端点 B）实验输出，scripts/import_results.py 导入并与论文表 4.1/4.4/4.6 核对

| 列 | 类型 | 含义 |
|---|---|---|
| cluster | INTEGER | 关联 test_clusters.cluster |
| variable | TEXT | 临床变量名 |
| expected_direction | INTEGER | 1 = 预期该簇更高，-1 = 预期更低 |
| smd | REAL | 测试集上的标准化均值差 |
| q | REAL | BH-FDR 校正后的 p 值 |
| n_cluster | INTEGER | 该簇有记录的人数 |
| n_rest | INTEGER | 其余患者有记录的人数 |
| direction_matches | INTEGER | 1 = 测试集方向与预期一致 |
| estimable | INTEGER | 1 = 可估计 |

### test_cluster_profiles（111 行）

测试集 GRU 各簇与其余患者的临床变量对比（没有中位数）。

来源：论文最终版（端点 B）实验输出，scripts/import_results.py 导入并与论文表 4.1/4.4/4.6 核对

| 列 | 类型 | 含义 |
|---|---|---|
| cluster | INTEGER | 关联 test_clusters.cluster |
| variable | TEXT | 临床变量名 |
| n_cluster | INTEGER | 该簇有记录的人数 |
| n_rest | INTEGER | 其余患者有记录的人数 |
| smd | REAL | 标准化均值差 |
| p | REAL | p 值 |
| q | REAL | BH-FDR 校正后的 p 值 |

### test_clusters（3 行）

测试集聚类：GRU 在测试集上的共识划分，簇编号已对齐到 GRU 验证集共识。

来源：论文最终版（端点 B）实验输出，scripts/import_results.py 导入并与论文表 4.1/4.4/4.6 核对

| 列 | 类型 | 含义 |
|---|---|---|
| cluster | INTEGER | 对齐后的簇编号 = GRU 验证集的 consensus_label |
| val_mortality_rank | INTEGER | 对应的 GRU 验证集死亡率名次（关联 val_clusters，encoder = 'GRU'） |
| rebuilt_label | INTEGER | 对齐前的簇编号 |
| n | INTEGER | 簇人数 |
| deaths | INTEGER | 死亡人数 |
| mortality | REAL | 死亡率（0–1） |

### test_pairwise_ari（90 行）

测试集上种子两两之间的 ARI（每个编码器 45 对）。

来源：论文最终版（端点 B）实验输出，scripts/import_results.py 导入并与论文表 4.1/4.4/4.6 核对

| 列 | 类型 | 含义 |
|---|---|---|
| encoder | TEXT | MLP 或 GRU |
| seed_a | INTEGER | 较小的种子 |
| seed_b | INTEGER | 较大的种子 |
| ari | REAL | ARI |

### test_runs（20 行）

测试集（1763 人）最终评估：参考配置（is_reference = 1）的 20 次训练（每个编码器 10 个种子）在测试集上的结果。

来源：论文最终版（端点 B）实验输出，scripts/import_results.py 导入并与论文表 4.1/4.4/4.6 核对

| 列 | 类型 | 含义 |
|---|---|---|
| encoder | TEXT | MLP 或 GRU |
| seed | INTEGER | 随机种子 0–9 |
| cindex | REAL | 测试集 C-index |
| logrank_stat | REAL | 测试集 log-rank 统计量 |
| logrank_p | REAL | 测试集 log-rank p 值 |
| cluster_sizes | TEXT | 各簇人数，JSON 数组 |
| min_cluster_share | REAL | 最小簇占比 |
| collapsed | INTEGER | 1 = 塌缩 |
| seed_level_ari | REAL | 测试集上与其他 9 个种子的平均 ARI |

### val_cluster_profiles（222 行）

验证集各簇与其余患者的临床变量对比（论文表 4.4、图 4.2）。

来源：论文最终版（端点 B）实验输出，scripts/import_results.py 导入并与论文表 4.1/4.4/4.6 核对

| 列 | 类型 | 含义 |
|---|---|---|
| encoder | TEXT | MLP 或 GRU |
| mortality_rank | INTEGER | 关联 val_clusters.mortality_rank（同一 encoder） |
| variable | TEXT | 临床变量名（如 BUN、GCS、Creatinine） |
| n_cluster | INTEGER | 该簇有这个变量记录的人数 |
| n_rest | INTEGER | 其余患者有记录的人数 |
| median_cluster | REAL | 该簇中位数 |
| median_rest | REAL | 其余患者中位数 |
| smd | REAL | 标准化均值差，正值 = 该簇更高 |
| p | REAL | Mann–Whitney 检验 p 值 |
| q | REAL | BH-FDR 校正后的 p 值 |
| measured_pct | REAL | 该变量的记录覆盖率（%） |

### val_clusters（6 行）

验证集聚类（论文表 4.4）：每个编码器参考配置 10 次训练的共识划分（K=3）。论文以 GRU 为主，MLP 作对照。

来源：论文最终版（端点 B）实验输出，scripts/import_results.py 导入并与论文表 4.1/4.4/4.6 核对

| 列 | 类型 | 含义 |
|---|---|---|
| encoder | TEXT | MLP 或 GRU |
| mortality_rank | INTEGER | 按验证集死亡率排序，0 = 最低，2 = 最高 |
| consensus_label | INTEGER | 共识划分里的原始簇编号 |
| n | INTEGER | 簇人数 |
| mortality | REAL | 死亡率（0–1 的比例） |


## 库里没有

- 患者级数据、原始临床时间序列、dip 检验、严重度十分位、K-means / Ward 等对比方法、K=2–8 扫描、潜空间 PCA、K=5 的聚类画像、早期流程（验证集 1774 人）的结果。问到这些要回答数据库里没有。

## 数据事实

- 验证集 1766 人，测试集 1763 人；验证集和测试集结果在不同的表里，不要混在一个聚合里。
- MLP 不预训练、K=3 时，加不加平衡项 10 个种子的聚类结果完全相同。
- MLP 共塌缩 41/80 次：预训练的 40 次全部塌缩，另 1 次是 K=5、不预训练、不加平衡；GRU 没有塌缩。

## 术语

- ARI（调整兰德指数）：两次聚类结果的一致程度，1 = 完全一致，0 ≈ 随机水平，可以为负。
- 跨种子稳定性：一个配置 10 个种子 seed_level_ari 的平均值（论文报告的就是它）；标准差用总体标准差（分母 10）。
- C-index：生存预测的区分能力，0.5 = 随机，越高越好。
- 塌缩：最小簇占比低于 1%。塌缩的划分 ARI 仍能算出来，但不代表稳定，引用塌缩配置的数字时必须同时说明塌缩。
- 参考配置：不预训练、加平衡、K=3（configs.is_reference = 1）；验证集画像和测试集评估都用它。
