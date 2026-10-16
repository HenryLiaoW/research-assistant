# 学习记录

每个阶段跑通后，用自己的话写三句：加了什么、为什么这样设计、哪里是学习用的简化。
写不出来就说明还没真正看懂，回去再读一遍那一轮的"核心原理"。

## 阶段 0 —— 一次普通模型请求（2026-10-16 跑通）

验收记录：`python ask.py "用一句话解释什么是总体标准差"` → `finish_reason=stop`，prompt 26 / completion 37 tokens，耗时 1.13 秒。

1. 这轮加了什么：
2. 为什么密钥放 .env、参数放 config.py、逻辑放 ask.py：
3. 学习用的简化（以后要改的地方）：

练习：把 THINKING 改成 "enabled" 再跑一次，completion tokens 和耗时变成了多少？为什么？
