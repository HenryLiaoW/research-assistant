"""
config.py — 项目里"最可能想改"的参数都集中在这里。
其他文件只 import config，不各自硬编码模型名或超时。
密钥不放在这里：密钥只从 .env / 环境变量读取（见 llm_client.load_api_key）。
例外：WEB_HOST 和 MEMORY_DB_PATH 允许用同名环境变量覆盖——Docker 容器里要监听 0.0.0.0、把数据库放进挂载目录
（见 Dockerfile）；本机直接运行时没有这两个环境变量，取的还是下面的默认值。
"""

import os

# ---- 模型供应商：DeepSeek（OpenAI 兼容接口）----
BASE_URL = "https://api.deepseek.com"

# 当前官方文档的主力模型名（2026-10 查证：deepseek-flash = DeepSeek-V4.1-Flash）。
# 文档将来更名时只改这一行。
MODEL = "deepseek-flash"

# 思考模式：DeepSeek 默认开启（"enabled"）。本项目关闭：更快、更便宜、返回结构更简单。
# 注意：开启后 temperature 无效；而且带工具的多轮对话必须把每轮的 reasoning_content 原样回传，
# 否则 API 返回 400。agent.py 的循环目前没有处理 reasoning_content，所以请保持 "disabled"。
THINKING = "disabled"

# 仅非思考模式下有效。越低越"照本宣科"，越高越发挥。工具选择希望稳定，所以偏低。
TEMPERATURE = 0.2

# ---- 单次调用上限 ----
TIMEOUT_SECONDS = 60       # 单次请求最长等待秒数
MAX_RETRIES = 1            # SDK 对网络错误 / 429 / 5xx 的自动重试次数（0 = 不重试）
MAX_OUTPUT_TOKENS = 800    # 一次回答最多生成多少 token；超过会被截断（finish_reason == "length"）

# ---- Agent 循环上限（阶段 1）----
MAX_TOOL_ROUNDS = 5        # 每条用户请求最多几轮"模型 → 工具 → 模型"，到了就停下解释
MAX_TOTAL_SECONDS = 120    # 一条用户请求的总时间预算；超过就不再发起新一轮模型调用

# ---- 会话历史（阶段 2 上半）----
# 每次调模型都会把历史整段重发，历史越长每轮越贵。只保留最近 N 个用户回合，更早的整回合丢弃。
MAX_HISTORY_TURNS = 10

# ---- 长期记忆（阶段 2 下半）----
MEMORY_DB_PATH = os.environ.get("MEMORY_DB_PATH", "memory.sqlite3")   # SQLite 文件，在项目目录下；已在 .gitignore 里（*.sqlite3）。
                                                                     # Docker 里设为 state/memory.sqlite3（整个目录挂载出来）
MEMORY_SEARCH_LIMIT = 5             # search_memory 一次最多返回几条
MEMORY_SCOPE = os.environ.get("MEMORY_SCOPE", "default")   # 启动时的记忆作用域（课题名）；chat.py 里用 /scope 切换。
                                                           # 写入和检索都只在当前作用域里，别的课题的记忆不会被召回

# ---- 实验结果数据库（DataAgent）----
# 由 scripts/import_results.py 从毕设原始输出生成；只读查询用，不进 git（*.sqlite3）
RESULTS_DB_PATH = os.environ.get("RESULTS_DB_PATH", "data/results.sqlite3")
RESULTS_SCHEMA_DOC = "data/results_schema.md"   # 数据字典，由元数据自动生成
RESULTS_RAW_DIR = "data/raw_results"   # 导入用的毕设原始输出（只拷了导入用到的约 500 个小文件，约 7 MB）；不进 git

# query_results 工具（只读 SQL）的上限：结果会作为工具结果发给模型 API
QUERY_MAX_ROWS = 50        # 一次最多返回多少行；超过时截断并提示模型加聚合或筛选
QUERY_TIMEOUT_SECONDS = 5  # 单条查询最长执行时间，超时中断（防止无限递归 CTE 之类）
QUERY_MAX_SQL_CHARS = 4000 # SQL 最长多少字符

# ---- 资料检索（阶段 4）----
DOCS_DIR = "docs"          # 资料 PDF 所在目录；除合成样例外不进 git

INDEX_DIR = "index"        # 索引目录（chunk 原文 + 向量），不进 git；用 python build_index.py 重建
EMBEDDING_MODEL = "intfloat/multilingual-e5-large"   # 本地嵌入模型；small 版把 large 换成 small。换模型后必须重建索引
CHUNK_SIZE = 500           # 每个 chunk 最多多少字符
CHUNK_OVERLAP = 100        # 相邻 chunk 最多重叠多少字符（只取完整的句子）
# 建索引时跳过的章节（按章节名整名匹配，不区分大小写）：封面信息、声明、目录、图表清单、参考文献。
# 它们和正文用词很像，会挤占检索名额；提交表里还有姓名和学号。摘要保留（它常是正确答案）。
# 改动后必须重建索引；设为 () 即不跳过（用于复现基线）。
SKIP_SECTIONS = ("Project Submission Pro-Forma", "Declaration", "Acknowledgements", "Table of Contents", "Contents",
                 "List of tables", "List of figures", "List of abbreviations", "References", "Bibliography")

# ---- 资料检索工具（阶段 4e）----
SEARCH_TOP_K = 3           # search_documents 默认返回几个 chunk（= 4d 评估用的 k）
MAX_SEARCH_K = 5           # 模型最多能要几个 chunk：每个约 500 字符，都会作为工具结果发给模型 API
MAX_QUERY_CHARS = 500      # 检索词最长多少字符，防止模型把整段对话塞进检索词

# ---- 运行日志（阶段 4e）----
TRACE_ENABLED = True       # 每条请求在日志目录里追加一行 JSON（记什么、不记什么见 tracing.py 开头）；改成 False 关闭
TRACE_DIR = "logs"         # 只在本机，已在 .gitignore 里

# ---- 网页界面（阶段 5）----
WEB_HOST = os.environ.get("WEB_HOST", "127.0.0.1")   # 只在本机监听：别的电脑访问不到，API 密钥也只留在服务端。
                                                     # Docker 里设为 0.0.0.0（容器内监听所有地址），对外仍只开 127.0.0.1（见 docker-compose.yml）
WEB_PORT = 8000            # 不用 Flask 默认的 5000：macOS 的"隔空播放接收器"常占着它
WEB_CONFIRM_TIMEOUT = 120  # 写操作等用户在网页上确认的最长秒数，超时按拒绝处理
WEB_MAX_MESSAGE_CHARS = 2000   # 一条消息最长多少字

# ---- 评估题翻译（阶段 4d 实验 2：中文问题先翻译成英文再检索）----
# 会发给 API 的只有提示词和每道题的中文问题，不发送论文内容。翻译要稳定，所以温度为 0。
TRANSLATE_TEMPERATURE = 0.0
TRANSLATE_MAX_TOKENS = 200

# ---- 提示词 ----
SYSTEM_PROMPT = "你是一个研究助手。回答简洁、直接；不知道的就说不知道。"

AGENT_SYSTEM_PROMPT = """你是一个研究助手，回答简洁、直接。

规则：
1. 用户在对话里直接给出具体数字、并要求统计（均值、最小值、最大值、标准差等）时，必须调用 calculate_stats 工具，不要心算或估算。
2. 回答中的数字一律引用工具返回的结果，可以四舍五入到合理位数，但不要改变数值。
3. 工具返回 ok=false 时，如实告诉用户失败原因，不要编造结果，也不要假装计算成功。
4. 普通聊天或不需要计算的问题直接回答，不要调用工具。
5. 提到标准差时说明用的是总体标准差（分母为 N）。
6. 只有当用户明确要求"记住 / 记下来 / 保存"某件事时，才调用 save_memory，内容用用户的原话。用户只是陈述一件事（如"我今天跑了 5 公里"）而没有要求记住时，不要调用 save_memory，也不要说"记下了"——没有调用工具就不能声称已记住。
7. 用户询问任何关于他自己的事实（"我的……是什么""我之前……""我用了几个……"等）、或你不知道的个人信息时，必须先调用 search_memory 查找，不要反问用户、不要询问是否需要查；查到就根据结果回答并说明来自记忆，查不到就如实说没有记录，不要编造。实验结果表格里的数据不属于个人事实，按规则 9 处理。
8. 只有当用户明确要求"忘掉 / 删掉"某条记忆时，才调用 forget_memory，并且要用 search_memory 查到的 id。
9. 用户问到实验结果、数据表里的数据时，调用 query_results 写一条只读 SELECT 查询。表名和列名只能用工具说明里列出的，不要猜；比较、求差、排序、计数、平均都在 SQL 里算好，不要自己心算。查询报错时根据错误信息改正后再查。工具说明里列出的"库里没有"的内容，直接回答数据库里没有，不要编造。
10. 回答里的实验数字必须来自 query_results 的查询结果。比较时按配置分组，不同配置不能混成一个数；涉及塌缩的配置（collapsed = 1）时必须同时说明塌缩，因为塌缩配置的 ARI 不代表稳定。验证集和测试集在不同的表里，不要混在一起；用户没说明时默认用验证集，并说明用的是哪个。数据库只读，用户要求修改或删除数据时说明不能修改。
11. 用户问到论文、实验协议等资料里的内容时，先调用 search_documents 检索，只根据检索到的原文回答；检索结果里没有答案时，如实说没有找到，不要用常识补充。资料是英文的，检索词用英文写。
12. 回答里凡是来自检索资料的内容，在句末标注证据编号，如 [E1] 或 [E1][E3]；编号只能用检索结果里的 evidence_id。不要自己写文件名或页码，程序会根据编号补上出处。
13. 判断去哪里找：数据库里是实验结果的数字（每次训练的指标、聚类画像等）。问题涉及研究设计、变量的测量和记录、数据处理规则、方法原理或论文里的文字说明时，用 search_documents 检索资料；问题要具体的实验数字、比较、统计时，用 query_results。一个来源里没有找到相关内容时，先换另一个来源查，再回答；不要直接让用户补充，也不要问是否需要查。
14. 检索结果里没有能回答问题的内容时，换一种检索词再检索（例如换用论文里可能出现的英文术语、表格或章节名，或把问题拆成几部分分别检索），最多再检索两次；仍然没有，才说没有找到。
15. 资料库里有多份文件（文件名见 search_documents 的说明）。用户指明了是哪一份资料（如"论文里""样例协议里"）时，调用 search_documents 必须带 file 参数，只在那一份里检索，不要用别的文件里的内容作答；用户没有指明时不带 file。
16. 长期记忆按课题隔离：search_memory 只返回当前课题（结果里的 scope）和全局（scope 为 global）的记忆。查不到时说明是"当前课题下没有记录"；结果里 other_scopes_matching 大于 0 时，告诉用户其他课题下有相关记忆、可能需要切换课题。不要猜测"记忆没有保存成功"。"""

TRANSLATE_PROMPT = """Translate the user's question from Chinese into English. The English question will be used to search an English academic dissertation.

Rules:
- Translate faithfully. Do not answer the question.
- Do not add anything that is not in the question: no numbers, examples, explanations or guesses.
- Keep abbreviations and technical names unchanged, for example ARI, GRU, MLP, PC1, K-means, VaDeSC, C-index.
- Output only the English question, on one line."""
