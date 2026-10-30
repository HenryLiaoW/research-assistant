"""导入脚本的离线测试：ARI 实现和 txt 解析。python -m unittest tests.test_import_results -v

真实数据的正确性由导入脚本自己的核对保证（和论文冻结的汇总表逐项比对）；这里只测不依赖真实数据的部分。
"""

import importlib.util
import unittest
from pathlib import Path

import numpy as np

_spec = importlib.util.spec_from_file_location(
    "import_results", Path(__file__).resolve().parent.parent / "scripts" / "import_results.py")
ir = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ir)

RESULTS_TXT = """Epochs= 200, batch_size= 256, latent_dim= 16, K= 3, mc samples= 1, weibull_shape= 1, learning_rate= 0.001000, pretrain_e= 50, decay= 0.000010, name= K3_seed0_run0_x, survival= True, sample_surv= True, seed= 0.
epochs_pretrain: 50. Pretrain accuracy: 0.432782 , Train (w t)    |   Accuracy: 0.429, NMI: 0.000, ARI: 0.000. CI: 0.791, RAE (nc.): 0.876, RAE (c.): 0.008.
Train (w/o t)  |   Accuracy: 0.428, NMI: 0.000, ARI: 0.000. CI: 0.785, RAE (nc.): 0.882, RAE (c.): 0.009.
Valid (w/o t)  |   Accuracy: 0.435, NMI: 0.000, ARI: 0.000. CI: 0.753, RAE (nc.): 0.881, RAE (c.): 0.009.
"""


class TestARI(unittest.TestCase):
    def test_identical_and_relabelled(self):
        a = [0, 0, 1, 1, 2, 2]
        self.assertEqual(ir.ari(a, a), 1.0)
        self.assertEqual(ir.ari(a, [2, 2, 0, 0, 1, 1]), 1.0)     # 只换编号不改划分

    def test_known_value(self):
        # sklearn 文档里的例子：adjusted_rand_score([0, 0, 1, 1], [0, 0, 1, 2]) = 0.5714...
        self.assertAlmostEqual(ir.ari([0, 0, 1, 1], [0, 0, 1, 2]), 0.5714285714285714)

    def test_independent_is_near_zero(self):
        rng = np.random.default_rng(0)
        self.assertLess(abs(ir.ari(rng.integers(0, 3, 5000), rng.integers(0, 3, 5000))), 0.01)

    def test_length_mismatch(self):
        with self.assertRaises(ValueError):
            ir.ari([0, 1], [0, 1, 1])

    def test_seed_level(self):
        pairs = {(0, 1): 0.2, (0, 2): 0.4, (1, 2): 0.6}
        self.assertEqual(ir.seed_level(pairs, [0, 1, 2]), {0: 0.30000000000000004, 1: 0.4, 2: 0.5})


class TestParsing(unittest.TestCase):
    def test_results_txt(self):
        r = ir.parse_results_txt(RESULTS_TXT)
        self.assertEqual((r["k"], r["seed"], r["epochs"], r["latent_dim"], r["pretrain_epochs"]), (3, 0, 200, 16, 50))
        self.assertEqual((r["ci_train_wt"], r["ci_train_wot"], r["ci_valid"]), (0.791, 0.785, 0.753))

    def test_diagnostics(self):
        text = ("train w/t   sizes=[2574, 2152, 3553]  logrank_p=5.051e-50  stat=227.0\n"
                "valid w/o t sizes=[497, 506, 771]  logrank_p=5.392e-06  stat=24.3\n")
        self.assertEqual(ir.parse_diagnostics(text), (24.3, 5.392e-06))
        self.assertEqual(ir.parse_diagnostics("nothing here"), (None, None))

    def test_label(self):
        self.assertEqual(ir.label_of(0, 1), "no pretrain, + balance")


if __name__ == "__main__":
    unittest.main()
